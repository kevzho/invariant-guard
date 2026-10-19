"""``guard verify`` and ``guard baseline``: change-aware static verification (M2, docs/07).

``guard verify --base REF`` compares an explicit, immutable base with a fresh capture of
the working tree:

* base: ``REF`` / ``git:REF`` (a commit read from Git objects; nothing is checked out) or
  ``baseline:LABEL`` (a saved baseline: a recorded commit, retained source, or a
  manifest with stored results only). A base is never inferred.
* candidate: the working tree, read once (in Git: index + untracked non-ignored files,
  so staged, unstaged, untracked, deleted and renamed files participate). If it changes
  during the run the run restarts once, then reports INCOMPLETE.
* impact: file diff -> symbol match -> base/candidate dependency graphs -> affected
  slice (fail-closed validity-key cross-check).
* analysis: ``--mode quick`` analyzes the affected slice of both snapshots within
  ``quick_budget_ms``; required coverage is the candidate slice. ``--mode full``
  analyzes every symbol of both snapshots within ``full_budget_ms``; required coverage
  is the whole candidate scope. Neither mode executes anything.
* cache: per-symbol results keyed by validity keys in ``.guard/`` (``--no-cache`` to
  bypass); entries are published only after a consistent run.
* attribution: each finding's witness is evaluated against the base model
  (``impact/attribution.py``); policy applies the patch rows of docs/03.

``guard verify`` never writes or replaces a baseline; ``guard baseline create`` is the
only command that records one, and baselines are append-only.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import ENGINE_VERSION, cancellation
from .analysis.obligations import Budget, ObligationOutcome, site_obligation_id
from .config import Config, ConfigError
from .contracts.checker import ExecContext, Side, record_contract_evidence, run_contract_checks
from .contracts.registry import REQUIRED_AUTHORITIES, ContractRegistry, RegistryView, contract_scope_digests
from .impact.attribution import BaseModel, classify, resolved
from .impact.graph import Slice, affected_slice, build_graph, diff_files, match_symbols, symbol_table
from .impact.keys import CONTRACT_SLOT, global_key_material, snapshot_keys
from .impact.scope import Scope, file_facts, lowering_scope
from .models.enums import PatchRelation, PropertyResult, ScopeValidity, Severity
from .models.spans import digest_json, short_hex
from .orchestration import (
    MAX_ATTEMPTS,
    SEMANTICS_PROFILE,
    CheckOptions,
    CheckResult,
    Lowered,
    Relation,
    UsageError,
    _message,
    analyze_lowered,
    build_report,
    lower_snapshot,
    make_evidence,
    make_plan,
    now_utc,
    resolve_inputs,
    setup_solver,
)
from .policy.verdict import severity_for_patch
from .snapshots.capture import Snapshot, capture, capture_from_blobs, capture_git_revision, verify_unchanged
from .snapshots.git import GitError, GitRepo
from .storage.cache import CACHE_KIND, SymbolCache
from .storage.store import Baseline, Store, StoreError

LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MODES = ("quick", "full")


class EngineFailure(Exception):
    """Verifier environment failure (exit 3), e.g. Git or the state store failing."""


@dataclass
class VerifyOptions:
    base: str
    mode: str = "quick"
    path: Path = Path(".")
    root: Path | None = None
    config_path: Path | None = None
    no_solver: bool = False
    timeout_ms: int | None = None
    no_cache: bool = False
    state_dir: Path | None = None
    execute: bool = False  # M3: dynamic contract checks (needs an enabled run profile)
    run_profile: str | None = None
    no_config_search: bool = False  # MCP: see CheckOptions.no_config_search
    # MCP: the profile definition digest preauthorized when the server started. It is checked
    # against the configuration object this run actually uses (audit A5-02).
    authorized_profile_digest: str | None = None
    # Pivot phase 4: differential replay of the changed symbols (needs --execute and a run profile).
    replay: bool = False
    replay_generate: bool = True


def _open_repo(root: Path) -> GitRepo | None:
    try:
        return GitRepo(root)
    except GitError:
        return None


def _listing(repo: GitRepo, root: Path):
    """Root-relative candidate paths from the index plus untracked, non-ignored files."""
    prefix = root.resolve().relative_to(repo.toplevel).as_posix()
    prefix = "" if prefix == "." else prefix + "/"

    def listing() -> set[str]:
        tracked, untracked = repo.worktree_listing()
        return {p[len(prefix):] for p in tracked | untracked if p.startswith(prefix)}

    return listing, prefix


def _capture_candidate(root: Path, path: Path, cfg: Config, repo: GitRepo | None) -> Snapshot:
    if repo is None:
        return capture(root, [path], cfg.exclude, cfg.digest, SEMANTICS_PROFILE, git_note={"kind": "working_tree", "git_listing": False})
    listing, _ = _listing(repo, root)
    return capture(root, [path], cfg.exclude, cfg.digest, SEMANTICS_PROFILE, listing=listing,
                   git_note={"kind": "working_tree", "head": repo.head(), "object_format": repo.object_format, "git_listing": True})


def _state_dir(opts_state: Path | None, root: Path) -> Path:
    return (opts_state.resolve() if opts_state is not None else root / ".guard")


# -- base resolution -------------------------------------------------------------------------
@dataclass
class BaseSpec:
    kind: str  # git | baseline
    value: str
    snapshot: Snapshot | None
    commit: str | None = None
    baseline: Baseline | None = None
    stored_results: dict[str, Any] | None = None  # manifest-only baselines
    manifest: dict[str, Any] | None = None
    note: str = ""


def _parse_base(text: str) -> tuple[str, str]:
    if text.startswith("baseline:"):
        return "baseline", text[len("baseline:"):]
    if text.startswith("git:"):
        return "git", text[len("git:"):]
    return "git", text


def resolve_base(base: str, root: Path, scope: list[str], cfg: Config, repo: GitRepo | None, store: Store | None) -> BaseSpec:
    kind, value = _parse_base(base)
    if not value:
        raise UsageError("--base needs a value (a Git reference, git:REF or baseline:LABEL)")
    if kind == "git":
        if repo is None:
            raise UsageError(f"--base {base!r} names a Git reference, but {root} is not inside a Git work tree; use baseline:LABEL for non-Git repositories")
        try:
            commit = repo.resolve_commit(value)
        except GitError as exc:
            raise UsageError(f"--base {base!r}: {exc}") from None
        try:
            snap = capture_git_revision(repo, commit, root, scope, cfg.exclude, SEMANTICS_PROFILE)
        except GitError as exc:
            raise EngineFailure(f"reading base revision {commit}: {exc}") from None
        return BaseSpec("git", value, snap, commit=commit)
    if store is None:
        raise UsageError("baseline:LABEL needs the state directory (.guard/)")
    b = store.get_baseline(value)
    if b is None:
        raise UsageError(f"no saved baseline named {value!r} (create one with `guard baseline create --label {value}`)")
    manifest = store.get_json(b.manifest_digest)
    if manifest is None:
        raise EngineFailure(f"baseline {value!r}: snapshot manifest {b.manifest_digest} is missing or corrupted")
    if b.git_commit:
        if repo is None or not repo.has_commit(b.git_commit):
            raise EngineFailure(f"baseline {value!r} records commit {b.git_commit}, which is not accessible in this repository")
        snap = capture_git_revision(repo, b.git_commit, root, scope, cfg.exclude, SEMANTICS_PROFILE)
        snap.kind = "saved_baseline"
        snap.source_identity = {"kind": "baseline", "label": value, "commit": b.git_commit, "object_format": repo.object_format}
        return BaseSpec("baseline", value, snap, commit=b.git_commit, baseline=b, manifest=manifest)
    if b.source_retained:
        blobs = {f["sha256"]: store.get_source(f["sha256"]) for f in manifest["files"]}
        try:
            snap = capture_from_blobs(root, manifest, {k: v for k, v in blobs.items() if v is not None}, value, SEMANTICS_PROFILE)
        except ValueError as exc:
            raise EngineFailure(f"baseline {value!r}: {exc}") from None
        return BaseSpec("baseline", value, snap, baseline=b, manifest=manifest)
    results = store.get_json(b.results_digest) if b.results_digest else None
    if results is None:
        raise EngineFailure(f"baseline {value!r} has neither retained source, a commit, nor stored results")
    return BaseSpec("baseline", value, None, baseline=b, stored_results=results, manifest=manifest,
                    note="baseline source was not retained (re-create it with --retain-source or --rev for full comparison)")


# -- verify -----------------------------------------------------------------------------------
def run_verify(opts: VerifyOptions) -> CheckResult:
    if opts.mode not in MODES:
        raise UsageError(f"--mode must be one of {MODES}")
    root, cfg = resolve_inputs(CheckOptions(path=opts.path, root=opts.root, config_path=opts.config_path, no_solver=opts.no_solver, timeout_ms=opts.timeout_ms,
                                            no_config_search=opts.no_config_search))
    log: list[str] = []
    repo = _open_repo(root)
    scope_rel = [opts.path.resolve().relative_to(root).as_posix() or "."]
    scope_rel = ["." if s in ("", ".") else s for s in scope_rel]
    state_dir = _state_dir(opts.state_dir, root)
    try:
        store = Store(state_dir)
    except StoreError as exc:
        if opts.base.startswith("baseline:"):
            raise EngineFailure(str(exc)) from None
        store = None
        log.append(f"state directory unavailable ({exc}); running without cache")
    try:
        registry = ContractRegistry(store) if store is not None else None
        view = registry.load() if registry is not None else RegistryView({}, [], 0)
        dyn = _prepare_execution(opts, cfg, view, log)
        cancellation.check()
        t0 = time.perf_counter()
        base = resolve_base(opts.base, root, scope_rel, cfg, repo, store)
        t_base_capture = (time.perf_counter() - t0) * 1000
        cancellation.check()
        base_lowered = None  # lowered per attempt: quick mode lowers only what the candidate's changes can reach (I-143)
        restart_note: dict[str, Any] | None = None
        started: list[str] = []
        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                report, code, changed = _verify_once(opts, root, cfg, repo, store, base, base_lowered, t_base_capture, log,
                                                    final=attempt == MAX_ATTEMPTS, restart_note=restart_note, registry=registry, view=view, dyn=dyn,
                                                    started=started)
                if changed and attempt < MAX_ATTEMPTS:
                    log.append(f"source changed during the run ({', '.join(changed[:5])}); restarting once on a fresh capture")
                    restart_note = {"kind": "snapshot_restarted", "symbol_id": None,
                                    "message": f"the working tree changed during the first attempt ({', '.join(changed[:5])}); the run was restarted once on a fresh capture"}
                    continue
                digest = _record_report(store, report, scope_rel, log) if store is not None else None
                return CheckResult(report, code, log, report_digest=digest)
            raise AssertionError("unreachable")
        except cancellation.Cancelled as exc:
            # T52: a cancelled run is finalized as failed, so none of its staged cache entries
            # is ever published and no contract evidence was recorded (that happens only after
            # a consistent finalize). No report is recorded for it.
            if store is not None:
                for rid in started:
                    if store.run_status(rid) == "running":
                        store.finalize_run(rid, ok=False, note=f"cancelled: {exc.reason}")
            raise
    finally:
        if store is not None:
            store.close()


REPLAY_EXCLUDE = [".git", ".guard", "__pycache__", ".venv", "venv", ".tox", ".nox", "node_modules", "build", "dist", ".eggs", ".mypy_cache"]


def _run_replay(opts: VerifyOptions, root: Path, cfg: Config, repo: GitRepo | None, base: BaseSpec, cand_snap: Snapshot, match: Any, sl: Slice,
                syms_c: dict[str, Any], dyn: DynamicSetup, run_id: str, created_at: str, log: list[str],
                store: Store | None = None) -> tuple[dict[str, Any], Snapshot | None]:
    """Differential replay of the changed symbols on base and candidate (``--replay``), plus the
    declared (doctests of changed modules) and approved (pinned) behaviors."""
    from .replay import declared as replay_declared
    from .replay import decisions as replay_decisions
    from .replay import engine as replay_engine
    from .replay import evidence as replay_evidence

    if not dyn.requested or dyn.status is None or dyn.unavailable_reason is not None:
        return replay_evidence.unavailable(dyn.unavailable_reason or "the runner was not started"), None
    if base.kind != "git" or base.commit is None or repo is None:
        return replay_evidence.unavailable("the base is not a Git commit; replay needs the base's code and tests (use --base git:REV)"), None
    changed = replay_engine.changed_for_replay(list(match.modified), sl.reasons, set(syms_c))
    modified = [c for c, how in changed if how == "modified"]
    data = [c for c, how in changed if how == "data"]
    bsrc = capture_git_revision(repo, base.commit, root, ["."], REPLAY_EXCLUDE, SEMANTICS_PROFILE, suffixes=(".py", ".pyi"))
    listing, _ = _listing(repo, root)
    csrc = capture(root, [root], REPLAY_EXCLUDE, cfg.digest, SEMANTICS_PROFILE, listing=listing,
                   git_note={"kind": "working_tree", "head": repo.head(), "object_format": repo.object_format, "git_listing": True}, suffixes=(".py", ".pyi"))
    bfiles = {f.path: f.raw for f in bsrc.files}
    cfiles = {f.path: f.raw for f in csrc.files}
    log.append(f"replay: {len(modified)} modified and {len(data)} data-dependent symbol(s); sources base {len(bfiles)} / candidate {len(cfiles)} file(s)")
    res = replay_engine.replay_patch(dyn.backend, bfiles, cfiles, changed, python_path=list(dyn.profile.python_path), generate=opts.replay_generate,
                                     progress=log.append)
    ds = replay_decisions.load(store) if store is not None else replay_decisions.Decisions([])
    pyp = list(dyn.profile.python_path)
    doct = replay_declared.check_doctests(dyn.backend, bfiles, cfiles, sorted({c.split("::", 1)[0] for c, _ in changed}), pyp)
    pins = replay_declared.check_pins(dyn.backend, bfiles, cfiles, ds.pins(), pyp)
    log.append(f"replay: doctests {doct.stats.get('unchanged', 0)} unchanged example(s), {doct.stats.get('regressions', 0)} regression(s); "
               f"pins {pins.stats.get('checked', 0)} checked, {len(pins.regressions)} violated")
    out = replay_evidence.build(res, cand_files=cfiles, cand_snapshot_id=csrc.snapshot_id, base_snapshot_id=bsrc.snapshot_id, run_id=run_id,
                                created_at=created_at, backend_status=dyn.status, run_profile_digest=dyn.run_profile_digest, engine_version=ENGINE_VERSION,
                                accepted=ds.accepted(), declared=[doct, pins])
    return out, csrc


@dataclass
class DynamicSetup:
    """``--execute`` state resolved once per command (before any target import)."""

    requested: bool
    profile: Any = None
    backend: Any = None
    status: Any = None
    run_profile_doc: dict[str, Any] | None = None
    run_profile_digest: str | None = None
    unavailable_reason: str | None = None
    probe_ms: float | None = None

    def section(self) -> dict[str, Any]:
        if not self.requested:
            return {"requested": False, "profile": None, "backend": None, "status": "DISABLED",
                    "reason": "dynamic execution was not requested (guard verify --execute --run-profile NAME)", "isolated": False,
                    "capabilities": [], "environment": {}, "environment_digest": None, "run_profile": None, "run_profile_digest": None}
        st = self.status
        status = "NOT_STARTED" if st is None else ("AVAILABLE" if st.available and self.unavailable_reason is None else "RUNNER_UNAVAILABLE")
        return {"requested": True, "profile": self.profile.name, "backend": self.profile.backend, "status": status,
                "reason": self.unavailable_reason or (st.reason if st is not None else "no active contracts; the runner was not started"),
                "isolated": bool(st.isolated) if st is not None else self.profile.backend == "docker",
                "capabilities": [c.to_json() for c in st.capabilities] if st is not None else [],
                "environment": st.environment if st is not None else {}, "environment_digest": st.environment_digest if st is not None else None,
                "run_profile": self.run_profile_doc, "run_profile_digest": self.run_profile_digest}


class ProfileNotAuthorized(UsageError):
    """The run's configuration does not carry the preauthorized profile definition (MCP)."""


def _prepare_execution(opts: Any, cfg: Config, view: RegistryView | None, log: list[str], needed: bool | None = None) -> DynamicSetup:
    """Resolve ``--execute`` once (``opts`` has ``execute`` and ``run_profile``). The backend is
    started only when work exists: active contracts (verify) or ``needed`` (discover)."""
    if not opts.execute:
        if opts.run_profile:
            raise UsageError("--run-profile is only meaningful with --execute")
        if getattr(opts, "replay", False):
            raise UsageError("--replay executes repository code and needs --execute with an enabled run profile")
        return DynamicSetup(False)
    try:
        prof = cfg.run_profile(opts.run_profile)
    except ConfigError as exc:
        raise UsageError(f"invalid execution request: {exc}") from None
    from .execution.profiles import authorization_digest, profile_digest, to_run_profile

    expected = getattr(opts, "authorized_profile_digest", None)
    if expected is not None:
        got, note = authorization_digest(cfg, opts.run_profile or "")
        if got != expected:
            raise ProfileNotAuthorized(f"run profile {opts.run_profile!r} differs from the definition preauthorized when the server started ({note}); "
                                       "nothing was executed")
    from .execution.runner import make_backend

    setup = DynamicSetup(True, prof)
    if not (needed if needed is not None else bool(view is not None and view.active()) or bool(getattr(opts, "replay", False))):
        log.append("execution requested, but there is nothing to execute; the runner was not started")
        return setup
    backend = make_backend(prof)
    setup.backend = backend
    t0 = time.perf_counter()
    st = backend.status()  # capability probe: before any target import
    setup.probe_ms = round((time.perf_counter() - t0) * 1000, 3)
    setup.status = st
    if not st.available:
        setup.unavailable_reason = st.reason
        log.append(f"RUNNER_UNAVAILABLE ({prof.backend}): {st.reason}")
        return setup
    doc = to_run_profile(prof, environment_digest=st.environment_digest, interpreter_version=st.interpreter_version or "unknown",
                         allow_unisolated=bool(cfg.execution.get("allow_unisolated")))
    try:
        from .models.schema import SchemaValidationUnavailable, validation_errors

        errs = validation_errors("run_profile", doc)
        if errs:
            raise EngineFailure(f"run profile document violates run_profile.schema.json: {errs[:3]}")
    except SchemaValidationUnavailable:
        pass
    setup.run_profile_doc = doc
    setup.run_profile_digest = profile_digest(doc, prof)
    log.append(f"runner {prof.backend}: {st.reason}")
    return setup


def _global_change(cfg: Config, solver_identity: str, base: BaseSpec, base_snap: Snapshot | None, cand: Snapshot) -> tuple[str | None, list[str]]:
    notes: list[str] = []
    cand_mat = global_key_material(cfg, solver_identity, SEMANTICS_PROFILE, cand.dependency_locks)
    if base_snap is not None:
        base_mat = global_key_material(cfg, solver_identity, SEMANTICS_PROFILE, base_snap.dependency_locks)
        base_cfg_digest = base_snap.config_digest
    else:
        stored = base.stored_results or {}
        base_mat = stored.get("global_material", {})
        base_cfg_digest = (base.manifest or {}).get("config_digest")
    if base_cfg_digest != cand.config_digest:
        notes.append("the guard configuration differs from the base's; both snapshots were analyzed with the candidate configuration")
    diff = sorted(k for k in set(base_mat) | set(cand_mat) if base_mat.get(k) != cand_mat.get(k))
    if not diff:
        return None, notes
    return "changed " + ", ".join(diff) + " (dependency locks, engine, analysis configuration or solver identity invalidate every summary)", notes


def _lowering_scope(base_snap: Snapshot, cand: Snapshot, cfg: Config) -> Scope:
    """Files that quick verify lowers (I-143): changed files plus files that can reach them."""
    files = diff_files(base_snap, cand)
    changed = set(files.added) | set(files.deleted) | set(files.modified) | set(files.renamed) | set(files.renamed.values())
    facts = {f.path: (file_facts(f.path, f.raw, cfg.analysis.max_file_nodes) if f.path not in changed else None) for f in cand.files}
    return lowering_scope(facts, changed)


def _align_summaries(a: Lowered, b: Lowered) -> None:
    """Both sides summarize the same modules, so changed_globals compares like with like."""
    for p in sorted(set(a.program.summaries) | set(b.program.summaries)):
        for side in (a, b):
            if p not in side.program.summaries:
                side.program.context(p)


def _stored_base_model(base: BaseSpec) -> tuple[dict[str, Any], dict[str, str], dict[str, dict[str, Any]]]:
    stored = base.stored_results or {}
    syms = stored.get("symbols", {})
    keys = {s: v["key"] for s, v in syms.items()}
    return syms, keys, stored.get("obligations", {})


def _verify_once(opts: VerifyOptions, root: Path, cfg: Config, repo: GitRepo | None, store: Store | None, base: BaseSpec,
                 base_lowered: Lowered | None, t_base_capture: float, log: list[str], *, final: bool, restart_note: dict[str, Any] | None,
                 registry: ContractRegistry | None = None, view: RegistryView | None = None, dyn: DynamicSetup | None = None,
                 started: list[str] | None = None):
    view = view if view is not None else RegistryView({}, [], 0)
    dyn = dyn if dyn is not None else DynamicSetup(False)
    contract_digests = contract_scope_digests(view)
    run_id = "run-" + uuid.uuid4().hex
    created_at = now_utc()
    solver = setup_solver(cfg, opts.no_solver)
    full = opts.mode == "full"

    t0 = time.perf_counter()
    cand_snap = _capture_candidate(root, opts.path, cfg, repo)
    t_capture = (time.perf_counter() - t0) * 1000
    log.append(f"captured {len(cand_snap.files)} file(s), candidate snapshot {cand_snap.snapshot_id[:19]}…; base {base.kind}:{base.value}")
    cancellation.check()
    scope: Scope | None = None
    t_scope = 0.0
    if base.snapshot is not None:
        if not full and _global_change(cfg, solver.identity, base, base.snapshot, cand_snap)[0] is None:
            ts = time.perf_counter()
            scope = _lowering_scope(base.snapshot, cand_snap, cfg)
            t_scope = (time.perf_counter() - ts) * 1000
        base_lowered = lower_snapshot(base.snapshot, cfg, only=scope.files if scope is not None else None)
        cancellation.check()
    cand_lowered = lower_snapshot(cand_snap, cfg, only=scope.files if scope is not None else None)
    if scope is not None and base_lowered is not None:
        _align_summaries(base_lowered, cand_lowered)
        log.append(f"lowered {len(scope.files)} of {len(cand_snap.files)} file(s): the others cannot reach a changed file through imports")
    cancellation.check()

    # -- impact -----------------------------------------------------------------------------
    t1 = time.perf_counter()
    _, keys_c = snapshot_keys(cand_lowered, cfg, solver.identity, SEMANTICS_PROFILE, contract_digests)
    syms_c = symbol_table(cand_lowered, keys_c)
    global_change, unresolved = _global_change(cfg, solver.identity, base, base.snapshot, cand_snap)
    if base.note:
        unresolved.append(base.note)
    if base_lowered is not None:
        _, keys_b = snapshot_keys(base_lowered, cfg, solver.identity, SEMANTICS_PROFILE, contract_digests)
        syms_b = symbol_table(base_lowered, keys_b)
        files = diff_files(base_lowered.snap, cand_snap)
        match = match_symbols(syms_b, syms_c, files)
        sl = affected_slice(base_lowered, cand_lowered, syms_b, syms_c, match, files, global_change)
    else:
        from .impact.graph import FileChanges, Sym, SymbolMatch

        stored_syms, keys_b, _ = _stored_base_model(base)
        syms_b = {s: Sym(s, s.split("::", 1)[0], s.split("::", 1)[1], v["kind"], v["fingerprint"], v["body_fingerprint"], tuple(v["params"]), v["key"],
                      v.get("code_fingerprint", ""))
                  for s, v in stored_syms.items()}
        bm = {f["path"]: f["sha256"] for f in (base.manifest or {}).get("files", [])}
        cm = {f.path: f.digest for f in cand_snap.files}
        files = FileChanges(added=sorted(set(cm) - set(bm)), deleted=sorted(set(bm) - set(cm)),
                            modified=sorted(p for p in set(bm) & set(cm) if bm[p] != cm[p]), unchanged=sorted(p for p in set(bm) & set(cm) if bm[p] == cm[p]))
        match = match_symbols(syms_b, syms_c, files)
        reasons = {c: "validity key differs from the stored base key (graph walk unavailable: base source not retained)"
                   for c in syms_c if c not in match.cand_to_base or keys_b.get(match.cand_to_base[c][0]) != keys_c.get(c)}
        if global_change:
            reasons = {c: f"global invalidation: {global_change}" for c in syms_c}
        sl = Slice(set(reasons), set(), reasons, [], global_change)
    select_c = set(syms_c) if full else set(sl.affected)
    select_b = (set(syms_b) if full else set(sl.base_affected)) if base_lowered is not None else set()
    graph_c = build_graph(cand_lowered)
    graph_b = build_graph(base_lowered) if base_lowered is not None else None
    t_impact = (time.perf_counter() - t1) * 1000

    sites_c = [(sym, s) for sym, s in cand_lowered.all_sites() if sym in select_c]
    required_ids = sorted(site_obligation_id(sym, s) for sym, s in sites_c if s.required)
    planned_all = sorted(site_obligation_id(sym, s) for sym, s in sites_c)
    invalidated = [f"{c}: {sl.reasons[c]}" for c in sorted(sl.affected)]
    budget_ms = cfg.analysis.full_budget_ms if full else cfg.analysis.quick_budget_ms
    plan = make_plan(command="verify", mode=opts.mode, base_snapshot=(base.snapshot.snapshot_id if base.snapshot else base.baseline.snapshot_id),  # type: ignore[union-attr]
                     snap=cand_snap, requested=sorted(select_c), required_ids=required_ids, cfg=cfg, solver_state=solver.state,
                     budget_ms=budget_ms, invalidated=invalidated, unresolved=unresolved)
    if dyn.requested:
        # Required dynamic coverage is fixed before any analysis or execution (docs/03 Aggregation).
        plan["required_contracts"] = sorted(c.contract_id for c in view.active() if c.authority in REQUIRED_AUTHORITIES) if cfg.analysis.require_coverage else []

    # -- analysis (base first: attribution needs it) ----------------------------------------------
    backend = solver.backend(cfg)
    budget = Budget(deadline=time.monotonic() + budget_ms / 1000.0, per_query_timeout_ms=cfg.analysis.per_query_timeout_ms, witness_search=cfg.analysis.witness_search,
                    assume_no_str_rmod_override=cfg.analysis.assume_no_str_rmod_override,
                    assume_dividend_operator_respects_nonzero=cfg.analysis.assume_dividend_operator_respects_nonzero)
    use_cache = store is not None and not opts.no_cache
    if store is not None:
        store.begin_run(run_id, "verify", base.snapshot.snapshot_id if base.snapshot else None, cand_snap.snapshot_id)
        if started is not None:
            started.append(run_id)
    cache_b = cache_c = None
    base_analysis = None
    t2 = time.perf_counter()
    if base_lowered is not None:
        if use_cache:
            cache_b = SymbolCache(store, keys_b, run_id=run_id, snapshot_id=base_lowered.snap.snapshot_id, created_at=created_at)
        base_analysis = analyze_lowered(base_lowered, cfg, backend, solver.status, budget, select=select_b, cache=cache_b)
    t_base_static = (time.perf_counter() - t2) * 1000
    if use_cache:
        cache_c = SymbolCache(store, keys_c, run_id=run_id, snapshot_id=cand_snap.snapshot_id, created_at=created_at)
    analysis = analyze_lowered(cand_lowered, cfg, backend, solver.status, budget, select=select_c, cache=cache_c)
    static_budget_exhausted = budget.exhausted()  # dynamic checks below have their own limits (run profile)
    errors = cand_lowered.errors + analysis.errors + ([] if base_lowered is None else [dict(e, stage="base:" + e["stage"]) for e in base_lowered.errors + base_analysis.errors])  # type: ignore[union-attr]
    produced = sorted(site_obligation_id(o.symbol_id, o.site) for o in analysis.outcomes)
    if produced != planned_all:
        errors.append({"symbol_id": None, "stage": "coverage", "error_type": "PlanMismatch", "message": "obligation results do not match the analysis plan"})

    # -- dynamic contract checks (M3; only with --execute and an enabled run profile) -------------------
    contracts_out = None
    if view.contracts or dyn.requested:
        ctx = None
        if dyn.requested and dyn.status is not None and dyn.unavailable_reason is None:
            ctx = ExecContext(backend=dyn.backend, status=dyn.status, profile=dyn.profile, run_profile_doc=dyn.run_profile_doc or {},
                              run_profile_digest=dyn.run_profile_digest or "", run_id=run_id, created_at=created_at, registry=registry,
                              store_witnesses=bool(cfg.privacy.get("store_witnesses", True)))
        base_files = {f.path: f.raw for f in base.snapshot.files} if base.snapshot is not None else None
        base_locks = base.snapshot.dependency_locks if base.snapshot is not None else list((base.manifest or {}).get("dependency_locks", []))
        contracts_out = run_contract_checks(
            view=view, registry=registry,
            base=Side("base", base.snapshot.snapshot_id if base.snapshot is not None else None, base_files,
                      note=base.note or "baseline source is not available for replay", locks=base_locks),
            cand=Side("candidate", cand_snap.snapshot_id, {f.path: f.raw for f in cand_snap.files}, locks=cand_snap.dependency_locks),
            execute=dyn.requested, ctx=ctx, unavailable_reason=dyn.unavailable_reason or (None if ctx is not None else "runner not started"),
            execution_section=dyn.section(), require_coverage=cfg.analysis.require_coverage)

    # -- differential replay (pivot phase 4; only with --replay --execute) ------------------------------
    replay_out: dict[str, Any] | None = None
    replay_src: Snapshot | None = None
    t_replay = 0.0
    if opts.replay:
        t5 = time.perf_counter()
        replay_out, replay_src = _run_replay(opts, root, cfg, repo, base, cand_snap, match, sl, syms_c, dyn, run_id, created_at, log, store)
        t_replay = (time.perf_counter() - t5) * 1000

    # -- consistency ----------------------------------------------------------------------------------
    cancellation.check()  # last safe point: after this the run may be finalized as consistent
    changed = verify_unchanged(cand_snap)
    if replay_src is not None:
        changed += [p for p in verify_unchanged(replay_src) if p not in changed]
    if changed and not final:
        if store is not None:
            store.finalize_run(run_id, ok=False, note="working tree changed during the run (restarting)")
        return None, 0, changed
    skipped = list(cand_lowered.skipped)
    for p in changed:
        skipped.append({"kind": "source_changed_during_run", "path": p, "symbol_id": None,
                        "reason": "file content or the file set changed after capture (also after one restart); results for this snapshot are incomplete", "required": True})
    if base_lowered is not None:
        for s in base_lowered.skipped:
            if s["kind"] in ("parse_failure", "resource_limit", "excluded_path"):
                unresolved.append(f"base: {s['kind']} {s['path']}: {s['reason']}")

    # -- attribution ----------------------------------------------------------------------------------
    bmodel = BaseModel(available=base_lowered is not None, note=base.note)
    if base_lowered is not None and base_analysis is not None:
        bmodel.functions = base_lowered.functions()
        bmodel.outcomes = {site_obligation_id(o.symbol_id, o.site): o for o in base_analysis.outcomes}
    bmodel.keys = keys_b
    if base_lowered is None:
        bmodel.stored = _stored_base_model(base)[2]
    relations: dict[str, Relation] = {}
    cand_fns = cand_lowered.functions()
    for o in analysis.outcomes:
        if o.result is PropertyResult.PROVED_SAFE:
            continue
        oid = site_obligation_id(o.symbol_id, o.site)
        relations[oid] = classify(o, keys_c.get(o.symbol_id), match.cand_to_base.get(o.symbol_id), match.uncertain.get(o.symbol_id), bmodel,
                                  cand_fns.get(o.symbol_id))

    extra_findings: list[dict[str, Any]] = []
    extra_evidence: list[dict[str, Any]] = []
    if base_analysis is not None:
        b2c = match.base_to_cand()
        cand_by_anchor = {(o.symbol_id, o.site.property_id, o.site.anchor): o for o in analysis.outcomes}
        for bo in base_analysis.outcomes:
            csym = b2c.get(bo.symbol_id)
            if csym is not None and csym not in select_c:
                continue
            co = cand_by_anchor.get((csym, bo.site.property_id, bo.site.anchor)) if csym else None
            rel = resolved(bo, cand_fns.get(csym) if csym else None, co, csym, bmodel.functions.get(bo.symbol_id))
            if rel is None:
                continue
            ev = bo.reused_evidence or make_evidence(bo, base_lowered.snap.snapshot_id, run_id, created_at, False)  # type: ignore[union-attr]
            extra_evidence.append(ev)
            boid = site_obligation_id(bo.symbol_id, bo.site)
            extra_findings.append({
                "finding_id": "fnd-" + short_hex([base_lowered.snap.snapshot_id, boid, bo.result.value, bo.witness, rel.relation.value]),  # type: ignore[union-attr]
                "obligation_id": boid, "symbol_id": bo.symbol_id, "property_id": bo.site.property_id,
                "property_result": bo.result.value, "scope_validity": bo.scope.value if bo.scope else None,
                "authority": bo.authority.value, "patch_relation": rel.relation.value, "severity": Severity.INFO.value,
                "source_span": bo.site.span.to_json(), "message": "base finding: " + _message(bo) + f" [{rel.relation.value}: {rel.comparison['reason']}]",
                "quantifier": bo.quantifier, "domain": bo.domain, "domain_source": bo.domain_source.value, "assumptions": bo.assumptions,
                "path_constraints": bo.path_constraints, "reasons": bo.reasons, "witness": bo.witness, "witness_validated": bo.witness_validated,
                "runtime_mode": bo.runtime_mode, "evidence_ids": [ev["evidence_id"]], "replay_available": False, "comparison": rel.comparison,
            })

    def required_fn(o: ObligationOutcome) -> bool:
        return o.site.required and (full or o.symbol_id in sl.affected)

    def severity_fn(o: ObligationOutcome, rel: Relation | None) -> Severity | None:
        return severity_for_patch(o.result, o.scope, rel.relation if rel else PatchRelation.UNDETERMINED)

    counts: dict[str, int] = {}
    incomplete_reasons: list[str] = []
    undetermined_exact = 0
    by_oid = {site_obligation_id(x.symbol_id, x.site): x for x in analysis.outcomes}
    for oid, rel in relations.items():
        o = by_oid[oid]
        if severity_fn(o, rel) is None or (o.result is PropertyResult.UNKNOWN and not o.site.required):
            continue
        counts[rel.relation.value] = counts.get(rel.relation.value, 0) + 1
        if rel.relation is PatchRelation.UNDETERMINED and o.result is PropertyResult.PROVED_UNSAFE and o.scope is ScopeValidity.EXACT_MODEL and required_fn(o):
            undetermined_exact += 1
    for f in extra_findings:
        counts[f["patch_relation"]] = counts.get(f["patch_relation"], 0) + 1
    if undetermined_exact:
        incomplete_reasons.append(f"{undetermined_exact} exact modeled failure(s) could not be attributed to the patch (relation UNDETERMINED); attribution is required work")
    extra_reasons = ["patch relations: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))] if counts else []
    if not full and sl.affected != set(syms_c):
        extra_reasons.append(f"quick mode: {len(syms_c) - len(select_c)} unaffected symbol(s) outside the patch slice were not re-verified (use --mode full to verify the whole scope)")
    budget_hit = any(o.method.value == "BUDGET" for o in analysis.outcomes) or (
        static_budget_exhausted and any(o.result is PropertyResult.UNKNOWN and o.site.required for o in analysis.outcomes))
    if budget_hit:
        extra_reasons.append(f"the {opts.mode} budget ({budget_ms} ms) was exhausted" + ("" if full else "; offer: --mode full (separate budget analysis.full_budget_ms)"))

    published = 0
    if store is not None:
        ok = not changed
        published = store.finalize_run(run_id, ok=ok, note=None if ok else "inconsistent snapshot")
        if ok and contracts_out is not None and registry is not None:
            # Contract evidence enters the append-only store only for a consistent snapshot.
            record_contract_evidence(registry, contracts_out.evidence)

    git_states: dict[str, list[str]] = {}
    if repo is not None:
        try:
            _, prefix = _listing(repo, root)
            changed_paths = set(files.added) | set(files.deleted) | set(files.modified) | set(files.renamed) | set(files.renamed.values())
            st = repo.status({prefix + p for p in changed_paths})
            git_states = {p[len(prefix):]: labels for p, labels in st.states.items() if p.startswith(prefix)}
        except GitError as exc:
            unresolved.append(f"git status unavailable: {exc}")

    base_counts: dict[str, int] = {}
    if base_analysis is not None:
        for o in base_analysis.outcomes:
            base_counts[o.result.value] = base_counts.get(o.result.value, 0) + 1
    patch = {
        "mode": opts.mode,
        "base": {
            "spec": opts.base, "kind": base.kind, "commit": base.commit,
            "label": base.baseline.label if base.baseline else None,
            "snapshot_id": base.snapshot.snapshot_id if base.snapshot else base.baseline.snapshot_id,  # type: ignore[union-attr]
            "model_available": base_lowered is not None,
            "files": len(base.snapshot.files) if base.snapshot else len((base.manifest or {}).get("files", [])),
            "config_digest": base.snapshot.config_digest if base.snapshot else (base.manifest or {}).get("config_digest"),
            "dependency_locks": base.snapshot.dependency_locks if base.snapshot else (base.manifest or {}).get("dependency_locks", []),
            "obligation_results": base_counts,
        },
        "files": {**files.to_json(), "git_states": git_states},
        "symbols": match.to_json(),
        "slice": {**sl.to_json(), "invalidated": [{"symbol_id": c, "reason": sl.reasons[c]} for c in sorted(sl.affected)],
                  "outside_slice_symbols": len(syms_c) - len(sl.affected)},
        "graph": {
            "candidate": graph_c.stats(), "base": graph_b.stats() if graph_b else None,
            "unresolved_dynamic_edges": [{"symbol_id": s, "call": t} for s, t in graph_c.dynamic[:200]],
            "unresolved_dynamic_edges_total": len(graph_c.dynamic),
            "dynamic_edge_policy": "unresolved calls are modeled as arbitrary results and effects (havoc) in every analysis, so the results of their callers do not depend on the callee bodies; they are disclosed, not followed",
        },
        "relations": counts,
        "cache": {
            "enabled": use_cache,
            "state_dir": str(store.dir) if store is not None else None,
            "base": cache_b.stats() if cache_b else None,
            "candidate": cache_c.stats() if cache_c else None,
            "published_entries": published,
        },
        "unresolved_changes": unresolved,
        "lowering": {
            "scoped": scope is not None,
            "files_lowered": len({m.path for m in cand_lowered.modules}) if scope is not None else len(cand_snap.files),
            "files_not_lowered": len(cand_snap.files) - len(set(scope.files) & {f.path for f in cand_snap.files}) if scope is not None else 0,
            "index_ms": round(t_scope, 3),
            "basis": ("quick mode: only changed files and, elsewhere, the statements whose bindings can reach a changed file through "
                      "names and 'from ... import' bindings were lowered; the rest is unaffected (I-143)") if scope is not None else
                     ("whole scope (full mode, global invalidation or no base source)" if base.snapshot is None or full or global_change else "whole scope"),
            "lowered": [{"path": p, "reason": scope.reasons[p], **({"statements": len(st)} if st is not None else {})}
                        for p, st in sorted(scope.files.items())[:200]] if scope is not None else [],
        },
    }
    diagnostics = list(cand_lowered.diagnostics) + ([restart_note] if restart_note else [])
    extra_sections: dict[str, Any] = {"patch": patch, "_incomplete_reasons": incomplete_reasons}
    coverage_extra: dict[str, Any] = {"slice": {"mode": opts.mode, "affected_symbols": len(sl.affected), "candidate_symbols": len(syms_c)}}
    if contracts_out is not None:
        extra_findings.extend(contracts_out.findings)
        incomplete_reasons.extend(contracts_out.incomplete_reasons)
        extra_reasons.extend(contracts_out.extra_reasons)
        errors.extend(contracts_out.errors)
        extra_sections["contracts"] = contracts_out.section
        extra_sections["_warn_reasons"] = contracts_out.warn_reasons
        coverage_extra["contracts"] = contracts_out.coverage
        diagnostics.extend(view.diagnostics)
    if replay_out is not None:
        extra_findings.extend(replay_out["findings"])
        incomplete_reasons.extend(replay_out["incomplete_reasons"])
        extra_reasons.extend(replay_out["extra_reasons"])
        extra_sections["replay"] = replay_out["section"]
        extra_sections["_warn_reasons"] = list(extra_sections.get("_warn_reasons", [])) + replay_out["warn_reasons"]
        coverage_extra["replay"] = replay_out["coverage"]
    if sl.widened_by_key_check:
        diagnostics.append({"kind": "slice_widened_by_key_check", "symbol_id": None,
                            "message": f"{len(sl.widened_by_key_check)} symbol(s) had changed validity keys but were not reached by the graph walk; they were added to the slice"})
    report = build_report(
        command="verify", run_id=run_id, created_at=created_at, cfg=cfg, snap=cand_snap, plan=plan, modules=cand_lowered.modules,
        outcomes=analysis.outcomes, engine_error_ids=analysis.engine_error_ids, skipped=skipped, errors=errors, diagnostics=diagnostics,
        analyzed_symbols=analysis.analyzed_symbols, requested_symbols=sorted(select_c), solver_state=solver.state, solver_detail=solver.detail,
        capability_warnings=solver.warnings,
        timing={"capture": round(t_capture, 3), "static": round(cand_lowered.t_ms + analysis.t_ms, 3), "base_capture": round(t_base_capture, 3),
                "base_static": round(t_base_static + (base_lowered.t_ms if base_lowered else 0.0), 3), "impact": round(t_impact, 3)},
        base_snapshot=patch["base"]["snapshot_id"], relations=relations, required_fn=required_fn, severity_fn=severity_fn,
        extra_findings=extra_findings, extra_reasons=extra_reasons,
        extra_sections=extra_sections,
        coverage_extra=coverage_extra,
    )
    if replay_out is not None:
        extra_evidence.extend(replay_out["evidence"])
        report["timing_ms"]["replay"] = round(t_replay, 3)
        if dyn.status is not None and report["capabilities"]["execution"] == "disabled":
            report["capabilities"]["execution"] = f"{dyn.status.backend} ({'isolated' if dyn.status.isolated else 'UNISOLATED'})"
    if contracts_out is not None:
        report["capabilities"]["execution"] = contracts_out.execution_label
        report["timing_ms"]["dynamic"] = contracts_out.dynamic_ms
        if dyn.probe_ms is not None:
            report["timing_ms"]["runner_probe"] = dyn.probe_ms
        extra_evidence.extend(contracts_out.evidence)
    seen = {e["evidence_id"] for e in report["evidence"]}
    for ev in extra_evidence:
        if ev["evidence_id"] not in seen:
            report["evidence"].append(ev)
            seen.add(ev["evidence_id"])
    code = report.pop("_exit_code")
    return report, code, []


def _record_report(store: Store, report: dict[str, Any], scope_rel: list[str], log: list[str]) -> str | None:
    """Index the finished report (also INCOMPLETE/ERROR ones) so findings can be fetched by ID
    (``guard explain``, MCP ``get_finding``). The report is stored as an immutable artifact."""
    try:
        return store.record_report(report, scope=scope_rel[0])
    except (StoreError, OSError, sqlite3.Error) as exc:
        log.append(f"report not indexed in the state directory ({exc}); findings of this run cannot be fetched by ID later")
        return None


# -- baselines --------------------------------------------------------------------------------------
@dataclass
class BaselineOptions:
    label: str
    reason: str
    path: Path = Path(".")
    rev: str | None = None
    retain_source: bool = False
    supersedes: str | None = None
    root: Path | None = None
    config_path: Path | None = None
    no_solver: bool = False
    state_dir: Path | None = None


def run_baseline_create(opts: BaselineOptions) -> tuple[dict[str, Any], list[str]]:
    if not LABEL_RE.match(opts.label):
        raise UsageError("--label must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    if not opts.reason.strip():
        raise UsageError("--reason must not be empty (a baseline records why it was chosen)")
    root, cfg = resolve_inputs(CheckOptions(path=opts.path, root=opts.root, config_path=opts.config_path, no_solver=opts.no_solver))
    log: list[str] = []
    try:
        store = Store(_state_dir(opts.state_dir, root))
    except StoreError as exc:
        raise EngineFailure(str(exc)) from None
    try:
        if store.get_baseline(opts.label) is not None:
            raise UsageError(f"baseline {opts.label!r} already exists; baselines are immutable (choose a new label, optionally --supersedes {opts.label})")
        if opts.supersedes is not None and store.get_baseline(opts.supersedes) is None:
            raise UsageError(f"--supersedes names an unknown baseline {opts.supersedes!r}")
        repo = _open_repo(root)
        scope = [opts.path.resolve().relative_to(root).as_posix() or "."]
        scope = ["." if s in ("", ".") else s for s in scope]
        commit = None
        if opts.rev is not None:
            if repo is None:
                raise UsageError("--rev needs a Git work tree")
            try:
                commit = repo.resolve_commit(opts.rev)
                snap = capture_git_revision(repo, commit, root, scope, cfg.exclude, SEMANTICS_PROFILE)
            except GitError as exc:
                raise UsageError(f"--rev {opts.rev!r}: {exc}") from None
            snap.kind = "saved_baseline"
            snap.source_identity = {"kind": "baseline", "label": opts.label, "commit": commit, "object_format": repo.object_format}
        else:
            snap = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                snap = _capture_candidate(root, opts.path, cfg, repo)
                if not verify_unchanged(snap):
                    break
                if attempt == MAX_ATTEMPTS:
                    raise EngineFailure("the working tree kept changing during capture; no baseline was recorded (a baseline must be a consistent snapshot)")
                log.append("source changed during capture; recapturing once")
            assert snap is not None
            snap.kind = "saved_baseline"
            snap.source_identity = {"kind": "baseline", "label": opts.label}
            from .snapshots.capture import finalize

            finalize(snap)
        retain = bool(opts.retain_source or cfg.privacy.get("retain_replay_source", False)) and commit is None
        snap.source_retained = retain
        solver = setup_solver(cfg, opts.no_solver)
        lowered = lower_snapshot(snap, cfg)
        g_mat = global_key_material(cfg, solver.identity, SEMANTICS_PROFILE, snap.dependency_locks)
        cview = ContractRegistry(store).load()
        _, keys = snapshot_keys(lowered, cfg, solver.identity, SEMANTICS_PROFILE, contract_scope_digests(cview))
        syms = symbol_table(lowered, keys)
        run_id = "run-" + uuid.uuid4().hex
        created_at = now_utc()
        store.begin_run(run_id, "baseline_create", None, snap.snapshot_id)
        cache = SymbolCache(store, keys, run_id=run_id, snapshot_id=snap.snapshot_id, created_at=created_at)
        budget = Budget(deadline=time.monotonic() + cfg.analysis.full_budget_ms / 1000.0, per_query_timeout_ms=cfg.analysis.per_query_timeout_ms, witness_search=cfg.analysis.witness_search,
                    assume_no_str_rmod_override=cfg.analysis.assume_no_str_rmod_override,
                    assume_dividend_operator_respects_nonzero=cfg.analysis.assume_dividend_operator_respects_nonzero)
        analysis = analyze_lowered(lowered, cfg, solver.backend(cfg), solver.status, budget, cache=cache)
        store.finalize_run(run_id, ok=True)
        obligations = {
            site_obligation_id(o.symbol_id, o.site): {"symbol_id": o.symbol_id, "property_id": o.site.property_id, "anchor": o.site.anchor,
                                                      "result": o.result.value, "scope": o.scope.value if o.scope else None, "witness": o.witness}
            for o in analysis.outcomes
        }
        results = {
            "schema": "baseline_results/v1",
            "global_material": g_mat,
            "global_digest": digest_json(g_mat),
            "symbols": {s: {"kind": v.kind, "fingerprint": v.fingerprint, "body_fingerprint": v.body_fingerprint,
                            "code_fingerprint": v.code_fingerprint, "params": list(v.params), "key": v.key}
                        for s, v in sorted(syms.items())},
            "obligations": obligations,
        }
        manifest_digest = store.put_manifest(snap.manifest())
        results_digest = store.put_json(results, "baseline_results")
        if retain:
            for f in snap.files:
                store.put_source(f.raw)
        b = Baseline(label=opts.label, created_at=created_at, reason=opts.reason.strip(), snapshot_id=snap.snapshot_id,
                     manifest_digest=manifest_digest, results_digest=results_digest, git_commit=commit, source_retained=retain,
                     supersedes=opts.supersedes, engine_version=ENGINE_VERSION,
                     contract_set=digest_json({"slot": CONTRACT_SLOT, "scopes": contract_scope_digests(cview)}))
        try:
            store.create_baseline(b)
        except StoreError as exc:
            raise UsageError(str(exc)) from None
        counts: dict[str, int] = {}
        for o in analysis.outcomes:
            counts[o.result.value] = counts.get(o.result.value, 0) + 1
        out = {
            "schema_version": "1.0",
            "baseline": b.to_json(),
            "snapshot": {"snapshot_id": snap.snapshot_id, "kind": snap.kind, "files": len(snap.files), "source_identity": snap.source_identity,
                         "dependency_locks": snap.dependency_locks, "config_digest": snap.config_digest},
            "replay_source": "retained" if retain else ("accessible Git revision" if commit else "not retained: static comparison of changed symbols and dynamic replay are unavailable for this baseline"),
            "obligation_results": counts,
            "errors": [{"symbol_id": e.get("symbol_id"), "stage": e["stage"], "error_type": e["error_type"], "message": e["message"]} for e in lowered.errors + analysis.errors],
        }
        return out, log
    finally:
        store.close()


def baseline_list(path: Path, state_dir: Path | None, root: Path | None = None) -> list[dict[str, Any]]:
    r, _ = resolve_inputs(CheckOptions(path=path, root=root))
    sd = _state_dir(state_dir, r)
    if not (sd / "state.sqlite").exists():
        return []
    store = Store(sd)
    try:
        return [b.to_json() for b in store.list_baselines()]
    finally:
        store.close()


__all__ = ["VerifyOptions", "run_verify", "BaselineOptions", "run_baseline_create", "baseline_list", "EngineFailure", "CACHE_KIND"]
