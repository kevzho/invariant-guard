"""Solver adapter: SAT / UNSAT / TIMEOUT / RESOURCE_LIMIT / UNAVAILABLE are distinct."""

import sys
from types import SimpleNamespace as NS

import pytest

from invariantguard.frontends.python import lower_module, parse_source
from invariantguard.ir.model import Op
from invariantguard.models.enums import SolverStatus
from invariantguard.solver.z3_backend import Z3Backend, probe_z3

SNAP = "sha256:" + "0" * 64
DECL = NS(params=None, profile="python_exact_builtin_ints", authority="DECLARED", reason="test")


def fn_of(src: str):
    res = lower_module(parse_source("m.py", src.encode()), SNAP, {"m.py::f": DECL})
    return res.module.functions[0]


def query(backend, fn):
    enc = backend.encoder(fn)
    site = fn.division_sites[0]
    block = fn.blocks[site.block_id]
    idx = next(i for i, ins in enumerate(block.instructions) if ins.result == site.result_id)
    divisor = block.instructions[idx].operands[1]
    return enc, [enc.prefix(site.block_id, idx), enc.int_term(divisor) == 0]


def test_sat_with_model_and_stable_digest():
    b = Z3Backend(1000)
    fn = fn_of("def f(a, b):\n    d = a - b\n    return 100 / d\n")
    enc, cs = query(b, fn)
    out = b.check(enc, cs)
    assert out.status is SolverStatus.SAT and out.model["a"] == out.model["b"]
    enc2, cs2 = query(b, fn)
    assert b.check(enc2, cs2).query_digest == out.query_digest  # deterministic query text
    assert out.query_digest.startswith("sha256:") and out.exact


def test_unsat():
    b = Z3Backend(1000)
    enc, cs = query(b, fn_of("def f(a, b):\n    if a == b:\n        return 0\n    return 100 / (a - b)\n"))
    assert b.check(enc, cs).status is SolverStatus.UNSAT


CUBES = "def f(x, y, z):\n    d = x * x * x + y * y * y + z * z * z - 33\n    return 100 / d\n"


def test_timeout_is_not_unsat_or_unknown():
    b = Z3Backend(50)
    enc, cs = query(b, fn_of(CUBES))
    out = b.check(enc, cs)
    assert out.status is SolverStatus.TIMEOUT and out.reason == "timeout"


def test_resource_limit_is_a_distinct_unknown():
    b = Z3Backend(10_000, rlimit=1000)
    enc, cs = query(b, fn_of(CUBES))
    out = b.check(enc, cs)
    assert out.status is SolverStatus.RESOURCE_LIMIT


def test_python_floor_division_encoding_matches_semantics():
    # q == -1 only for a in {-2, -1} under floor semantics; a + 1 == 0 forces a == -1.
    b = Z3Backend(1000)
    fn = fn_of("def f(a):\n    if a // 2 == -1:\n        return 100 // (a + 1)\n    return 0\n")
    site = fn.division_sites[1]
    enc = b.encoder(fn)
    blk = fn.blocks[site.block_id]
    idx = next(i for i, ins in enumerate(blk.instructions) if ins.result == site.result_id)
    out = b.check(enc, [enc.prefix(site.block_id, idx), enc.int_term(blk.instructions[idx].operands[1]) == 0])
    assert out.status is SolverStatus.SAT and out.model["a"] == -1


def test_havoc_marks_query_inexact():
    b = Z3Backend(1000)
    enc, cs = query(b, fn_of("def f(a):\n    log(a)\n    return 100 / g(a)\n"))
    out = b.check(enc, cs)
    assert out.status is SolverStatus.SAT and not out.exact and out.havoc_values


def test_unavailable_when_z3_cannot_be_imported(monkeypatch):
    monkeypatch.setitem(sys.modules, "z3", None)  # makes `import z3` raise ImportError
    cap = probe_z3()
    assert cap.available is False and "z3" in cap.reason
