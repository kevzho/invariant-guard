"""Adversarial static cases added by the M4/M5 audit (docs/IMPLEMENTATION_STATUS.md, "Audit (M4/M5)").

The same two-way check as ``test_audit_m2m3_adversarial``: every PROVED_SAFE obligation is
compared with an exhaustive CPython run in an isolated child interpreter, every PROVED_UNSAFE
witness is replayed in CPython, and the expected label is pinned. These cases target
constructs the earlier audits did not cover: shadowed builtins, chained comparisons,
augmented division, bool constants in arithmetic, floor/modulo-derived zeros with negative
operands, ``del`` and rebinding, and unsupported short-circuit/conditional expressions (which
must stay UNKNOWN, never PROVED_SAFE).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ..conftest import check_json
from .test_audit_m2m3_adversarial import EXC, GROUND_TRUTH, INTS, REPLAY, _child

# (name, source, {line: expected property_result}, expected verdict); domain: declared exact ints for m.py::f
CASES = [
    ("module_function_shadows_abs", """
        def abs(x):
            return 1

        def f(a):
            return 10 / abs(a)
        """, {6: "PROVED_SAFE"}, "PASS"),
    ("parameter_shadows_len", """
        def f(a, len):
            return 10 // len
        """, {3: "PROVED_UNSAFE"}, "BLOCK"),
    ("local_rebinding_of_len", """
        def f(a):
            len = a - a
            return 10 // len
        """, {4: "PROVED_UNSAFE"}, "BLOCK"),
    ("chained_comparison_excludes_zero", """
        def f(a):
            if 0 < a < 10:
                return 10 // a
            return 0
        """, {4: "PROVED_SAFE"}, "PASS"),
    ("chained_comparison_admits_zero", """
        def f(a):
            if -1 < a < 10:
                return 10 // a
            return 0
        """, {4: "PROVED_UNSAFE"}, "BLOCK"),
    ("augmented_floordiv", """
        def f(a, b):
            a //= b
            return a
        """, {3: "PROVED_UNSAFE"}, "BLOCK"),
    ("augmented_mod_after_guard", """
        def f(a, b):
            if b == 0:
                b = 1
            a %= b
            return a
        """, {5: "PROVED_SAFE"}, "PASS"),
    ("bool_constant_in_arithmetic", """
        def f(a):
            return 10 / (a - True)
        """, {3: "PROVED_UNSAFE"}, "BLOCK"),
    ("modulo_derived_zero", """
        def f(a):
            m = a % 3
            return 10 / (m - 2)
        """, {3: "PROVED_SAFE", 4: "PROVED_UNSAFE"}, "BLOCK"),
    ("floor_of_negative_derived_zero", """
        def f(a):
            q = a // 2
            return 10 / (q + 1)
        """, {3: "PROVED_SAFE", 4: "PROVED_UNSAFE"}, "BLOCK"),
    ("floordiv_by_negative_divisor", """
        def f(a):
            q = a // -3
            return 10 // q
        """, {3: "PROVED_SAFE", 4: "PROVED_UNSAFE"}, "BLOCK"),
    ("modulo_by_negative_divisor_sign", """
        def f(a):
            r = a % -3
            if r == 0:
                return 0
            return 10 // (r + 2)
        """, {3: "PROVED_SAFE", 6: "PROVED_UNSAFE"}, "BLOCK"),
    ("modulo_by_negative_divisor_never_positive", """
        def f(a):
            r = a % -3
            return 10 // (r - 1)
        """, {3: "PROVED_SAFE", 4: "PROVED_SAFE"}, "PASS"),
    ("del_then_rebind", """
        def f(a):
            d = a
            del d
            d = 1
            return 10 / d
        """, {6: "PROVED_SAFE"}, "PASS"),
    ("guard_then_double_reassignment", """
        def f(a):
            if a <= 0:
                return 0
            a = a - 1
            a = a + 1
            return 10 // a
        """, {7: "PROVED_SAFE"}, "PASS"),
    ("conditional_expression_is_not_trusted", """
        def f(a):
            return 0 if a else 10 // a
        """, {3: "UNKNOWN"}, "INCOMPLETE"),
    ("short_circuit_is_not_trusted", """
        def f(a):
            return a == 0 or 10 // a
        """, {3: "UNKNOWN"}, "INCOMPLETE"),
    ("nested_function_mutating_closure_is_unknown", """
        def f(a):
            d = 1
            def g():
                nonlocal d
                d = 0
            g()
            return 10 // d
        """, {8: "UNKNOWN"}, "INCOMPLETE"),
]


@pytest.mark.parametrize("name,src,expected,verdict", CASES, ids=[c[0] for c in CASES])
def test_audit_m4m5_adversarial_case(tmp_path: Path, name, src, expected, verdict):
    root = tmp_path / "proj"
    root.mkdir()
    path = root / "m.py"
    path.write_text(textwrap.dedent(src), encoding="utf-8")  # line 1 is blank; expected lines count it
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text(
        'schema_version = "1.0"\n[analysis]\nquick_budget_ms = 20000\nper_query_timeout_ms = 20000\nper_query_rlimit = 10000000\n'
        f'[[domains]]\nsymbol = "m.py::f"\nprofile = "{INTS}"\nauthority = "DECLARED"\nreason = "audit fixture"\n', encoding="utf-8")
    code, rep, err = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    failing = {(fn, line, exc) for fn, line, exc in _child(GROUND_TRUTH, str(path), INTS)}
    got: dict[int, list[str]] = {}
    for o in rep["obligation_results"]:
        if not o["symbol_id"].endswith("::f"):
            continue
        line = o["source_span"]["start_line"]
        got.setdefault(line, []).append(o["property_result"])
        exc = EXC[o["property_id"]]
        if o["property_result"] == "PROVED_SAFE":
            assert ("f", line, exc) not in failing, f"unsound PROVED_SAFE at line {line}: CPython raises {exc} there"
        if o["property_result"] == "PROVED_UNSAFE":
            assert o["witness_validated"] is True
            outcome = _child(REPLAY, str(path), "f", json.dumps(o["witness"]))
            assert outcome == [exc, line], f"witness {o['witness']} does not fail at line {line} in CPython: {outcome}"
    assert {line: sorted(set(v)) for line, v in got.items()} == {line: [r] for line, r in expected.items()}, got
    assert rep["verdict"] == verdict, rep["verdict_reasons"]
    assert code == {"PASS": 0, "WARN": 0, "BLOCK": 1, "INCOMPLETE": 2}[verdict]


# -- A4-01: the dividend's annotation-only type is an assumption of the claim ----------------------------------------
DIVIDEND_SRC = '''class Quiet(int):
    def __truediv__(self, other):
        return 0.0


def literal_zero(a: int):
    return a / 0


def constant_zero(a: int):
    return a / D


def safe_literal(a: int):
    return a / 2


def no_inputs(a: int):
    return 1 / 0


D = 0
'''


def _by_symbol(rep: dict) -> dict[str, dict]:
    return {o["symbol_id"].split("::")[1]: o for o in rep["obligation_results"]}


def test_a4_01_annotation_only_dividend_makes_the_claim_conditional(tmp_path: Path):
    """``a / 0`` with ``a: int`` was PROVED_UNSAFE EXACT_MODEL (BLOCK) with the domain "no function inputs
    influence this obligation". The annotation is not enforced: an int subclass (or any object) with
    ``__truediv__`` makes the same call succeed, so the claim is conditional on exact built-in ints."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "m.py").write_text(DIVIDEND_SRC, encoding="utf-8")
    code, rep, _ = check_json(root)
    got = _by_symbol(rep)
    for sym in ("literal_zero", "constant_zero"):
        o = got[sym]
        assert (o["property_result"], o["scope_validity"], o["domain_source"]) == ("PROVED_UNSAFE", "CONDITIONAL", "ANNOTATION"), o
        assert any("annotation-only domain (a: int)" in a for a in o["assumptions"]), o["assumptions"]
    o = got["safe_literal"]
    assert (o["property_result"], o["scope_validity"], o["domain_source"]) == ("PROVED_SAFE", "CONDITIONAL", "ANNOTATION"), o
    o = got["no_inputs"]  # nothing about the inputs matters: still exact
    assert (o["property_result"], o["scope_validity"], o["domain_source"]) == ("PROVED_UNSAFE", "EXACT_MODEL", "LITERAL_ONLY"), o
    # Why it must be conditional: an admitted-by-annotation value that is not an exact int does not fail.
    probe = ("import runpy, sys; ns = runpy.run_path(sys.argv[1]); q = ns['Quiet'](5)\n"
             "print(ns['literal_zero'](q), ns['constant_zero'](q))")
    p = subprocess.run([sys.executable, "-I", "-c", probe, str(root / "m.py")], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and p.stdout.split() == ["0.0", "0.0"], p.stderr


def test_a4_01_declared_domain_keeps_the_dividend_exact(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "m.py").write_text(DIVIDEND_SRC, encoding="utf-8")
    (root / ".guard").mkdir()
    (root / ".guard" / "config.toml").write_text(
        'schema_version = "1.0"\n[[domains]]\nsymbol = "m.py::literal_zero"\nprofile = "python_exact_builtin_ints"\nauthority = "DECLARED"\n'
        'reason = "audit fixture"\n', encoding="utf-8")
    code, rep, _ = check_json(root)
    o = _by_symbol(rep)["literal_zero"]
    assert (o["property_result"], o["scope_validity"], o["domain_source"]) == ("PROVED_UNSAFE", "EXACT_MODEL", "CONFIG_DECLARED"), o
    assert o["witness"] == {"a": 0} or isinstance(o["witness"].get("a"), int)


def test_nan_self_comparison_is_never_proved_safe(tmp_path: Path):
    """``a == a`` is False for NaN, so the division after it is reachable for a float input."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "m.py").write_text("def f(a: float):\n    if a == a:\n        return 0\n    return 1 / 0\n", encoding="utf-8")
    code, rep, _ = check_json(root)
    (o,) = rep["obligation_results"]
    assert o["property_result"] != "PROVED_SAFE", o
    p = subprocess.run([sys.executable, "-I", "-c", "import runpy,sys; runpy.run_path(sys.argv[1])['f'](float('nan'))", str(root / "m.py")],
                       capture_output=True, text=True, timeout=60)
    assert "ZeroDivisionError" in p.stderr
