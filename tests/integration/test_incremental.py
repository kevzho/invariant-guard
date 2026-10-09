"""T28: incremental (quick slice + cache) versus full analysis on fixtures (docs/07, docs/12).

For every scenario:

(a) every obligation verified in quick mode has the same result, scope, witness and patch
    relation as in a cold full-mode run;
(b) every candidate obligation *outside* the quick slice has the same result as an
    independent single-snapshot `guard check` of the base tree (so the slice did not
    miss a dependency);
(c) a warm quick run (cache) reproduces the cold quick run exactly.

Scenarios are the docs/07 "required change cases" that apply to a static M2 engine, plus
seeded random multi-module projects with random mutations.

Solver limits: these tests use Z3's deterministic resource limit (``per_query_rlimit``)
with a generous wall-clock timeout. With the default 100 ms wall-clock timeout a query that
takes ~100 ms is UNSAT in one run and TIMEOUT (UNKNOWN) in another, which is honest but
makes run-to-run comparison depend on machine load rather than on the slice.
"""


from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import pytest

from ..conftest import run_cli
from .gitrepo import config, git, make_repo, verify, write

DETERMINISTIC = "per_query_timeout_ms = 20000\nper_query_rlimit = 10000000"


def _key(o: dict) -> tuple:
    return (o["property_result"], o["scope_validity"], json.dumps(o["witness"], sort_keys=True), o.get("patch_relation"))


def _check_tree(tmp_path: Path, files: dict[str, str], cfg: str, name: str) -> dict[str, dict]:
    root = tmp_path / name
    root.mkdir()
    (root / ".git").mkdir()
    write(root, {**files, ".guard/config.toml": cfg})
    code, out, err = run_cli(["check", str(root), "--format", "json"])
    assert out.strip(), err
    return {o["obligation_id"]: o for o in json.loads(out)["obligation_results"]}


def assert_consistent(tmp_path: Path, base_files: dict[str, str], cfg: str, mutate) -> dict:
    root, base = make_repo(tmp_path, base_files, cfg)
    mutate(root)
    cold_q = verify(root, base, "--state-dir", str(tmp_path / "s_quick"))[1]
    warm_q = verify(root, base, "--state-dir", str(tmp_path / "s_quick"))[1]
    full = verify(root, base, "--mode", "full", "--no-cache")[1]
    for r in (cold_q, warm_q, full):
        assert r["errors"] == [], r["errors"]
        assert r["patch"]["slice"]["widened_by_key_check"] == [], "graph walk missed a dependency (caught by the key cross-check)"
    fo = {o["obligation_id"]: o for o in full["obligation_results"]}
    qo = {o["obligation_id"]: o for o in cold_q["obligation_results"]}
    # (a) quick == full on the slice
    for oid, o in qo.items():
        assert _key(o) == _key(fo[oid]), (oid, o, fo[oid])
    # (c) warm == cold
    assert {k: _key(v) for k, v in qo.items()} == {o["obligation_id"]: _key(o) for o in warm_q["obligation_results"]}
    assert cold_q["verdict"] == warm_q["verdict"]
    # (b) outside the slice, results equal an independent check of the base tree
    base_results = _check_tree(tmp_path, base_files, cfg, "base_tree")
    outside = [oid for oid in fo if oid not in qo]
    for oid in outside:
        assert oid in base_results, f"{oid} is outside the slice but has no base counterpart"
        b = base_results[oid]
        assert (b["property_result"], b["scope_validity"], json.dumps(b["witness"], sort_keys=True)) == _key(fo[oid])[:3], (oid, b, fo[oid])
    return {"quick": cold_q, "full": full, "outside": len(outside)}


# -- docs/07 required change cases --------------------------------------------------------------------
LIB = {
    "util.py": "SCALE = 3\n\n\ndef denom(x):\n    return x + 1\n\n\ndef shift(x):\n    return x - SCALE\n",
    "app.py": ("from util import denom, shift\n\n\ndef ratio(x):\n    if x < 0:\n        return 0\n    return 100 // denom(x)\n\n\n"
               "def scaled(x):\n    d = shift(x)\n    if d != 0:\n        return 1 / d\n    return 0\n\n\ndef table(i):\n    xs = [1, 2, 3]\n    if 0 <= i < 3:\n        return xs[i]\n    return 0\n"),
    "leaf.py": "def lone(a, b):\n    if a > b:\n        return 1 % (a - b)\n    return 0\n",
    "rec.py": "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n\n\ndef use(n):\n    return 10 // fact(n)\n",
    "cb.py": "def apply(fn, x):\n    return fn(x)\n\n\ndef caller(x):\n    return 10 // (apply(abs, x) + 1)\n",
}
DOMAINS = config("util.py::denom", "util.py::shift", "app.py::ratio", "app.py::scaled", "app.py::table", "leaf.py::lone",
                 "rec.py::fact", "rec.py::use", "cb.py::caller", "new.py::fresh", "util2.py::denom", extra=DETERMINISTIC)

CASES = {
    "function-body": lambda r: write(r, {"leaf.py": LIB["leaf.py"].replace("a > b", "a >= b")}),
    "callee-body": lambda r: write(r, {"util.py": LIB["util.py"].replace("return x + 1", "return x")}),
    "signature": lambda r: write(r, {"leaf.py": LIB["leaf.py"].replace("def lone(a, b)", "def lone(a, b, c=0)")}),
    "shared-constant": lambda r: write(r, {"util.py": LIB["util.py"].replace("SCALE = 3", "SCALE = 0")}),
    "lock-file": lambda r: write(r, {"requirements.txt": "z3-solver==5.1.0.0\n"}),
    "module-initializer": lambda r: write(r, {"util.py": LIB["util.py"] + "\nTOTAL = 10 / SCALE\n"}),
    "new-file": lambda r: write(r, {"new.py": "from util import denom\n\n\ndef fresh(x):\n    return 7 // denom(x)\n"}),
    "removed-file": lambda r: write(r, {"leaf.py": None}),
    "removed-callee": lambda r: write(r, {"util.py": "SCALE = 3\n\n\ndef shift(x):\n    return x - SCALE\n"}),
    "renamed-file": lambda r: git(r, "mv", "leaf.py", "leaf2.py"),
    "renamed-module-of-callee": lambda r: (git(r, "mv", "util.py", "util2.py"), write(r, {"app.py": LIB["app.py"].replace("from util import", "from util2 import")})),
    "recursion": lambda r: write(r, {"rec.py": LIB["rec.py"].replace("n <= 1", "n <= 0")}),
    "unresolved-callback": lambda r: write(r, {"cb.py": LIB["cb.py"].replace("apply(abs, x) + 1", "apply(abs, x)")}),
    "comment-only": lambda r: write(r, {"app.py": "# comment\n" + LIB["app.py"]}),
    "rebind-builtin-len": lambda r: write(r, {"app.py": LIB["app.py"] + "\n\nlen = max\n"}),
}


@pytest.mark.parametrize("case", sorted(CASES), ids=sorted(CASES))
def test_required_change_case_incremental_equals_full(tmp_path, case):
    out = assert_consistent(tmp_path, LIB, DOMAINS, CASES[case])
    if case == "comment-only":
        assert out["quick"]["patch"]["slice"]["affected_symbols"] == 0  # layout-only change: nothing to re-verify
    if case == "callee-body":
        inv = {i["symbol_id"] for i in out["quick"]["patch"]["slice"]["invalidated"]}
        assert {"app.py::ratio", "util.py::denom"} <= inv
    if case == "lock-file":
        assert out["quick"]["patch"]["slice"]["global_change"]


# -- seeded random projects ---------------------------------------------------------------------------
# Extra sweeps: IG_INCREMENTAL_SEEDS=100,101,... (the default seeds run in the suite).
SEEDS = [int(s) for s in __import__("os").environ.get("IG_INCREMENTAL_SEEDS", "11,23,37,41,59,61").split(",") if s.strip()]


def _gen_project(rng: random.Random, n_mod: int = 4, n_fn: int = 5) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    symbols: list[str] = []
    for m in range(n_mod):
        lines = [f"C{m} = {rng.randint(-2, 3)}", ""]
        imports = []
        for j in range(n_fn):
            name = f"f{m}_{j}"
            choices = [f"x - C{m}", f"{rng.randint(-2, 2)} - x", f"x * {rng.randint(1, 3)} + C{m}"]
            if j > 0:
                callee = f"f{m}_{rng.randrange(j)}"
                choices += [f"{callee}(x) + {rng.randint(-1, 1)}", f"x - {callee}(x)"]
            if m > 0:
                om = rng.randrange(m)
                ext = f"f{om}_{rng.randrange(n_fn)}"
                imports.append(ext if ext not in imports else None)
                choices += [f"{ext}(x) - {rng.randint(0, 2)}"]
            expr = rng.choice(choices)
            body = [f"def {name}(x):", f"    d = {expr}"]
            if rng.random() < 0.5:
                body += ["    if d != 0:", "        return 100 // d", "    return 0"]
            else:
                body += ["    return 100 // d"]
            lines += body + ["", ""]
            symbols.append(f"mod{m}.py::{name}")
        imp = sorted({i for i in imports if i})
        head = [f"from mod{int(i[1:].split('_')[0])} import {i}" for i in imp]
        files[f"mod{m}.py"] = "\n".join(head + [""] + lines)
    return files, symbols


def _mutate(rng: random.Random, files: dict[str, str]):
    ops = []
    for _ in range(rng.randint(1, 3)):
        kind = rng.choice(["const", "guard", "expr", "rename", "delete_fn", "new_mod"])
        mod = f"mod{rng.randrange(len(files))}.py"
        ops.append((kind, mod, rng.randint(-2, 3), rng.randrange(5)))

    def apply(root: Path) -> None:
        for kind, mod, val, j in ops:
            p = root / mod
            if not p.exists():
                continue
            text = p.read_text()
            m = mod[3:-3]
            if kind == "const":
                text = "\n".join(f"C{m} = {val}" if line.startswith(f"C{m} = ") else line for line in text.split("\n"))
            elif kind == "guard":
                text = text.replace("    if d != 0:\n        return 100 // d\n    return 0", "    return 100 // d", 1)
            elif kind == "expr":
                text = text.replace(f"def f{m}_{j}(x):\n    d = ", f"def f{m}_{j}(x):\n    d = 1 + ", 1)
            elif kind == "rename" and mod != "mod0.py":
                git(root, "mv", mod, f"moved_{mod}")
                continue
            elif kind == "delete_fn":
                start = text.find(f"def f{m}_{j}(x):")
                if start >= 0:
                    end = text.find("\ndef ", start + 1)
                    text = text[:start] + (text[end + 1:] if end >= 0 else "")
            elif kind == "new_mod":
                write(root, {"extra.py": f"from {mod[:-3]} import f{m}_0\n\n\ndef extra(x):\n    return 5 // f{m}_0(x)\n"})
                continue
            p.write_text(text)

    return apply, ops


@pytest.mark.parametrize("seed", SEEDS)
def test_random_projects_incremental_equals_full(tmp_path, seed):
    rng = random.Random(seed)
    files, symbols = _gen_project(rng)
    cfg = config(*symbols, "extra.py::extra", *[s.replace("mod", "moved_mod", 1) for s in symbols], extra=DETERMINISTIC)
    apply, ops = _mutate(rng, files)
    out = assert_consistent(tmp_path, files, cfg, apply)
    # Non-vacuity: the projects contain real cross-module dependencies and real findings.
    assert out["full"]["patch"]["graph"]["candidate"].get("calls_edges", 0) > 0
    assert any(o["property_result"] == "PROVED_UNSAFE" for o in out["full"]["obligation_results"])
