"""Bounded input profiles (M3 T30/T36): admission, determinism, hints and domain-preserving shrinking."""

import random

import pytest

from invariantguard.contracts.dsl import EvalEnv, check_expression, evaluate
from invariantguard.contracts.profiles import PROFILES, admit_inputs, check_value, complexity, domain_hints, shrink_candidates

NUM = PROFILES["bounded_builtin_numeric_list_v1"]
INTS = PROFILES["bounded_builtin_int_list_v1"]
XS = {"kind": "arg", "name": "xs"}
POSITIVE_SUM = {"kind": "and", "args": [
    {"kind": "gt", "left": {"kind": "len", "value": XS}, "right": {"kind": "literal", "value": 0}},
    {"kind": "all_between", "value": XS, "lower": 0.0, "upper": 100.0},
    {"kind": "gt", "left": {"kind": "sum", "value": XS}, "right": {"kind": "literal", "value": 0.0}},
]}


def holds(pre):
    canon = check_expression(pre, {"xs": "num_list"}, "precondition", "pre").canonical

    def f(args):
        r = evaluate(canon, EvalEnv(args=args))
        return {"TRUE": True, "FALSE": False}.get(r.status), r.reason

    return canon, f


@pytest.mark.parametrize("value,ok", [([1.0], True), ([], False), ([True], False), ([float("nan")], False), ([1e7], False), ((1.0,), False),
                                      ([1, 2.5], True), (["1"], False)])
def test_numeric_list_profile_membership(value, ok):
    assert (check_value(NUM, value) is None) is ok


def test_admission_is_deterministic_and_respects_precondition():
    canon, f = holds(POSITIVE_SUM)
    hints = domain_hints(canon, ["xs"])
    assert hints["xs"].elem_lo == 0.0 and hints["xs"].elem_hi == 100.0 and hints["xs"].len_lo == 1
    a1 = admit_inputs(NUM, ["xs"], f, hints, seed=7, limit=40, examples=[{"xs": [1.0, 1.0]}, {"xs": [0.0, 0.0]}])
    a2 = admit_inputs(NUM, ["xs"], f, hints, seed=7, limit=40, examples=[{"xs": [1.0, 1.0]}, {"xs": [0.0, 0.0]}])
    assert [x for x, _ in a1.inputs] == [x for x, _ in a2.inputs] and len(a1.inputs) == 40
    assert a1.inputs[0] == ({"xs": [1.0, 1.0]}, "example")
    # The all-zero example is outside the positive-sum domain: excluded, never probed.
    assert ({"xs": [0.0, 0.0]}, "example", "precondition is false") in a1.excluded
    for args, _ in a1.inputs:
        assert f(args)[0] is True and check_value(NUM, args["xs"]) is None
    a3 = admit_inputs(NUM, ["xs"], f, hints, seed=8, limit=40)
    assert [x for x, _ in a3.inputs] != [x for x, _ in a1.inputs]


def test_tiny_domains_terminate():
    pre = {"kind": "all_between", "value": XS, "lower": 0.0, "upper": 0.0}
    canon, f = holds(pre)
    adm = admit_inputs(NUM, ["xs"], f, domain_hints(canon, ["xs"]), seed=1, limit=48, max_attempts=200)
    assert adm.inputs and all(set(a["xs"]) <= {0, 0.0} for a, _ in adm.inputs)


def test_shrink_candidates_are_strictly_simpler_and_in_profile():
    for v in ([37.25, 5.5, 88.0], [1.0, 1.0], [100.0], [3, -7]):
        for c in shrink_candidates(NUM, v):
            assert check_value(NUM, c) is None
            assert complexity(c) < complexity(v)


@pytest.mark.parametrize("seed", range(20))
def test_shrinking_never_leaves_the_contract_domain(seed):
    """Simulate the checker's shrink loop with an arbitrary 'still failing' oracle: every
    accepted step satisfies the profile and the full precondition (docs/06: never erase
    relevant preconditions while shrinking)."""
    pre = {"kind": "and", "args": [POSITIVE_SUM, {"kind": "le", "left": {"kind": "literal", "value": 2}, "right": {"kind": "len", "value": XS}},
                                   {"kind": "all_between", "value": XS, "lower": 1.0, "upper": 50.0}]}
    canon, f = holds(pre)
    rng = random.Random(seed)
    adm = admit_inputs(NUM, ["xs"], f, domain_hints(canon, ["xs"]), seed=seed, limit=5)
    cur = adm.inputs[-1][0]["xs"]
    steps = 0
    while steps < 50:
        cands = [c for c in shrink_candidates(NUM, cur) if f({"xs": c})[0] is True]
        if not cands:
            break
        cur = rng.choice(cands)  # any oracle choice
        steps += 1
        assert f({"xs": cur})[0] is True and len(cur) >= 2 and all(1.0 <= x <= 50.0 for x in cur)
    assert complexity(cur) <= complexity(adm.inputs[-1][0]["xs"])


def test_int_list_profile_and_hints_from_length_comparisons():
    pre = {"kind": "and", "args": [{"kind": "le", "left": {"kind": "literal", "value": 2}, "right": {"kind": "len", "value": XS}},
                                   {"kind": "lt", "left": {"kind": "len", "value": XS}, "right": {"kind": "literal", "value": 5}}]}
    canon, f = holds(pre)
    h = domain_hints(canon, ["xs"])
    assert (h["xs"].len_lo, h["xs"].len_hi) == (2, 4)
    adm = admit_inputs(INTS, ["xs"], f, h, seed=3, limit=20)
    assert all(2 <= len(a["xs"]) <= 4 and all(type(x) is int for x in a["xs"]) for a, _ in adm.inputs)
