"""Contract scope: per-parameter input profiles (DECISIONS I-146) with backward compatibility of the
single-profile form, str and bool profiles (I-147) and the DSL str operations (I-148)."""

import copy

import pytest

from invariantguard.contracts.dsl import EvalEnv, Rejection, check_expression, evaluate
from invariantguard.contracts.model import validate_revision
from invariantguard.contracts.profiles import (PROFILES, ProfileAssignment, admit_inputs, boundary, check_value, complexity, domain_hints,
                                               profile_label, shrink_candidates)
from invariantguard.contracts.vacuity import check_nonvacuous
from invariantguard.contracts.values import Opaque
from invariantguard.models.schema import validation_errors

from .test_dsl import DOC, SNAP, SRC

TAKE = {"demo/take.py": b"def take(xs, n):\n    return xs[:n]\n\n\nclass T:\n    @classmethod\n    def first(cls, xs, *, n):\n        return xs[:n]\n"}
N = {"kind": "arg", "name": "n"}
XS = {"kind": "arg", "name": "xs"}
R = {"kind": "result"}
PRE_N = {"kind": "and", "args": [{"kind": "le", "left": {"kind": "literal", "value": 0}, "right": N},
                                 {"kind": "le", "left": N, "right": {"kind": "literal", "value": 16}}]}
PRED_LEN = {"kind": "le", "left": {"kind": "len", "value": {"kind": "result"}}, "right": N}
PER = {"xs": "bounded_builtin_int_list_v1", "n": "bounded_builtin_ints_v1"}


def take_doc(**kw):
    d = copy.deepcopy(DOC)
    d.update(contract_id="take.len", scope="demo/take.py::take", precondition=PRE_N, predicate=PRED_LEN, input_profile=dict(PER))
    d.update(kw)
    return d


def test_single_profile_form_keeps_its_semantic_digest():
    # Recorded before per-parameter profiles existed: stored revisions keep their evidence binding.
    tc = validate_revision(DOC, SRC, SNAP)
    assert tc.semantic_digest == "sha256:07fe522c2b88103f036a637dcb22b3bb04cb02f47fdf8bb76b498b94bd102398"
    assert tc.input_profile.uniform and tc.input_profile.name == "bounded_builtin_numeric_list_v1"
    assert tc.domain_text == "inputs of profile bounded_builtin_numeric_list_v1 that satisfy the contract precondition"
    assert validation_errors("contract_revision", DOC) == []


def test_per_parameter_profiles_validate_type_and_admit():
    d = take_doc()
    assert validation_errors("contract_revision", d) == []
    tc = validate_revision(d, TAKE, SNAP)
    a = tc.input_profile
    assert isinstance(a, ProfileAssignment) and not a.uniform
    assert a.of("xs").kind == "int_list" and a.of("n").kind == "int" and a.names == sorted(PER.values())
    assert a.name == "{xs: bounded_builtin_int_list_v1, n: bounded_builtin_ints_v1}" == profile_label(PER)
    # Key order does not matter for meaning (canonical JSON sorts keys).
    assert tc.semantic_digest == validate_revision(take_doc(input_profile={"n": PER["n"], "xs": PER["xs"]}), TAKE, SNAP).semantic_digest
    params = list(tc.target.params)
    adm = admit_inputs(a, params, tc.precondition_holds, domain_hints(tc.precondition, params), seed=3, limit=40)
    assert len(adm.inputs) == 40
    for args, _ in adm.inputs:
        assert type(args["n"]) is int and 0 <= args["n"] <= 16 and type(args["xs"]) is list and all(type(x) is int for x in args["xs"])
    again = admit_inputs(a, params, tc.precondition_holds, domain_hints(tc.precondition, params), seed=3, limit=40)
    assert [x for x, _ in again.inputs] == [x for x, _ in adm.inputs]  # seeded, deterministic


def test_per_parameter_profile_on_a_class_method_with_keyword_only_parameter():
    tc = validate_revision(take_doc(scope="demo/take.py::T.first"), TAKE, SNAP)
    assert tc.target.params == ("xs", "n") and tc.target.keyword_only == ("n",)


def test_uniform_form_equals_per_parameter_admission_for_same_profile():
    tc = validate_revision(DOC, SRC, SNAP)
    params = list(tc.target.params)
    h = domain_hints(tc.precondition, params)
    a1 = admit_inputs(PROFILES["bounded_builtin_numeric_list_v1"], params, tc.precondition_holds, h, seed=11, limit=20)
    a2 = admit_inputs(tc.input_profile, params, tc.precondition_holds, h, seed=11, limit=20)
    assert a1.inputs == a2.inputs


@pytest.mark.parametrize("profile,code", [
    ({"xs": "bounded_builtin_int_list_v1"}, "MISSING_DOMAIN"),
    ({**PER, "m": "bounded_builtin_ints_v1"}, "UNKNOWN_SYMBOL"),
    ({**PER, "n": "arbitrary_objects_v1"}, "UNKNOWN_PROFILE"),
    ({}, "MISSING_DOMAIN"),
    ({**PER, "n": ""}, "MISSING_DOMAIN"),
    ({**PER, "n": 3}, "MISSING_DOMAIN"),
    ({"xs; import os": "bounded_builtin_ints_v1"}, "MALFORMED_CONTRACT"),
    (["bounded_builtin_ints_v1"], "MISSING_DOMAIN"),
])
def test_bad_per_parameter_profiles_are_rejected(profile, code):
    with pytest.raises(Rejection) as exc:
        validate_revision(take_doc(input_profile=profile), TAKE, SNAP)
    assert exc.value.code == code, exc.value


def test_per_parameter_profile_types_the_dsl():
    # n is a number under its own profile: len(n) is a static type error, not a runtime guess.
    with pytest.raises(Rejection) as exc:
        validate_revision(take_doc(precondition={"kind": "lt", "left": {"kind": "len", "value": N}, "right": {"kind": "literal", "value": 3}}), TAKE, SNAP)
    assert exc.value.code == "TYPE_ERROR"


def test_examples_are_checked_against_their_parameter_profile():
    validate_revision(take_doc(examples=[{"xs": [1, 2], "n": 1}]), TAKE, SNAP)
    with pytest.raises(Rejection) as exc:
        validate_revision(take_doc(examples=[{"xs": [1, 2], "n": [1]}]), TAKE, SNAP)
    assert exc.value.code == "MALFORMED_CONTRACT" and "bounded_builtin_ints_v1" in exc.value.message


# -- str and bool profiles (I-147) and DSL str (I-148) ----------------------------------------------------
ASCII = PROFILES["bounded_builtin_ascii_str_v1"]
UNI = PROFILES["bounded_builtin_unicode_str_v1"]
BOOL = PROFILES["bounded_builtin_bool_v1"]
S = {"kind": "arg", "name": "s"}
FLAG = {"kind": "arg", "name": "flag"}
ANY = {"kind": "literal", "value": True}


@pytest.mark.parametrize("profile,value,ok", [
    (ASCII, "", True), (ASCII, "Hello, World!", True), (ASCII, "~" * 32, True), (ASCII, "a" * 33, False), (ASCII, "café", False),
    (ASCII, "\t", False), (ASCII, "\x7f", False), (ASCII, b"x", False), (ASCII, ["a"], False), (ASCII, 1, False),
    (UNI, "é\x00\U0010FFFF\U0001F600", True), (UNI, "\ud800", False), (UNI, "\U0001F600" * 32, True), (UNI, "x" * 33, False),
    (BOOL, True, True), (BOOL, False, True), (BOOL, 1, False), (BOOL, 0, False), (BOOL, None, False), (BOOL, "True", False),
])
def test_str_and_bool_profile_membership(profile, value, ok):
    assert (check_value(profile, value) is None) is ok


def test_str_bool_dsl_types():
    assert (ASCII.dsl_type, UNI.dsl_type, BOOL.dsl_type) == ("str", "str", "bool")
    assert ASCII.to_json()["alphabet"] == "ascii_printable" and "alphabet" not in PROFILES["bounded_builtin_ints_v1"].to_json()


def _holds(pre, params):
    canon = check_expression(pre, params, "precondition", "pre").canonical

    def f(a):
        return {"TRUE": True, "FALSE": False}.get(evaluate(canon, EvalEnv(args=a)).status), ""

    return canon, f


@pytest.mark.parametrize("profile", [ASCII, UNI], ids=["ascii", "unicode"])
def test_str_generation_is_seeded_bounded_and_hinted(profile):
    pre = {"kind": "and", "args": [{"kind": "le", "left": {"kind": "literal", "value": 2}, "right": {"kind": "str_len", "value": S}},
                                   {"kind": "lt", "left": {"kind": "str_len", "value": S}, "right": {"kind": "literal", "value": 6}}]}
    canon, f = _holds(pre, {"s": "str"})
    h = domain_hints(canon, ["s"])
    assert (h["s"].len_lo, h["s"].len_hi) == (2, 5)
    a1 = admit_inputs(profile, ["s"], f, h, seed=5, limit=60)
    a2 = admit_inputs(profile, ["s"], f, h, seed=5, limit=60)
    assert a1.inputs == a2.inputs and len(a1.inputs) == 60
    vals = [a["s"] for a, _ in a1.inputs]
    assert all(type(v) is str and 2 <= len(v) <= 5 and check_value(profile, v) is None for v in vals)
    if profile is UNI:
        assert any(ord(c) > 0x7F for v in vals for c in v) and any(ord(c) > 0xFFFF for v in vals for c in v)
    b = boundary(profile, h["s"])
    assert b and all(2 <= len(v) <= 5 and check_value(profile, v) is None for v in b)
    assert boundary(profile, domain_hints(ANY, ["s"])["s"])[0] == ""  # the empty string is a boundary input


def test_unicode_boundary_includes_hard_cases():
    b = boundary(UNI, domain_hints(ANY, ["s"])["s"])
    for case in ("é", "\U0001F600", "\x00", "İ", "\U0010FFFF"):
        assert case in b
    assert all(check_value(ASCII, v) is None for v in boundary(ASCII, domain_hints(ANY, ["s"])["s"]))


@pytest.mark.parametrize("value", ["Hello, World!", "zz~", "\U0001F600é\x00", "a", ""])
def test_str_shrinking_is_strictly_simpler_and_stays_in_profile(value):
    for profile in (ASCII, UNI):
        if check_value(profile, value) is not None:
            continue
        cur, steps = value, 0
        while True:
            cands = shrink_candidates(profile, cur)
            assert all(check_value(profile, c) is None and complexity(c) < complexity(cur) for c in cands)
            if not cands:
                break
            cur, steps = cands[-1], steps + 1  # any choice terminates (well-founded measure)
            assert steps < 200
        assert cur == ""


def test_bool_generation_and_shrinking():
    adm = admit_inputs(BOOL, ["flag"], lambda a: (True, ""), domain_hints(ANY, ["flag"]), seed=1, limit=8)
    assert [a["flag"] for a, _ in adm.inputs] == [False, True]  # the whole domain; deduplication ends the random phase
    assert shrink_candidates(BOOL, True) == [False] and shrink_candidates(BOOL, False) == []


def _ev(node, params, **env):
    return evaluate(check_expression(node, params, "predicate", "p").canonical, EvalEnv(**env))


def test_str_dsl_evaluation():
    params = {"s": "str", "flag": "bool"}
    same_len = {"kind": "eq", "left": {"kind": "str_len", "value": R}, "right": {"kind": "str_len", "value": S}}
    assert _ev(same_len, params, args={"s": "ab", "flag": True}, result="XY", has_result=True).status == "TRUE"
    assert _ev(same_len, params, args={"s": "\U0001F600", "flag": True}, result="ab", has_result=True).status == "FALSE"  # code points
    r = _ev(same_len, params, args={"s": "ab", "flag": True}, result=["a", "b"], has_result=True)
    assert r.status == "FALSE" and "type mismatch" in r.reason  # a list is not a str
    assert _ev(same_len, params, args={"s": "ab", "flag": True}, result=Opaque("str", "lone surrogate"), has_result=True).status == "UNKNOWN"
    # len keeps its list-only meaning on dyn values: recorded contracts are not reinterpreted.
    r = _ev({"kind": "eq", "left": {"kind": "len", "value": R}, "right": {"kind": "literal", "value": 2}}, params, args={"s": "", "flag": False},
            result="ab", has_result=True)
    assert r.status == "FALSE" and "type mismatch" in r.reason
    is_str = {"kind": "is_type", "value": R, "type": "str"}
    assert _ev(is_str, params, args={"s": "", "flag": False}, result="", has_result=True).status == "TRUE"
    assert _ev(is_str, params, args={"s": "", "flag": False}, result=None, has_result=True).status == "FALSE"
    is_int = {"kind": "is_type", "value": R, "type": "int"}
    assert _ev(is_int, params, args={"s": "", "flag": False}, result=True, has_result=True).status == "FALSE"  # bool is not int
    lit = {"kind": "eq", "left": R, "right": {"kind": "literal", "value": "__import__('os').system('x')"}}
    assert _ev(lit, params, args={"s": "", "flag": False}, result="__import__('os').system('x')", has_result=True).status == "TRUE"  # inert data
    cond = {"kind": "or", "args": [{"kind": "not", "arg": FLAG}, {"kind": "eq", "left": R, "right": {"kind": "literal", "value": ""}}]}
    assert _ev(cond, params, args={"s": "q", "flag": False}, result="x", has_result=True).status == "TRUE"
    assert _ev(cond, params, args={"s": "q", "flag": True}, result="x", has_result=True).status == "FALSE"
    with pytest.raises(Rejection):
        check_expression({"kind": "eq", "left": S, "right": FLAG}, params, "predicate", "p")  # str vs bool
    with pytest.raises(Rejection):
        check_expression({"kind": "lt", "left": S, "right": {"kind": "literal", "value": "b"}}, params, "predicate", "p")  # no str ordering


STR_SRC = {"demo/text.py": b"def shout(s, loud):\n    return s.upper() if loud else s\n"}


def test_str_and_bool_contract_validates_with_examples_and_nonvacuity():
    d = take_doc(contract_id="shout.len", scope="demo/text.py::shout", precondition=ANY,
                 predicate={"kind": "eq", "left": {"kind": "str_len", "value": R}, "right": {"kind": "str_len", "value": S}},
                 input_profile={"s": "bounded_builtin_unicode_str_v1", "loud": "bounded_builtin_bool_v1"},
                 examples=[{"s": "straße", "loud": True}])
    assert validation_errors("contract_revision", d) == []
    tc = validate_revision(d, STR_SRC, SNAP)
    assert check_nonvacuous(tc)
    with pytest.raises(Rejection) as exc:
        validate_revision(dict(d, examples=[{"s": "x", "loud": 1}]), STR_SRC, SNAP)
    assert exc.value.code == "MALFORMED_CONTRACT" and "bool" in exc.value.message
