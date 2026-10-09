"""Step 3: seeded regressions. base = the corpus commit, patch = the same commit with one mutant applied.

usage: python seeded.py  -> results/seeded.jsonl
"""
import ast
import json
import time
from pathlib import Path

from changes import inventory, repo
from replay import DIFF_CLASSES, replay_commit

HERE = Path(__file__).parent
out = HERE / "results" / "seeded.jsonl"
done = {json.loads(l)["id"] for l in out.read_text().splitlines()} if out.exists() else set()
for m in json.loads((HERE / "mutants.json").read_text()):
    if m["id"] in done:
        continue
    r = repo(m["repo"])
    commit = r.resolve_commit(m["commit"])
    raw = r._run(["show", f"{commit}:{m['path']}"], cwd=r.toplevel)
    src = raw.decode()
    fd = inventory(raw)[m["qualname"]]
    seg = ast.get_source_segment(src, fd.node)
    assert seg.count(m["old"]) == 1, (m["id"], seg.count(m["old"]))
    mutated = src.replace(seg, seg.replace(m["old"], m["new"]), 1)
    ast.parse(mutated)
    t0 = time.time()
    rec = replay_commit(m["repo"], commit, None, patch_override={m["path"]: mutated.encode()})
    wall = time.time() - t0
    sym = f"{m['path']}::{m['qualname']}"
    fns = [f for f in rec["functions"] if f["symbol"] == sym]
    diffs = sum(f["differences"] for f in fns)
    by_rel = {f["relation"]: f["differences"] for f in fns}
    srcs = {}
    for f in fns:
        for s, cl in f.get("classes_by_source", {}).items():
            srcs[s] = srcs.get(s, 0) + sum(v for k, v in cl.items() if k in DIFF_CLASSES)
    res = {**m, "commit": commit, "detected": diffs > 0, "differences": diffs, "inputs": sum(f["inputs_total"] for f in fns),
           "executable": sum(f["executable"] for f in fns), "nondeterministic": sum(f["nondeterministic"] for f in fns),
           "diffs_by_relation": by_rel, "diffs_by_source": srcs, "wall_s": round(wall, 1), "t_replay_ms": rec.get("t_replay_ms"),
           "examples": [ex for f in fns for k, exs in f["examples"].items() if k in DIFF_CLASSES for ex in exs[:2]][:4],
           "other_changed": [f["symbol"] for f in rec["functions"] if f["symbol"] != sym], "skipped": rec.get("skipped")}
    with out.open("a") as fh:
        fh.write(json.dumps(res) + "\n")
    print(m["id"], m["category"], "detected" if res["detected"] else "MISSED", diffs, "of", res["inputs"], f"{wall:.0f}s", flush=True)
