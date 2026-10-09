"""Shared helpers: export snapshots, run pyright (bundled node dist, no network), map diagnostics to functions."""
import resource, ast, json, os, subprocess, tempfile, shutil, time, re, sys
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "corpus")
WORK = os.path.join(HERE, "work")
PYRIGHT_JS = os.path.join(HERE, ".venv/lib/python3.14/site-packages/pyright/dist/index.js")
PY = os.path.join(HERE, ".venv/bin/python")
NODE = shutil.which("node")
REPOS = {"more-itertools": "more_itertools", "boltons": "boltons", "dateutil": "src/dateutil"}

def sh(*a, cwd=None, inp=None):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, input=inp).stdout

def export(repo, sha, pkg, dest):
    """git archive of the package dir only; clone working tree untouched."""
    if os.path.exists(dest): shutil.rmtree(dest)
    os.makedirs(dest)
    tar = sh("git", "archive", sha, pkg, cwd=os.path.join(CORPUS, repo))
    subprocess.run(["tar", "-x", "-C", dest], input=tar, check=True)
    with open(os.path.join(dest, "pyrightconfig.json"), "w") as f:
        json.dump({"include": [pkg], "pythonVersion": "3.12", "pythonPlatform": "Linux"}, f)

def run_pyright(root, mode="standard"):
    cmd = ["/usr/bin/time", "-p", NODE, PYRIGHT_JS, "--outputjson", "--pythonpath", PY, "-p", root]
    if mode: cmd += []
    t = time.time(); r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    p = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                       env={**os.environ, "PYRIGHT_PYTHON_IGNORE_WARNINGS": "1"})
    dt = time.time() - t; r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    m = re.search(r'user ([\d.]+)\nsys ([\d.]+)', p.stderr); cpu = float(m[1]) + float(m[2]) if m else None
    out = json.loads(p.stdout)
    diags = []
    for d in out["generalDiagnostics"]:
        r = d["range"]["start"]
        diags.append(dict(file=os.path.relpath(d["file"], root), line=r["line"] + 1, sev=d["severity"],
                          rule=d.get("rule", "<none>"), msg=d["message"]))
    return diags, dt, out["summary"], cpu

def func_table(path):
    """[(qualname, start, end, simple_name)] for every def; plus call-name sets per def."""
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except Exception:
        return []
    res = []
    def walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + ch.name
                res.append(dict(q=q, s=ch.lineno, e=ch.end_lineno, name=ch.name, node=ch))
                walk(ch, q + ".<locals>.")
            elif isinstance(ch, ast.ClassDef):
                walk(ch, prefix + ch.name + ".")
            else:
                walk(ch, prefix)
    walk(tree, "")
    return res

def enclosing(table, line):
    best = None
    for f in table:
        if f["s"] <= line <= f["e"] and (best is None or f["s"] >= best["s"]):
            best = f
    return best["q"] if best else "<module>"

def called_names(fnode):
    names = set()
    for n in ast.walk(fnode):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name): names.add(n.func.id)
            elif isinstance(n.func, ast.Attribute): names.add(n.func.attr)
    return names
