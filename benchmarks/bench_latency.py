"""Latency and incremental-vs-full consistency on synthetic repositories (docs/12 performance experiment).

Projects are generated deterministically by benchmarks/incremental_timing.py (seed 2026): modules
of 25 module-level functions importing a shared ``pkg/core.py``, all with declared exact-int
domains, at about 100, 1,000 and 10,000 supported functions. Candidate changes:

* ``leaf``: one function body changes (small dependency slice);
* ``hub``: ``core.SCALE`` changes (every reader, directly or through a callee, is invalidated);
* ``callee``: the shared helper ``unit(x)`` changes from ``x + 1`` to ``x - 1``; callers that
  compute ``10 % unit(a)`` under ``a > 0`` now fail at ``a = 1`` (real INTRODUCED defects in
  unchanged callers). Used for the consistency check (one run per mode), not for the latency series.

Each measurement is ``guard verify --base <commit> --format json --mode quick|full`` in a fresh
child process (interpreter start and imports included); *cold* = empty state directory, *warm* =
state directory populated by one identical earlier run. The default ``quick_budget_ms = 2000``
applies (a quick run that exceeds it is INCOMPLETE, never PASS).

Consistency: the obligations of the first cold quick run are compared with the same obligation IDs
in the first cold full run. *mismatched* = both runs make a claim (not UNKNOWN) and the claims
differ; *coverage_loss* = quick says UNKNOWN (budget or slice) where full makes a claim;
*missed_introduced* = an INTRODUCED/CHANGED failure finding of the full run that the quick run does
not report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from bench_common import PY, compact_report, dist, flagged, host_manifest, load_avg, now, write_result
from incremental_timing import CHILD, gen_project, git_init

CHANGES = ("leaf", "hub", "callee")


def apply_change(root: Path, change: str) -> None:
    if change == "leaf":
        p = root / "pkg" / "m0.py"
        p.write_text(p.read_text().replace("def f0(a, b):", "def f0(a, b):\n    a = a + 0", 1))
    elif change == "hub":
        p = root / "pkg" / "core.py"
        p.write_text(p.read_text().replace("SCALE = 3", "SCALE = 0"))
    elif change == "callee":
        p = root / "pkg" / "core.py"
        p.write_text(p.read_text().replace("return x + 1", "return x - 1"))
    else:
        raise ValueError(change)


def run_child(root: Path, base: str, mode: str, state: Path, keep: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    p = subprocess.run([PY, "-c", CHILD, "verify", str(root), "--base", base, "--mode", mode, "--format", "json", "--state-dir", str(state)],
                       capture_output=True, text=True, timeout=7200)
    wall = (time.perf_counter() - t0) * 1000
    if not p.stdout.strip():
        raise RuntimeError(p.stderr[-2000:])
    out = json.loads(p.stdout)
    r = out["report"]
    t = r["timing_ms"]
    engine = sum(v for k, v in t.items() if isinstance(v, (int, float)) and k != "solver")
    rec = {"wall_ms": round(wall, 1), "in_process_ms": round(out["in_process_ms"], 1), "engine_ms": round(engine, 1), "timing_ms": t,
           "peak_rss_mb": round(out["peak_rss_mb"], 1), "exit": out["code"], "verdict": r["verdict"], "run_outcome": r["run_outcome"],
           "affected": r["patch"]["slice"]["affected_symbols"], "required": r["coverage"]["required_obligations"],
           "resolved": r["coverage"]["resolved_required_obligations"],
           "introduced_findings": len(flagged(r.get("findings", []), "static")),
           "cache": {k: (v or {}).get("hits") if isinstance(v, dict) else v for k, v in r["patch"]["cache"].items() if k in ("base", "candidate")},
           "load_avg": load_avg()}
    if keep:
        rec["_compact"] = compact_report(r)
    return rec


def consistency(q: dict[str, Any], f: dict[str, Any]) -> dict[str, Any]:
    oq, of = q["obligations"], f["obligations"]
    c = {"quick_obligations": len(oq), "full_obligations": len(of), "compared": 0, "equal": 0, "mismatched": 0, "coverage_loss": 0,
         "quick_more_precise": 0, "missing_in_full": 0, "examples": []}
    for oid, a in oq.items():
        b = of.get(oid)
        if b is None:
            c["missing_in_full"] += 1
            continue
        c["compared"] += 1
        if (a["result"], a["scope"]) == (b["result"], b["scope"]):
            c["equal"] += 1
        elif a["result"] == "UNKNOWN":
            c["coverage_loss"] += 1
        elif b["result"] == "UNKNOWN":
            c["quick_more_precise"] += 1
        else:
            c["mismatched"] += 1
            if len(c["examples"]) < 5:
                c["examples"].append({"obligation_id": oid, "quick": [a["result"], a["scope"]], "full": [b["result"], b["scope"]]})
    fq = {x["obligation_id"] for x in flagged(q["findings"], "static")}
    ff = {x["obligation_id"] for x in flagged(f["findings"], "static")}
    c["introduced_full"] = len(ff)
    c["introduced_quick"] = len(fq)
    c["missed_introduced"] = len(ff - fq)
    c["extra_introduced_in_quick"] = len(fq - ff)
    c["quick_verdict"], c["full_verdict"] = q["verdict"], f["verdict"]
    c["quick_run_outcome"], c["full_run_outcome"] = q["run_outcome"], f["run_outcome"]
    return c


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"n": len(runs)}
    for k in ("wall_ms", "in_process_ms", "engine_ms", "peak_rss_mb"):
        out[k] = dist([r[k] for r in runs])
    out["outcomes"] = sorted({f"{r['verdict']}/{r['run_outcome']}" for r in runs})
    out["affected_symbols"] = sorted({r["affected"] for r in runs})
    out["required_obligations"] = sorted({r["required"] for r in runs})
    out["resolved_required"] = sorted({r["resolved"] for r in runs})
    out["introduced_findings"] = sorted({r["introduced_findings"] for r in runs})
    out["cache_hits_last_run"] = runs[-1]["cache"]
    out["load_avg_range_1min"] = [min(r["load_avg"][0] for r in runs if r["load_avg"]), max(r["load_avg"][0] for r in runs if r["load_avg"])] \
        if any(r["load_avg"] for r in runs) else None
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--reps-large", type=int, default=5, help="repetitions at >= 10,000 functions")
    ap.add_argument("--scales", default="100,1000,10000")
    ap.add_argument("--out-name", default="paper_latency")
    args = ap.parse_args(argv)
    doc: dict[str, Any] = {"schema_version": "1.0", "benchmark": "latency_incremental", "started_at": now(), "host": host_manifest(),
                           "definitions": __doc__, "settings": {"reps": args.reps, "reps_large": args.reps_large, "scales": args.scales,
                                                                "quick_budget_ms": 2000, "full_budget_ms": 600000, "generator_seed": 2026},
                           "conditions": [], "consistency": []}
    work = Path(tempfile.mkdtemp(prefix="ig-paper-lat-"))
    try:
        for scale in [int(s) for s in args.scales.split(",")]:
            reps = args.reps if scale < 10000 else args.reps_large
            for change in CHANGES:
                root = work / f"p{scale}_{change}"
                root.mkdir()
                symbols = gen_project(root, scale)
                base = git_init(root)
                apply_change(root, change)
                kept: dict[str, dict[str, Any]] = {}
                for mode in ("quick", "full"):
                    n = reps if change != "callee" else 1
                    cold, warm = [], []
                    for i in range(n):
                        st = work / f"st_{scale}_{change}_{mode}_cold_{i}"
                        rec = run_child(root, base, mode, st, keep=(i == 0))
                        if i == 0:
                            kept[mode] = rec.pop("_compact")
                        cold.append(rec)
                        shutil.rmtree(st, ignore_errors=True)
                    if change != "callee":
                        wst = work / f"st_{scale}_{change}_{mode}_warm"
                        run_child(root, base, mode, wst)  # populate
                        for i in range(n):
                            rec = run_child(root, base, mode, wst, keep=(i == 0 and mode == "quick"))
                            if "_compact" in rec:
                                kept["quick_warm"] = rec.pop("_compact")
                            warm.append(rec)
                        shutil.rmtree(wst, ignore_errors=True)
                    for label, rs in (("cold", cold), ("warm", warm)):
                        if not rs:
                            continue
                        cond = {"functions": len(symbols), "change": change, "mode": mode, "cache": label, "summary": summarize(rs), "runs": rs}
                        doc["conditions"].append(cond)
                        s = cond["summary"]
                        print(f"{len(symbols):>6} {change:<6} {mode:<5} {label:<4} n={s['n']:<3} wall p50 {s['wall_ms']['p50']:>9.1f} p95 {s['wall_ms']['p95']:>9.1f} "
                              f"engine p50 {s['engine_ms']['p50']:>9.1f} rss p50 {s['peak_rss_mb']['p50']:>6.1f} affected {s['affected_symbols']} "
                              f"{s['outcomes']} introduced {s['introduced_findings']}", file=sys.stderr, flush=True)
                cons = {"functions": len(symbols), "change": change, "quick_cold_vs_full_cold": consistency(kept["quick"], kept["full"])}
                if "quick_warm" in kept:
                    cons["quick_warm_vs_full_cold"] = consistency(kept["quick_warm"], kept["full"])
                doc["consistency"].append(cons)
                print(f"   consistency {scale} {change}: {json.dumps({k: v for k, v in cons['quick_cold_vs_full_cold'].items() if k != 'examples'})}",
                      file=sys.stderr, flush=True)
                shutil.rmtree(root, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    doc["finished_at"] = now()
    out = write_result(args.out_name, doc)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
