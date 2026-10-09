import sys, ast
from common import *
repo, sha, f, q = sys.argv[1:5]
p = f"{WORK}/{repo}/{sha}/patch/{f}"
src = open(p).read(); lines = src.splitlines()
for fn in func_table(p):
    if fn["q"] == q:
        n = fn["node"]; print(f"--- {q} L{n.lineno}-{n.end_lineno}")
        body_start = n.body[1].lineno if (isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0],'value',None), ast.Constant) and len(n.body)>1) else n.body[0].lineno
        print("\n".join(f"{i+1}: {lines[i]}" for i in range(n.lineno-1, n.lineno+1)), "..." )
        print("\n".join(f"{i+1}: {lines[i]}" for i in range(body_start-1, n.end_lineno)))
