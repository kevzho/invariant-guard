"""Agent-facing service shared by the MCP server and ``guard explain`` (M5, docs/09 "MCP integration").

Every tool calls the same orchestration code as the CLI (``run_verify``, ``run_check``,
``contracts.commands``) and returns a versioned JSON envelope:

    {"schema_version": "1.0", "tool": NAME, "status": STATUS, ...}

``status`` is one of

* ``OK``: the tool ran; for verify/check the verdict is in ``verdict`` (BLOCK is still OK).
* ``STALE_SNAPSHOT``: the evidence does not describe the current working tree (it
  changed during the run even after one restart, or it changed after the evidence was
  produced). Data is returned, flagged, never attributed to the current code.
* ``TIMEOUT`` / ``CANCELLED``: the run was stopped; it is finalized as failed and none of
  its cache entries or contract evidence is published (T52).
* ``INVALID_REQUEST``, ``NOT_AUTHORIZED``, ``NOT_FOUND``, ``ENGINE_ERROR``.

Restrictions (T51):

* Repositories: only configured roots, named by alias (or by the exact root path). A
  ``path`` argument is a relative scope that must stay inside the root after resolving
  symlinks. Configuration is read only from ``<root>/.guard/config.toml``.
* Tools: read/verify only. Contract approval/revision/rejection, baseline creation,
  runner setup, model discovery and arbitrary command execution are not tools.
* Execution: ``verify_patch(run_profile=NAME)`` runs dynamic checks only when NAME was
  preauthorized when the server started (``--allow-profile``) AND its definition is
  byte-for-byte the one recorded then (a repository edit of the profile revokes it) AND
  it uses the isolated ``docker`` backend. There is no unisolated execution through MCP.

Nothing here writes to stdout. Nothing here imports the MCP SDK.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import ENGINE_VERSION, cancellation
from .contracts.profiles import profile_label
from .config import CONFIG_DIR, CONFIG_NAME, ConfigError, load_config
from .models.spans import canonical_json, sha256_bytes
from .orchestration import CheckOptions, CheckResult, UsageError

SERVICE_SCHEMA_VERSION = "1.0"
ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FINDING_RE = re.compile(r"^fnd-[0-9a-f]{8,64}$")
RUN_RE = re.compile(r"^run-[0-9a-f]{8,64}$")
DEFAULT_TIMEOUT_S = 600.0
SESSION_REPORTS = 32
MAX_LIST = 50

STATUSES = ("OK", "STALE_SNAPSHOT", "TIMEOUT", "CANCELLED", "INVALID_REQUEST", "NOT_AUTHORIZED", "NOT_FOUND", "ENGINE_ERROR")
ERROR_STATUSES = frozenset({"TIMEOUT", "CANCELLED", "INVALID_REQUEST", "NOT_AUTHORIZED", "NOT_FOUND", "ENGINE_ERROR"})

NOT_EXPOSED = {
    "contracts declare/approve/revise/reject/retire/import": "authority changes are maintainer actions (guard contracts ... --reason TEXT); an agent cannot approve or revise a contract",
    "baseline create": "baselines are recorded only by a maintainer (guard baseline create); verify never writes or replaces one",
    "behavior accept/pin/retire": ("deciding that a behavior change was intended, or pinning behavior as approved, is a maintainer action "
                                   "(guard behavior ... --reason TEXT); an agent answers a replay finding's question in its patch rationale"),
    "runner setup": "preparing an execution environment may use the network; it is an explicit maintainer command",
    "contracts discover": ("model/template discovery is slow, opt-in and never approves; it stays a CLI command (guard contracts discover). The calling "
                           "agent can propose candidates itself with get_context_packet + propose_contracts (recorded INFERRED only)"),
    "arbitrary command execution": "no tool runs a command, test or script; dynamic checks run only through a preauthorized isolated run profile",
}

ENFORCEMENT_NOTE = ("MCP exposes verification; it cannot force an agent to call it or to respect a BLOCK. Enforced acceptance needs a client hook "
                    "or CI that runs `guard verify` and checks the exit code.")


class ServiceError(Exception):
    def __init__(self, status: str, message: str, **extra: Any) -> None:
        assert status in STATUSES
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


# -- tool specifications (also the MCP tools/list input schemas) -----------------------------------------
_REPO = {"type": "string", "minLength": 1, "maxLength": 4096,
         "description": "alias of a configured repository root (see get_capabilities), or that root's exact absolute path"}
_FINDING = {"type": "string", "pattern": FINDING_RE.pattern, "description": "finding_id from a verify_patch/check_scope result"}
_RUN = {"type": "string", "pattern": RUN_RE.pattern, "description": "optional run_id; default: the newest report that contains the finding"}

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "verify_patch": {
        "description": ("Verify the current working tree of a configured repository against an explicit base (Git commit/ref or baseline:LABEL): "
                        "static runtime-failure analysis of the affected slice (or the whole scope with mode=full) plus approved behavioral "
                        "contracts. With run_profile (preauthorized, isolated) it also replays contracts in the isolated runner. Never modifies the "
                        "repository, never approves anything. Returns verdict, coverage and compact findings; fetch full evidence with get_finding."),
        "read_only": False,  # writes only its own state under <root>/.guard (cache, evidence, report index)
        "input_schema": {
            "type": "object", "additionalProperties": False, "required": ["repository", "base"],
            "properties": {
                "repository": _REPO,
                "base": {"type": "string", "minLength": 1, "maxLength": 256,
                         "description": "base to compare with: a commit or ref (nothing is checked out), git:REF, or baseline:LABEL"},
                "mode": {"type": "string", "enum": ["quick", "full"], "default": "quick"},
                "path": {"type": "string", "maxLength": 1024, "default": ".", "description": "relative scope inside the repository"},
                "run_profile": {"type": "string", "maxLength": 64,
                                "description": "preauthorized isolated run profile for dynamic contract checks (see get_capabilities)"},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact",
                           "description": "full also returns the complete schema-valid report"},
                "timeout_s": {"type": "number", "exclusiveMinimum": 0, "description": "wall-clock limit for this call (capped by the server's limit)"},
                "no_cache": {"type": "boolean", "default": False},
                "replay": {"type": "boolean", "default": False,
                           "description": "with run_profile: differential replay of the changed symbols on base and candidate; returns confirmed "
                                          "behavior changes with a replayable witness and the question whether each was intended"},
            },
        },
    },
    "check_scope": {
        "description": ("Static single-snapshot analysis of a path in a configured repository (no base, so no patch attribution). Never imports "
                        "or runs the code. Findings are kept for this server session (get_finding)."),
        "read_only": True,
        "input_schema": {
            "type": "object", "additionalProperties": False, "required": ["repository"],
            "properties": {
                "repository": _REPO,
                "path": {"type": "string", "maxLength": 1024, "default": "."},
                "detail": {"type": "string", "enum": ["compact", "full"], "default": "compact"},
                "timeout_s": {"type": "number", "exclusiveMinimum": 0},
            },
        },
    },
    "get_contracts": {
        "description": "List the repository's behavioral contracts with authority, lifecycle and latest evidence (read-only; approval is not available here).",
        "read_only": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["repository"], "properties": {"repository": _REPO}},
    },
    "get_finding": {
        "description": ("Fetch the full evidence for one finding: the finding, its obligation or contract result, the evidence records and the "
                        "run identity, plus whether that evidence still describes the current working tree."),
        "read_only": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["repository", "finding_id"],
                         "properties": {"repository": _REPO, "finding_id": _FINDING, "run_id": _RUN}},
    },
    "explain_finding": {
        "description": ("Explain one finding from its stored evidence: what was established, under which domain and assumptions, what the "
                        "witness shows, what it does not show, and what would resolve it. Deterministic (no model)."),
        "read_only": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["repository", "finding_id"],
                         "properties": {"repository": _REPO, "finding_id": _FINDING, "run_id": _RUN}},
    },
    "get_context_packet": {
        "description": ("The exact bounded ContextPacket for one contract target (the target function, selected callers/callees/tests, trusted static "
                        "facts), plus the candidate JSON schema, the allowed DSL node list and the proposer protocol. Use it to propose behavioral "
                        "contracts with propose_contracts. Reads source only; never imports or runs it; writes nothing."),
        "read_only": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["repository", "symbol"],
                         "properties": {"repository": _REPO, "symbol": {"type": "string", "minLength": 4, "maxLength": 512,
                                                                         "description": "target symbol id, e.g. pkg/mod.py::fn"}}},
    },
    "propose_contracts": {
        "description": ("Propose behavioral contract candidates for one target, written against the packet from get_context_packet (pass its "
                        "context_packet_digest). Every candidate is validated by the same strict pipeline as model output (schema, typed DSL, "
                        "source references, non-vacuity, duplicates) and, only with a preauthorized isolated run_profile, falsified in the runner. "
                        "Accepted candidates are recorded as INFERRED with agent_proposed provenance: never approved, never evidence or proof. "
                        "Returns per-candidate accept/reject reasons and evidence ids. There is no approve tool."),
        "read_only": False,  # records INFERRED candidates and discovery artifacts under <root>/.guard
        "input_schema": {
            "type": "object", "additionalProperties": False, "required": ["repository", "symbol", "context_packet_digest", "candidates"],
            "properties": {
                "repository": _REPO,
                "symbol": {"type": "string", "minLength": 4, "maxLength": 512},
                "context_packet_digest": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
                "candidates": {"type": "array", "maxItems": 8, "items": {"type": "object"},
                               "description": "candidate objects as described by candidate_schema in get_context_packet"},
                "run_profile": {"type": "string", "maxLength": 64,
                                "description": "preauthorized isolated run profile used to falsify accepted candidates (see get_capabilities)"},
                "dry_run": {"type": "boolean", "default": False, "description": "validate (and falsify) but record nothing"},
                "timeout_s": {"type": "number", "exclusiveMinimum": 0},
            },
        },
    },
    "get_capabilities": {
        "description": "Engine, solver, execution and model capabilities, the configured repository roots and the preauthorized run profiles.",
        "read_only": True,
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
}
TOOLS = tuple(TOOL_SPECS)

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["schema_version", "tool", "status"],
    "properties": {"schema_version": {"const": SERVICE_SCHEMA_VERSION}, "tool": {"type": "string"}, "status": {"enum": list(STATUSES)},
                   "message": {"type": "string"}},
}


def validate_args(tool: str, args: Any) -> dict[str, Any]:
    """Minimal closed validation of tool arguments (no unknown keys, declared types and enums)."""
    if tool not in TOOL_SPECS:
        raise ServiceError("INVALID_REQUEST", f"unknown tool {tool!r}; tools: {', '.join(TOOLS)}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ServiceError("INVALID_REQUEST", "arguments must be an object")
    schema = TOOL_SPECS[tool]["input_schema"]
    props = schema["properties"]
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ServiceError("INVALID_REQUEST", f"unknown argument(s) {unknown} for {tool}")
    for k in schema.get("required", []):
        if k not in args:
            raise ServiceError("INVALID_REQUEST", f"{tool} needs argument {k!r}")
    out: dict[str, Any] = {}
    for k, spec in props.items():
        if k not in args:
            if "default" in spec:
                out[k] = spec["default"]
            continue
        v = args[k]
        t = spec["type"]
        ok = (isinstance(v, str) if t == "string" else isinstance(v, bool) if t == "boolean"
              else (isinstance(v, (int, float)) and not isinstance(v, bool)) if t == "number"
              else isinstance(v, list) if t == "array" else False)
        if not ok:
            raise ServiceError("INVALID_REQUEST", f"argument {k!r} must be a {t}")
        if t == "array":
            if len(v) > spec.get("maxItems", MAX_LIST):
                raise ServiceError("INVALID_REQUEST", f"argument {k!r} has more than {spec.get('maxItems', MAX_LIST)} items")
            if spec.get("items", {}).get("type") == "object" and not all(isinstance(x, dict) for x in v):
                raise ServiceError("INVALID_REQUEST", f"every item of {k!r} must be an object")
        if t == "string":
            if "\x00" in v or len(v) > spec.get("maxLength", 4096) or len(v) < spec.get("minLength", 0):
                raise ServiceError("INVALID_REQUEST", f"argument {k!r} has an invalid length or a NUL byte")
            if "enum" in spec and v not in spec["enum"]:
                raise ServiceError("INVALID_REQUEST", f"argument {k!r} must be one of {spec['enum']}")
            if "pattern" in spec and not re.fullmatch(spec["pattern"], v):
                raise ServiceError("INVALID_REQUEST", f"argument {k!r} is not a valid identifier")
        if t == "number" and not (v > spec.get("exclusiveMinimum", float("-inf"))):
            raise ServiceError("INVALID_REQUEST", f"argument {k!r} must be > {spec['exclusiveMinimum']}")
        out[k] = v
    return out


# -- roots and preauthorized profiles ------------------------------------------------------------------------
def _file_digest(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


def _profile_digest(path: Path, name: str) -> tuple[str | None, str]:
    """Digest of a profile's resolved definition in ``path`` (None + reason if unusable through MCP)."""
    from .execution.profiles import authorization_digest

    try:
        cfg = load_config(path)
    except ConfigError as exc:
        return None, f"not usable: {exc}"
    return authorization_digest(cfg, name)


@dataclass
class RootEntry:
    alias: str
    path: Path
    config_digest_at_start: str | None
    profiles: dict[str, str] = field(default_factory=dict)  # name -> definition digest at start
    profile_notes: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def config_path(self) -> Path:
        return self.path / CONFIG_DIR / CONFIG_NAME

    def config_arg(self) -> Path | None:
        return self.config_path if self.config_path.is_file() else None


def parse_root_spec(spec: str) -> tuple[str, Path]:
    alias, sep, rest = spec.partition("=")
    if not sep:
        alias, rest = "", spec
    path = Path(rest).expanduser()
    if not path.is_absolute():
        raise UsageError(f"--root {spec!r}: the path must be absolute (it is fixed when the server starts)")
    try:
        path = path.resolve(strict=True)
    except OSError:
        raise UsageError(f"--root {spec!r}: {rest} does not exist") from None
    if not path.is_dir():
        raise UsageError(f"--root {spec!r}: not a directory")
    alias = alias or path.name
    if not ALIAS_RE.fullmatch(alias):
        raise UsageError(f"--root {spec!r}: alias {alias!r} must match {ALIAS_RE.pattern}")
    return alias, path


@dataclass
class _SessionReport:
    alias: str
    scope: str
    report: dict[str, Any]
    digest: str
    recorded_at: str


class AgentService:
    def __init__(self, roots: list[RootEntry], *, max_timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        if not roots:
            raise UsageError("at least one repository root is required (--root [ALIAS=]PATH)")
        self.roots = {r.alias: r for r in roots}
        self.max_timeout_s = max_timeout_s
        self._session: OrderedDict[str, _SessionReport] = OrderedDict()
        self._session_lock = threading.Lock()

    @classmethod
    def from_specs(cls, root_specs: list[str], allowed_profiles: list[str], *, max_timeout_s: float = DEFAULT_TIMEOUT_S) -> "AgentService":
        if max_timeout_s <= 0:
            raise UsageError("--timeout-s must be positive")
        roots: list[RootEntry] = []
        seen_alias: set[str] = set()
        seen_path: set[Path] = set()
        for spec in root_specs:
            alias, path = parse_root_spec(spec)
            if alias in seen_alias or path in seen_path:
                raise UsageError(f"--root {spec!r}: duplicate alias or path")
            seen_alias.add(alias)
            seen_path.add(path)
            entry = RootEntry(alias, path, _file_digest(path / CONFIG_DIR / CONFIG_NAME))
            for name in allowed_profiles:
                if not ALIAS_RE.fullmatch(name):
                    raise UsageError(f"--allow-profile {name!r} is not a valid profile name")
                if entry.config_path.is_file():
                    digest, note = _profile_digest(entry.config_path, name)
                else:
                    digest, note = None, "not usable: the repository has no .guard/config.toml"
                if digest is not None:
                    entry.profiles[name] = digest
                entry.profile_notes[name] = note
            roots.append(entry)
        return cls(roots, max_timeout_s=max_timeout_s)

    # -- dispatch -------------------------------------------------------------------------------------------
    def timeout_for(self, tool: str, args: Any) -> float:
        t = args.get("timeout_s") if isinstance(args, dict) else None
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
            return min(float(t), self.max_timeout_s)
        return self.max_timeout_s

    def call(self, tool: str, args: Any, client: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one tool. Returns an envelope; raises only ``cancellation.Cancelled``.

        ``client`` is the MCP client's self-reported ``clientInfo`` (name/version); it is
        recorded as the proposer identity of agent proposals and is never trusted for anything."""
        t0 = time.perf_counter()
        try:
            a = validate_args(tool, args)
            a["_client"] = client
            handler: Callable[[dict[str, Any]], dict[str, Any]] = getattr(self, "_t_" + tool)
            out = handler(a)
        except ServiceError as exc:
            out = {"status": exc.status, "message": exc.message, **exc.extra}
        except UsageError as exc:
            out = {"status": "INVALID_REQUEST", "message": str(exc)}
        except cancellation.Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - reported as ENGINE_ERROR, never as a pass
            out = {"status": "ENGINE_ERROR", "message": f"{type(exc).__name__}: {exc}"}
        return {"schema_version": SERVICE_SCHEMA_VERSION, "tool": tool, **out, "service_ms": round((time.perf_counter() - t0) * 1000, 3)}

    @staticmethod
    def stopped(tool: str, status: str, reason: str) -> dict[str, Any]:
        assert status in ("TIMEOUT", "CANCELLED")
        return {"schema_version": SERVICE_SCHEMA_VERSION, "tool": tool, "status": status,
                "message": f"{reason}; the run is stopped at its next safe point and finalized as failed: no cache entry, contract evidence "
                           "or report from it is published, and nothing it produced may be treated as a pass. Exception: a run that had already "
                           "passed its last safe point (the final consistency check) completes normally and is indexed as an ordinary consistent "
                           "report; this response does not carry its verdict, so call verify_patch again",
                "verdict": None}

    # -- helpers -------------------------------------------------------------------------------------------
    def _root(self, repository: str) -> RootEntry:
        if repository in self.roots:
            return self.roots[repository]
        p = Path(repository)
        if p.is_absolute():
            try:
                rp = p.resolve(strict=True)
            except OSError:
                rp = None
            for r in self.roots.values():
                if rp == r.path:
                    return r
        raise ServiceError("NOT_AUTHORIZED", f"repository {repository!r} is not a configured root; configured: {sorted(self.roots)}")

    @staticmethod
    def _scope(root: RootEntry, rel: str) -> tuple[Path, str]:
        if rel.startswith("/") or rel.startswith("\\") or re.match(r"^[A-Za-z]:", rel):
            raise ServiceError("INVALID_REQUEST", "path must be relative to the repository root")
        if ".." in Path(rel).parts:
            raise ServiceError("NOT_AUTHORIZED", "path must not contain '..'")
        target = (root.path / rel)
        try:
            resolved = target.resolve(strict=True)
        except OSError:
            raise ServiceError("INVALID_REQUEST", f"path {rel!r} does not exist in repository {root.alias}") from None
        try:
            scope = resolved.relative_to(root.path).as_posix()
        except ValueError:
            raise ServiceError("NOT_AUTHORIZED", f"path {rel!r} resolves outside repository {root.alias}") from None
        return resolved, scope or "."

    def _authorize_profile(self, root: RootEntry, name: str) -> None:
        if name not in root.profiles:
            note = root.profile_notes.get(name, "not preauthorized when the server started (--allow-profile)")
            raise ServiceError("NOT_AUTHORIZED", f"run profile {name!r} is not available to agents for {root.alias}: {note}")
        digest, note = _profile_digest(root.config_path, name) if root.config_path.is_file() else (None, "configuration missing")
        if digest != root.profiles[name]:
            raise ServiceError("NOT_AUTHORIZED", f"run profile {name!r} changed since the server started ({note}); a changed profile is not "
                                                 "preauthorized. A maintainer must restart the server to authorize the new definition")

    def _lock(self, root: RootEntry) -> None:
        while not root.lock.acquire(timeout=0.2):
            cancellation.check()

    def _config_state(self, root: RootEntry) -> dict[str, Any]:
        now = _file_digest(root.config_path)
        return {"path": str(root.config_path) if now else None, "digest": now, "changed_since_server_start": now != root.config_digest_at_start}

    # -- tools ---------------------------------------------------------------------------------------------
    def _t_get_capabilities(self, a: dict[str, Any]) -> dict[str, Any]:
        from .cli import capabilities

        return {"status": "OK", "capabilities": capabilities(),
                "server": {"engine_version": ENGINE_VERSION, "tools": list(TOOLS), "not_exposed": NOT_EXPOSED, "max_timeout_s": self.max_timeout_s,
                           "roots": [{"alias": r.alias, "path": str(r.path), "run_profiles": sorted(r.profiles),
                                      "run_profile_notes": r.profile_notes, **self._proposer(r)} for r in self.roots.values()],
                           "enforcement": ENFORCEMENT_NOTE}}

    @staticmethod
    def _proposer(root: RootEntry) -> dict[str, Any]:
        """The root's configured model proposer and its data egress (static; contacts nothing). Omitted when none is configured."""
        from .cli import configured_proposer

        if not root.config_path.is_file():
            return {}
        try:
            sem = load_config(root.config_path).semantic
        except ConfigError as exc:
            return {"configured_proposer": {"error": str(exc)}}
        if not sem.get("enabled"):
            return {}
        return {"configured_proposer": configured_proposer(sem, root.config_path)}

    def _t_verify_patch(self, a: dict[str, Any]) -> dict[str, Any]:
        from .verify import EngineFailure, ProfileNotAuthorized, VerifyOptions, run_verify

        root = self._root(a["repository"])
        target, scope = self._scope(root, a["path"])
        base = a["base"]
        if base.startswith("-") or any(c.isspace() or ord(c) < 32 for c in base):
            raise ServiceError("INVALID_REQUEST", "base must be a commit, ref, git:REF or baseline:LABEL (no leading '-', whitespace or control characters)")
        profile = a.get("run_profile")
        if profile is not None:
            self._authorize_profile(root, profile)
        if a.get("replay") and profile is None:
            raise ServiceError("INVALID_REQUEST", "replay executes repository code: it needs a preauthorized isolated run_profile")
        opts = VerifyOptions(base=base, mode=a["mode"], path=target, root=root.path, config_path=root.config_arg(), no_cache=a["no_cache"],
                             execute=profile is not None, run_profile=profile, no_config_search=True, replay=bool(a.get("replay")),
                             authorized_profile_digest=root.profiles[profile] if profile is not None else None)
        self._lock(root)
        try:
            t0 = time.perf_counter()
            try:
                result = run_verify(opts)
            except ProfileNotAuthorized as exc:
                raise ServiceError("NOT_AUTHORIZED", str(exc)) from None
            except UsageError as exc:
                raise ServiceError("INVALID_REQUEST", str(exc)) from None
            except EngineFailure as exc:
                raise ServiceError("ENGINE_ERROR", str(exc)) from None
            wall = (time.perf_counter() - t0) * 1000
        finally:
            root.lock.release()
        return self._report_response(root, scope, result, a["detail"], wall)

    def _t_check_scope(self, a: dict[str, Any]) -> dict[str, Any]:
        from .orchestration import run_check

        root = self._root(a["repository"])
        target, scope = self._scope(root, a["path"])
        self._lock(root)
        try:
            t0 = time.perf_counter()
            result = run_check(CheckOptions(path=target, root=root.path, config_path=root.config_arg(), no_config_search=True))
            wall = (time.perf_counter() - t0) * 1000
        finally:
            root.lock.release()
        result.report_digest = sha256_bytes(canonical_json(result.report))
        with self._session_lock:
            self._session[result.report["run_id"]] = _SessionReport(root.alias, scope, result.report, result.report_digest, _now())
            while len(self._session) > SESSION_REPORTS:
                self._session.popitem(last=False)
        out = self._report_response(root, scope, result, a["detail"], wall)
        out["report_retention"] = f"kept in this server session only (last {SESSION_REPORTS} check reports); guard check writes no state"
        return out

    def _t_get_contracts(self, a: dict[str, Any]) -> dict[str, Any]:
        from .contracts import commands as cc

        root = self._root(a["repository"])
        if not (root.path / CONFIG_DIR / "state.sqlite").is_file():
            # A read-only tool never creates state in the repository (audit A5-01): opening the
            # store would create .guard/state.sqlite.
            unindexed = (root.path / CONFIG_DIR / "contracts").is_dir()
            return {"status": "OK", "repository": root.alias, "contracts": [],
                    "diagnostics": ["no guard state in this repository (.guard/state.sqlite is absent), so no contract is recorded; nothing was created"]
                    + (["a .guard/contracts directory exists but is not indexed; a maintainer can index it with `guard contracts import --reason TEXT`"]
                       if unindexed else []),
                    "authority_changes": "not available to agents: a maintainer uses guard contracts approve|revise|reject|retire --reason TEXT"}
        ctx = cc.open_ctx(root.path, None, root.path, root.config_arg(), no_config_search=True)
        try:
            out = cc.cmd_list(ctx)
        finally:
            ctx.close()
        return {"status": "OK", "repository": root.alias, "contracts": out["contracts"], "diagnostics": out["diagnostics"],
                "authority_changes": "not available to agents: a maintainer uses guard contracts approve|revise|reject|retire --reason TEXT"}

    def _t_get_finding(self, a: dict[str, Any]) -> dict[str, Any]:
        root = self._root(a["repository"])
        found = self.lookup(root, a["finding_id"], a.get("run_id"))
        fresh = self.freshness(root, found)
        detail = finding_detail(found)
        detail["contract_definition"] = contract_definition(root.path, detail.get("contract_result"), root.config_arg(), no_config_search=True)
        return {"status": "STALE_SNAPSHOT" if fresh["evidence_status"] != "CURRENT" else "OK", "repository": root.alias,
                **detail, "freshness": fresh}

    def _t_explain_finding(self, a: dict[str, Any]) -> dict[str, Any]:
        root = self._root(a["repository"])
        found = self.lookup(root, a["finding_id"], a.get("run_id"))
        fresh = self.freshness(root, found)
        detail = finding_detail(found)
        detail["contract_definition"] = contract_definition(root.path, detail.get("contract_result"), root.config_arg(), no_config_search=True)
        exp = explain(detail, fresh)
        return {"status": "STALE_SNAPSHOT" if fresh["evidence_status"] != "CURRENT" else "OK", "repository": root.alias,
                "finding_id": a["finding_id"], "run": detail["run"], "freshness": fresh, "explanation": exp, "text": explanation_text(exp)}

    def _t_get_context_packet(self, a: dict[str, Any]) -> dict[str, Any]:
        from .contracts import commands as cc
        from .orchestration import resolve_inputs

        root = self._root(a["repository"])
        _, cfg = resolve_inputs(CheckOptions(path=root.path, root=root.path, config_path=root.config_arg(), no_config_search=True))
        try:
            out = cc.cmd_context(root.path, cfg, a["symbol"])
        except UsageError as exc:
            raise ServiceError("NOT_FOUND" if "not a contract target" in str(exc) else "INVALID_REQUEST", str(exc)) from None
        return {"status": "OK", "repository": root.alias, **{k: v for k, v in out.items() if k != "schema_version"}}

    def _t_propose_contracts(self, a: dict[str, Any]) -> dict[str, Any]:
        from .contracts import commands as cc
        from .semantic.agent import MAX_BATCH_BYTES, normalize_client
        from .verify import EngineFailure, ProfileNotAuthorized

        root = self._root(a["repository"])
        batch = {"schema_version": "1.0", "symbol": a["symbol"], "context_packet_digest": a["context_packet_digest"], "candidates": a["candidates"]}
        if len(json.dumps(batch, default=str).encode("utf-8")) > MAX_BATCH_BYTES:
            raise ServiceError("INVALID_REQUEST", f"the proposal exceeds {MAX_BATCH_BYTES} bytes")
        profile = a.get("run_profile")
        if profile is not None:
            self._authorize_profile(root, profile)
        client = normalize_client(a.get("_client"))
        self._lock(root)
        try:
            ctx = cc.open_ctx(root.path, None, root.path, root.config_arg(), no_config_search=True)
            try:
                out, code = cc.cmd_propose(ctx, batch, client=client, execute=profile is not None, run_profile=profile, dry_run=a["dry_run"],
                                           authorized_profile_digest=root.profiles[profile] if profile is not None else None)
            except ProfileNotAuthorized as exc:
                raise ServiceError("NOT_AUTHORIZED", str(exc)) from None
            except EngineFailure as exc:
                raise ServiceError("ENGINE_ERROR", str(exc)) from None
            finally:
                ctx.close()
        finally:
            root.lock.release()
        rep = (out["agent"].get("batches") or [{}])[0]
        cands = []
        for c in out["candidates"]:
            fals = c.get("falsification") or {}
            accepted = c["status"] in ("proposed", "would_propose", "exists")
            cands.append({"candidate_index": c.get("candidate_index"), "contract_id": c["contract_id"] if c["status"] != "rejected" or c.get("semantic_digest") else None,
                          "status": c["status"], "accepted": accepted, "authority": "INFERRED" if accepted else None,
                          "reason": c.get("reason") or None, "rejection_code": c.get("rejection_code"),
                          "precondition": c.get("precondition"), "predicate": c.get("predicate"),
                          "falsification": {k: fals.get(k) for k in ("status", "property_result", "quantifier", "probes", "counterexample", "reasons")} if fals else None,
                          "evidence_ids": c.get("evidence_ids", [])})
        status = "OK"
        return {"status": status, "repository": root.alias, "symbol": a["symbol"], "run_outcome": out["run_outcome"], "exit_code": code,
                "snapshot_id": out["snapshot_id"], "batch": rep, "candidates": cands, "summary": out["summary"],
                "falsification": {k: out["falsification"].get(k) for k in ("requested", "status", "reason", "results", "elapsed_ms") if k in out["falsification"]},
                "incomplete_reasons": out["incomplete_reasons"], "engine_errors": out["engine_errors"], "proposer": out["agent"].get("client"),
                "authority_note": "accepted candidates are INFERRED proposals; approval, revision and rejection are maintainer commands that agents cannot call",
                "evidence_note": ("evidence_ids are content-addressed discovery falsification artifacts under .guard/artifacts (current snapshot only; "
                                  "not a regression claim and not a proof)")}

    # -- report lookup --------------------------------------------------------------------------------------
    def lookup(self, root: RootEntry, finding_id: str, run_id: str | None) -> "Found":
        return lookup_finding(root.path, finding_id, run_id, session=self._session_candidates(root.alias), state_dir=None)

    def _session_candidates(self, alias: str) -> list[_SessionReport]:
        with self._session_lock:
            return [s for s in self._session.values() if s.alias == alias]

    def freshness(self, root: RootEntry, found: "Found") -> dict[str, Any]:
        return evidence_freshness(root.path, found, root.config_arg(), no_config_search=True)

    def _report_response(self, root: RootEntry, scope: str, result: CheckResult, detail: str, wall_ms: float) -> dict[str, Any]:
        r = result.report
        schema_errors = _schema_errors(r)
        if schema_errors:
            raise ServiceError("ENGINE_ERROR", "generated report violates report.schema.json: " + "; ".join(schema_errors[:5]))
        stale = [s for s in r["coverage"]["skipped_checks"] if s.get("kind") == "source_changed_during_run"]
        out: dict[str, Any] = {
            "status": "STALE_SNAPSHOT" if stale else "OK",
            "repository": root.alias, "scope": scope, "command": r["command"],
            "run_id": r["run_id"], "report_digest": result.report_digest,
            "verdict": r["verdict"], "run_outcome": r["run_outcome"], "exit_code": result.exit_code,
            "base_snapshot": r["base_snapshot"], "candidate_snapshot": r["candidate_snapshot"],
            "coverage": compact_coverage(r["coverage"]),
            "findings": [compact_finding(f) for f in r["findings"]],
            "verdict_reasons": r["verdict_reasons"],
            "errors": [{k: e.get(k) for k in ("symbol_id", "stage", "error_type", "message")} for e in r["errors"]][:MAX_LIST],
            "capabilities": r["capabilities"], "timing_ms": r["timing_ms"], "verifier_wall_ms": round(wall_ms, 3),
            "config": self._config_state(root),
            "log": result.log[-30:],
            "evidence_retrieval": "get_finding(repository, finding_id[, run_id]) returns the full finding, its obligation/contract result and evidence records; "
                                  "explain_finding explains it",
        }
        if stale:
            out["message"] = ("the working tree changed during the run (also after one restart): this report is INCOMPLETE and does not describe "
                              "a single consistent snapshot; call verify_patch again once edits have stopped")
        if r.get("contracts") is not None:
            out["contracts"] = compact_contracts(r["contracts"])
        if r.get("replay") is not None:
            rp = r["replay"]
            out["replay"] = {k: rp.get(k) for k in ("status", "reason", "clusters", "findings", "probes", "unconfirmed", "errors") if k in rp}
            out["replay"]["targets"] = [{k: t.get(k) for k in ("symbol_id", "target", "relation", "required", "inputs", "executed", "classes")}
                                        for t in rp.get("targets", [])][:MAX_LIST]
        if r.get("patch") is not None:
            p = r["patch"]
            out["patch"] = {"base": {k: p["base"].get(k) for k in ("spec", "kind", "commit", "label", "snapshot_id")}, "files": {k: p["files"].get(k) for k in ("added", "deleted", "modified", "renamed")},
                            "affected_symbols": [x["symbol_id"] for x in p["slice"]["invalidated"]][:MAX_LIST], "relations": p["relations"],
                            "unresolved_changes": p["unresolved_changes"][:MAX_LIST]}
        if detail == "full":
            out["report"] = r
        return out


# -- shared report helpers (also used by `guard explain`) -------------------------------------------------------
def _now() -> str:
    from .orchestration import now_utc

    return now_utc()


def _schema_errors(report: dict[str, Any]) -> list[str]:
    try:
        from .models.schema import SchemaValidationUnavailable, validation_errors
    except ImportError:  # pragma: no cover
        return []
    try:
        return validation_errors("report", report)
    except SchemaValidationUnavailable:
        return []


def _loc(span: dict[str, Any] | None) -> str | None:
    if not span:
        return None
    return f"{span['path']}:{span['start_line']}:{span['start_column'] + 1}"


def compact_finding(f: dict[str, Any]) -> dict[str, Any]:
    return {"finding_id": f["finding_id"], "symbol_id": f["symbol_id"], "property_id": f["property_id"], "property_result": f["property_result"],
            "scope_validity": f["scope_validity"], "authority": f["authority"], "patch_relation": f["patch_relation"], "severity": f["severity"],
            "location": _loc(f.get("source_span")), "message": f["message"], "witness": f["witness"], "witness_validated": f["witness_validated"],
            "replay_available": f["replay_available"], "evidence_ids": f["evidence_ids"],
            **({"behavior": f["behavior"]} if "behavior" in f else {})}


def compact_coverage(c: dict[str, Any]) -> dict[str, Any]:
    return {"requested_symbols": c["requested_symbols"], "analyzed_symbols": c["analyzed_symbols"], "required_obligations": c["required_obligations"],
            "resolved_required_obligations": c["resolved_required_obligations"],
            "unknown_obligations": c["unknown_obligations"][:MAX_LIST], "unknown_obligations_total": len(c["unknown_obligations"]),
            "skipped_checks": [{k: s.get(k) for k in ("kind", "path", "symbol_id", "reason", "required")} for s in c["skipped_checks"][:MAX_LIST]],
            "skipped_checks_total": len(c["skipped_checks"]),
            **({"contracts": c["contracts"]} if "contracts" in c else {}), **({"replay": c["replay"]} if "replay" in c else {})}


def compact_contracts(section: dict[str, Any]) -> dict[str, Any]:
    ex = section["execution"]
    results = []
    for c in section["results"]:
        item = {k: c.get(k) for k in ("contract_id", "revision", "authority", "required", "status", "property_result", "scope_validity", "patch_relation",
                                       "severity", "finding_id", "witness", "evidence_ids")}
        obs = c.get("observations") or {}
        if obs:
            item["observations"] = {side: {k: o.get(k) for k in ("reason", "result", "exception") if k in o} for side, o in obs.items() if isinstance(o, dict)}
        item["reasons"] = c.get("reasons", [])[-3:]
        results.append(item)
    return {"execution": {k: ex.get(k) for k in ("requested", "profile", "backend", "status", "isolated", "reason", "environment_digest")}, "results": results}


# -- finding lookup -------------------------------------------------------------------------------------------
@dataclass
class Found:
    report: dict[str, Any]
    finding: dict[str, Any]
    digest: str
    scope: str
    source: str  # state_index | session
    created_at: str


def lookup_finding(root: Path, finding_id: str, run_id: str | None, *, session: list[_SessionReport] | None = None,
                   state_dir: Path | None = None) -> Found:
    """Newest report (state index or this session) containing ``finding_id`` (or exactly ``run_id``)."""
    from .storage.store import Store, StoreError

    if not FINDING_RE.fullmatch(finding_id):
        raise ServiceError("INVALID_REQUEST", "finding_id must look like fnd-<hex>")
    if run_id is not None and not RUN_RE.fullmatch(run_id):
        raise ServiceError("INVALID_REQUEST", "run_id must look like run-<hex>")
    candidates: list[tuple[str, int, dict[str, Any], str, str, str]] = []  # (created_at, order, report, digest, scope, source)
    for i, s in enumerate(session or []):
        if run_id is not None and s.report["run_id"] != run_id:
            continue
        if any(f["finding_id"] == finding_id for f in s.report["findings"]):
            candidates.append((s.recorded_at, i, s.report, s.digest, s.scope, "session"))
    sd = state_dir if state_dir is not None else root / CONFIG_DIR
    if (sd / "state.sqlite").is_file():
        try:
            store = Store(sd)
        except StoreError as exc:
            raise ServiceError("ENGINE_ERROR", str(exc)) from None
        try:
            rows = store.report_rows(finding_id=finding_id, run_id=run_id, limit=5)
            for row in rows:
                rep = store.get_json(row["report_digest"])
                if rep is None:
                    raise ServiceError("ENGINE_ERROR", f"report artifact {row['report_digest']} of run {row['run_id']} is missing or corrupted")
                candidates.append((row["created_at"], row["seq"], rep, row["report_digest"], row["scope"], "state_index"))
        finally:
            store.close()
    if not candidates:
        where = f" in run {run_id}" if run_id else ""
        raise ServiceError("NOT_FOUND", f"no recorded report contains finding {finding_id}{where} (verify reports are indexed in .guard/; "
                                        "check_scope reports live only in the MCP session that produced them)")
    created, _, rep, digest, scope, source = max(candidates, key=lambda c: (c[0], c[1]))
    f = next(x for x in rep["findings"] if x["finding_id"] == finding_id)
    return Found(rep, f, digest, scope, source, created)


def finding_detail(found: Found) -> dict[str, Any]:
    r, f = found.report, found.finding
    ev_ids = set(f.get("evidence_ids", []))
    obligation = next((o for o in r["obligation_results"] if o.get("obligation_id") == f.get("obligation_id")), None)
    contract = None
    for c in (r.get("contracts") or {}).get("results", []):
        if c.get("finding_id") == f["finding_id"]:
            contract = c
            ev_ids |= set(c.get("evidence_ids", []))
    evidence = [e for e in r["evidence"] if e["evidence_id"] in ev_ids]
    return {
        "finding": f, "obligation_result": obligation, "contract_result": contract, "evidence": evidence,
        "missing_evidence_ids": sorted(ev_ids - {e["evidence_id"] for e in evidence}),
        "run": {"run_id": r["run_id"], "command": r["command"], "verdict": r["verdict"], "run_outcome": r["run_outcome"],
                "base_snapshot": r["base_snapshot"], "candidate_snapshot": r["candidate_snapshot"], "report_digest": found.digest,
                "scope": found.scope, "source": found.source, "recorded_at": found.created_at, "policy_id": r["policy_id"]},
    }


def contract_definition(root: Path, contract_result: dict[str, Any] | None, config_path: Path | None, *, no_config_search: bool = False,
                        state_dir: Path | None = None) -> dict[str, Any] | None:
    """The exact contract revision a contract finding was checked against (read-only).

    The report carries only the contract ID, revision and semantic digest; a repairing agent also
    needs the property itself. The revision document is taken from the registry and its semantic
    digest is compared with the evidence, so a later revision is never shown as the checked one.
    """
    if not contract_result:
        return None
    from .contracts import commands as cc

    cid, rev, sem = contract_result.get("contract_id"), contract_result.get("revision"), contract_result.get("semantic_digest")
    try:
        ctx = cc.open_ctx(root, state_dir, root, config_path, no_config_search=no_config_search)
    except UsageError as exc:
        return {"contract_id": cid, "revision": rev, "available": False, "reason": str(exc)}
    try:
        st = ctx.registry.load().contracts.get(cid)
        doc = st.revisions.get(rev) if st is not None else None
        if st is None or doc is None:
            return {"contract_id": cid, "revision": rev, "available": False, "reason": "revision not found in the contract registry"}
        if st.semantic.get(rev) != sem:
            return {"contract_id": cid, "revision": rev, "available": False,
                    "reason": f"the registry's revision {rev} has semantic digest {st.semantic.get(rev)}, not the checked {sem}"}
        return {"contract_id": cid, "revision": rev, "semantic_digest": sem, "available": True, "current_authority": st.authority,
                "current_revision": st.current_revision, **{k: doc.get(k) for k in ("scope", "input_profile", "precondition", "predicate", "examples")}}
    finally:
        ctx.close()


def evidence_freshness(root: Path, found: Found, config_path: Path | None, *, no_config_search: bool = False,
                       state_dir: Path | None = None) -> dict[str, Any]:
    """Does this evidence describe the current working tree? (no stale evidence attributed to repaired code)"""
    from .orchestration import resolve_inputs
    from .snapshots.capture import capture
    from .storage.store import Store, StoreError
    from .verify import SEMANTICS_PROFILE, _capture_candidate, _open_repo

    r = found.report
    current: str | None = None
    note = None
    try:
        target = root / found.scope if found.scope != "." else root
        _, cfg = resolve_inputs(CheckOptions(path=target, root=root, config_path=config_path, no_config_search=no_config_search))
        if r["command"] == "verify":
            snap = _capture_candidate(root, target, cfg, _open_repo(root))
        else:
            snap = capture(root, [target], cfg.exclude, cfg.digest, SEMANTICS_PROFILE)
        current = snap.snapshot_id
    except (UsageError, OSError, ValueError) as exc:
        note = f"current snapshot could not be captured: {exc}"
    newer: str | None = None
    sd = state_dir if state_dir is not None else root / CONFIG_DIR
    if (sd / "state.sqlite").is_file():
        try:
            store = Store(sd)
            try:
                rows = [x for x in store.report_rows(limit=50) if x["scope"] == found.scope and x["command"] == r["command"]]
            finally:
                store.close()
            if rows and rows[0]["run_id"] != r["run_id"]:
                newer = rows[0]["run_id"]
        except StoreError:
            pass
    matches = None if current is None else current == r["candidate_snapshot"]
    if matches is None:
        status = "UNKNOWN"
    elif not matches:
        status = "STALE"
    else:
        status = "CURRENT"
    out = {"evidence_status": status, "evidence_snapshot": r["candidate_snapshot"], "current_snapshot": current, "superseded_by_run": newer}
    if status == "STALE":
        out["message"] = ("the working tree changed after this evidence was produced: it describes snapshot "
                          f"{r['candidate_snapshot']}, not the current code ({current}). It is not attributed to the current code; call verify_patch again")
    elif status == "UNKNOWN":
        out["message"] = note or "current snapshot unknown"
    elif newer:
        out["message"] = f"a newer report for the same scope exists ({newer}); this finding may have been resolved there"
    return out


# -- deterministic explanation -----------------------------------------------------------------------------------
_RESULT_MEANING = {
    "PROVED_UNSAFE": "a failure is proved to exist within the modeled semantics, the stated domain and assumptions (an existential claim over admitted inputs)",
    "POSSIBLY_UNSAFE": "a failure may exist: an over-approximation found a candidate path, but no exact witness was confirmed; this is not a proof of a real failure",
    "PROVED_SAFE": "the property holds for every admitted input within the modeled semantics, domain and assumptions",
    "TESTED": "the property passed the executed probes; this is test evidence, not a proof",
    "OBSERVED_FAILURE": "an executed run violated the property on the recorded input",
    "OBSERVED_REGRESSION": "the same admitted input passed on the base and failed on the candidate, both executed in recorded environments",
    "UNVERIFIED": "the property was not checked (for example because execution was not requested or not available)",
    "UNKNOWN": "the engine could not decide the property (unsupported construct, unknown call, budget or solver limit); unknown is not safe",
}
_SCOPE_MEANING = {
    "EXACT_MODEL": "exact with respect to the modeled semantics of the declared domain",
    "CONDITIONAL": "valid only if the stated condition (for example, annotation-only types) holds for real callers",
    "OVERAPPROXIMATE": "derived from an over-approximation; spurious paths are possible",
    "BOUNDED": "valid only within the stated bound",
    "EXECUTED": "observed by execution in the recorded environment",
}
_RELATION_MEANING = {
    "INTRODUCED": "the base does not have this failure on the same witness: the patch introduced it",
    "PRE_EXISTING": "the base fails the same way: not introduced by this patch (pre-existing, or an invalid specification)",
    "RESOLVED": "the base had this failure and the candidate no longer does",
    "CHANGED": "the base and candidate both have a related result, but it changed",
    "UNDETERMINED": "the relation to the patch could not be established",
}


def explain(detail: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    f = detail["finding"]
    res = f["property_result"]
    lim = []
    if f["property_result"] in ("PROVED_UNSAFE", "POSSIBLY_UNSAFE") and f.get("quantifier"):
        lim.append("this is a function-input claim (" + f["quantifier"] + "); it does not establish that any production caller reaches this input")
    if f["property_result"] in ("TESTED", "OBSERVED_REGRESSION", "OBSERVED_FAILURE"):
        lim.append("execution evidence covers the executed inputs and the recorded environment only; it is not a proof")
    if f.get("domain_source") == "ANNOTATION":
        lim.append("the domain comes from type annotations only, which Python does not enforce; the claim is conditional")
    if f["authority"] not in ("DECLARED", "APPROVED"):
        lim.append(f"authority {f['authority']}: the expected behavior was not approved by a maintainer; this is advisory")
    lim.append("other properties and runtime errors outside " + f["property_id"] + " are not covered by this finding")
    if fresh.get("evidence_status") != "CURRENT":
        lim.append(fresh.get("message", "the evidence may not describe the current working tree"))
    witness = f.get("witness")
    if res in ("PROVED_SAFE", "TESTED") or f["patch_relation"] == "RESOLVED":
        resolve = "nothing to repair for this finding; keep the behavior and re-verify after further edits"
    elif res == "UNKNOWN":
        resolve = ("make the obligation decidable (for example, avoid the unknown call on the divisor path or guard the operation explicitly), or have a "
                   "maintainer declare a domain or summary; an UNKNOWN required obligation keeps the verdict INCOMPLETE")
    elif witness is not None:
        resolve = (f"change the code so that {f['property_id']} holds for the witness input {witness} and every other admitted input, then call "
                   "verify_patch again: the finding should disappear or be reported RESOLVED against the base. Changing the contract, domain or "
                   "baseline instead is a maintainer decision and is not available to agents")
    else:
        resolve = f"change the code so that {f['property_id']} holds for all admitted inputs, then call verify_patch again"
    ctr = detail.get("contract_result")
    cdef = detail.get("contract_definition")
    obs = None
    if ctr and ctr.get("observations"):
        obs = {side: {k: v for k, v in o.items() if k in ("reason", "result", "exception", "environment_digest")} for side, o in ctr["observations"].items()
               if isinstance(o, dict)}
    return {
        "summary": f["message"],
        "location": _loc(f.get("source_span")),
        "symbol_id": f["symbol_id"],
        "property_id": f["property_id"],
        "result": {"value": res, "meaning": _RESULT_MEANING.get(res, res)},
        "scope_validity": {"value": f["scope_validity"], "meaning": _SCOPE_MEANING.get(f["scope_validity"] or "", "not applicable")},
        "patch_relation": {"value": f["patch_relation"], "meaning": _RELATION_MEANING.get(f["patch_relation"], f["patch_relation"]),
                           "comparison": f.get("comparison")},
        "severity": f["severity"], "authority": f["authority"],
        "quantifier": f.get("quantifier"), "domain": f.get("domain"), "domain_source": f.get("domain_source"),
        "assumptions": f.get("assumptions", []), "path_constraints": f.get("path_constraints", []),
        "witness": witness, "witness_validated": f.get("witness_validated"),
        "observations": obs,
        "contract": ({k: cdef.get(k) for k in ("contract_id", "revision", "input_profile", "precondition", "predicate")}
                     if cdef and cdef.get("available") else None),
        "evidence": [{"evidence_id": e["evidence_id"], "kind": e["kind"]} for e in detail["evidence"]],
        "what_this_does_not_show": lim,
        "what_would_resolve_it": resolve,
        "explainer": "deterministic template over the stored evidence (no model; the local model's explain_finding operation is not implemented)",
    }


def explanation_text(e: dict[str, Any]) -> str:
    lines = [f"{e['location']}  {e['symbol_id']}  [{e['property_id']}]", e["summary"], "",
             f"result: {e['result']['value']}: {e['result']['meaning']}",
             f"scope: {e['scope_validity']['value']}: {e['scope_validity']['meaning']}",
             f"patch relation: {e['patch_relation']['value']}: {e['patch_relation']['meaning']}",
             f"severity {e['severity']}, authority {e['authority']}"]
    if e.get("quantifier"):
        lines.append(f"quantifier: {e['quantifier']}")
    if e.get("domain"):
        lines.append(f"domain ({e['domain_source']}): {e['domain']}")
    lines += [f"assumes: {a}" for a in e["assumptions"]]
    if e["path_constraints"]:
        lines.append("path: " + " AND ".join(e["path_constraints"]))
    if e["witness"] is not None:
        lines.append(f"witness: {json.dumps(e['witness'])}" + ("" if e.get("witness_validated") in (None, True) else " (not validated)"))
    for side, o in (e.get("observations") or {}).items():
        lines.append(f"{side}: " + ", ".join(f"{k}={v}" for k, v in o.items()))
    if e.get("contract"):
        c = e["contract"]
        lines.append(f"contract {c['contract_id']} r{c['revision']} ({profile_label(c['input_profile'])}):")
        lines.append("  precondition: " + json.dumps(c["precondition"], separators=(",", ":")))
        lines.append("  predicate: " + json.dumps(c["predicate"], separators=(",", ":")))
    lines.append("does not show:")
    lines += [f"  - {x}" for x in e["what_this_does_not_show"]]
    lines.append("to resolve: " + e["what_would_resolve_it"])
    return "\n".join(lines)


__all__ = ["AgentService", "ServiceError", "TOOL_SPECS", "TOOLS", "NOT_EXPOSED", "OUTPUT_SCHEMA", "lookup_finding", "finding_detail",
           "evidence_freshness", "explain", "explanation_text", "compact_finding"]
