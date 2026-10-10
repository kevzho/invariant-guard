"""Run profiles (docs/10 "Dynamic operation contract").

A dynamic check needs both ``--execute`` and a named profile enabled in
``.guard/config.toml``::

    [execution]
    enabled = true
    profile = "isolated-python"        # default for --execute
    allow_unisolated = false

    [execution.profiles.isolated-python]
    backend = "docker"
    image = "python:3.13-slim"         # prepared by `guard runner setup`, never pulled during a scan
    allowed_targets = ["demo/math_ops.py::normalize", "demo/*.py::*"]

The configuration is the explicit, reusable execution authorization; no prompt is added
during an authorized workflow. Unknown keys are configuration errors. ``to_run_profile``
renders the schema-valid RunProfile document (``run_profile.schema.json``) whose digest is
recorded in every execution evidence record.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any

from ..contracts.profiles import PROFILES
from ..models.spans import digest_json

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
BACKENDS = ("docker", "unisolated")
DOCKER_CAPABILITIES = ("deny_network", "readonly_source", "no_host_credentials", "scratch_only_writes", "resource_limits")
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,200}(@sha256:[0-9a-f]{64})?$")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


class ProfileError(Exception):
    pass


@dataclass
class RunProfileConfig:
    name: str
    backend: str
    allowed_targets: list[str]
    image: str | None = None
    image_digest: str | None = None
    docker: str | None = None
    input_profiles: list[str] = field(default_factory=lambda: sorted(PROFILES))
    python_path: list[str] = field(default_factory=lambda: ["."])
    cpu_seconds: int = 10
    cpus: float = 1.0
    memory_mb: int = 256
    wall_seconds: int = 120
    probe_wall_seconds: float = 5.0
    max_processes: int = 32
    scratch_mb: int = 16
    env_allowlist: list[str] = field(default_factory=list)
    max_input_bytes: int = 1 << 20
    max_output_bytes: int = 4 << 20
    max_result_bytes: int = 256 * 1024
    max_probes: int = 48
    max_shrink_rounds: int = 12
    seed: int = 0
    witness_storage: bool = True
    # Differential replay only: put the image's site-packages on sys.path (declared target
    # dependencies installed in the image). The installed distributions are recorded in the
    # replay environment digest. Contract probes keep -S and never see site-packages.
    site_packages: bool = False
    required_capabilities: list[str] = field(default_factory=lambda: list(DOCKER_CAPABILITIES))

    def allows(self, symbol_id: str) -> bool:
        return any(symbol_id == p or fnmatch.fnmatchcase(symbol_id, p) for p in self.allowed_targets)

    def to_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


_INT_KEYS = {"cpu_seconds": (1, 3600), "memory_mb": (32, 65536), "wall_seconds": (1, 3600), "max_processes": (4, 4096), "scratch_mb": (1, 4096),
             "max_input_bytes": (1024, 64 << 20), "max_output_bytes": (4096, 256 << 20), "max_result_bytes": (1024, 64 << 20),
             "max_probes": (1, 512), "max_shrink_rounds": (0, 100), "seed": (-(2 ** 62), 2 ** 62)}
_FLOAT_KEYS = {"cpus": (0.1, 64.0), "probe_wall_seconds": (0.1, 600.0)}
_LIST_KEYS = ("allowed_targets", "input_profiles", "python_path", "env_allowlist", "required_capabilities")
_STR_KEYS = ("backend", "image", "image_digest", "docker")
_KNOWN = set(_INT_KEYS) | set(_FLOAT_KEYS) | set(_LIST_KEYS) | set(_STR_KEYS) | {"witness_storage", "site_packages"}


def parse_profile(name: str, table: Any) -> RunProfileConfig:
    where = f"execution.profiles.{name}"
    if not PROFILE_NAME_RE.match(name):
        raise ProfileError(f"{where}: profile names match [A-Za-z0-9_.-]{{1,64}}")
    if not isinstance(table, dict):
        raise ProfileError(f"{where} must be a table")
    unknown = set(table) - _KNOWN
    if unknown:
        raise ProfileError(f"unknown key(s) in {where}: {sorted(unknown)}")
    backend = table.get("backend")
    if backend not in BACKENDS:
        raise ProfileError(f"{where}.backend must be one of {BACKENDS}")
    targets = table.get("allowed_targets")
    if not isinstance(targets, list) or not targets or not all(isinstance(t, str) and "::" in t for t in targets):
        raise ProfileError(f"{where}.allowed_targets must be a non-empty list of symbol ids or patterns like 'pkg/*.py::*'")
    p = RunProfileConfig(name=name, backend=backend, allowed_targets=list(targets))
    for k, (lo, hi) in _INT_KEYS.items():
        if k in table:
            v = table[k]
            if type(v) is not int or not lo <= v <= hi:
                raise ProfileError(f"{where}.{k} must be an integer in [{lo}, {hi}]")
            setattr(p, k, v)
    for k, (lo, hi) in _FLOAT_KEYS.items():
        if k in table:
            v = table[k]
            if type(v) not in (int, float) or not lo <= v <= hi:
                raise ProfileError(f"{where}.{k} must be a number in [{lo}, {hi}]")
            setattr(p, k, float(v))
    for k in _LIST_KEYS:
        if k in table and k != "allowed_targets":
            v = table[k]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ProfileError(f"{where}.{k} must be a list of strings")
            setattr(p, k, list(v))
    for key in ("witness_storage", "site_packages"):
        if key in table:
            if type(table[key]) is not bool:
                raise ProfileError(f"{where}.{key} must be a boolean")
            setattr(p, key, table[key])
    for k in ("image", "image_digest", "docker"):
        if k in table:
            if not isinstance(table[k], str) or not table[k]:
                raise ProfileError(f"{where}.{k} must be a non-empty string")
            setattr(p, k, table[k])
    bad = [x for x in p.input_profiles if x not in PROFILES]
    if bad:
        raise ProfileError(f"{where}.input_profiles names unknown profiles {bad}; known: {sorted(PROFILES)}")
    for root in p.python_path:
        if root.startswith("/") or ".." in root.split("/"):
            raise ProfileError(f"{where}.python_path entries are relative paths inside the snapshot")
    bad_env = [x for x in p.env_allowlist if not _ENV_RE.match(x)]
    if bad_env:
        raise ProfileError(f"{where}.env_allowlist entries must be environment variable names: {bad_env}")
    interp_env = [x for x in p.env_allowlist if x.startswith("PYTHON")]
    if interp_env:
        # The harness runs without -E (so PYTHONHASHSEED applies); interpreter variables
        # would change how the interpreter runs the target and are not passed through.
        raise ProfileError(f"{where}.env_allowlist must not contain interpreter variables (PYTHON*): {interp_env}")
    if p.backend == "docker":
        if not p.image or not _IMAGE_RE.match(p.image):
            raise ProfileError(f"{where}.image is required for the docker backend (for example 'python:3.13-slim')")
        if p.image_digest is not None and not re.match(r"^sha256:[0-9a-f]{64}$", p.image_digest):
            raise ProfileError(f"{where}.image_digest must be 'sha256:<64 hex>' (a repository digest)")
        if p.docker is not None and not p.docker.startswith("/"):
            raise ProfileError(f"{where}.docker must be an absolute path to the docker CLI")
        bad_caps = [c for c in p.required_capabilities if c not in DOCKER_CAPABILITIES]
        if bad_caps:
            raise ProfileError(f"{where}.required_capabilities: unknown {bad_caps}; known {list(DOCKER_CAPABILITIES)}")
    else:
        if p.image or p.image_digest or p.docker:
            raise ProfileError(f"{where}: image/image_digest/docker apply only to the docker backend")
        if "required_capabilities" in table and table["required_capabilities"]:
            raise ProfileError(f"{where}: an unisolated profile cannot require isolation capabilities (it enforces none)")
        p.required_capabilities = []
    return p


def to_run_profile(p: RunProfileConfig, *, environment_digest: str, interpreter_version: str, allow_unisolated: bool) -> dict[str, Any]:
    """The schema-valid RunProfile document (docs/10 profile fields)."""
    return {
        "schema_version": "1.0",
        "profile_id": p.name,
        "allowed_targets": list(p.allowed_targets),
        "input_templates": list(p.input_profiles),
        "interpreter": {"implementation": "cpython", "version": interpreter_version},
        "environment_lock_digest": environment_digest,
        "isolation": {"backend": "container" if p.backend == "docker" else "unisolated", "required_capabilities": list(p.required_capabilities)},
        "network_policy": "deny",
        "writable_paths": [f"/tmp (tmpfs, {p.scratch_mb} MiB, noexec)"] if p.backend == "docker" else ["<host temporary directory> (not enforced)"],
        "limits": {"cpu_seconds": p.cpu_seconds, "memory_mb": p.memory_mb, "wall_seconds": p.wall_seconds, "max_processes": p.max_processes},
        "env_allowlist": list(p.env_allowlist),
        "max_sizes": {"input_bytes": p.max_input_bytes, "output_bytes": p.max_output_bytes, "artifact_bytes": p.max_output_bytes},
        "baseline_environment": environment_digest,
        "candidate_environment": environment_digest,
        "source_retention": False,
        "witness_storage": p.witness_storage,
        "allow_unisolated": allow_unisolated and p.backend == "unisolated",
    }


def profile_digest(doc: dict[str, Any], p: RunProfileConfig) -> str:
    return digest_json({"run_profile": doc, "settings": p.to_json()})


__all__ = ["RunProfileConfig", "ProfileError", "parse_profile", "to_run_profile", "profile_digest", "authorization_digest", "DOCKER_CAPABILITIES"]


def authorization_digest(cfg: Any, name: str) -> tuple[str | None, str]:
    """Digest of a profile's resolved definition for agent (MCP) preauthorization, or
    (None, reason) when the profile cannot be used through MCP (only isolated ``docker``).

    The MCP server records this digest when it starts; ``verify`` recomputes it from the
    very configuration object the run uses, so a profile edited between the server's
    authorization check and the run is refused (audit A5-02)."""
    from ..config import ConfigError
    from ..models.spans import canonical_json, sha256_bytes

    try:
        prof = cfg.run_profile(name)
    except ConfigError as exc:
        return None, f"not usable: {exc}"
    if prof.backend != "docker":
        return None, f"backend {prof.backend!r} is not isolated; MCP runs only docker profiles"
    doc = {"profile": prof.to_json(), "allow_unisolated": bool(cfg.execution.get("allow_unisolated")), "enabled": bool(cfg.execution.get("enabled"))}
    return sha256_bytes(canonical_json(doc)), "authorized"
