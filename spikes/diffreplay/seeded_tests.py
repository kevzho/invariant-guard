"""Would the repository's own tests catch each seeded mutant? (same container boundary, pytest)

usage: python seeded_tests.py -> results/seeded_tests.jsonl
"""
import ast
import json
import time
from pathlib import Path

import drrunner
from changes import inventory, pkg_info, repo, snapshot

HERE = Path(__file__).parent
TESTS = {  # mutant module -> test files at that commit
    "more_itertools/more.py": ["tests/test_more.py"], "more_itertools/recipes.py": ["tests/test_recipes.py"],
    "boltons/iterutils.py": ["tests/test_iterutils.py"], "boltons/mathutils.py": ["tests/test_mathutils.py"],
    "boltons/urlutils.py": ["tests/test_urlutils.py"], "boltons/strutils.py": ["tests/test_strutils.py"],
    "boltons/namedutils.py": ["tests/test_namedutils.py"], "boltons/dictutils.py": ["tests/test_dictutils.py"],
    "dateutil/parser/isoparser.py": ["dateutil/test/test_isoparser.py"],
}
out = HERE / "results" / "seeded_tests.jsonl"
done = {json.loads(l)["id"] for l in out.read_text().splitlines()} if out.exists() else set()
for m in json.loads((HERE / "mutants.json").read_text()):
    if m["id"] in done:
        continue
    r = repo(m["repo"])
    commit = r.resolve_commit(m["commit"])
    files = {f.path: f.raw for f in snapshot(m["repo"], commit).files}
    src = files[m["path"]].decode()
    seg = ast.get_source_segment(src, inventory(files[m["path"]])[m["qualname"]].node)
    mutated = dict(files)
    mutated[m["path"]] = src.replace(seg, seg.replace(m["old"], m["new"]), 1).encode()
    _, pyroot = pkg_info(m["repo"], files)
    res = {}
    t0 = time.time()
    for side, fs in (("base", files), ("mutant", mutated)):
        ws = drrunner.workspace(fs, "pt")
        try:
            run = drrunner.run(ws, {"mode": "pytest", "python_path_root": pyroot, "tests": TESTS[m["path"]]}, harness="dr_pytest.py")
            res[side] = run.response if run.response else {"error": run.error}
        finally:
            ws.close()
    rec = {"id": m["id"], "category": m["category"], "base": res["base"], "mutant": res["mutant"],
           "caught_by_tests": bool(res["mutant"].get("counts", {}).get("failed") or res["mutant"].get("counts", {}).get("error"))
           and not res["base"].get("counts", {}).get("failed"), "wall_s": round(time.time() - t0, 1)}
    with out.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(m["id"], "base", res["base"].get("counts"), "mutant", res["mutant"].get("counts"), "caught" if rec["caught_by_tests"] else "NOT caught", flush=True)
