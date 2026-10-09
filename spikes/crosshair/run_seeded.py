"""usage: run_seeded.py <timeout_s> [workers] -> seeded_T<timeout>.jsonl"""
import json, sys, os, concurrent.futures as cf
from run_container import docker_run, HERE
T = float(sys.argv[1]); W = int(sys.argv[2]) if len(sys.argv) > 2 else 4
cases = json.load(open(os.path.join(HERE, "seeded_cases.json")))
def one(c):
    r = docker_run(os.path.join(HERE, "work_seeded", c["id"]), dict(case_dir="/case", syspath=c["syspath"], module=c["module"], qualname=c["qualname"], timeout=T), hard_timeout=2 * T + 90)
    r.update({k: c[k] for k in ("id", "kind", "repo")}); return r
with cf.ThreadPoolExecutor(W) as ex, open(os.path.join(HERE, f"seeded_T{int(T)}.jsonl"), "w") as f:
    for r in ex.map(one, cases):
        f.write(json.dumps(r) + "\n"); f.flush()
