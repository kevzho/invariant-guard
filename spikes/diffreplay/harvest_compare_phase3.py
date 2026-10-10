"""Phase 3: product harvester (invariantguard.replay.harvest) vs. the spike's harvested (non-generated) inputs, per changed function."""
import json, sys, time
from changes import snapshot
from replay import _test_paths
from invariantguard.replay.harvest import harvest, default_variants
from invariantguard.replay.run import Target

HARVESTED = ("test", "test_new", "doctest", "doctest_new", "defaults")
rows = []
t0 = time.time()
for repo in ("more-itertools", "boltons", "dateutil"):
    for line in open(f"results/replay_{repo}.jsonl"):
        rec = json.loads(line)
        fns = [f for f in rec["functions"] if f["relation"] == "direct"]
        if not fns:
            continue
        files = {f.path: f.raw for f in snapshot(repo, rec["commit"]).files}
        tests = _test_paths(files)
        for f in fns:
            path, q = f["symbol"].split("::", 1)
            t = Target(path, q, rec["pyroot"])
            try:
                h = harvest(t, files, tests, package=rec["pkg"].split("/")[-1])
                d = default_variants(files, t, h.inputs)
                ours = len(h.inputs) + len(d)
                stats = h.stats
            except Exception as e:  # noqa: BLE001 - recorded
                ours, stats = -1, {"error": repr(e)[:200]}
            spike = sum(v for k, v in f.get("inputs_by_source", {}).items() if k in HARVESTED)
            rows.append({"repo": repo, "commit": rec["commit"][:8], "symbol": f["symbol"], "spike": spike, "ours": ours, "stats": stats})
json.dump(rows, open("results/harvest_compare_phase3.json", "w"), indent=1)
both = lambda pred: sum(1 for r in rows if pred(r))  # noqa: E731
print(f"functions {len(rows)} | spike>0 {both(lambda r: r['spike'] > 0)} | ours>0 {both(lambda r: r['ours'] > 0)} | "
      f"spike only {both(lambda r: r['spike'] > 0 and r['ours'] <= 0)} | ours only {both(lambda r: r['ours'] > 0 and r['spike'] == 0)} | "
      f"errors {both(lambda r: r['ours'] < 0)} | inputs spike {sum(r['spike'] for r in rows)} ours {sum(max(0, r['ours']) for r in rows)} | {time.time()-t0:.0f}s")
for r in rows:
    if r["spike"] > 0 and r["ours"] <= 0:
        print("  spike only:", r["repo"], r["commit"], r["symbol"], r["spike"], r["stats"])
