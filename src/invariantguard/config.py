"""Load and validate ``.guard/config.toml`` (docs/09 configuration draft).

Unknown sections/keys or invalid values are configuration errors (CLI exit 4), never
silently ignored. Parsing uses stdlib ``tomllib`` only.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models.spans import sha256_bytes

CONFIG_DIR = ".guard"
CONFIG_NAME = "config.toml"
DOMAIN_PROFILES = frozenset({
    "python_exact_builtin_ints",
    "python_exact_builtin_list",
    "python_exact_builtin_tuple",
    "python_optional_exact_builtin_int",
})
DOMAIN_AUTHORITIES = frozenset({"DECLARED", "APPROVED"})
DEFAULT_EXCLUDES = [".git", ".guard", ".venv", "venv", "vendor", "node_modules", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache"]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class DomainDecl:
    symbol: str
    profile: str
    authority: str
    reason: str
    params: tuple[str, ...] | None = None


@dataclass
class AnalysisConfig:
    languages: list[str] = field(default_factory=lambda: ["python"])
    mode: str = "quick"
    solver_enabled: bool = True
    per_query_timeout_ms: int = 100
    per_query_rlimit: int = 0
    quick_budget_ms: int = 2000
    require_coverage: bool = True
    witness_search: bool = True
    full_budget_ms: int = 60000  # M2: static budget of `guard verify --mode full`


@dataclass
class Config:
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    execution: dict[str, Any] = field(default_factory=lambda: {"enabled": False, "profile": "", "allow_unisolated": False})
    semantic: dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    privacy: dict[str, Any] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    domains: list[DomainDecl] = field(default_factory=list)
    path: Path | None = None
    digest: str | None = None
    run_profiles: dict[str, Any] = field(default_factory=dict)  # M3: name -> execution.profiles.RunProfileConfig

    def run_profile(self, name: str | None) -> Any:
        """The named (or default) run profile for ``--execute``; ConfigError when not authorized."""
        if not self.execution.get("enabled"):
            raise ConfigError("dynamic execution is not enabled: set [execution] enabled = true and define [execution.profiles.NAME] "
                              "(the configuration is the explicit execution authorization)")
        chosen = name or self.execution.get("profile") or ""
        if not chosen:
            raise ConfigError("--execute needs --run-profile NAME (or [execution] profile = NAME)")
        prof = self.run_profiles.get(chosen)
        if prof is None:
            raise ConfigError(f"run profile {chosen!r} is not defined in [execution.profiles] (known: {sorted(self.run_profiles)})")
        return prof

    def domain_map(self) -> dict[str, DomainDecl | list[DomainDecl]]:
        """symbol -> declaration, or a list of declarations for disjoint parameter sets."""
        out: dict[str, DomainDecl | list[DomainDecl]] = {}
        for d in self.domains:
            prev = out.get(d.symbol)
            if prev is None:
                out[d.symbol] = d
            else:
                out[d.symbol] = (prev if isinstance(prev, list) else [prev]) + [d]
        return out

    def analysis_key(self) -> dict[str, Any]:
        """Configuration that can change analysis results (part of every summary key)."""
        a = self.analysis
        return {
            "languages": list(a.languages),
            "solver_enabled": a.solver_enabled,
            "per_query_timeout_ms": a.per_query_timeout_ms,
            "per_query_rlimit": a.per_query_rlimit,
            "witness_search": a.witness_search,
            "exclude": sorted(self.exclude),
        }


_ANALYSIS_KEYS = {
    "languages": list,
    "mode": str,
    "solver_enabled": bool,
    "per_query_timeout_ms": int,
    "per_query_rlimit": int,
    "quick_budget_ms": int,
    "require_coverage": bool,
    "witness_search": bool,
    "full_budget_ms": int,
}
_TOP_KEYS = {"schema_version", "analysis", "execution", "semantic", "privacy", "scope", "domains"}


def _check_type(where: str, key: str, value: Any, typ: type) -> None:
    if typ is int and isinstance(value, bool):
        raise ConfigError(f"{where}.{key} must be an integer")
    if not isinstance(value, typ):
        raise ConfigError(f"{where}.{key} must be {typ.__name__}, got {type(value).__name__}")


def parse_config(raw: bytes, path: Path | None = None) -> Config:
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from None
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"unknown configuration keys: {sorted(unknown)}")
    if data.get("schema_version", "1.0") != "1.0":
        raise ConfigError(f"unsupported config schema_version {data.get('schema_version')!r} (expected '1.0')")
    cfg = Config(path=path, digest=sha256_bytes(raw))
    analysis = data.get("analysis", {})
    if not isinstance(analysis, dict):
        raise ConfigError("[analysis] must be a table")
    for k, v in analysis.items():
        if k not in _ANALYSIS_KEYS:
            raise ConfigError(f"unknown key analysis.{k}")
        _check_type("analysis", k, v, _ANALYSIS_KEYS[k])
        setattr(cfg.analysis, k, v)
    a = cfg.analysis
    if a.mode not in ("quick", "full"):
        raise ConfigError("analysis.mode must be 'quick' or 'full'")
    if a.per_query_timeout_ms <= 0 or a.quick_budget_ms <= 0 or a.full_budget_ms <= 0 or a.per_query_rlimit < 0:
        raise ConfigError("analysis budgets must be positive")
    if any(lang != "python" for lang in a.languages):
        raise ConfigError(f"unsupported analysis.languages {a.languages}: only 'python' is implemented")
    execution = data.get("execution", {})
    if not isinstance(execution, dict):
        raise ConfigError("[execution] must be a table")
    for k in execution:
        if k not in ("enabled", "profile", "allow_unisolated", "profiles"):
            raise ConfigError(f"unknown key execution.{k}")
    for k, typ in (("enabled", bool), ("allow_unisolated", bool), ("profile", str)):
        if k in execution:
            _check_type("execution", k, execution[k], typ)
    profiles = execution.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ConfigError("[execution.profiles] must be a table of named profiles")
    from .execution.profiles import ProfileError, parse_profile

    for name, table in profiles.items():
        try:
            prof = parse_profile(name, table)
        except ProfileError as exc:
            raise ConfigError(str(exc)) from None
        if prof.backend == "unisolated" and not execution.get("allow_unisolated", False):
            raise ConfigError(f"execution.profiles.{name} uses the unisolated backend, which requires [execution] allow_unisolated = true "
                              "(unisolated execution is never a sandbox)")
        cfg.run_profiles[name] = prof
    if execution.get("profile") and execution["profile"] not in cfg.run_profiles:
        raise ConfigError(f"execution.profile names an undefined profile {execution['profile']!r}")
    cfg.execution.update({k: v for k, v in execution.items() if k != "profiles"})
    semantic = data.get("semantic", {})
    if not isinstance(semantic, dict):
        raise ConfigError("[semantic] must be a table")
    cfg.semantic.update(parse_semantic(semantic))
    privacy = data.get("privacy", {})
    if not isinstance(privacy, dict):
        raise ConfigError("[privacy] must be a table")
    for k in privacy:
        if k not in ("telemetry", "store_raw_model_context", "retain_replay_source", "store_witnesses"):
            raise ConfigError(f"unknown key privacy.{k}")
    if privacy.get("telemetry"):
        raise ConfigError("privacy.telemetry = true is not supported (no telemetry exists)")
    cfg.privacy.update(privacy)
    scope = data.get("scope", {})
    if not isinstance(scope, dict):
        raise ConfigError("[scope] must be a table")
    for k in scope:
        if k != "exclude":
            raise ConfigError(f"unknown key scope.{k}")
    if "exclude" in scope:
        if not isinstance(scope["exclude"], list) or not all(isinstance(x, str) for x in scope["exclude"]):
            raise ConfigError("scope.exclude must be a list of strings")
        cfg.exclude = sorted(set(scope["exclude"]) | {".git", ".guard"})
    domains = data.get("domains", [])
    if not isinstance(domains, list):
        raise ConfigError("[[domains]] must be an array of tables")
    seen = set()
    for i, d in enumerate(domains):
        if not isinstance(d, dict):
            raise ConfigError(f"domains[{i}] must be a table")
        extra = set(d) - {"symbol", "profile", "authority", "reason", "params"}
        if extra:
            raise ConfigError(f"unknown keys in domains[{i}]: {sorted(extra)}")
        for req in ("symbol", "profile", "authority", "reason"):
            if not isinstance(d.get(req), str) or not d[req].strip():
                raise ConfigError(f"domains[{i}].{req} is required and must be a non-empty string")
        if "::" not in d["symbol"]:
            raise ConfigError(f"domains[{i}].symbol must look like 'path/to/file.py::function'")
        if d["profile"] not in DOMAIN_PROFILES:
            raise ConfigError(f"domains[{i}].profile {d['profile']!r} is not a known profile {sorted(DOMAIN_PROFILES)}")
        if d["authority"] not in DOMAIN_AUTHORITIES:
            raise ConfigError(f"domains[{i}].authority must be one of {sorted(DOMAIN_AUTHORITIES)}")
        params = d.get("params")
        if params is not None and (not isinstance(params, list) or not all(isinstance(p, str) for p in params)):
            raise ConfigError(f"domains[{i}].params must be a list of parameter names")
        if d["symbol"] in seen:
            # Several declarations for one symbol are allowed only for disjoint, explicit
            # parameter lists (e.g. an int index and a list receiver with different profiles).
            prior = [x for x in cfg.domains if x.symbol == d["symbol"]]
            if params is None or any(x.params is None for x in prior) or any(set(params) & set(x.params or ()) for x in prior):
                raise ConfigError(f"duplicate domain declaration for {d['symbol']} (multiple entries need disjoint explicit params)")
        seen.add(d["symbol"])
        cfg.domains.append(DomainDecl(d["symbol"], d["profile"], d["authority"], d["reason"].strip(), tuple(params) if params is not None else None))
    return cfg


_SEMANTIC_INT = {"request_timeout_s": (5, 3600, 600), "max_context_bytes": (1024, 65536, 12000), "max_context_tokens": (256, 32768, 4000),
                 "max_candidates": (1, 8, 3), "seed": (0, 2 ** 31 - 1, 0), "num_ctx": (2048, 32768, 8192), "num_predict": (128, 8192, 1024),
                 "max_functions": (1, 1000, 64), "max_output_tokens": (256, 64000, 16000), "max_request_bytes": (4096, 1 << 20, 256 * 1024)}
_SEMANTIC_STR = ("provider", "api", "endpoint", "model_id", "model_digest", "keep_alive", "base_url", "api_key_env", "structured_output", "effort")
PROVIDERS = ("loopback", "openai_compatible", "anthropic")
PROVIDER_API = {"loopback": "ollama", "openai_compatible": "openai_chat_completions", "anthropic": "anthropic_messages"}
STRUCTURED_OUTPUT = {"openai_compatible": ("auto", "json_schema", "json_object"), "anthropic": ("auto", "json_schema", "none")}
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ENV_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _valid_env_name(name: str) -> bool:
    return 1 <= len(name) <= 128 and set(name) <= ENV_NAME_CHARS and not name[0].isdigit()


def parse_semantic(semantic: dict[str, Any]) -> dict[str, Any]:
    """[semantic]: a contract proposer endpoint (M4; remote adapters added by DECISIONS I-105).

    ``provider = "loopback"`` (default) is the Ollama HTTP API on a validated loopback
    endpoint. ``openai_compatible`` and ``anthropic`` are bring-your-own-key adapters at
    ``base_url``; a non-loopback destination additionally needs https and an explicit
    ``remote_allowed = true``. ``api_key_env`` names the environment variable that holds the
    key; the key itself is never part of the configuration. No download, no fallback.
    """
    known = {"enabled", "download_during_scan", "temperature", "remote_allowed", *_SEMANTIC_INT, *_SEMANTIC_STR}
    for k in semantic:
        if k not in known:
            raise ConfigError(f"unknown key semantic.{k}" + (" (store the key in an environment variable and name it with semantic.api_key_env)"
                                                             if "key" in k.lower() or "token" in k.lower() or "secret" in k.lower() else ""))
    out: dict[str, Any] = {"enabled": False}
    for k in ("enabled", "download_during_scan", "remote_allowed"):
        if k in semantic:
            _check_type("semantic", k, semantic[k], bool)
            out[k] = semantic[k]
    out.setdefault("remote_allowed", False)
    if semantic.get("download_during_scan"):
        raise ConfigError("semantic.download_during_scan = true is not supported: models are installed by an explicit separate step, never during a scan")
    for k in _SEMANTIC_STR:
        if k in semantic:
            _check_type("semantic", k, semantic[k], str)
            out[k] = semantic[k]
    for k, (lo, hi, default) in _SEMANTIC_INT.items():
        v = semantic.get(k, default)
        _check_type("semantic", k, v, int)
        if not lo <= v <= hi:
            raise ConfigError(f"semantic.{k} must be in [{lo}, {hi}]")
        out[k] = v
    t = semantic.get("temperature", 0.0)
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0.0 <= float(t) <= 2.0:
        raise ConfigError("semantic.temperature must be a number in [0, 2]")
    out["temperature"] = float(t)
    provider = out.setdefault("provider", "loopback")
    if provider not in PROVIDERS:
        raise ConfigError(f"semantic.provider {provider!r} is not supported: use one of {list(PROVIDERS)} (there is no other provider and no fallback)")
    api = out.setdefault("api", PROVIDER_API[provider])
    if api != PROVIDER_API[provider]:
        raise ConfigError(f"semantic.api {api!r} does not match provider {provider!r} (its API is {PROVIDER_API[provider]!r})")
    if "api_key_env" in out and not _valid_env_name(out["api_key_env"]):
        # Never echo the value: a user may have pasted the key itself here.
        raise ConfigError("semantic.api_key_env must be the NAME of an environment variable (A-Z, 0-9, _), not the key itself")
    if out.get("effort") and out["effort"] not in ("low", "medium", "high", "xhigh", "max"):
        raise ConfigError("semantic.effort must be one of low, medium, high, xhigh, max")
    if provider == "loopback":
        for k in ("base_url", "api_key_env", "structured_output", "effort"):
            if k in out:
                raise ConfigError(f"semantic.{k} applies to the openai_compatible/anthropic providers, not to provider = 'loopback'")
        if out["remote_allowed"]:
            raise ConfigError("semantic.remote_allowed has no meaning for provider = 'loopback' (the loopback adapter never contacts a remote host)")
        out.setdefault("keep_alive", "10m")
        if out.get("endpoint"):
            from .semantic.endpoint import EndpointError, validate_loopback_endpoint

            try:
                validate_loopback_endpoint(out["endpoint"])
            except EndpointError as exc:
                raise ConfigError(f"semantic.endpoint: {exc}") from None
        if out.get("model_digest"):
            d = out["model_digest"].lower()
            hexd = d.split(":", 1)[1] if d.startswith("sha256:") else d
            if len(hexd) != 64 or any(c not in "0123456789abcdef" for c in hexd):
                raise ConfigError("semantic.model_digest must be a sha256 digest (64 hex digits, optionally prefixed 'sha256:')")
        if out["enabled"]:
            if not out.get("endpoint"):
                raise ConfigError("semantic.enabled = true needs semantic.endpoint (a loopback URL such as http://127.0.0.1:11434)")
            if not out.get("model_id"):
                raise ConfigError("semantic.enabled = true needs semantic.model_id (a model already installed at the endpoint)")
        return out
    # Bring-your-own-key providers.
    for k in ("endpoint", "model_digest", "keep_alive"):
        if k in out:
            raise ConfigError(f"semantic.{k} applies to provider = 'loopback' only; {provider} uses semantic.base_url")
    if provider == "anthropic":
        out.setdefault("base_url", ANTHROPIC_DEFAULT_BASE_URL)
    out.setdefault("structured_output", "auto")
    if out["structured_output"] not in STRUCTURED_OUTPUT[provider]:
        raise ConfigError(f"semantic.structured_output for {provider} must be one of {list(STRUCTURED_OUTPUT[provider])}")
    if out.get("base_url"):
        from .semantic.remote import DestinationError, validate_provider_url

        try:
            validate_provider_url(out["base_url"], remote_allowed=out["remote_allowed"])
        except DestinationError as exc:
            raise ConfigError(f"semantic.base_url: {exc}") from None
    if out["enabled"]:
        if not out.get("base_url"):
            raise ConfigError(f"semantic.enabled = true with provider = {provider!r} needs semantic.base_url")
        if not out.get("model_id"):
            raise ConfigError("semantic.enabled = true needs semantic.model_id")
        if provider == "anthropic" and not out.get("api_key_env"):
            raise ConfigError("provider = 'anthropic' needs semantic.api_key_env (the NAME of the environment variable holding the API key)")
    return out


def find_config(start: Path) -> Path | None:
    """Nearest ``.guard/config.toml`` at or above ``start``; stops at a Git root."""
    cur = start if start.is_dir() else start.parent
    cur = cur.resolve()
    while True:
        candidate = cur / CONFIG_DIR / CONFIG_NAME
        if candidate.is_file():
            return candidate
        if (cur / ".git").exists() or cur.parent == cur:
            return None
        cur = cur.parent


def load_config(path: Path) -> Config:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from None
    return parse_config(raw, path)


INIT_TEMPLATE = """\
# InvariantGuard local configuration (written by `guard init`).
schema_version = "1.0"

[analysis]
languages = ["python"]
mode = "quick"
solver_enabled = true
per_query_timeout_ms = 100
quick_budget_ms = 2000
require_coverage = true

[execution]
# Dynamic contract checks run only with `guard verify --execute --run-profile NAME` and an
# enabled profile below. Prepare the image explicitly with `guard runner setup` (scans never pull).
enabled = false
profile = ""
allow_unisolated = false

# [execution.profiles.isolated-python]
# backend = "docker"
# image = "python:3.13-slim"
# allowed_targets = ["pkg/math_ops.py::normalize"]

[semantic]
# Optional contract proposer for `guard contracts discover --model local|remote`.
# Default: a local model on a loopback endpoint; guard never downloads a model. Example (not active):
#   enabled = true
#   endpoint = "http://127.0.0.1:11434"
#   model_id = "qwen2.5-coder:7b"
#   model_digest = "sha256:..."   # optional pin; another artifact is refused
# Bring-your-own-key remote proposer (opt-in; context packets LEAVE this machine; free tiers may
# retain or train on submitted data). Never a fallback for the local model. Example (not active):
#   enabled = true
#   provider = "anthropic"          # or "openai_compatible" with base_url = "https://HOST/v1"
#   model_id = "claude-sonnet-5-5"
#   api_key_env = "ANTHROPIC_API_KEY"   # the NAME of the variable; never the key itself
#   remote_allowed = true           # required before any non-loopback destination is contacted
enabled = false

[privacy]
telemetry = false
store_raw_model_context = false
retain_replay_source = false
store_witnesses = true

[scope]
exclude = [".git", ".guard", ".venv", "vendor", "node_modules"]

# Declare admitted input domains explicitly, for example:
# [[domains]]
# symbol = "pkg/math_ops.py::ratio"
# profile = "python_exact_builtin_ints"
# authority = "DECLARED"
# reason = "public API admits exact built-in integer inputs"
"""

SUGGESTED_IGNORE = """\
# Suggested ignore rules for InvariantGuard local state (not applied automatically):
.guard/cache/
.guard/runs/
.guard/artifacts/
.guard/snapshots/
.guard/state.sqlite*
"""
