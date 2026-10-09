"""M2 performance measurement: cold vs warm, quick vs full `guard verify` (docs/12, T28).

This script measures; it does not assert targets. It

1. records the host (OS, CPU, RAM, Python, z3, Git versions);
2. generates synthetic Git projects (deterministic seed) with ~100, ~1,000 and ~10,000
   module-level functions spread over modules that import a shared ``core`` module;
3. applies one of two candidate changes to a fresh copy:
   * ``leaf``: one leaf function body changes (small dependency slice);
   * ``hub``: a constant that every module's functions read changes (highly connected);
4. times `guard verify --base <commit> --format json` in a fresh process per run:
   * ``cold``: empty state directory (no cache);
   * ``warm``: state directory populated by one previous identical run;
   for ``--mode quick`` and ``--mode full``;
5. writes every raw measurement plus p50/p95 to a JSON file under ``benchmarks/results``.

Definitions: wall time is the child process's elapsed time including interpreter start
and imports; ``engine_ms`` is the sum of the report's own timing fields (capture, base
capture, lowering/static, impact). Peak RSS is ``ru_maxrss`` of the child process.

Usage: .venv/bin/python benchmarks/incremental_timing.py [--reps 30] [--scales 100,1000,10000]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable

CHILD = r"""
import json, resource, sys, time
t0 = time.perf_counter()
from invariantguard.cli import main
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    code = main(sys.argv[1:])
wall = (time.perf_counter() - t0) * 1000
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
rss_mb = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
print(json.dumps({"code": code, "in_process_ms": wall, "peak_rss_mb": rss_mb, "report": json.loads(buf.getvalue())}))
"""


def sh(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def host_info() -> dict:
    info = {"platform": platform.platform(), "machine": platform.machine(), "python": sys.version.split()[0]}
    if sys.platform == "darwin":
        for key, name in (("machdep.cpu.brand_string", "cpu"), ("hw.memsize", "memory_bytes"), ("hw.model", "model"), ("hw.ncpu", "logical_cpus")):
            try:
                info[name] = sh(["sysctl", "-n", key])
            except (OSError, subprocess.CalledProcessError):
                pass
        try:
            info["os"] = sh(["sw_vers", "-productVersion"]) + " (" + sh(["sw_vers", "-buildVersion"]) + ")"
        except (OSError, subprocess.CalledProcessError):
            pass
    try:
        import z3

        info["z3"] = z3.get_version_string()
    except ImportError:
        info["z3"] = None
    info["git"] = sh(["git", "--version"])
    info["invariantguard"] = sh([PY, "-c", "import invariantguard; print(invariantguard.__version__)"])
    return info


def gen_project(root: Path, n_functions: int, seed: int = 2026) -> list[str]:
    """Deterministic synthetic project; returns symbols to declare (exact-int domains)."""
    rng = random.Random(seed)
    per_module = 25
    n_mod = max(1, n_functions // per_module)
    symbols = []
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text("SCALE = 3\nLIMIT = 2\n\n\ndef unit(x):\n    return x + 1\n")
    symbols.append("pkg/core.py::unit")
    for m in range(n_mod):
        lines = ["from pkg.core import SCALE, LIMIT, unit", ""]
        for j in range(per_module):
            name = f"f{j}"
            kind = rng.random()
            if kind < 0.3:
                body = [f"def {name}(a, b):", "    d = a - b + SCALE", "    if d != 0:", "        return 100 // d", "    return 0"]
            elif kind < 0.5:
                body = [f"def {name}(a, b):", "    xs = [a, b, 1]", "    if 0 <= a < 3:", "        return xs[a]", "    return xs[LIMIT]"]
            elif kind < 0.7 and j > 0:
                callee = f"f{rng.randrange(j)}"
                body = [f"def {name}(a, b):", f"    v = {callee}(a, b)", "    if a > 0:", "        return 10 % unit(a)", "    return 0"]
            elif kind < 0.85:
                body = [f"def {name}(a, b):", "    if a > b:", "        return 1 / (a - b)", "    return a * SCALE"]
            else:
                body = [f"def {name}(a, b):", "    d = a * a + b * b + 1", "    return 7 // d"]
            lines += body + ["", ""]
            symbols.append(f"pkg/m{m}.py::{name}")
        (root / "pkg" / f"m{m}.py").write_text("\n".join(lines))
    (root / "pkg" / "__init__.py").write_text("")
    cfg = ['schema_version = "1.0"', "[analysis]", "quick_budget_ms = 2000", "full_budget_ms = 600000"]
    cfg += [f'[[domains]]\nsymbol = "{s}"\nprofile = "python_exact_builtin_ints"\nauthority = "DECLARED"\nreason = "benchmark"' for s in symbols]
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text("\n".join(cfg) + "\n")
    (root / ".gitignore").write_text(".guard/cache/\n.guard/runs/\n.guard/artifacts/\n.guard/snapshots/\n.guard/state.sqlite*\n")
    return symbols


def git_init(root: Path) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "b", "GIT_AUTHOR_EMAIL": "b@example.invalid",
           "GIT_COMMITTER_NAME": "b", "GIT_COMMITTER_EMAIL": "b@example.invalid"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return sh(["git", "rev-parse", "HEAD"], cwd=root)


def apply_change(root: Path, change: str) -> None:
    if change == "leaf":
        p = root / "pkg" / "m0.py"
        p.write_text(p.read_text().replace("def f0(a, b):", "def f0(a, b):\n    a = a + 0", 1))
    elif change == "hub":
        p = root / "pkg" / "core.py"
        p.write_text(p.read_text().replace("SCALE = 3", "SCALE = 0"))


def run_child(root: Path, base: str, mode: str, state: Path) -> dict:
    t0 = time.perf_counter()
    p = subprocess.run([PY, "-c", CHILD, "verify", str(root), "--base", base, "--mode", mode, "--format", "json", "--state-dir", str(state)],
                       capture_output=True, text=True, timeout=3600)
    wall = (time.perf_counter() - t0) * 1000
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:])
    out = json.loads(p.stdout)
    r = out["report"]
    t = r["timing_ms"]
    engine = sum(v for k, v in t.items() if isinstance(v, (int, float)) and k != "solver")
    return {
        "wall_ms": round(wall, 1), "in_process_ms": round(out["in_process_ms"], 1), "engine_ms": round(engine, 1),
        "timing_ms": t, "peak_rss_mb": round(out["peak_rss_mb"], 1), "exit": out["code"], "verdict": r["verdict"],
        "run_outcome": r["run_outcome"], "affected": r["patch"]["slice"]["affected_symbols"],
        "required": r["coverage"]["required_obligations"], "resolved": r["coverage"]["resolved_required_obligations"],
        "cache": {k: (v or {}).get("hits") if isinstance(v, dict) else v for k, v in r["patch"]["cache"].items() if k in ("base", "candidate")},
    }


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * len(s) + 0.5)) - 1))  # nearest rank
    return s[k]


def summarize(runs: list[dict]) -> dict:
    out = {"n": len(runs)}
    for k in ("wall_ms", "in_process_ms", "engine_ms", "peak_rss_mb"):
        xs = [r[k] for r in runs]
        out[k] = {"p50": round(statistics.median(xs), 1), "p95": round(pct(xs, 0.95), 1), "min": round(min(xs), 1), "max": round(max(xs), 1)}
    out["outcomes"] = sorted({f"{r['verdict']}/{r['run_outcome']}" for r in runs})
    out["affected_symbols"] = sorted({r["affected"] for r in runs})
    out["required_obligations"] = sorted({r["required"] for r in runs})
    out["resolved_required"] = sorted({r["resolved"] for r in runs})
    out["cache_hits"] = runs[-1]["cache"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--scales", default="100,1000,10000")
    ap.add_argument("--reps-large", type=int, default=3, help="repetitions for scales >= 10000")
    ap.add_argument("--out", type=Path, default=REPO / "benchmarks" / "results")
    args = ap.parse_args()
    results = {"generated_at": datetime.now(timezone.utc).isoformat(), "host": host_info(), "definitions": __doc__, "conditions": []}
    work = Path(tempfile.mkdtemp(prefix="ig-bench-"))
    try:
        for scale in [int(s) for s in args.scales.split(",")]:
            reps = args.reps if scale < 10000 else args.reps_large
            for change in ("leaf", "hub"):
                root = work / f"p{scale}_{change}"
                root.mkdir()
                symbols = gen_project(root, scale)
                base = git_init(root)
                apply_change(root, change)
                for mode in ("quick", "full"):
                    cold, warm = [], []
                    for i in range(reps):
                        st = work / f"state_{scale}_{change}_{mode}_cold_{i}"
                        cold.append(run_child(root, base, mode, st))
                        shutil.rmtree(st, ignore_errors=True)
                    warm_state = work / f"state_{scale}_{change}_{mode}_warm"
                    run_child(root, base, mode, warm_state)  # populate
                    for _ in range(reps):
                        warm.append(run_child(root, base, mode, warm_state))
                    for label, runs in (("cold", cold), ("warm", warm)):
                        cond = {"functions": len(symbols), "change": change, "mode": mode, "cache": label, "summary": summarize(runs), "runs": runs}
                        results["conditions"].append(cond)
                        s = cond["summary"]
                        print(f"{len(symbols):>6} fn  {change:<4} {mode:<5} {label:<4} n={s['n']:<3} wall p50 {s['wall_ms']['p50']:>9.1f} ms  p95 {s['wall_ms']['p95']:>9.1f} ms  "
                              f"engine p50 {s['engine_ms']['p50']:>9.1f} ms  rss p50 {s['peak_rss_mb']['p50']:>6.1f} MB  affected {s['affected_symbols']}  "
                              f"{s['outcomes']}  hits {s['cache_hits']}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"m2_incremental_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(results, indent=1))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
