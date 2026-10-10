"""Phase 4: differential replay of a patch (engine + comparison), on the unisolated backend."""

from __future__ import annotations

import pytest

from invariantguard.execution.profiles import parse_profile
from invariantguard.execution.runner import UnisolatedBackend
from invariantguard.replay import compare
from invariantguard.replay.engine import replay_patch, same_module_callers

BASE = b'''import random, uuid

TABLE = {"K": 1024}


def clamp(x, lo=0, hi=10):
    return max(lo, min(hi, x))


def chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def total(xs):
    s = 0
    for x in xs:
        s += x
    return s


def token(n):
    return uuid.uuid4().hex[:n]


def _norm(s):
    return s.strip()


def greet(name):
    return "hi " + _norm(name)


def human(n):
    for k, v in TABLE.items():
        if n >= v:
            return f"{n // v}{k}"
    return str(n)


def untested(a):
    return a


class Box:
    def __init__(self, items=()):
        self.items = list(items)

    def pop(self):
        return self.items.pop()
'''

CAND = BASE.replace(b"return max(lo, min(hi, x))", b"return max(lo, min(hi, x)) if x is not None else lo") \
    .replace(b"return [xs[i:i + n] for i in range(0, len(xs), n)]", b"return [xs[i:i + n] for i in range(0, len(xs) - 1, n)]") \
    .replace(b"    s = 0\n    for x in xs:\n        s += x\n    return s", b"    return sum(xs)") \
    .replace(b"return uuid.uuid4().hex[:n]", b"return uuid.uuid4().hex[:n].upper()") \
    .replace(b"return s.strip()", b"return s.strip().title()") \
    .replace(b'TABLE = {"K": 1024}', b'TABLE = {"M": 1024 ** 2, "K": 1024}') \
    .replace(b"return a\n", b"return a + 1\n") \
    .replace(b"self.items = list(items)", b"self.items = list(items)[::-1]")

TESTS = b'''from pkg.mod import clamp, chunks, total, token, greet, human, Box

def test_all():
    assert clamp(5) == 5 and clamp(20) == 10
    assert chunks([1, 2, 3, 4, 5], 2)
    assert total([1, 2, 3]) == 6
    assert len(token(4)) == 4
    assert greet(" bob ") == "hi bob"
    assert human(4096)
    assert human(5 * 1024 ** 2)
    b = Box([1, 2, 3])
    assert b.pop() == 3
'''
NEW_TEST = b'''from pkg.mod import clamp

def test_none():
    assert clamp(None) == 0
'''

BASE_FILES = {"pkg/__init__.py": b"", "pkg/mod.py": BASE, "tests/test_mod.py": TESTS}
CAND_FILES = {"pkg/__init__.py": b"", "pkg/mod.py": CAND, "tests/test_mod.py": TESTS, "tests/test_new.py": NEW_TEST}
CHANGED = [(f"pkg/mod.py::{q}", "modified") for q in ("clamp", "chunks", "total", "token", "_norm", "untested", "Box.__init__")] + \
    [("pkg/mod.py::human", "data")]


@pytest.fixture(scope="module")
def result():
    b = UnisolatedBackend(parse_profile("u", {"backend": "unisolated", "allowed_targets": ["pkg/*.py::*"], "probe_wall_seconds": 2.0}))
    return replay_patch(b, BASE_FILES, CAND_FILES, CHANGED, generate=False)


def _clusters(res, sym):
    return [c for c in res.clusters if c.symbol == f"pkg/mod.py::{sym}"]


def test_intended_change_is_acknowledged_by_the_patch_test(result):
    (c,) = [c for c in _clusters(result, "clamp") if c.cls == "removed_exception"]
    assert c.acknowledged and c.witness.call == "clamp(None)"
    assert c.witness.base.startswith("raised TypeError") and c.witness.patch == "returned 0"


def test_unintended_change_is_reported_with_a_replayable_witness(result):
    (c,) = _clusters(result, "chunks")
    assert c.cls == "different_return" and not c.acknowledged
    assert c.witness.base == "returned [[1, 2], [3, 4], [5]]" and c.witness.patch == "returned [[1, 2], [3, 4]]"


def test_refactor_without_behavior_change_is_silent(result):
    assert _clusters(result, "total") == []
    (tp,) = [t for t in result.targets if t.target.qualname == "total"]
    assert tp.classes.get("same", 0) >= 1 and tp.executed


def test_nondeterminism_is_never_a_difference(result):
    assert _clusters(result, "token") == []
    (tp,) = [t for t in result.targets if t.target.qualname == "token"]
    assert tp.classes.get("nondeterministic", 0) >= 1


def test_private_helper_is_observed_through_its_caller(result):
    (c,) = _clusters(result, "_norm")
    assert c.relation == "caller" and c.target == "pkg/mod.py::greet" and c.witness.patch == "returned 'hi Bob'"


def test_module_data_change_reaches_its_reader(result):
    (c,) = _clusters(result, "human")
    assert c.relation == "data_reader" and "5M" in c.witness.patch and "5120K" in c.witness.base


def test_constructor_change_is_replayed_through_the_class(result):
    cs = _clusters(result, "Box.__init__")
    assert cs and cs[0].relation == "constructor"
    assert any("Box" in c.target for c in cs)


def test_untested_public_symbol_is_a_required_gap(result):
    gaps = {g["symbol_id"]: g for g in result.coverage()["required_gaps"]}
    assert "pkg/mod.py::untested" in gaps and "no input" in gaps["pkg/mod.py::untested"]["reason"]
    assert "pkg/mod.py::clamp" not in gaps


def test_every_difference_was_confirmed_in_fresh_runs(result):
    assert result.unconfirmed == 0 and result.runs["confirm_base"] and len(result.runs) >= 6


def test_classification_rules():
    ok = lambda v: {"status": "ok", "result": {"kind": "returned", "value": v}}  # noqa: E731
    raised = lambda t, m: {"status": "ok", "result": {"kind": "raised", "exception": {"type": t, "message": m}}}  # noqa: E731
    i = lambda n: {"t": "int", "v": str(n)}  # noqa: E731
    assert compare.classify(ok(i(1)), ok(i(1)), ok(i(1))) == "same"
    assert compare.classify(ok(i(1)), ok(i(2)), ok(i(1))) == "nondeterministic"
    assert compare.classify(ok(i(1)), ok(i(1)), ok(i(2))) == "different_return"
    assert compare.classify(raised("E", "a"), raised("E", "a"), raised("E", "b")) == "exception_message"
    assert compare.classify(raised("E", "a"), raised("E", "a"), raised("F", "a")) == "different_exception"
    it = lambda typ, items: {"t": "iter", "type": typ, "v": [i(x) for x in items], "truncated": False}  # noqa: E731
    assert compare.classify(ok(it("map", [1])), ok(it("map", [1])), ok(it("generator", [1]))) == "internal_state_only"  # not reported
    obj = lambda state, items: {"t": "object", "type": "m.C", "state": state, "iter": [i(x) for x in items], "iter_truncated": False}  # noqa: E731
    a, b = obj([["slots", i(1)]], [1, 2]), obj([["slots", i(9)]], [1, 2])
    assert compare.classify(ok(a), ok(a), ok(b)) == "internal_state_only"  # same iteration protocol
    priv = lambda v: {"t": "object", "type": "m.D", "state": [["_cache", i(v)], ["n", i(1)]]}  # noqa: E731
    assert compare.classify(ok(priv(1)), ok(priv(1)), ok(priv(2))) == "internal_state_only"
    assert compare.classify({"status": "timeout"}, {"status": "timeout"}, ok(i(1))) == "timeout"
    assert compare.classify(ok(i(1)), {"status": "timeout"}, ok(i(1))) == "unconfirmed"
    assert compare.classify({"status": "input_error"}, {"status": "input_error"}, {"status": "input_error"}) == "not_executable"


def test_same_module_callers_prefer_public():
    assert same_module_callers(CAND, "_norm") == ["greet"]
    assert same_module_callers(CAND, "Box.__init__") == []


def test_only_readers_of_changed_data_bindings_become_data_targets():
    from invariantguard.replay.engine import changed_for_replay

    syms = {"m.py::C.eq", "m.py::C.get", "m.py::h", "m.py::g", "m.py::f"}
    reasons = {"m.py::C.eq": "reads m.py::C (binding changed)",  # the class itself changed (code): not data
               "m.py::h": "reads m.py::TABLE (binding changed)",  # module data: a target
               "m.py::g": "reads m.py::TABLE (transitively)",  # transitive readers are reached through h
               "m.py::f": "reads m.py::f (binding changed)"}  # a changed function is its own target
    assert changed_for_replay(["m.py::f"], reasons, syms) == [("m.py::f", "modified"), ("m.py::h", "data")]


def test_a_target_shared_by_two_changed_symbols_is_generated_once():
    from invariantguard.replay.engine import plan_targets

    src = b"def _a(x):\n    return x\n\ndef _b(x):\n    return x\n\ndef top(x: int) -> int:\n    return _a(x) + _b(x)\n"
    plans, _ = plan_targets([("m.py::_a", "modified"), ("m.py::_b", "modified")], {"m.py": src}, [], 3)
    assert [p.target.qualname for p in plans].count("top") == 2  # one caller plan per changed helper
    b = UnisolatedBackend(parse_profile("u", {"backend": "unisolated", "allowed_targets": ["*::*"], "site_packages": True}))
    res = replay_patch(b, {"m.py": src}, {"m.py": src.replace(b"return x\n\ndef _b", b"return x + 1\n\ndef _b")},
                       [("m.py::_a", "modified"), ("m.py::_b", "modified")])
    assert not any("unique" in e for e in res.errors)  # used to raise: generation job ids must be unique
