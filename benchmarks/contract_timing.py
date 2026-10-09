"""M3 measurement: cost of `guard verify --execute` on Fixture B (docs/12; measures, asserts nothing).

For each backend (docker, unisolated) and each scenario:

* ``regression``: the Fixture B patch (`sum(xs) + 1.0`) with the approved positive-sum
  contract: batch probes, shrinking, fresh-run confirmation (base once, candidate twice);
* ``tested``: the repaired candidate (same behavior as the base): batch probes only;

it runs the real CLI in a fresh child process per repetition and records wall time, the
report's ``timing_ms.dynamic``, the runner invocations, the verdict and the contract result.
The docker backend needs Docker and the image prepared by `guard runner setup`; if it is
unavailable that is recorded (RUNNER_UNAVAILABLE), not hidden.

Usage: .venv/bin/python benchmarks/contract_timing.py [--reps 10] [--backends docker,unisolated]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks"))
sys.path.insert(0, str(REPO / "examples" / "contract_demo"))

from incremental_timing import CHILD, host_info  # noqa: E402
from run_demo import CONTRACTS, NORMALIZE, config, git  # noqa: E402


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]


def make_repo(tmp: Path, backend: str) -> tuple[Path, str]:
    root = tmp / f"repo-{backend}"
    (root / ".guard").mkdir(parents=True)
    (root / ".guard" / "config.toml").write_text(config(backend))
    (root / ".gitignore").write_text(".guard/cache/\n.guard/runs/\n.guard/artifacts/\n.guard/snapshots/\n.guard/state.sqlite*\n")
    (root / "demo").mkdir()
    (root / "demo" / "math_ops.py").write_text(NORMALIZE)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "baseline")
    base = git(root, "rev-parse", "HEAD")
    f = tmp / "contract.json"
    f.write_text(json.dumps(CONTRACTS[0]))
    p = subprocess.run([sys.executable, "-m", "invariantguard.cli", "contracts", "declare", "--file", str(f), "--reason", "benchmark", "--path", str(root)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(p.stderr)
    return root, base


def run_once(root: Path, base: str) -> dict:
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-c", CHILD, "verify", str(root), "--base", base, "--format", "json", "--execute"],
                       capture_output=True, text=True, cwd=root)
    wall = (time.perf_counter() - t0) * 1000
    out = json.loads(p.stdout)
    r = out["report"]
    c = r["contracts"]["results"][0]
    return {"wall_ms": round(wall, 3), "dynamic_ms": r["timing_ms"]["dynamic"], "runner_probe_ms": r["timing_ms"].get("runner_probe"), "peak_rss_mb": round(out["peak_rss_mb"], 1), "exit": out["code"],
            "verdict": r["verdict"], "execution": r["contracts"]["execution"]["status"], "property_result": c["property_result"],
            "patch_relation": c["patch_relation"], "runner_invocations": (c.get("probes") or {}).get("runner_invocations"),
            "admitted": (c.get("probes") or {}).get("admitted"), "witness": c.get("witness"), "original_witness": c.get("original_witness")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--backends", default="docker,unisolated")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="ig-bench-m3-"))
    results = {"benchmark": "m3_contract_timing", "created_at": datetime.now(timezone.utc).isoformat(), "host": host_info(), "reps": args.reps,
               "definitions": {"wall_ms": "child process elapsed time incl. interpreter start, imports, static analysis and dynamic checks",
                               "dynamic_ms": "report timing_ms.dynamic: contract checks (admission, container/process runs, shrinking, confirmation, evidence)",
                               "runner_probe_ms": "report timing_ms.runner_probe: backend availability + capability probe before any target import (once per command)"},
               "conditions": []}
    try:
        for backend in args.backends.split(","):
            root, base = make_repo(tmp, backend)
            for scenario in ("regression", "tested"):
                (root / "demo" / "math_ops.py").write_text(NORMALIZE.replace("sum(xs)", "sum(xs) + 1.0") if scenario == "regression" else NORMALIZE + "\n# repaired\n")
                runs = [run_once(root, base) for _ in range(args.reps)]
                walls = [r["wall_ms"] for r in runs]
                dyn = [r["dynamic_ms"] for r in runs if r["dynamic_ms"] is not None]
                probe = [r["runner_probe_ms"] for r in runs if r["runner_probe_ms"] is not None]
                cond = {"backend": backend, "scenario": scenario, "n": len(runs), "runs": runs,
                        "wall_p50_ms": round(statistics.median(walls), 1), "wall_p95_ms": round(pct(walls, 0.95), 1),
                        "dynamic_p50_ms": round(statistics.median(dyn), 1) if dyn else None, "dynamic_p95_ms": round(pct(dyn, 0.95), 1) if dyn else None,
                        "runner_probe_p50_ms": round(statistics.median(probe), 1) if probe else None,
                        "outcomes": sorted({(r["verdict"], r["property_result"], r["execution"]) for r in runs})}
                results["conditions"].append(cond)
                print(f"{backend:10s} {scenario:10s} n={len(runs)} wall p50/p95 {cond['wall_p50_ms']}/{cond['wall_p95_ms']} ms; "
                      f"dynamic p50/p95 {cond['dynamic_p50_ms']}/{cond['dynamic_p95_ms']} ms; probe p50 {cond['runner_probe_p50_ms']} ms; outcomes {cond['outcomes']}; "
                      f"runner invocations {sorted({r['runner_invocations'] for r in runs}, key=str)}")
        out = REPO / "benchmarks" / "results" / f"m3_contracts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(results, indent=1, default=str) + "\n")
        print(f"raw data: {out.relative_to(REPO)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
