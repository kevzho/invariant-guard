"""Aggregates results_*.jsonl, seeded_*.jsonl, labels.json into the numbers quoted in REPORT.md."""
import json, collections, statistics, os
H = os.path.dirname(os.path.abspath(__file__))
rd = lambda f: [json.loads(l) for l in open(os.path.join(H, f))]
def merged(T):
    r = [x for x in rd(f"results_T{T}.jsonl") if x["repo"] != "dateutil"] + rd(f"results_T{T}_six.jsonl")
    return r
for T in (10, 30):
    r = merged(T)
    print(f"\n== T={T}s per-function  n={len(r)}  (dateutil uses the +six image)")
    for repo in ("more-itertools", "boltons", "dateutil", None):
        rr = [x for x in r if repo is None or x["repo"] == repo]
        c = collections.Counter(x["status"] for x in rr)
        print(f"{repo or 'ALL':15s} n={len(rr):3d}", dict(c))
    cr = collections.Counter((x.get("detail") or "")[:75] for x in r if x["status"] in ("crashed", "unsupported", "hard_timeout", "container_error"))
    for k, v in cr.most_common(): print("   crash:", v, k)
    w = [x["host_wall_s"] for x in r]
    print(f"host wall per function: mean {statistics.mean(w):.1f}s median {statistics.median(w):.1f}s max {max(w):.1f}s; total CPU-serial {sum(w)/60:.1f} min")
    ok = [x for x in r if x["status"] in ("no_diff_budget", "no_diff_exhausted", "diff_found")]
    print(f"ran-to-completion (supported) = {len(ok)}/{len(r)};  of these exhausted-claim={sum(x['status']=='no_diff_exhausted' for x in ok)}")
    f = [x for x in r if x["status"] == "diff_found"]
    rep = [x for x in f if any(d["replay"].get("differs") for d in x["diffs"])]
    unr = [x for x in f if all("unreplayable" in d["replay"] for d in x["diffs"])]
    print(f"diff_found={len(f)}  replay-confirmed={len(rep)}  unreplayable={len(unr)}  replay-refuted={len(f)-len(rep)-len(unr)}")
L = json.load(open(os.path.join(H, "labels.json")))
u = {}
for T in (10, 30):
    for x in merged(T):
        if x["status"] == "diff_found": u.setdefault((x["short"], x["qualname"]), []).append(T)
print("\n== labeled diff_found functions (union of T10/T30):", len(u))
lab = collections.Counter()
for (s, q), Ts in sorted(u.items()):
    k = f"{s}|{q}"; lab[L[k][0]] += 1
print(dict(lab)); assert all(f"{s}|{q}" in L for s, q in u)
n = len(u); print("noise (non-INTENDED, non-UNINTENDED) share:", sum(v for k, v in lab.items() if k.startswith("ARTIFACT")), "/", n)
for T in (10, 30):
    s = rd(f"seeded_T{T}.jsonl"); c = collections.Counter(x["status"] for x in s)
    conf = [x["id"] for x in s if x["status"] == "diff_found" and any(d["replay"].get("differs") for d in x["diffs"])]
    print(f"\n== seeded T={T}: {dict(c)}; found={c['diff_found']}/20; replay-confirmed={len(conf)}/20; false 'exhausted' claims={c['no_diff_exhausted']}/20")
    print("   mean host wall", round(statistics.mean(x['host_wall_s'] for x in s), 1), "s; mean in-driver wall", round(statistics.mean(x['wall_s'] for x in s), 1))
