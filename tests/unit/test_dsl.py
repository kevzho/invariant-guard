"""Typed property DSL v1 (M3 T30): validation, canonicalization, vacuity, admissible witnesses, evaluation."""

import copy
import json
import math

import pytest

from invariantguard.contracts.dsl import EvalEnv, Rejection, approx_eq, canonicalize, check_expression, evaluate
from invariantguard.contracts.model import load_document, semantic_digest, validate_revision
from invariantguard.contracts.values import Opaque

SNAP = "sha256:" + "0" * 64
SRC = {
    "demo/math_ops.py": b"def normalize(xs):\n    total = sum(xs)\n    return [x / total for x in xs]\n\n\n"
                        b"def pair(a, b):\n    return a + b\n\n\nclass K:\n    def m(self, xs):\n        return xs\n\n\n"
                        b"@staticmethod\ndef deco(xs):\n    return xs\n\n\ndef star(*xs):\n    return xs\n\n\nasync def coro(xs):\n    return xs\n",
}
XS = {"kind": "arg", "name": "xs"}
LIT0 = {"kind": "literal", "value": 0}
PRE = {"kind": "and", "args": [
    {"kind": "gt", "left": {"kind": "len", "value": XS}, "right": LIT0},
    {"kind": "all_between", "value": XS, "lower": 0.0, "upper": 100.0},
    {"kind": "gt", "left": {"kind": "sum", "value": XS}, "right": {"kind": "literal", "value": 0.0}},
]}
PRED = {"kind": "approx_eq", "left": {"kind": "sum", "value": {"kind": "result"}}, "right": {"kind": "literal", "value": 1.0},
        "atol": 1e-9, "rtol": 1e-9, "require_finite": True}
DOC = {"schema_version": "1.0", "contract_id": "normalize.sum.v1", "revision": 1, "scope": "demo/math_ops.py::normalize", "authority": "APPROVED",
       "lifecycle_state": "REVIEWED", "precondition": PRE, "predicate": PRED, "input_profile": "bounded_builtin_numeric_list_v1",
       "provenance": [{"kind": "maintainer_approval", "reference": "contract-review-1"}], "supersedes": None, "reason": "docs/06 example",
       "created_at": "2026-10-04T12:00:00Z"}


def doc_with(**kw):
    d = copy.deepcopy(DOC)
    d.update(kw)
    return d


def test_docs06_example_validates_and_gt_is_canonicalized():
    tc = validate_revision(DOC, SRC, SNAP)
    assert tc.target.module == "demo.math_ops" and tc.target.params == ("xs",)
    first = tc.precondition["args"][0]
    assert first == {"kind": "lt", "left": LIT0, "right": {"kind": "len", "value": XS}}  # gt(len, 0) -> lt(0, len)
    assert "gt" not in json.dumps(tc.precondition)
    assert tc.admissible_witness is not None and tc.precondition_holds(tc.admissible_witness) == (True, "holds")
    # The same contract written with lt instead of gt has the same semantic digest (evidence binds to meaning).
    lt_form = doc_with(precondition=canonicalize(PRE))
    assert validate_revision(lt_form, SRC, SNAP).semantic_digest == tc.semantic_digest
    # ... while any semantic change (tolerance) is a different digest.
    p2 = dict(PRED, atol=1e-6)
    assert semantic_digest(DOC["scope"], PRE, p2, DOC["input_profile"], []) != tc.semantic_digest


def _deep(n):
    node = {"kind": "literal", "value": True}
    for _ in range(n):
        node = {"kind": "not", "arg": node}
    return node


REJECTIONS = [
    ("unknown node call", dict(predicate={"kind": "call", "name": "os.system", "args": []}), "UNKNOWN_NODE"),
    ("unknown node eval", dict(predicate={"kind": "eval", "code": "__import__('os').system('true')"}), "UNKNOWN_NODE"),
    ("ge is not a v1 node", dict(predicate={"kind": "ge", "left": {"kind": "result"}, "right": LIT0}), "UNKNOWN_NODE"),
    ("smuggled field", dict(predicate=dict(PRED, __class__="os")), "MALFORMED_NODE"),
    ("string literal", dict(predicate={"kind": "eq", "left": {"kind": "result"}, "right": {"kind": "literal", "value": "__import__('os')"}}), "TYPE_ERROR"),
    ("huge literal", dict(predicate={"kind": "eq", "left": {"kind": "result"}, "right": {"kind": "literal", "value": 10 ** 30}}), "TYPE_ERROR"),
    ("nan literal", dict(predicate={"kind": "eq", "left": {"kind": "result"}, "right": {"kind": "literal", "value": float("nan")}}), "TYPE_ERROR"),
    ("bool is not a number", dict(predicate={"kind": "lt", "left": {"kind": "literal", "value": True}, "right": {"kind": "result"}}), "TYPE_ERROR"),
    ("unknown parameter", dict(predicate={"kind": "unchanged_argument", "name": "ys"}), "UNKNOWN_SYMBOL"),
    ("injection in a name", dict(predicate={"kind": "unchanged_argument", "name": "xs; import os"}), "MALFORMED_NODE"),
    ("node is not an object", dict(predicate=["kind", "result"]), "MALFORMED_NODE"),
    ("too deep", dict(predicate={"kind": "and", "args": [{"kind": "eq", "left": {"kind": "result"}, "right": LIT0}, _deep(40)]}), "BUDGET_EXCEEDED"),
    ("pathological depth", dict(predicate=_deep(300)), "BUDGET_EXCEEDED"),
    ("pathological depth and size", dict(predicate=_deep(5000)), "OVERSIZED"),
    ("too many nodes", dict(predicate={"kind": "and", "args": [{"kind": "eq", "left": {"kind": "result"}, "right": LIT0}] * 30}), "BUDGET_EXCEEDED"),
    ("too many operands", dict(predicate={"kind": "or", "args": [{"kind": "eq", "left": {"kind": "result"}, "right": LIT0}] * 33}), "MALFORMED_NODE"),
    ("approx without NaN policy", dict(predicate={k: v for k, v in PRED.items() if k != "require_finite"}), "MALFORMED_NODE"),
    ("negative tolerance", dict(predicate=dict(PRED, atol=-1.0)), "TYPE_ERROR"),
    ("contradictory bounds", dict(precondition={"kind": "all_between", "value": XS, "lower": 5.0, "upper": 1.0}), "CONTRADICTORY_DOMAIN"),
    ("repeat budget", dict(predicate={"kind": "repeatability", "repeats": 1000, "seed": 0, "comparison": "exact"}), "BUDGET_EXCEEDED"),
    ("approx repeat", dict(predicate={"kind": "repeatability", "repeats": 3, "seed": 0, "comparison": "approx"}), "UNSUPPORTED_NODE"),
    ("two repeat nodes", dict(predicate={"kind": "and", "args": [{"kind": "repeatability", "repeats": 3, "seed": 0, "comparison": "exact"}] * 2}), "UNSUPPORTED_NODE"),
    ("result in precondition", dict(precondition={"kind": "eq", "left": {"kind": "result"}, "right": LIT0}), "TYPE_ERROR"),
    ("relation in precondition", dict(precondition={"kind": "unchanged_argument", "name": "xs"}), "TYPE_ERROR"),
    ("non-boolean predicate", dict(predicate={"kind": "sum", "value": {"kind": "result"}}), "TYPE_ERROR"),
    ("vacuous predicate", dict(predicate={"kind": "le", "left": {"kind": "len", "value": XS}, "right": {"kind": "literal", "value": 100}}), "VACUOUS"),
    ("constant predicate", dict(predicate={"kind": "literal", "value": True}), "VACUOUS"),
    ("unsatisfiable precondition", dict(precondition={"kind": "and", "args": [PRE, {"kind": "lt", "left": {"kind": "sum", "value": XS}, "right": LIT0}]}), "INVALID_CONTRACT"),
    ("unknown profile", dict(input_profile="arbitrary_objects_v1"), "UNKNOWN_PROFILE"),
    ("missing domain", dict(input_profile=""), "MISSING_DOMAIN"),
    ("missing provenance", dict(provenance=[]), "MISSING_PROVENANCE"),
    ("model-claimed provenance kind", dict(provenance=[{"kind": "llm_says_so", "reference": "x"}]), "MISSING_PROVENANCE"),
    ("unknown symbol", dict(scope="demo/math_ops.py::nope"), "UNKNOWN_SYMBOL"),
    ("file not in snapshot", dict(scope="demo/other.py::normalize"), "UNKNOWN_SYMBOL"),
    ("method target", dict(scope="demo/math_ops.py::K.m"), "UNSUPPORTED_TARGET"),
    ("decorated target", dict(scope="demo/math_ops.py::deco"), "UNSUPPORTED_TARGET"),
    ("star-args target", dict(scope="demo/math_ops.py::star"), "UNSUPPORTED_TARGET"),
    ("async target", dict(scope="demo/math_ops.py::coro"), "UNSUPPORTED_TARGET"),
    ("unknown top-level field", dict(code="print(1)"), "MALFORMED_CONTRACT"),
    ("schema version", dict(schema_version="9.9"), "SCHEMA_VERSION"),
    ("example outside profile", dict(examples=[{"xs": [True]}]), "MALFORMED_CONTRACT"),
    ("example with wrong params", dict(examples=[{"ys": [1.0]}]), "MALFORMED_CONTRACT"),
]


@pytest.mark.parametrize("name,changes,code", REJECTIONS, ids=[r[0] for r in REJECTIONS])
def test_invalid_vacuous_and_malicious_contracts_are_rejected(name, changes, code):
    with pytest.raises(Rejection) as exc:
        validate_revision(doc_with(**changes), SRC, SNAP)
    assert exc.value.code == code, exc.value


def test_oversized_and_non_finite_files_are_rejected():
    big = json.dumps(doc_with(reason="x" * 40000)).encode()
    with pytest.raises(Rejection) as exc:
        load_document(big)
    assert exc.value.code == "OVERSIZED"
    with pytest.raises(Rejection):
        load_document(b'{"a": NaN}')
    with pytest.raises(Rejection):
        load_document(b"[" * 100000 + b"]" * 100000)


def test_unsatisfiable_precondition_is_invalid_not_a_proof():
    with pytest.raises(Rejection) as exc:
        # Satisfiable as arithmetic, but disjoint from the input profile's bounds ([-1e6, 1e6]).
        validate_revision(doc_with(precondition={"kind": "all_between", "value": XS, "lower": 2e6, "upper": 3e6}), SRC, SNAP)
    assert exc.value.code == "INVALID_CONTRACT" and "never evidence that the contract holds" in exc.value.message


def ev(node, **env):
    return evaluate(check_expression(node, {"xs": "num_list", "a": "num", "b": "num"}, "predicate", "p").canonical, EvalEnv(**env))


R = {"kind": "result"}


@pytest.mark.parametrize("a,b,atol,rtol,finite,expected", [
    (1.0, 1.0 + 1e-12, 1e-9, 1e-9, True, True),
    (2 / 3, 1.0, 1e-9, 1e-9, True, False),
    (float("nan"), float("nan"), 1.0, 1.0, False, False),  # NaN is never approximately equal
    (float("inf"), float("inf"), 0.0, 0.0, False, True),
    (float("inf"), float("inf"), 0.0, 0.0, True, False),  # require_finite
    (float("inf"), -float("inf"), 1.0, 1.0, False, False),
    (10 ** 20, 10 ** 20 + 1, 0.0, 1e-9, True, True),  # big ints are finite
])
def test_approx_eq_semantics(a, b, atol, rtol, finite, expected):
    assert approx_eq(a, b, atol, rtol, finite) is expected


def test_eval_normalize_predicate_on_observed_values():
    node = PRED
    assert ev(node, args={"xs": [1.0, 1.0]}, result=[0.5, 0.5], has_result=True).status == "TRUE"
    r = ev(node, args={"xs": [1.0, 1.0]}, result=[1 / 3, 1 / 3], has_result=True)
    assert r.status == "FALSE" and any("approx_eq" in t for t in r.trace)


@pytest.mark.parametrize("result,status", [
    (None, "FALSE"),  # sum(None): the observed shape violates the contract
    ([True, False], "FALSE"),  # bool is not a number
    ("ab", "FALSE"),
    (Opaque("pkg.Obj", "unsupported type"), "UNKNOWN"),  # no supported serialization: undecided
    ([1.0, Opaque("pkg.Obj", "x")], "UNKNOWN"),
])
def test_runtime_type_mismatch_fails_but_unsupported_values_are_unknown(result, status):
    assert ev(PRED, args={"xs": [1.0]}, result=result, has_result=True).status == status


def test_strict_equality_categories():
    eq = {"kind": "eq", "left": R, "right": {"kind": "literal", "value": 1}}
    assert ev(eq, args={}, result=1.0, has_result=True).status == "TRUE"  # numbers by value
    assert ev(eq, args={}, result=True, has_result=True).status == "FALSE"  # True is not 1 here
    eqa = {"kind": "eq", "left": R, "right": {"kind": "arg", "name": "xs"}}
    assert ev(eqa, args={"xs": [1, 2]}, result=(1, 2), has_result=True).status == "FALSE"  # tuple is not a list
    assert ev(eqa, args={"xs": [1, 2]}, result=[1.0, 2.0], has_result=True).status == "TRUE"


def test_kleene_logic_with_unknown_operands():
    unk = {"kind": "eq", "left": R, "right": LIT0}
    false = {"kind": "lt", "left": {"kind": "literal", "value": 2}, "right": {"kind": "literal", "value": 1}}
    o = Opaque("x", "y")
    assert ev({"kind": "and", "args": [unk, false]}, args={}, result=o, has_result=True).status == "FALSE"
    assert ev({"kind": "or", "args": [unk, {"kind": "not", "arg": false}]}, args={}, result=o, has_result=True).status == "TRUE"
    assert ev({"kind": "and", "args": [unk, {"kind": "not", "arg": false}]}, args={}, result=o, has_result=True).status == "UNKNOWN"


def test_evaluation_budget_gives_unknown(monkeypatch):
    import invariantguard.contracts.dsl as dsl

    monkeypatch.setattr(dsl, "MAX_EVAL_OPS", 10)
    r = ev(PRED, args={"xs": [1.0]}, result=[0.01] * 100, has_result=True)
    assert r.status == "UNKNOWN" and "budget" in r.reason


def test_evaluator_never_calls_user_defined_methods():
    class Evil:
        def __eq__(self, other):  # pragma: no cover - must not run
            raise AssertionError("user code ran")

        __hash__ = object.__hash__

    eqa = {"kind": "eq", "left": R, "right": R}
    r = ev(eqa, args={}, result=Evil(), has_result=True)
    assert r.status == "UNKNOWN"


def test_unchanged_argument_and_repeatability_use_exact_encodings():
    from invariantguard.contracts.checker import _canon
    from invariantguard.contracts.values import encode

    node = {"kind": "unchanged_argument", "name": "xs"}
    same = {"xs": _canon(encode([2, 1]))}
    assert ev(node, args={"xs": [2, 1]}, before={"xs": [2, 1]}, after={"xs": [2, 1]}, before_enc=same, after_enc=same).status == "TRUE"
    assert ev(node, args={"xs": [2, 1]}, before={"xs": [2, 1]}, after={"xs": [1, 2]}, before_enc=same,
              after_enc={"xs": _canon(encode([1, 2]))}).status == "FALSE"
    # 1 -> 1.0 is a change of the argument even though 1 == 1.0
    assert ev(node, args={"xs": [1]}, before={"xs": [1]}, after={"xs": [1.0]}, before_enc={"xs": _canon(encode([1]))},
              after_enc={"xs": _canon(encode([1.0]))}).status == "FALSE"
    rep = {"kind": "repeatability", "repeats": 3, "seed": 0, "comparison": "exact"}
    assert ev(rep, args={}, repeat_encodings=["a", "a", "a"]).status == "TRUE"
    assert ev(rep, args={}, repeat_encodings=["a", "b", "a"]).status == "FALSE"
    assert ev(rep, args={}, repeat_encodings=["a", "a"]).status == "UNKNOWN"  # fewer observations than declared


def test_finite_float_semantics_of_sum_are_cpython_builtin():
    xs = [0.1] * 10
    r = ev({"kind": "eq", "left": {"kind": "sum", "value": R}, "right": {"kind": "literal", "value": 1.0}}, args={}, result=xs, has_result=True)
    assert r.status == ("TRUE" if sum(xs) == 1.0 else "FALSE")
    assert math.isclose(sum(xs), 1.0)
