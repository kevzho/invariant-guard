#!/usr/bin/env python3
"""HOST-SIDE, STATIC ONLY: select commits, `git archive` base/patch package trees, map changed functions via ast.
Never imports or executes corpus code. Output: work/<repo>/<sha8>/{base,patch}/ and cases.json."""
import ast, json, os, subprocess, sys, io, tarfile, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "..", "corpus")
WORK = os.path.join(HERE, "work")
REPOS = {"more-itertools": "more_itertools", "boltons": "boltons", "dateutil": "src/dateutil|dateutil"}
NCOMMITS = 30

def git(repo, *a, text=True):
    r = subprocess.run(["git", "-C", os.path.join(CORPUS, repo), *a], capture_output=True, check=True)
    return r.stdout.decode("utf-8", "replace") if text else r.stdout

def funcs(src):
    """qualname -> (lineno, end_lineno, normalized dump, arg signature) for top-level funcs and class methods."""
    out = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    def visit(body, prefix):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + n.name
                dump = ast.dump(ast.Module(body=n.body, type_ignores=[]), include_attributes=False)
                out[q] = dict(line=n.lineno, end=n.end_lineno, body=dump, args=ast.dump(n.args), is_async=isinstance(n, ast.AsyncFunctionDef),
                              decos=[ast.unparse(d) for d in n.decorator_list], nested=False)
            elif isinstance(n, ast.ClassDef):
                visit(n.body, prefix + n.name + ".")
    visit(tree.body, "")
    return out

def main():
    os.makedirs(WORK, exist_ok=True)
    allcases = []
    for repo, pkgs in REPOS.items():
        pkgl = pkgs.split("|")
        spec = []
        for pk in pkgl: spec += [f"{pk}/*.py", f":(exclude){pk}/test*", f":(exclude){pk}/**/test*"]
        shas = git(repo, "log", "--no-merges", "--format=%H", "--", *spec).split()
        cr = lambda rev, pk: subprocess.run(["git","-C",os.path.join(CORPUS,repo),"cat-file","-e",f"{rev}:{pk}"],capture_output=True).returncode==0
        sel = []
        for s_ in shas:
            pk = next((p_ for p_ in pkgl if cr(s_+"^", p_) and cr(s_, p_)), None)
            if pk: sel.append((s_, pk))
        for sha, pkg in sel[:NCOMMITS]:
            subj = git(repo, "log", "-1", "--format=%s", sha).strip()
            files = [f for f in git(repo, "diff", "--name-only", sha + "^", sha, "--", pkg).split() if f.endswith(".py") and "/test" not in f and not os.path.basename(f).startswith("test")]
            d = os.path.join(WORK, repo, sha[:8])
            shutil.rmtree(d, ignore_errors=True)
            for side, rev in (("base", sha + "^"), ("patch", sha)):
                os.makedirs(os.path.join(d, side))
                data = git(repo, "archive", rev, pkg, text=False)
                with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                    tf.extractall(os.path.join(d, side), filter="data")
            root = pkg.split("/")[0] if pkg.startswith("src/") is False else "src"
            syspath = "src" if pkg.startswith("src/") else "."
            modpkg = os.path.basename(pkg)
            for f in files:
                try:
                    b = git(repo, "show", f"{sha}^:{f}")
                except subprocess.CalledProcessError:
                    b = None   # new file
                try:
                    p = git(repo, "show", f"{sha}:{f}")
                except subprocess.CalledProcessError:
                    p = None   # deleted file
                if b is None or p is None:
                    continue
                rel = os.path.relpath(f, syspath)[:-3].replace("/", ".")
                if rel.endswith(".__init__"): rel = rel[:-9]
                fb, fp = funcs(b), funcs(p)
                for q in sorted(set(fb) & set(fp)):
                    if fb[q]["body"] != fp[q]["body"] or fb[q]["args"] != fp[q]["args"]:
                        allcases.append(dict(repo=repo, sha=sha, short=sha[:8], subject=subj, file=f, module=rel, qualname=q, syspath=syspath,
                                             sig_changed=fb[q]["args"] != fp[q]["args"], is_async=fp[q]["is_async"], decos=fp[q]["decos"],
                                             base_line=fb[q]["line"], patch_line=fp[q]["line"]))
                # added/removed functions are not diffable
                for q in sorted(set(fb) ^ set(fp)):
                    allcases.append(dict(repo=repo, sha=sha, short=sha[:8], subject=subj, file=f, module=rel, qualname=q, syspath=syspath,
                                         only_in="base" if q in fb else "patch"))
    json.dump(allcases, open(os.path.join(HERE, "cases.json"), "w"), indent=1)
    comp = [c for c in allcases if "only_in" not in c]
    print("commits per repo:", {r: len({c['sha'] for c in allcases if c['repo']==r}) for r in REPOS})
    print("changed (comparable) functions:", len(comp), "added/removed:", len(allcases) - len(comp))
main()
