"""Shared helpers for the paper benchmarks (benchmarks/run_all.py): CLI runs, report summaries, manifests."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
RESULTS = REPO / "benchmarks" / "results"

FLAG_RESULTS = {"PROVED_UNSAFE", "POSSIBLY_UNSAFE", "OBSERVED_REGRESSION", "OBSERVED_FAILURE"}
FLAG_RELATIONS = {"INTRODUCED", "CHANGED"}

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.invalid",
           "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.invalid"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_tree(root: Path, pattern: str = "*.py") -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob(pattern)):
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return "sha256:" + h.hexdigest()


def sh(args: list[str], cwd: Path | None = None, timeout: float = 60) -> str:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, env={**os.environ, **GIT_ENV}, check=True, capture_output=True, text=True).stdout.strip()


def guard(argv: list[str], cwd: Path | None = None, timeout: float = 1800) -> tuple[int, str, str, float]:
    """Run the real CLI in a fresh child process; returns (exit, stdout, stderr, wall_ms)."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    t0 = time.perf_counter()
    p = subprocess.run([PY, "-m", "invariantguard.cli", *argv], capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout)
    return p.returncode, p.stdout, p.stderr, round((time.perf_counter() - t0) * 1000, 1)


def load_avg() -> list[float]:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return []


def pct(xs: list[float], q: float) -> float:
    """Nearest-rank percentile."""
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * len(s) + 0.5)) - 1))
    return s[k]


def dist(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "p50": round(statistics.median(xs), 1), "p95": round(pct(xs, 0.95), 1), "min": round(min(xs), 1), "max": round(max(xs), 1),
            "mean": round(statistics.mean(xs), 1)}


def compact_report(r: dict[str, Any]) -> dict[str, Any]:
    """The fields the benchmarks score, kept verbatim from the report."""
    cov = r.get("coverage") or {}
    findings = []
    for f in r.get("findings", []):
        findings.append({k: f.get(k) for k in ("finding_id", "obligation_id", "symbol_id", "property_id", "property_result", "scope_validity",
                                                 "authority", "patch_relation", "severity", "witness", "witness_validated", "contract")})
    obligations = {o["obligation_id"]: {"result": o["property_result"], "scope": o.get("scope_validity"), "relation": o.get("patch_relation"),
                                        "method": o.get("method"), "witness": o.get("witness"), "required": o.get("required"),
                                        "reasons": (o.get("reasons") or [])[:2]}
                   for o in r.get("obligation_results", [])}
    contracts = []
    for c in ((r.get("contracts") or {}).get("results") or []):
        contracts.append({k: c.get(k) for k in ("contract_id", "authority", "status", "property_result", "scope_validity", "patch_relation",
                                                 "severity", "required", "witness", "original_witness")}
                         | {"probes": {k: (c.get("probes") or {}).get(k) for k in ("admitted", "excluded_by_domain", "runner_invocations",
                                                                                     "candidate_pass", "candidate_fail", "candidate_unknown")}})
    ex = (r.get("contracts") or {}).get("execution") or {}
    return {"verdict": r.get("verdict"), "run_outcome": r.get("run_outcome"), "verdict_reasons": r.get("verdict_reasons"),
            "timing_ms": r.get("timing_ms"), "required_obligations": cov.get("required_obligations"),
            "resolved_required_obligations": cov.get("resolved_required_obligations"),
            "unknown_required": sum(1 for u in cov.get("unknown_obligations", []) if u.get("required")),
            "skipped_checks": [s.get("kind") for s in cov.get("skipped_checks", [])], "contract_coverage": cov.get("contracts"),
            "slice": (r.get("patch") or {}).get("slice"), "findings": findings, "obligations": obligations, "contract_results": contracts,
            "execution": {k: ex.get(k) for k in ("status", "backend", "isolated")} if ex else None,
            "capabilities": {k: (r.get("capabilities") or {}).get(k) for k in ("solver", "execution", "model")}}


def flagged(findings: list[dict[str, Any]], kind: str = "any") -> list[dict[str, Any]]:
    out = []
    for f in findings:
        if f.get("patch_relation") not in FLAG_RELATIONS or f.get("property_result") not in FLAG_RESULTS:
            continue
        is_contract = bool(f.get("contract"))
        if kind == "static" and is_contract or kind == "dynamic" and not is_contract:
            continue
        out.append(f)
    return out


def host_manifest() -> dict[str, Any]:
    """Hardware/software identity for every results file."""
    info: dict[str, Any] = {"recorded_at": now(), "platform": platform.platform(), "machine": platform.machine(),
                            "python": sys.version.split()[0], "python_executable": PY}
    if sys.platform == "darwin":
        for key, name in (("machdep.cpu.brand_string", "cpu"), ("hw.memsize", "memory_bytes"), ("hw.model", "model"), ("hw.ncpu", "logical_cpus")):
            info[name] = sh(["sysctl", "-n", key])
        info["os"] = f"macOS {sh(['sw_vers', '-productVersion'])} ({sh(['sw_vers', '-buildVersion'])})"
    try:
        import z3

        info["z3"] = z3.get_version_string()
    except ImportError:
        info["z3"] = None
    try:
        import invariantguard
        from invariantguard import ENGINE_VERSION

        info["invariantguard"] = invariantguard.__version__
        info["engine_version"] = ENGINE_VERSION
    except ImportError:
        pass
    info["semantics_profile"] = _semantics_profile()
    info["git"] = sh(["git", "--version"])
    info["docker_server"] = sh(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30)
    info["runner_image_id"] = sh(["docker", "image", "inspect", "python:3.13-slim", "--format", "{{.Id}}"], timeout=30)
    info["ollama"] = sh(["ollama", "--version"])
    info["requirements_lock"] = sha256_file(REPO / "requirements.lock")
    frozen = sh([PY, "-m", "pip", "freeze", "--exclude-editable"], timeout=120)
    info["pip_freeze_sha256"] = "sha256:" + hashlib.sha256(frozen.encode()).hexdigest()
    lock_lines = sorted(l for l in (REPO / "requirements.lock").read_text().splitlines() if l and not l.startswith("#"))
    info["pip_freeze_equals_lock"] = sorted(l for l in frozen.splitlines() if l) == lock_lines
    info["source_tree_sha256"] = sha256_tree(REPO / "src" / "invariantguard")
    info["load_average_at_start"] = load_avg()
    return info


def _semantics_profile() -> str | None:
    try:
        from invariantguard import orchestration

        for name in ("SEMANTICS_PROFILE", "SEMANTIC_PROFILE"):
            if hasattr(orchestration, name):
                return getattr(orchestration, name)
    except ImportError:
        return None
    import re

    for p in (REPO / "src" / "invariantguard").rglob("*.py"):
        m = re.search(r"python3\.13-cpython-builtin-v\d+", p.read_text(encoding="utf-8"))
        if m:
            return m.group(0)
    return None


def write_result(name: str, doc: dict[str, Any]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{name}.json"
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return out
