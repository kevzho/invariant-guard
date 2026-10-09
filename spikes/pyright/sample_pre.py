"""Deterministic sample (seed 0) of PRE-EXISTING diagnostics inside changed functions: what a non-differential run would show."""
import json, random
from common import *
from show import changed
rnd = random.Random(0); pool = []
for repo in REPOS:
    for x in json.load(open(f"results_{repo}.json"))["results"]:
        if not x["pre_scope"]: continue
        ch = {(f, q) for f, qs in changed(repo, x["sha"], x["files"]) for q in qs}
        root = f"{WORK}/{repo}/{x['sha'][:10]}/patch"
        diags, _, _, _ = run_pyright(root)
        tabs = {}
        for d in diags:
            tab = tabs.setdefault(d["file"], func_table(os.path.join(root, d["file"])))
            if (d["file"], enclosing(tab, d["line"])) in ch:
                src = open(os.path.join(root, d["file"])).read().split("\n")[d["line"]-1].strip()
                pool.append(dict(repo=repo, sha=x["sha"][:10], **d, src=src[:140]))
print("pool", len(pool))
for i, d in enumerate(rnd.sample(pool, 12)):
    print(f"P{i+1} {d['repo']} {d['sha']} {d['file']}:{d['line']} {d['rule']}\n   MSG: {d['msg'][:260]}\n   SRC: {d['src']}")
import collections; json.dump(pool, open("pre_existing_pool.json", "w"))
print(collections.Counter(d["rule"] for d in pool).most_common())
