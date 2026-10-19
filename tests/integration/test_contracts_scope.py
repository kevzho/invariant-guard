"""Contract scope end to end with the explicit *unisolated* backend: static and class method
targets, per-parameter input profiles, str and bool profiles (DECISIONS I-145..I-149)."""

import json

from ..conftest import run_cli
from .contract_helpers import contract_result, declare, exec_config, repo, side_evidence, verify_exec, write

TRUE = {"kind": "literal", "value": True}
XS = {"kind": "arg", "name": "xs"}
R = {"kind": "result"}

GEO_BASE = '''class Geo:
    FACTOR = 2

    @staticmethod
    def sorted_copy(xs):
        return sorted(xs)

    @classmethod
    def scaled(cls, xs):
        return [x * cls.FACTOR for x in xs]

    def inst(self, xs):
        return xs
'''
GEO_PATCH = GEO_BASE.replace("        return sorted(xs)\n", "        xs.sort()\n        return xs\n")


TAKE_BASE = "def take(xs, n):\n    return xs[:n]\n"
TAKE_PATCH = "def take(xs, n):\n    return xs[:n + 1]\n"
N = {"kind": "arg", "name": "n"}
PER = {"xs": "bounded_builtin_int_list_v1", "n": "bounded_builtin_ints_v1"}
TAKE_CONTRACT = {"contract_id": "take.len", "scope": "demo/take.py::take",
                 "precondition": {"kind": "and", "args": [{"kind": "le", "left": {"kind": "literal", "value": 0}, "right": N},
                                                          {"kind": "le", "left": N, "right": {"kind": "literal", "value": 16}}]},
                 "predicate": {"kind": "le", "left": {"kind": "len", "value": R}, "right": N}, "input_profile": PER,
                 "examples": [{"xs": [5, 6, 7], "n": 2}]}


def _declare_raw(root, tmp_path, contract):
    f = tmp_path / f"{contract['contract_id']}.json"
    f.write_text(json.dumps(contract), encoding="utf-8")
    return run_cli(["contracts", "declare", "--file", str(f), "--reason", "maintainer intent", "--path", str(root)])


def test_static_method_regression_and_class_method_tested(tmp_path):
    root, base = repo(tmp_path, {"demo/geo.py": GEO_BASE}, exec_config())
    declare(root, {"contract_id": "geo.sorted.pure", "scope": "demo/geo.py::Geo.sorted_copy", "precondition": TRUE,
                   "predicate": {"kind": "unchanged_argument", "name": "xs"}, "input_profile": "bounded_builtin_int_list_v1",
                   "examples": [{"xs": [2, 1]}]}, tmp_path)
    declare(root, {"contract_id": "geo.scaled.len", "scope": "demo/geo.py::Geo.scaled", "precondition": TRUE,
                   "predicate": {"kind": "eq", "left": {"kind": "len", "value": R}, "right": {"kind": "len", "value": XS}},
                   "input_profile": "bounded_builtin_int_list_v1"}, tmp_path)
    write(root, {"demo/geo.py": GEO_PATCH})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "geo.sorted.pure")
    assert (c["property_result"], c["patch_relation"]) == ("OBSERVED_REGRESSION", "INTRODUCED")
    assert c["original_witness"] == {"xs": [2, 1]} and code == 1
    assert all((x["base"], x["candidate_run_1"], x["candidate_run_2"]) == ("PASS", "FAIL", "FAIL") for x in c["confirmations"])
    s = contract_result(r, "geo.scaled.len")
    assert (s["property_result"], s["scope_validity"]) == ("TESTED", "EXECUTED") and "not a proof" in s["quantifier"]
    ev = side_evidence(r, s, "candidate")
    assert ev["execution"]["probes"] and all(p["verdict"] == "PASS" for p in ev["execution"]["probes"])


def test_instance_method_is_rejected_at_declaration(tmp_path):
    root, _ = repo(tmp_path, {"demo/geo.py": GEO_BASE}, exec_config())
    code, out, err = _declare_raw(root, tmp_path, {"contract_id": "geo.inst", "scope": "demo/geo.py::Geo.inst", "precondition": TRUE,
                                                    "predicate": {"kind": "eq", "left": R, "right": XS}, "input_profile": "bounded_builtin_int_list_v1"})
    assert code != 0 and "UNSUPPORTED_TARGET" in (out + err) and "instance methods" in (out + err)


def test_runtime_replacement_of_a_static_method_is_a_target_error(tmp_path):
    # Statically a staticmethod; at import time the module swaps the class attribute. The runner checks the raw
    # descriptor and the wrapped function's qualified name, so it never calls the replacement.
    src = GEO_BASE + "\n\ndef _other(xs):\n    return xs\n\n\nsetattr(Geo, 'sorted_copy', staticmethod(_other))\n"
    root, base = repo(tmp_path, {"demo/geo.py": GEO_BASE}, exec_config(require_coverage=False))
    declare(root, {"contract_id": "geo.sorted.pure", "scope": "demo/geo.py::Geo.sorted_copy", "precondition": TRUE,
                   "predicate": {"kind": "unchanged_argument", "name": "xs"}, "input_profile": "bounded_builtin_int_list_v1"}, tmp_path)
    write(root, {"demo/geo.py": src})
    _, r, _ = verify_exec(root, base)
    c = contract_result(r, "geo.sorted.pure")
    assert c["property_result"] == "UNKNOWN" and c["status"] == "CHECKED"
    assert any("not the analyzed def" in x for x in c["reasons"]), c["reasons"]


def test_per_parameter_profiles_regression_with_shrunk_witness(tmp_path):
    root, base = repo(tmp_path, {"demo/take.py": TAKE_BASE}, exec_config())
    declare(root, TAKE_CONTRACT, tmp_path)
    write(root, {"demo/take.py": TAKE_PATCH})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "take.len")
    assert (c["property_result"], c["patch_relation"], code) == ("OBSERVED_REGRESSION", "INTRODUCED", 1)
    assert c["input_profile"] == PER and c["original_witness"] == {"xs": [5, 6, 7], "n": 2}
    w = c["witness"]
    assert type(w["n"]) is int and 0 <= w["n"] <= 16 and len(w["xs"]) == w["n"] + 1 and all(type(x) is int for x in w["xs"])
    assert w == {"xs": [0], "n": 0}  # shrunk within each parameter's own profile and the precondition
    ev = side_evidence(r, c, "candidate")
    assert "profiles {xs: bounded_builtin_int_list_v1, n: bounded_builtin_ints_v1}" in ev["domain"]
    code, out, err = run_cli(["contracts", "list", "--path", str(root), "--format", "human"])
    assert code == 0 and "({n: bounded_builtin_ints_v1, xs: bounded_builtin_int_list_v1})" in out, (out, err)  # stored with sorted keys


def test_run_profile_must_allow_every_parameter_profile(tmp_path):
    cfg = exec_config(require_coverage=False, extra_profile='input_profiles = ["bounded_builtin_int_list_v1"]')
    root, base = repo(tmp_path, {"demo/take.py": TAKE_BASE}, cfg)
    declare(root, TAKE_CONTRACT, tmp_path)
    write(root, {"demo/take.py": TAKE_PATCH})
    _, r, _ = verify_exec(root, base)
    c = contract_result(r, "take.len")
    assert c["status"] == "NOT_ALLOWED" and "bounded_builtin_ints_v1" in c["reasons"][0]
