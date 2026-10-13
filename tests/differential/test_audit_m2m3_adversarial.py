"""Adversarial semantic cases added by the M2/M3 audit (docs/IMPLEMENTATION_STATUS.md, "Audit (M2/M3)").

Every case is checked two ways:

1. Soundness against CPython: each function is run (in an isolated child interpreter,
   never in the test process) on every input of a small exhaustive domain. A
   PROVED_SAFE obligation must never fail in CPython at its line with the property's
   exception, and every PROVED_UNSAFE witness must fail in CPython at that line with
   that exception.
2. The specific label the audit expects (so a regression to a weaker-but-sound answer
   is also visible).

A2-01 and A2-02 were real defects found by these cases (false PROVED_UNSAFE through an
in-place list operation on an alias; index obligations silently dropped after list
concatenation/repetition).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ..conftest import check_json

INTS = "python_exact_builtin_ints"
LIST = "python_exact_builtin_list"
OPT = "python_optional_exact_builtin_int"
EXC = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError", "none.attribute_access": "AttributeError"}

# (name, profile, source, {line: expected property_result}, expected verdict)
CASES = [
    ("self_difference", INTS, """
        def f(a):
            d = a - a
            return 10 / d
        """, {4: "PROVED_UNSAFE"}, "BLOCK"),
    ("empty_open_interval", INTS, """
        def f(a):
            if a > 0 and a < 1:
                return 10 // 0
            return 1
        """, {4: "PROVED_SAFE"}, "PASS"),
    ("nested_branches_join", INTS, """
        def f(a, b):
            if a > 0:
                if b > 0:
                    d = a + b
                else:
                    d = a - 1
            else:
                d = 1
            return 10 / d
        """, {10: "PROVED_UNSAFE"}, "BLOCK"),
    ("reassignment_after_guard", INTS, """
        def f(a):
            if a != 0:
                a = a - 1
                return 10 / a
            return 0
        """, {5: "PROVED_UNSAFE"}, "BLOCK"),
    ("one_branch_fact_not_kept", INTS, """
        def f(a, b):
            if b > 0:
                a = 5
            return 10 / a
        """, {5: "PROVED_UNSAFE"}, "BLOCK"),
    ("division_by_bool", INTS, """
        def f(a):
            return 10 / (a > 3)
        """, {3: "PROVED_UNSAFE"}, "BLOCK"),
    ("bool_arithmetic", INTS, """
        def f(a):
            return 1 // ((a > 0) - (a > 0))
        """, {3: "PROVED_UNSAFE"}, "BLOCK"),
    ("floordiv_by_minus_one", INTS, """
        def f(a):
            q = a // -1
            return 10 // (q + 3)
        """, {3: "PROVED_SAFE", 4: "PROVED_UNSAFE"}, "BLOCK"),
    ("floor_of_negative_literal", INTS, """
        def f(a):
            q = (-7) // 2
            return 10 // (q + 4)
        """, {3: "PROVED_SAFE", 4: "PROVED_UNSAFE"}, "BLOCK"),
    ("modulo_sign_of_divisor", INTS, """
        def f(a):
            r = a % -3
            return 10 // (r + 2)
        """, {3: "PROVED_SAFE", 4: "PROVED_UNSAFE"}, "BLOCK"),
    ("walrus_unsupported", INTS, """
        def f(a):
            if (d := a - 2) != 0:
                return 10 / d
            return 10 / d
        """, {4: "UNKNOWN", 5: "UNKNOWN"}, "INCOMPLETE"),
    ("global_mutation_unsupported", INTS, """
        G = 1
        def f(a):
            global G
            G = 0
            return 10 / G
        """, {6: "UNKNOWN"}, "INCOMPLETE"),
    ("assert_may_be_disabled", INTS, """
        def f(a):
            assert a != 0
            return 10 / a
        """, {4: "PROVED_UNSAFE"}, "WARN"),
    # Loops (I-132): the witness a=1 runs one real iteration (d = 0) before the division.
    ("loop_carried_value_reaches_zero", INTS, """
        def f(a):
            d = 1
            for i in range(a):
                d = i
            return 10 / d
        """, {6: "PROVED_UNSAFE"}, "BLOCK"),
    ("unicode_columns", INTS, """
        def f(ä, b):
            é = ä - b
            return "ü" * 0 + str(100 // é)
        """, {4: "PROVED_UNSAFE"}, "BLOCK"),
    ("negative_index_too_far", LIST, """
        def f(xs):
            if len(xs) == 1:
                return xs[-2]
            return 0
        """, {4: "PROVED_UNSAFE"}, "BLOCK"),
    ("negative_index_ok", LIST, """
        def f(xs):
            if xs:
                return xs[-1]
            return 0
        """, {4: "PROVED_SAFE"}, "PASS"),
    # A2-01: ``ys *= 0`` empties the list xs shares; the evaluator used ``ys * 0``
    # (a fresh list) and validated a witness CPython never fails on.
    ("alias_inplace_repeat", LIST, """
        def f(xs):
            ys = xs
            ys *= 0
            if len(xs) > 0:
                return 10 // 0
            return 1
        """, {6: "POSSIBLY_UNSAFE"}, "WARN"),
    ("alias_inplace_concat", LIST, """
        def f(xs):
            ys = xs
            ys += [1]
            if len(xs) == 0:
                return 10 // 0
            return 1
        """, {6: "POSSIBLY_UNSAFE"}, "WARN"),
    # A2-02: list concatenation/repetition results used to be untyped objects, so the
    # index obligation disappeared and the run said PASS.
    ("inplace_repeat_then_index", LIST, """
        def f(xs):
            xs *= 0
            return xs[0]
        """, {4: "UNKNOWN"}, "INCOMPLETE"),
    ("concat_then_index_ok", LIST, """
        def f(xs):
            ys = xs + [1]
            return ys[0]
        """, {4: "PROVED_SAFE"}, "PASS"),
    ("concat_then_index_bad", LIST, """
        def f(xs):
            ys = xs + [1]
            return ys[1]
        """, {4: "PROVED_UNSAFE"}, "BLOCK"),
    ("repeat_then_index_bad", LIST, """
        def f(xs):
            ys = [1] * len(xs)
            return ys[0]
        """, {4: "PROVED_UNSAFE"}, "BLOCK"),
    ("alias_clear", LIST, """
        def f(xs):
            if len(xs) > 0:
                ys = xs
                ys.clear()
                return xs[0]
            return 0
        """, {6: "UNKNOWN"}, "INCOMPLETE"),
    ("none_reassigned_after_guard", OPT, """
        def f(x):
            if x is not None:
                x = None
                return x.bit_length()
            return 0
        """, {5: "PROVED_UNSAFE"}, "BLOCK"),
    ("none_join_of_branches", OPT, """
        def f(x):
            if x is None:
                y = 1
            else:
                y = None
            return y.bit_length()
        """, {7: "PROVED_UNSAFE"}, "BLOCK"),
]

GROUND_TRUTH = r"""
import itertools, json, runpy, sys, traceback
path, profile = sys.argv[1], sys.argv[2]
ns = runpy.run_path(path)
ints = list(range(-5, 6))
lists = [[]] + [[a] for a in range(-2, 3)] + [[a, b] for a in range(-2, 3) for b in range(-2, 3)]
dom = {"python_exact_builtin_ints": ints, "python_exact_builtin_list": lists, "python_optional_exact_builtin_int": ints + [None]}[profile]
import inspect
fails = []
for name, fn in ns.items():
    if not inspect.isfunction(fn) or fn.__module__ != "<run_path>":
        continue
    n = len(inspect.signature(fn).parameters)
    for args in itertools.product(dom, repeat=n):
        args = [list(a) if isinstance(a, list) else a for a in args]
        try:
            fn(*args)
        except Exception as e:
            fr = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path][-1]
            fails.append([name, fr.lineno, type(e).__name__])
print(json.dumps(sorted(set(map(tuple, fails)))))
"""

REPLAY = r"""
import json, runpy, sys, traceback
path, fn, args = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
ns = runpy.run_path(path)
try:
    ns[fn](**args)
    print(json.dumps(["OK", 0]))
except Exception as e:
    fr = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path][-1]
    print(json.dumps([type(e).__name__, fr.lineno]))
"""


def _child(script: str, *argv: str, optimize: bool = False) -> object:
    p = subprocess.run([sys.executable, "-I", *(["-O"] if optimize else []), "-c", script, *argv], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.parametrize("name,profile,src,expected,verdict", CASES, ids=[c[0] for c in CASES])
def test_audit_adversarial_case(tmp_path: Path, name, profile, src, expected, verdict):
    root = tmp_path / "proj"
    root.mkdir()
    path = root / "m.py"
    path.write_text(textwrap.dedent(src), encoding="utf-8")  # line 1 is blank; expected lines count it
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text(
        'schema_version = "1.0"\n[analysis]\nquick_budget_ms = 20000\nper_query_timeout_ms = 20000\nper_query_rlimit = 10000000\n'
        f'[[domains]]\nsymbol = "m.py::f"\nprofile = "{profile}"\nauthority = "DECLARED"\nreason = "audit fixture"\n', encoding="utf-8")
    code, rep, err = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    failing = {(fn, line, exc) for fn, line, exc in _child(GROUND_TRUTH, str(path), profile)}
    got: dict[int, list[str]] = {}
    for o in rep["obligation_results"]:
        line = o["source_span"]["start_line"]
        got.setdefault(line, []).append(o["property_result"])
        exc = EXC[o["property_id"]]
        if o["property_result"] == "PROVED_SAFE":
            assert ("f", line, exc) not in failing, f"unsound PROVED_SAFE at line {line}: CPython raises {exc} there"
        if o["property_result"] == "PROVED_UNSAFE":
            assert o["witness_validated"] is True
            disabled = (o.get("runtime_mode") or "").startswith("python -O")
            if disabled:  # a failure reachable only without assertions is CONDITIONAL, never EXACT
                assert o["scope_validity"] == "CONDITIONAL"
            outcome = _child(REPLAY, str(path), "f", json.dumps(o["witness"]), optimize=disabled)
            assert outcome == [exc, line], f"witness {o['witness']} does not fail at line {line} in CPython: {outcome}"
    assert {line: sorted(set(v)) for line, v in got.items()} == {line: [r] for line, r in expected.items()}, got
    assert rep["verdict"] == verdict, rep["verdict_reasons"]
    assert code == {"PASS": 0, "WARN": 0, "BLOCK": 1, "INCOMPLETE": 2}[verdict]
