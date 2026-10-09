"""Run every paper benchmark and write benchmarks/results/paper_*.json plus a hardware/software manifest.

    .venv/bin/python benchmarks/run_all.py                       # everything (about 3.5-4 hours on the reference host)
    .venv/bin/python benchmarks/run_all.py --parts semantic,seeded
    .venv/bin/python benchmarks/run_all.py --parts latency --latency-reps 30 --latency-reps-large 5

Parts (each writes one results file; nothing here asserts a target):

* ``semantic``  -> paper_semantic_fixtures.json  (bench_semantic.py: 31 controlled fixtures, solver on/off, ground truth)
* ``seeded``    -> paper_seeded_patches.json     (bench_seeded.py: 35 labeled baseline/patch pairs; solver, incremental and
                                                   static/dynamic ablations; needs Docker and the prepared runner image)
* ``model``     -> paper_model_eval.json         (model_eval.py: templates vs local-model candidates; needs Ollama with the
                                                   pinned qwen2.5-coder:7b and Docker)
* ``latency``   -> paper_latency.json            (bench_latency.py: ~100/1,000/10,000 functions, cold/warm, quick/full)
* ``tests``     -> paper_test_suite.json         (the full pytest suite, `.venv/bin/python -m pytest -q`, summary line and duration)
* ``agent``     -> paper_agent_eval_run{1,2,3}.json (benchmarks/agent_eval/score.py on the three recorded agent-proposer
                                                   runs in benchmarks/agent_eval/proposals/run*/; needs Docker; never contacts a model)
* ``latency_repeat`` -> paper_latency_repeat.json (the latency part again, last, with identical settings: the latency
                                                   numbers are sensitive to host load, so they are measured twice; the paper
                                                   reports the repeat as primary and the first run alongside, DECISIONS I-111)
* ``transport`` -> paper_transport_openai_compat.json (model_eval.py --provider openai_compatible against the SAME local
                                                   Ollama server's loopback /v1 endpoint, 1 repeat: a transport check of the
                                                   I-105 adapter, not a separate quality arm; needs Ollama and Docker)

The manifest (paper_manifest.json) records the host, software versions, lock digest, source digest, the command line,
and for every part its start/finish time, exit status and the sha256 of its results file. Running a subset updates
only those parts' entries. Parts run sequentially so that timings do not overlap.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_common import PY, REPO, RESULTS, host_manifest, now, sha256_file  # noqa: E402

# Default order: cheap and deterministic parts first, then the timing-sensitive latency part (nothing else runs
# meanwhile), then the long model-bound parts.
PARTS = ("tests", "semantic", "seeded", "agent", "latency", "model", "transport", "latency_repeat")
FILES = {"semantic": "paper_semantic_fixtures.json", "seeded": "paper_seeded_patches.json", "model": "paper_model_eval.json",
         "latency": "paper_latency.json", "tests": "paper_test_suite.json", "transport": "paper_transport_openai_compat.json",
         "latency_repeat": "paper_latency_repeat.json"}
AGENT_RUNS = ("run1", "run2", "run3")
AGENT_CLIENT = "claude-code-subagent/claude-sonnet-5-5"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parts", default=",".join(PARTS))
    ap.add_argument("--latency-reps", type=int, default=30)
    ap.add_argument("--latency-reps-large", type=int, default=5)
    ap.add_argument("--latency-scales", default="100,1000,10000")
    ap.add_argument("--model-repeats", type=int, default=3)
    args = ap.parse_args()
    parts = [p for p in args.parts.split(",") if p]
    bad = [p for p in parts if p not in PARTS]
    if bad:
        raise SystemExit(f"unknown parts: {bad}")
    mpath = RESULTS / "paper_manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {"schema_version": "1.0", "parts": {}}
    manifest["last_invocation"] = {"command": " ".join([".venv/bin/python", "benchmarks/run_all.py", *sys.argv[1:]]), "started_at": now(),
                                   "host": host_manifest()}
    RESULTS.mkdir(parents=True, exist_ok=True)
    for part in parts:
        t0 = time.perf_counter()
        started = now()
        if part == "semantic":
            cmd = [PY, str(REPO / "benchmarks" / "bench_semantic.py")]
        elif part == "seeded":
            cmd = [PY, str(REPO / "benchmarks" / "bench_seeded.py")]
        elif part in ("latency", "latency_repeat"):
            cmd = [PY, str(REPO / "benchmarks" / "bench_latency.py"), "--reps", str(args.latency_reps), "--reps-large", str(args.latency_reps_large),
                   "--scales", args.latency_scales, "--out-name", FILES[part][:-5]]
        elif part == "tests":
            cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
            t1 = time.perf_counter()
            p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
            lines = [l for l in p.stdout.splitlines() if l.strip()]
            summary = lines[-1] if lines else ""
            doc = {"schema_version": "1.0", "command": ".venv/bin/python -m pytest -q -p no:cacheprovider", "exit": p.returncode,
                   "summary_line": summary.strip("= "), "short_test_summary": [l for l in lines if l.startswith(("SKIPPED", "XFAIL", "XPASS", "FAILED", "ERROR"))],
                   "elapsed_s": round(time.perf_counter() - t1, 1), "finished_at": now()}
            (RESULTS / FILES["tests"]).write_text(json.dumps(doc, indent=1) + "\n")
            out = RESULTS / FILES[part]
            manifest["parts"][part] = {"command": doc["command"], "started_at": started, "finished_at": now(), "elapsed_s": doc["elapsed_s"],
                                       "exit": p.returncode, "results_file": str(out.relative_to(REPO)), "results_sha256": sha256_file(out)}
            mpath.write_text(json.dumps(manifest, indent=1) + "\n")
            print(f"tests: {doc['summary_line']}", file=sys.stderr)
            if p.returncode != 0:
                return p.returncode
            continue
        elif part == "agent":
            entries = []
            code = 0
            for run in AGENT_RUNS:
                out = RESULTS / f"paper_agent_eval_{run}.json"
                cmd = [PY, str(REPO / "benchmarks" / "agent_eval" / "score.py"), "--proposals", str(REPO / "benchmarks" / "agent_eval" / "proposals" / run),
                       "--client", AGENT_CLIENT, "--label", f"agent-proposed {run}: Claude Sonnet 5.5 subagent, packets + README only (blinded), cap 8/batch",
                       "--out", str(out)]
                print(f"== agent {run}: {' '.join(cmd)}", file=sys.stderr, flush=True)
                t1 = time.perf_counter()
                rc = subprocess.run(cmd, cwd=REPO).returncode
                entries.append({"run": run, "command": " ".join(c.replace(str(REPO) + "/", "") for c in cmd), "exit": rc,
                                "elapsed_s": round(time.perf_counter() - t1, 1), "results_file": str(out.relative_to(REPO)),
                                "results_sha256": sha256_file(out) if out.exists() else None})
                code = code or rc
            manifest["parts"][part] = {"started_at": started, "finished_at": now(), "elapsed_s": round(time.perf_counter() - t0, 1), "exit": code,
                                       "runs": entries}
            mpath.write_text(json.dumps(manifest, indent=1) + "\n")
            if code != 0:
                print(f"part {part} failed with exit {code}", file=sys.stderr)
                return code
            continue
        elif part == "transport":
            cmd = [PY, str(REPO / "benchmarks" / "model_eval.py"), "--provider", "openai_compatible", "--base-url", "http://127.0.0.1:11434/v1",
                   "--repeats", "1", "--template-repeats", "0", "--out", str(RESULTS / FILES["transport"])]
        else:
            cmd = [PY, str(REPO / "benchmarks" / "model_eval.py"), "--repeats", str(args.model_repeats), "--template-repeats", str(args.model_repeats),
                   "--out", str(RESULTS / FILES["model"])]
        print(f"== {part}: {' '.join(cmd)}", file=sys.stderr, flush=True)
        code = subprocess.run(cmd, cwd=REPO / "benchmarks").returncode
        out = RESULTS / FILES[part]
        manifest["parts"][part] = {"command": " ".join(c.replace(str(REPO) + "/", "") for c in cmd), "started_at": started, "finished_at": now(),
                                   "elapsed_s": round(time.perf_counter() - t0, 1), "exit": code, "results_file": str(out.relative_to(REPO)),
                                   "results_sha256": sha256_file(out) if out.exists() else None}
        mpath.write_text(json.dumps(manifest, indent=1) + "\n")
        if code != 0:
            print(f"part {part} failed with exit {code}", file=sys.stderr)
            return code
    manifest["last_invocation"]["finished_at"] = now()
    mpath.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"wrote {mpath}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
