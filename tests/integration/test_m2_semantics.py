"""Targeted M2 semantics: call summaries, module constants, bounds and None-flow (T22, T25).

Every PROVED_UNSAFE witness is replayed in an isolated CPython process at the exact line;
expected results are derived from Python semantics.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ..conftest import check_json, result_for, write_project

EXC = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError", "none.attribute_access": "AttributeError"}

REPLAY = r"""
import json, runpy, sys, traceback
ns = runpy.run_path(sys.argv[1])
try:
    ns[sys.argv[2]](**json.loads(sys.argv[3]))
    print(json.dumps(None))
except BaseException as e:
    tb = traceback.extract_tb(e.__traceback__)[-1]
    print(json.dumps([type(e).__name__, tb.lineno]))
"""


def _proj(tmp_path: Path, src: str, decls: dict[str, tuple[list[str] | None, str] | list[tuple[list[str] | None, str]]] | None = None) -> Path:
    """Project with ``m.py``; ``decls`` maps a symbol to (params, profile) or a list of them."""
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    cfg = ['schema_version = "1.0"', "[analysis]", "quick_budget_ms = 30000"]
    for sym, entries in (decls or {}).items():
        for params, prof in (entries if isinstance(entries, list) else [entries]):
            cfg += ["[[domains]]", f'symbol = "{sym}"', f'profile = "{prof}"', 'authority = "DECLARED"', 'reason = "m2 fixture"']
            if params is not None:
                cfg.append("params = " + json.dumps(params))
    if "g(" in src and "def g" not in src:
        src += "\n\ndef g(*a):\n    return 0\n"
    return write_project(root, {"m.py": src}, "\n".join(cfg) + "\n")


def _replay_ok(root: Path, o: dict) -> None:
    func = o["symbol_id"].split("::", 1)[1]
    p = subprocess.run([sys.executable, "-I", "-c", REPLAY, str(root / "m.py"), func, json.dumps(o["witness"])], capture_output=True, text=True, timeout=60)
    got = json.loads(p.stdout)
    assert got == [EXC[o["property_id"].removesuffix(".via_call")], o["source_span"]["start_line"]], (o["obligation_id"], o["witness"], got)


INT = "python_exact_builtin_ints"
LIST = "python_exact_builtin_list"
OPT = "python_optional_exact_builtin_int"

CASES = [
    # (id, source, declarations, obligation id suffix, expected result, expected scope)
    ("empty-list", "def f(xs):\n    return xs[0]\n", {"m.py::f": (None, LIST)}, "index.out_of_range#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("negative-index-empty", "def f(xs):\n    return xs[-1]\n", {"m.py::f": (None, LIST)}, "index.out_of_range#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("negative-index-guarded-len", "def f(xs):\n    if len(xs) > 0:\n        return xs[-1]\n    return 0\n", {"m.py::f": (None, LIST)}, "index.out_of_range#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("negative-index-guarded-truthiness", "def f(xs):\n    if xs:\n        return xs[-1]\n    return 0\n", {"m.py::f": (None, LIST)}, "index.out_of_range#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("negative-index-too-far", "def f(xs):\n    if len(xs) >= 2:\n        return xs[-3]\n    return 0\n", {"m.py::f": (None, LIST)}, "index.out_of_range#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("relational-index", "def f(xs, i):\n    if 0 <= i < len(xs):\n        return xs[i]\n    return 0\n", {"m.py::f": [(["xs"], LIST), (["i"], INT)]}, "index.out_of_range#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("relational-index-off-by-one", "def f(xs, i):\n    if 0 <= i <= len(xs):\n        return xs[i]\n    return 0\n", {"m.py::f": [(["xs"], LIST), (["i"], INT)]}, "index.out_of_range#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("literal-in-range", "def f():\n    xs = [1, 2, 3]\n    return xs[-3]\n", {}, "index.out_of_range#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("literal-out-of-range", "def f():\n    ys = (1, 2)\n    return ys[2]\n", {}, "index.out_of_range#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("second-index-after-first", "def f(xs):\n    a = xs[3]\n    return xs[-4]\n", {"m.py::f": (None, LIST)}, "index.out_of_range#1", "PROVED_SAFE", "EXACT_MODEL"),
    ("len-taken-before-mutation", "def f():\n    xs = [1]\n    n = len(xs)\n    g(xs)\n    if n > 0:\n        return xs[0]\n    return 0\n", {}, "index.out_of_range#0", "UNKNOWN", None),
    ("alias-mutation", "def f():\n    xs = [1]\n    ys = xs\n    ys.append(2)\n    return xs[1]\n", {}, "index.out_of_range#0", "UNKNOWN", None),
    ("tuple-survives-unknown-call", "def f():\n    t = (1, 2)\n    g(t)\n    return t[1]\n", {}, "index.out_of_range#0", "PROVED_SAFE", "CONDITIONAL"),
    ("annotated-list-conditional", "def f(xs: list[int]):\n    return xs[0]\n", {}, "index.out_of_range#0", "PROVED_UNSAFE", "CONDITIONAL"),
    ("none-receiver", "def f(code):\n    return code.bit_length()\n", {"m.py::f": (None, OPT)}, "none.attribute_access#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("none-guarded", "def f(code):\n    if code is not None:\n        return code.bit_length()\n    return 0\n", {"m.py::f": (None, OPT)}, "none.attribute_access#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("none-early-return", "def f(code):\n    if code is None:\n        return 0\n    return code.bit_length()\n", {"m.py::f": (None, OPT)}, "none.attribute_access#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("none-attr-on-none-literal", "def f():\n    x = None\n    return x.real\n", {}, "none.attribute_access#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("none-annotation-optional", "from typing import Optional\n\n\ndef f(x: Optional[int]):\n    return x.bit_length()\n", {}, "none.attribute_access#0", "PROVED_UNSAFE", "CONDITIONAL"),
    ("none-annotation-union", "def f(x: int | None):\n    if x is None:\n        return 0\n    return x.bit_length()\n", {}, "none.attribute_access#0", "PROVED_SAFE", "CONDITIONAL"),
    ("none-via-callee", "def h(x):\n    if x > 0:\n        return x\n    return None\n\n\ndef f(a):\n    v = h(a)\n    return v.bit_length()\n",
     {"m.py::f": (None, INT)}, "none.attribute_access#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    # Regression (found by tests/differential/test_m2_differential.py): `v is None` on an optional
    # callee result was decided False by the interval analysis, giving a false UNREACHABLE proof.
    ("is-none-on-optional-callee-result", "def h(x):\n    if x > 0:\n        return x\n    return None\n\n\ndef f(a):\n    v = h(a)\n    if v is None:\n        return 1 // 0\n    return 0\n",
     {"m.py::f": (None, INT)}, "division.zero_denominator#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("summary-divisor", "def d(x):\n    return x - 3\n\n\ndef f(a):\n    return 10 // d(a)\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("summary-guarded-caller", "def d(x):\n    return x - 3\n\n\ndef f(a):\n    if a > 3:\n        return 10 // d(a)\n    return 0\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("summary-default-arg", "def d(x, k=2):\n    return x - k\n\n\ndef f(a):\n    return 10 % d(a)\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("summary-keyword-arg", "def d(x, k):\n    return x - k\n\n\ndef f(a):\n    if a != 5:\n        return 10 // d(k=5, x=a)\n    return 0\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "PROVED_SAFE", "EXACT_MODEL"),
    # Regression: arguments are evaluated in call-site order (keywords as written), not parameter
    # order. With parameter order `1 // a` became unreachable behind `1 // 0` (a false proof).
    ("summary-keyword-arg-order", "def d(x, k):\n    return 0\n\n\ndef f(a):\n    return d(k=1 // a, x=1 // 0)\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("summary-keyword-arg-order-second", "def d(x, k):\n    return 0\n\n\ndef f(a):\n    return d(k=1 // a, x=1 // 0)\n", {"m.py::f": (None, INT)}, "division.zero_denominator#1", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("summary-callee-raises", "def d(x):\n    raise ValueError(x)\n\n\ndef f(a):\n    d(a)\n    return 1 // 0\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("via-call-helper-failure", "def inv(d):\n    return 1 / d\n\n\ndef f(a):\n    return inv(a - a)\n", {"m.py::f": (None, INT)}, "division.zero_denominator.via_call#0", "PROVED_UNSAFE", "EXACT_MODEL"),
    ("via-call-excluded-by-caller", "def inv(d):\n    return 1 / d\n\n\ndef f(a):\n    if a != 0:\n        return inv(a)\n    return 0\n", {"m.py::f": (None, INT)}, "division.zero_denominator.via_call#0", "PROVED_SAFE", "EXACT_MODEL"),
    ("recursion-is-unknown", "def r(n):\n    if n <= 0:\n        return 0\n    return r(n - 1)\n\n\ndef f(a):\n    return 1 // r(a)\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "UNKNOWN", None),
    ("decorated-callee-unknown", "import functools\n\n\n@functools.cache\ndef d(x):\n    return x\n\n\ndef f(a):\n    return 1 // d(a)\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "UNKNOWN", None),
    ("rebound-callee-unknown", "def d(x):\n    return x + 1\n\n\nd = abs\n\n\ndef f(a):\n    if a >= 0:\n        return 1 // d(a)\n    return 0\n", {"m.py::f": (None, INT)}, "division.zero_denominator#0", "UNKNOWN", None),
    ("globals-call-disables-resolution", "LIMIT = 1\n\n\ndef f():\n    return 1 // LIMIT\n\n\ndef g():\n    globals()['LIMIT'] = 0\n", {}, "division.zero_denominator#0", "UNKNOWN", None),
    ("shadowed-len-unknown", "def len(x):\n    return 5\n\n\ndef f(xs):\n    if len(xs) > 0:\n        return xs[0]\n    return 0\n", {"m.py::f": (None, LIST)}, "index.out_of_range#0", "PROVED_UNSAFE", "EXACT_MODEL"),
]


@pytest.mark.parametrize("case_id,src,decls,suffix,result,scope", CASES, ids=[c[0] for c in CASES])
def test_m2_semantics_case(tmp_path, case_id, src, decls, suffix, result, scope):
    root = _proj(tmp_path, src, decls)
    _, report, _ = check_json(root)
    assert report["errors"] == [], report["errors"]
    oid = f"m.py::f::{suffix}"
    o = next((x for x in report["obligation_results"] if x["obligation_id"] == oid), None)
    assert o is not None, [x["obligation_id"] for x in report["obligation_results"]]
    assert (o["property_result"], o["scope_validity"]) == (result, scope), o["reasons"]
    if result == "PROVED_UNSAFE":
        _replay_ok(root, o)


def test_no_solver_bounds_and_none_use_interval_facts_and_bounded_search(tmp_path):
    src = "def f(xs):\n    if xs:\n        return xs[-1]\n    return xs[0]\n"
    root = _proj(tmp_path, src, {"m.py::f": (None, LIST)})
    _, report, _ = check_json(root, "--no-solver")
    a = result_for_prop(report, "m.py::f::index.out_of_range#0")
    b = result_for_prop(report, "m.py::f::index.out_of_range#1")
    assert (a["property_result"], a["method"]) == ("PROVED_SAFE", "INTERVAL")
    assert (b["property_result"], b["method"], b["witness"]) == ("PROVED_UNSAFE", "WITNESS_SEARCH", {"xs": []})


def result_for_prop(report: dict, oid: str) -> dict:
    return next(x for x in report["obligation_results"] if x["obligation_id"] == oid)


def test_call_summary_dependencies_are_recorded(tmp_path):
    src = "K = 3\n\n\ndef d(x):\n    return x - K\n\n\ndef f(a: int):\n    return 10 // d(a)\n"
    root = _proj(tmp_path, src)
    from invariantguard.frontends.python import lower_module, parse_source

    fn = next(f for f in lower_module(parse_source("m.py", src.encode()), "sha256:" + "0" * 64, {}).module.functions if f.name == "f")
    kinds = {(d["kind"], d.get("name") or d.get("symbol")) for d in fn.deps}
    assert ("function_body", "m.py::d") in kinds and ("global", "K") in kinds and ("global", "d") in kinds and ("global", "int") in kinds
    assert fn.inlined == ["m.py::d"]
