"""Patch-scoped pyright measurement. usage: measure.py REPO [N]"""
import sys, os, json, re, subprocess
from collections import Counter
import common
from common import *
from concurrent.futures import ThreadPoolExecutor

def commits(repo, pkg, n):
    pkgs = [pkg] + (['dateutil'] if repo=='dateutil' else [])
    out = sh("git", "log", "--no-merges", "--format=%H", "-n", "400", "--", *[p + "/*.py" for p in pkgs], cwd=os.path.join(CORPUS, repo)).decode().split()
    res = []
    for sha in out:
        files = sh("git", "diff-tree", "--no-commit-id", "--name-only", "-r", "--diff-filter=AMD", sha, "--", *pkgs, cwd=os.path.join(CORPUS, repo)).decode().split()
        files = [f for f in files if f.endswith(".py") and "/tests/" not in f and "/test/" not in f and "/test_" not in f and not os.path.basename(f).startswith("test_")]
        if not files: continue
        parent = subprocess.run(["git", "rev-parse", sha + "^"], cwd=os.path.join(CORPUS, repo), capture_output=True, text=True)
        if parent.returncode: continue
        par = parent.stdout.strip()
        lay = [p for p in pkgs if all(subprocess.run(["git","cat-file","-e",f"{r}:{p}"],cwd=os.path.join(CORPUS,repo),capture_output=True).returncode==0 for r in (par, sha))]
        if not lay: continue
        files = [f for f in files if f.startswith(lay[0] + "/")]
        if not files: continue
        res.append((sha, par, files, lay[0]))
        if len(res) == n: break
    return res

def hunks(repo, parent, sha, f):
    d = sh("git", "diff", "-U0", parent, sha, "--", f, cwd=os.path.join(CORPUS, repo)).decode()
    old, new = [], []
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", d, re.M):
        a, al, b, bl = int(m[1]), int(m[2] or 1) if m[2] != "0" else 0, int(m[3]), int(m[4] or 1) if m[4] != "0" else 0
        old += list(range(a, a + max(al, 1))); new += list(range(b, b + max(bl, 1)))
    return old, new

def read_table(root, f):
    p = os.path.join(root, f)
    return func_table(p) if os.path.exists(p) else []

def scope(root, pkgfiles_tables, changed):  # changed: {(file, q)}
    names = {q.split(".")[-1] for _, q in changed}
    callers = set()
    for f, tab in pkgfiles_tables.items():
        for fn in tab:
            if (f, fn["q"]) in changed: continue
            if called_names(fn["node"]) & names: callers.add((f, fn["q"]))
    return callers

def key(d, tab):
    return (d["file"], enclosing(tab.get(d["file"], []), d["line"]), d["rule"], re.sub(r"\d+", "N", d["msg"]))

def main(repo, n):
    pkg = REPOS[repo]
    results = []; skipped = []
    for i, (sha, parent, files, pkg) in enumerate(commits(repo, pkg, n)):
        base, patch = f"{WORK}/{repo}/{sha[:10]}/base", f"{WORK}/{repo}/{sha[:10]}/patch"
        try:
            export(repo, parent, pkg, base); export(repo, sha, pkg, patch)
        except Exception as e:
            print("SKIP", sha[:8], "export failed (package dir absent in parent/commit)", flush=True); skipped.append(sha); continue
        btab = {}; ptab = {}
        for root, tab in ((base, btab), (patch, ptab)):
            for dirpath, _, fs in os.walk(root):
                for x in fs:
                    if x.endswith(".py"): tab[os.path.relpath(os.path.join(dirpath, x), root)] = func_table(os.path.join(dirpath, x))
        changed_new, changed_old = set(), set()
        for f in files:
            old, new = hunks(repo, parent, sha, f)
            for l in new:
                changed_new.add((f, enclosing(ptab.get(f, []), l)))
            for l in old:
                changed_old.add((f, enclosing(btab.get(f, []), l)))
        callers = scope(patch, ptab, {c for c in changed_new if c[1] != "<module>"})
        callers |= scope(base, btab, {c for c in changed_old if c[1] != "<module>"})
        scope_files = sorted(set(files) | {f for f, _ in callers})
        for root in (base, patch):
            cfg = json.load(open(root + "/pyrightconfig.json")); cfg["include"] = [f for f in scope_files if os.path.exists(os.path.join(root, f))]
            json.dump(cfg, open(root + "/pyrightconfig.json", "w"))
        with ThreadPoolExecutor(2) as ex:
            fb = ex.submit(run_pyright, base); fp = ex.submit(run_pyright, patch)
            (bd, bt, _, bc), (pd, pt, _, pc) = fb.result(), fp.result()
        pt2 = None
        if i < 3: _, pt2, _, _ = run_pyright(patch)
        sc = changed_new | changed_old | callers
        bk = Counter(); pk = Counter(); bex = {}; pex = {}
        for d in bd:
            k = key(d, btab); bk[k] += 1; bex[k] = d
        for d in pd:
            k = key(d, ptab); pk[k] += 1; pex[k] = d
        def inscope(k): return (k[0], k[1]) in sc
        intro = pk - bk; res = bk - pk; pre = pk & bk
        rec = dict(sha=sha, parent=parent, files=files, n_changed_fn=len(changed_new), n_callers=len(callers), n_scope_files=len(scope_files),
                   cpu_base=bc, cpu_patch=pc, load=os.getloadavg()[0], t_base=bt, t_patch=pt, t_patch_repeat=pt2, base_total=len(bd), patch_total=len(pd),
                   intro_all=sum(intro.values()), res_all=sum(res.values()),
                   intro_scope=[dict(**pex[k], q=k[1], role=("changed" if (k[0],k[1]) in changed_new else "caller"), count=c) for k, c in intro.items() if inscope(k)],
                   res_scope=sum(c for k, c in res.items() if inscope(k)),
                   pre_scope=sum(c for k, c in pre.items() if inscope(k)),
                   intro_out_of_scope=sum(c for k, c in intro.items() if not inscope(k)),
                   intro_out_rules=Counter(k[2] for k, c in intro.items() if not inscope(k) for _ in range(c)))
        results.append(rec)
        print(repo, i, sha[:8], "intro_scope", len(rec["intro_scope"]), "intro_all", rec["intro_all"], f"{bt:.1f}s {pt:.1f}s {pt2}", flush=True)
    json.dump(dict(results=results, skipped=skipped), open(f"{HERE}/results_{repo}.json", "w"), indent=1)

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 30)
