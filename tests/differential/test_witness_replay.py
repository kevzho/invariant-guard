"""Differential checks against real, isolated CPython execution (test suite only).

Production static analysis never runs repository code. Here the test suite executes
its own fixture files in a separate `python -I` process to establish ground truth:
every PROVED_UNSAFE witness must actually raise the exception of its property
(ZeroDivisionError, IndexError or AttributeError) at the reported line, and the IR
evaluator must agree with CPython on supported operations.
"""

import itertools
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

from invariantguard.frontends.python import lower_module, parse_source
from invariantguard.ir.evaluator import evaluate

from ..conftest import DEMO, REPO, check_json

RUNNER = r"""
import json, runpy, sys, traceback
req = json.loads(sys.stdin.read())
out = []
for item in req:
    ns = runpy.run_path(item["path"])
    fn = ns[item["func"]]
    try:
        out.append({"ok": True, "value": repr(fn(**item["args"]))})
    except BaseException as exc:
        last = traceback.extract_tb(exc.__traceback__)[-1]
        out.append({"ok": False, "exc": type(exc).__name__, "line": last.lineno, "file": last.filename})
print(json.dumps(out))
"""


def run_isolated(items, optimize=False):
    cmd = [sys.executable, "-I"] + (["-O"] if optimize else []) + ["-c", RUNNER]
    p = subprocess.run(cmd, input=json.dumps(items), capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


EXPECTED_EXCEPTION = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError", "none.attribute_access": "AttributeError"}


def test_every_proved_unsafe_witness_raises_zero_division_in_cpython():
    _, report, _ = check_json(DEMO)
    unsafe = [r for r in report["obligation_results"] if r["property_result"] == "PROVED_UNSAFE"]
    assert len(unsafe) >= 5
    for r in unsafe:
        path = DEMO / r["source_span"]["path"]
        caller_path = DEMO / r["symbol_id"].split("::", 1)[0]
        func = r["symbol_id"].split("::", 1)[1]
        optimize = r["runtime_mode"] == "python -O (assertions disabled)"
        (res,) = run_isolated([{"path": str(caller_path), "func": func, "args": r["witness"]}], optimize=optimize)
        expected = EXPECTED_EXCEPTION[r["property_id"].removesuffix(".via_call")]
        assert res["exc"] == expected, (r["obligation_id"], r["witness"], res)
        assert res["line"] == r["source_span"]["start_line"] and res["file"] == str(path)  # raised at this obligation


def test_assert_fixture_witness_fails_differently_with_assertions_enabled():
    path = str(DEMO / "adversarial" / "assert_guard.py")
    (res,) = run_isolated([{"path": path, "func": "checked_ratio", "args": {"a": 1, "b": 0}}])
    assert res["exc"] == "AssertionError"


def test_unknown_cubes_case_is_an_abstention_not_a_false_safe():
    # Booker (2019): 33 is a sum of three cubes, so a failing input exists.
    args = {"x": 8866128975287528, "y": -8778405442862239, "z": -2736111468807040}
    (res,) = run_isolated([{"path": str(DEMO / "adversarial" / "sum_of_cubes.py"), "func": "cube_sum_ratio", "args": args}])
    assert res["exc"] == "ZeroDivisionError"
    _, report, _ = check_json(DEMO / "adversarial" / "sum_of_cubes.py")
    assert report["obligation_results"][0]["property_result"] == "UNKNOWN"


def test_proved_safe_obligations_survive_boundary_inputs():
    """Safe results are checked against isolated execution on a boundary grid (finite evidence)."""
    _, report, _ = check_json(DEMO)
    grid = [-3, -2, -1, 0, 1, 2, 3]
    items = []
    for r in report["obligation_results"]:
        if r["property_result"] != "PROVED_SAFE" or r["domain_source"] not in ("CONFIG_DECLARED", "LITERAL_ONLY"):
            continue
        path = DEMO / r["source_span"]["path"]
        func = r["symbol_id"].split("::", 1)[1]
        parsed = parse_source(r["source_span"]["path"], path.read_bytes())
        fn_node = next(n for n in parsed.tree.body if getattr(n, "name", None) == func)
        names = [a.arg for a in fn_node.args.args]
        for combo in itertools.product(grid, repeat=len(names)):
            items.append({"path": str(path), "func": func, "args": dict(zip(names, combo)), "line": r["source_span"]["start_line"]})
    assert items
    results = run_isolated(items)
    for item, res in zip(items, results):
        # A ZeroDivisionError may come from another (unsafe) obligation in the same function, never from this one.
        assert not (res.get("exc") == "ZeroDivisionError" and res["line"] == item["line"] and res["file"] == item["path"]), (item, res)


CONFORMANCE = """
def ops(a, b):
    if b != 0:
        q = a // b
        r = a % b
        t = a / b
        if q * b + r == a:
            return q - r
        return 999
    return a - b * 3

def branches(a, b):
    c = a - b
    if c > 2 and not a == 0 or b < -1:
        x = c * 2
    else:
        x = c + 7
    if x >= 0:
        return 100 // (x + 1)
    return x % 5
"""


def test_ir_evaluator_agrees_with_cpython(tmp_path):
    src = tmp_path / "conf.py"
    src.write_text(CONFORMANCE)
    decl = NS(params=None, profile="python_exact_builtin_ints", authority="DECLARED", reason="t")
    fns = {f.name: f for f in lower_module(parse_source("conf.py", CONFORMANCE.encode()), "sha256:" + "0" * 64,
                                           {"conf.py::ops": decl, "conf.py::branches": decl}).module.functions}
    grid = [-7, -3, -2, -1, 0, 1, 2, 3, 7]
    items, expected = [], []
    for name in ("ops", "branches"):
        for a, b in itertools.product(grid, grid):
            items.append({"path": str(src), "func": name, "args": {"a": a, "b": b}})
            expected.append(evaluate(fns[name], {"a": a, "b": b}))
    results = run_isolated(items)
    for item, ev, real in zip(items, expected, results):
        if real["ok"]:
            assert ev.kind == "RETURNED" and repr(ev.return_value) == real["value"], (item, ev.kind, real)
        else:
            assert real["exc"] == "ZeroDivisionError" and ev.kind == "REACHED_FAILURE", (item, ev.kind, real)
