"""Step 2: replay the 30 most recent package-modifying commits of one repository.

usage: python run_corpus.py REPO [N]   -> results/replay_REPO.jsonl (resumable)
"""
import json
import sys
import time
import traceback
from pathlib import Path

from changes import recent_commits
from replay import replay_commit

name = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
out = Path(__file__).parent / "results" / f"replay_{name}.jsonl"
done = set()
if out.exists():
    done = {json.loads(l)["commit"] for l in out.read_text().splitlines() if l.strip()}
for c in recent_commits(name, n):
    if c["commit"] in done:
        continue
    t0 = time.time()
    try:
        rec = replay_commit(name, c["commit"], c["parent"])
    except Exception as exc:  # noqa: BLE001 - recorded as an engine error for this commit
        rec = {"repo": name, "commit": c["commit"], "parent": c["parent"], "error": f"{type(exc).__name__}: {exc}", "tb": traceback.format_exc()[-2000:]}
    rec["subject"] = c["subject"]
    rec["date"] = c["date"]
    rec["wall_s"] = round(time.time() - t0, 1)
    with out.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    fns = rec.get("functions", [])
    print(f"{rec['wall_s']:7.1f}s {name} {c['commit'][:8]} {c['subject'][:50]!r} fns={len(fns)} probes={rec.get('probes')} "
          f"diffs={sum(f['differences'] for f in fns)} nondet={sum(f['nondeterministic'] for f in fns)} err={rec.get('error', '')[:80]}", flush=True)
