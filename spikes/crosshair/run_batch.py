"""usage: run_batch.py <timeout_s> [workers]  -> results_T<timeout>.jsonl (one line per comparable changed function)"""
import json, sys, os, concurrent.futures as cf
from run_container import docker_run, HERE
T = float(sys.argv[1]); W = int(sys.argv[2]) if len(sys.argv) > 2 else 4
cases = [c for c in json.load(open(os.path.join(HERE, "cases.json"))) if "only_in" not in c]
outp = os.path.join(HERE, f"results_T{int(T)}{os.environ.get('IG_TAG', '')}.jsonl")
if os.environ.get("IG_REPO"): cases = [c for c in cases if c["repo"] == os.environ["IG_REPO"]]
done = set()
if os.path.exists(outp):
    done = {(r["repo"], r["short"], r["qualname"], r["module"]) for r in map(json.loads, open(outp))}
def one(c):
    d = os.path.join(HERE, "work", c["repo"], c["short"])
    r = docker_run(d, dict(case_dir="/case", syspath=c["syspath"], module=c["module"], qualname=c["qualname"], timeout=T), hard_timeout=2 * T + 90)
    r.update({k: c[k] for k in ("repo", "short", "subject", "file")})
    return r
todo = [c for c in cases if (c["repo"], c["short"], c["qualname"], c["module"]) not in done]
with cf.ThreadPoolExecutor(W) as ex, open(outp, "a") as f:
    for r in ex.map(one, todo):
        f.write(json.dumps(r) + "\n"); f.flush()
