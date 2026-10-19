"""Contract scope: per-parameter input profiles (DECISIONS I-146), backward compatibility of the single-profile form."""

import copy

import pytest

from invariantguard.contracts.dsl import Rejection
from invariantguard.contracts.model import validate_revision
from invariantguard.contracts.profiles import PROFILES, ProfileAssignment, admit_inputs, domain_hints, profile_label
from invariantguard.models.schema import validation_errors

from .test_dsl import DOC, SNAP, SRC

TAKE = {"demo/take.py": b"def take(xs, n):\n    return xs[:n]\n\n\nclass T:\n    @classmethod\n    def first(cls, xs, *, n):\n        return xs[:n]\n"}
N = {"kind": "arg", "name": "n"}
XS = {"kind": "arg", "name": "xs"}
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
