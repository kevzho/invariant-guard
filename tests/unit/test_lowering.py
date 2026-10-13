"""AST -> typed IR lowering: CFG shape, phi at joins, source mapping, unsupported regions."""

import ast
from types import SimpleNamespace as NS

import pytest

from invariantguard.frontends.python import lower_module, parse_source
from invariantguard.ir.model import IR_SCHEMA_VERSION, Effect, Kind, ModuleIR, Op, Origin, check_ir_version, IncompatibleIRVersion
from invariantguard.ir.printer import format_function

SNAP = "sha256:" + "0" * 64
DECL = NS(params=None, profile="python_exact_builtin_ints", authority="DECLARED", reason="test")


def lower(src: str, domains=None):
    parsed = parse_source("m.py", src.encode())
    res = lower_module(parsed, SNAP, domains or {})
    assert res.function_errors == []
    return parsed, res


def fn_named(res, name):
    return next(f for f in res.module.functions if f.name == name)


def test_ratio_cfg_matches_docs_example():
    src = "def ratio(a: int, b: int):\n    d = a - b\n    if d != 0:\n        return 100 / d\n    return 0.0\n"
    parsed, res = lower(src, {"m.py::ratio": DECL})
    fn = fn_named(res, "ratio")
    entry = fn.blocks[fn.entry_id]
    ops = [i.op for i in entry.instructions]
    assert ops[:3] == [Op.PARAM, Op.PARAM, Op.SUB]
    assert entry.instructions[2].flavor == "python.bigint"
    assert entry.terminator.op is Op.BRANCH
    t, f = entry.terminator.attrs["true_target"], entry.terminator.attrs["false_target"]
    div = next(i for i in fn.blocks[t].instructions if i.op is Op.DIV)
    assert div.flavor == "python.true_division"
    assert fn.blocks[t].terminator.op is Op.RETURN
    assert fn.blocks[f].terminator.op is Op.JUMP  # empty else falls through to the join
    assert fn.blocks[fn.blocks[f].terminator.attrs["target"]].terminator.op is Op.RETURN
    assert fn.params[0].type.kind is Kind.INT and fn.params[0].type.origin is Origin.DECLARED
    # source mapping of the division obligation
    site = fn.division_sites[0]
    assert (site.span.start_line, site.span.start_column, site.span.end_column) == (4, 15, 22)
    assert parsed.raw[site.span.start_byte:site.span.end_byte] == b"100 / d"
    assert site.divisor_text == "d" and site.block_id == t
    assert "DIV" in format_function(fn)


def test_phi_at_join_and_versioned_values():
    src = "def f(a: int):\n    x = 1\n    if a > 0:\n        x = a\n    else:\n        x = 2\n    return 10 // x\n"
    _, res = lower(src)
    fn = fn_named(res, "f")
    phis = [i for b in fn.blocks.values() for i in b.instructions if i.op is Op.PHI]
    assert len(phis) == 1
    phi = phis[0]
    assert phi.attrs["variable"] == "x" and len(phi.attrs["incoming"]) == 2
    join = next(b for b in fn.blocks.values() if phi in b.instructions)
    assert sorted(join.preds) == sorted(p for p, _ in phi.attrs["incoming"])
    div = next(i for b in fn.blocks.values() for i in b.instructions if i.op is Op.FLOORDIV)
    assert div.operands[1] == phi.result


def test_maybe_unbound_phi():
    src = "def f(a: int):\n    if a > 0:\n        y = 1\n    return 10 // y\n"
    _, res = lower(src)
    phi = next(i for b in fn_named(res, "f").blocks.values() for i in b.instructions if i.op is Op.PHI)
    assert phi.attrs["maybe_unbound"] is True and phi.type.kind is Kind.OBJECT


def test_short_circuit_and_chain_lower_to_branches():
    src = "def f(a: int, b: int):\n    if 0 < a < 10 and not b == 0:\n        return a // b\n    return 0\n"
    _, res = lower(src)
    fn = fn_named(res, "f")
    branches = [b for b in fn.blocks.values() if b.terminator is not None and b.terminator.op is Op.BRANCH]
    assert len(branches) == 3  # 0 < a, a < 10, b == 0
    fn.topo_order()


def test_unsupported_statement_taints_names_and_sites():
    src = (
        "def f(n: int, cm):\n"
        "    total = 0\n"
        "    with cm:\n"
        "        total += 100 // n\n"
        "    return 100 / total\n"
    )
    _, res = lower(src)
    fn = fn_named(res, "f")
    s0, s1 = fn.division_sites
    assert s0.block_id is None and "with statement" in s0.unsupported_reason
    assert s1.block_id is not None
    div = next(i for b in fn.blocks.values() for i in b.instructions if i.result == s1.result_id)
    divisor_def = fn.instr_by_result()[div.operands[1]]
    assert divisor_def.op is Op.UNKNOWN_EFFECT and divisor_def.effect is Effect.UNKNOWN


def test_every_division_site_is_accounted_for():
    src = (
        "X = 1 / 3\n"
        "def top(a, b=4 % 3):\n"
        "    g = lambda z: z / a\n"
        "    vals = [v // a for v in range(3)]\n"
        "    return a % b + (a / b if a else 0)\n"
        "class C:\n"
        "    k = 6 // 2\n"
        "    def m(self, x):\n"
        "        return x / self.k\n"
        "def gen(a):\n"
        "    yield 1 / a\n"
    )
    parsed, res = lower(src)
    syntactic = sum(1 for n in ast.walk(parsed.tree) if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Div, ast.FloorDiv, ast.Mod)))
    lowered = sum(len(f.division_sites) for f in res.module.functions)
    unsupported = sum(len(u.division_sites) for u in res.module.unsupported_symbols)
    assert lowered + unsupported == syntactic == 9
    kinds = {u.symbol_id.split("::")[1]: u.kind for u in res.module.unsupported_symbols}
    assert kinds["<module>"] == "module_initializer"  # X = 1/3 and default 4 % 3
    assert kinds["C"] == "class_body" and "C.m" not in kinds  # plain methods are lowered (I-130)
    assert next(f for f in res.module.functions if f.symbol_id.endswith("::C.m")).division_sites[0].text == "x / self.k"
    assert kinds["top.<locals>.<lambda>#1"] == "lambda"
    assert "generator" in next(u.reason for u in res.module.unsupported_symbols if u.symbol_id.endswith("::gen"))
    top = fn_named(res, "top")
    reasons = {s.text: s.unsupported_reason for s in top.division_sites}
    assert reasons["a % b"] is None
    assert "IfExp" in reasons["a / b"]
    assert "ListComp" in reasons["v // a"]


@pytest.mark.parametrize(
    "src,needle",
    [
        ("import functools\n@functools.cache\ndef f(a):\n    return 1 / a\n", "decorated"),
        ("async def f(a):\n    return 1 / a\n", "async"),
        ("def f(a):\n    global G\n    return 1 / a\n", "global"),
        ("def f(a):\n    if (b := a):\n        return 1 / b\n", "assignment expression"),
        ("def f(a):\n    def g():\n        nonlocal a\n        a = 0\n    g()\n    return 1 / a\n", "nonlocal"),
    ],
)
def test_function_level_unsupported(src, needle):
    _, res = lower(src)
    assert res.module.functions == [] or all(f.name != "f" for f in res.module.functions)
    u = next(u for u in res.module.unsupported_symbols if u.symbol_id == "m.py::f")
    assert needle in u.reason and len(u.division_sites) == 1


def test_assert_lowers_with_runtime_mode_branch():
    _, res = lower("def f(a: int, b: int):\n    assert b != 0\n    return a / b\n")
    fn = fn_named(res, "f")
    assert fn.has_asserts
    debug = [i for i in fn.blocks[fn.entry_id].instructions if i.op is Op.PARAM and i.attrs.get("runtime_mode")]
    assert len(debug) == 1 and fn.blocks[fn.entry_id].terminator.attrs["kind"] == "assertions_enabled"


def test_global_and_call_are_unknown_effects():
    # M2: a module constant bound once is a constant with a stated resolution assumption;
    # an undefined callee and a rebound global stay unknown.
    _, res = lower("LIMIT = 3\nOTHER = 1\nOTHER = 2\ndef f(a: int):\n    x = helper(a) + LIMIT + OTHER\n    return a / x\n")
    fn = fn_named(res, "f")
    instrs = [i for b in fn.blocks.values() for i in b.instructions]
    call = next(i for i in instrs if i.op is Op.CALL)
    assert call.effect is Effect.UNKNOWN and call.attrs["callee"] == "helper"
    limit = next(i for i in instrs if i.op is Op.CONST and i.attrs.get("global") == "LIMIT")
    assert limit.attrs["value"] == 3 and "LIMIT" in limit.attrs["resolution_assumption"]
    assert any(i.op is Op.UNKNOWN_EFFECT and "OTHER" in i.attrs.get("reason", "") and "2 module-level bindings" in i.attrs["reason"] for i in instrs)
    assert {d["name"] for d in fn.deps if d["kind"] == "global"} >= {"LIMIT", "OTHER", "helper", "int"}


def test_ir_serialization_and_version_check():
    _, res = lower("def f(a: int):\n    return 1 // a\n")
    data = res.module.to_json()
    assert data["schema_version"] == IR_SCHEMA_VERSION == "ir-0.3" and data["language"] == "python"
    assert data["symbols"][0]["blocks"][0]["instructions"][0]["op"] == "PARAM"
    bad = ModuleIR(path="m.py", source_digest=SNAP, functions=[], unsupported_symbols=[], imports=[], requested_symbols=[], schema_version="ir-9.9")
    with pytest.raises(IncompatibleIRVersion):
        check_ir_version(bad)


def test_imports_recorded_not_executed():
    _, res = lower("import os, json\nfrom vendor_math import external_transform\n")
    assert [i["module"] for i in res.module.imports] == ["os,json", "vendor_math"]
    assert all(i["executed"] is False and i["resolved"] is False for i in res.module.imports)
