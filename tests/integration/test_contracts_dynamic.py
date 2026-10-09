"""Differential contract checks end to end (M3 T35-T38) with the explicit *unisolated* backend.

These tests exercise the checker logic quickly on the host (the profile sets
``allow_unisolated``; evidence is labelled unisolated). The same scenarios through real
containers are in ``test_docker_isolation.py``.
"""

import json
import math

import pytest

from invariantguard.contracts.model import validate_revision
from invariantguard.contracts.values import encode

from ..conftest import run_cli
from .contract_helpers import (
    POSITIVE_SUM,
    NORMALIZE_BASE,
    NORMALIZE_PATCH,
    SORTED_BASE,
    SORTED_PATCH,
    XS,
    contract_result,
    contracts_cli,
    declare,
    exec_config,
    normalize_contract,
    probe_with_input,
    repo,
    side_evidence,
    unchanged_contract,
    verify_exec,
    write,
)


def _fixture_b(tmp_path, cfg=None):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, cfg or exec_config())
    declare(root, normalize_contract(), tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    return root, base


def test_fixture_b_positive_sum_regression_blocks(tmp_path):
    root, base = _fixture_b(tmp_path)
    code, r, err = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert (c["status"], c["property_result"], c["scope_validity"], c["patch_relation"], c["severity"]) == \
        ("CHECKED", "OBSERVED_REGRESSION", "EXECUTED", "INTRODUCED", "ERROR")
    assert code == 1 and r["verdict"] == "BLOCK"
    assert any("blocking contract regression" in x for x in r["verdict_reasons"])
    # The docs/13 witness: the example xs=[1.0, 1.0] was observed first; the reported witness is minimized within the domain.
    assert c["original_witness"] == {"xs": [1.0, 1.0]}
    tc = validate_revision(json.loads((root / ".guard/contracts/normalize.sum.v1/r0001.json").read_text()), {"demo/math_ops.py": NORMALIZE_PATCH.encode()},
                           "sha256:" + "0" * 64)
    assert tc.precondition_holds(c["witness"])[0] is True
    for conf in c["confirmations"]:
        assert (conf["base"], conf["candidate_run_1"], conf["candidate_run_2"]) == ("PASS", "FAIL", "FAIL")
    # Same input in both snapshots: base [0.5, 0.5] sums to 1, candidate [1/3, 1/3] sums to 2/3.
    enc = {"xs": encode([1.0, 1.0])}
    b = probe_with_input(side_evidence(r, c, "base"), enc)
    k = probe_with_input(side_evidence(r, c, "candidate"), enc)
    assert b["verdict"] == "PASS" and b["result"] == [0.5, 0.5]
    assert k["verdict"] == "FAIL" and all(math.isclose(v, 1 / 3) for v in k["result"])
    # All-zero input is outside this contract: excluded, never probed, never a regression.
    assert {"input": {"xs": [0.0, 0.0]}, "origin": "example", "why": "precondition is false"} in c["excluded_examples"]
    assert all(p["input"] != {"xs": encode([0.0, 0.0])} for p in side_evidence(r, c, "candidate")["execution"]["probes"])
    # Honest labelling of the backend.
    assert r["capabilities"]["execution"] == "unisolated" and r["contracts"]["execution"]["isolated"] is False
    ev = side_evidence(r, c, "candidate")
    assert ev["kind"] == "EXECUTION" and ev["execution"]["backend"] == "unisolated" and ev["result_label"] == "OBSERVED_REGRESSION"
    assert any("UNISOLATED" in a for a in ev["assumptions"]) and ev["execution"]["run_profile_digest"].startswith("sha256:")
    f = next(f for f in r["findings"] if f["property_id"] == "contract.behavior")
    assert f["severity"] == "ERROR" and f["witness_validated"] and f["contract"]["contract_id"] == "normalize.sum.v1"
    assert r["analysis_plan"]["required_contracts"] == ["normalize.sum.v1"]


def test_inferred_contract_change_only_warns(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(require_coverage=False))
    from invariantguard.contracts.model import validate_revision as vr
    from invariantguard.contracts.registry import ContractRegistry
    from invariantguard.storage.store import Store

    doc = {"schema_version": "1.0", "revision": 1, "authority": "INFERRED", "lifecycle_state": "VALIDATED", "supersedes": None,
           "provenance": [{"kind": "template", "reference": "test"}], "reason": "proposed", "created_at": "2026-10-04T00:00:00Z", **normalize_contract()}
    store = Store(root / ".guard")
    try:
        sem = vr(doc, {"demo/math_ops.py": NORMALIZE_BASE.encode()}, "sha256:" + "0" * 64).semantic_digest
        ContractRegistry(store).create(doc, sem, kind="propose", reason="template proposal")
    finally:
        store.close()
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert c["property_result"] == "OBSERVED_REGRESSION" and c["authority"] == "INFERRED" and c["severity"] == "WARNING" and not c["required"]
    assert r["verdict"] == "WARN" and code == 0  # a change to an inferred property never blocks


def test_both_versions_fail_is_pre_existing_not_a_regression(tmp_path):
    zero_only = {"kind": "and", "args": [{"kind": "gt", "left": {"kind": "len", "value": XS}, "right": {"kind": "literal", "value": 0}},
                                         {"kind": "all_between", "value": XS, "lower": 0.0, "upper": 0.0}]}
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(require_coverage=False))
    declare(root, normalize_contract("normalize.zero.v1", precondition=zero_only, examples=[{"xs": [0.0, 0.0]}]), tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.zero.v1")
    assert (c["property_result"], c["patch_relation"], c["severity"]) == ("OBSERVED_FAILURE", "PRE_EXISTING", "WARNING")
    assert c["observations"]["base"]["verdict"] == "FAIL" and "ZeroDivisionError" in c["observations"]["base"]["reason"]
    assert c["observations"]["candidate"]["verdict"] == "FAIL"  # returns [0.0, ...], whose sum is not 1
    assert r["verdict"] != "BLOCK" and code == 0


def _length_contract(pre):
    return {"contract_id": "normalize.len.v1", "scope": "demo/math_ops.py::normalize", "precondition": pre,
            "predicate": {"kind": "eq", "left": {"kind": "len", "value": {"kind": "result"}}, "right": {"kind": "len", "value": XS}},
            "input_profile": "bounded_builtin_numeric_list_v1"}


def test_base_only_violation_is_resolved_info(tmp_path):
    """Without a positive-sum precondition, zero-sum inputs make the base raise while the patch returns: RESOLVED (INFO)."""
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(require_coverage=False))
    declare(root, _length_contract({"kind": "literal", "value": True}), tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.len.v1")
    assert c["property_result"] == "TESTED" and c["severity"] == "INFO" and c["probes"]["resolved"] >= 1 and c["probes"]["regression"] == 0
    assert "ZeroDivisionError" in c["resolved"]["base"]["reason"] and sum(c["resolved"]["witness"]["xs"]) == 0
    f = next(f for f in r["findings"] if f["finding_id"] == c["finding_id"])
    assert f["patch_relation"] == "RESOLVED" and f["severity"] == "INFO" and code == 0


def test_passing_probes_are_tested_not_proved(tmp_path):
    length = {"contract_id": "normalize.len.v1", "scope": "demo/math_ops.py::normalize", "precondition": POSITIVE_SUM,
              "predicate": {"kind": "eq", "left": {"kind": "len", "value": {"kind": "result"}}, "right": {"kind": "len", "value": XS}},
              "input_profile": "bounded_builtin_numeric_list_v1"}
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE, "demo/other.py": "X = 1\n"}, exec_config(require_coverage=False))
    declare(root, normalize_contract(), tmp_path)
    declare(root, length, tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.len.v1")
    assert (c["property_result"], c["scope_validity"], c["finding_id"], c["severity"]) == ("TESTED", "EXECUTED", None, None)
    assert "finite probes; not a proof" in c["quantifier"]
    assert c["probes"]["regression"] == 0 and c["probes"]["candidate_pass"] == c["probes"]["admitted"]
    # Repair: restore the base behavior; the approved contract is TESTED and nothing blocks.
    write(root, {"demo/math_ops.py": NORMALIZE_BASE + "\n# repaired\n"})
    code2, r2, _ = verify_exec(root, base)
    c2 = contract_result(r2, "normalize.sum.v1")
    assert c2["property_result"] == "TESTED" and c2["patch_relation"] is None and r2["verdict"] in ("PASS", "WARN") and code2 == 0


def test_fixture_e_mutation_regression(tmp_path):
    root, base = repo(tmp_path, {"demo/listy.py": SORTED_BASE}, exec_config())
    declare(root, unchanged_contract(), tmp_path)
    write(root, {"demo/listy.py": SORTED_PATCH})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "sorted_copy.pure.v1")
    assert (c["property_result"], c["patch_relation"], c["severity"]) == ("OBSERVED_REGRESSION", "INTRODUCED", "ERROR") and code == 1
    assert c["original_witness"] == {"xs": [2, 1]}
    w = c["witness"]["xs"]
    assert w != sorted(w)  # the minimized witness still exercises the mutation
    assert c["observations"]["candidate"]["arguments_after_call"]["xs"] == sorted(w)
    assert c["observations"]["base"]["arguments_after_call"]["xs"] == w


def test_repeatability_regression_with_finite_observations(tmp_path):
    base_src = "def scale(xs):\n    return [x * 2 for x in xs]\n"
    patch_src = "_CALLS = [0]\n\n\ndef scale(xs):\n    _CALLS[0] += 1\n    return [x * 2 + _CALLS[0] for x in xs]\n"
    contract = {"contract_id": "scale.repeatable.v1", "scope": "demo/s.py::scale", "precondition": {"kind": "literal", "value": True},
                "predicate": {"kind": "repeatability", "repeats": 3, "seed": 0, "comparison": "exact"}, "input_profile": "bounded_builtin_int_list_v1"}
    root, base = repo(tmp_path, {"demo/s.py": base_src}, exec_config(require_coverage=False))
    declare(root, contract, tmp_path)
    write(root, {"demo/s.py": patch_src})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "scale.repeatable.v1")
    assert c["property_result"] == "OBSERVED_REGRESSION" and code == 1
    assert c["observations"]["candidate"]["distinct_results"] == 3 and c["observations"]["base"]["distinct_results"] == 1
    assert "3 repeats" in " ".join(c["observations"]["candidate"]["trace"])


def test_shrinking_preserves_the_contract_domain(tmp_path):
    pre = {"kind": "and", "args": [{"kind": "le", "left": {"kind": "literal", "value": 3}, "right": {"kind": "len", "value": XS}},
                                   {"kind": "all_between", "value": XS, "lower": 2.0, "upper": 60.0}]}
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(require_coverage=False))
    declare(root, normalize_contract("normalize.domain.v1", precondition=pre, examples=[{"xs": [37.5, 12.25, 59.0, 8.0]}]), tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    _, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.domain.v1")
    assert c["property_result"] == "OBSERVED_REGRESSION" and c["probes"]["shrink_rounds"] >= 1
    w = c["witness"]["xs"]
    assert len(w) >= 3 and all(2.0 <= x <= 60.0 for x in w)  # never shrunk out of the domain
    assert c["original_witness"] == {"xs": [37.5, 12.25, 59.0, 8.0]} and len(w) == 3


def test_missing_baseline_target_is_undetermined_and_incomplete(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": "X = 1\n"}, exec_config())
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    declare(root, normalize_contract(), tmp_path)
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert (c["property_result"], c["patch_relation"], c["severity"]) == ("OBSERVED_FAILURE", "UNDETERMINED", "WARNING")
    assert "does not apply to the base snapshot" in c["base_replay"]
    assert code == 2 and r["verdict"] == "INCOMPLETE" and r["run_outcome"] == "INCOMPLETE"  # never BLOCK without a baseline comparison
    assert any("could not be attributed to the patch (comparison UNDETERMINED)" in x for x in r["verdict_reasons"])


@pytest.mark.parametrize("patch,needle", [
    ("def normalize(xs):\n    while True:\n        pass\n", "timeout"),
    ("import not_installed_dependency_xyz\n\n\ndef normalize(xs):\n    return xs\n", "import_error"),
])
def test_runner_outcomes_are_unknown_not_defects(tmp_path, patch, needle):
    cfg = exec_config().replace("max_probes = 24", "max_probes = 2").replace("probe_wall_seconds = 3", "probe_wall_seconds = 0.5")
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, cfg)
    declare(root, normalize_contract(examples=[]), tmp_path)
    write(root, {"demo/math_ops.py": patch})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert c["property_result"] == "UNKNOWN" and c["severity"] is None and c["finding_id"] is None
    assert any(needle in x for x in c["reasons"]) and "not a program defect" in " ".join(c["reasons"])
    assert code == 2 and r["verdict"] == "INCOMPLETE"
    assert any("1 required contract check(s) did not produce evidence (CHECKED)" in x for x in r["verdict_reasons"])


def test_static_default_never_starts_a_runner(tmp_path, monkeypatch):
    import invariantguard.execution.process as proc
    import invariantguard.execution.runner as runner

    root, base = _fixture_b(tmp_path, exec_config(require_coverage=False))

    def boom(*a, **k):
        raise AssertionError("a runner was started without --execute")

    monkeypatch.setattr(runner, "make_backend", boom)
    monkeypatch.setattr(proc, "run_argv", boom)
    code, out, err = run_cli(["verify", str(root), "--base", base, "--format", "json"])
    r = json.loads(out)
    c = contract_result(r, "normalize.sum.v1")
    assert (c["status"], c["property_result"], c["required"]) == ("NOT_RUN", "UNVERIFIED", False)
    assert r["contracts"]["execution"]["status"] == "DISABLED" and r["capabilities"]["execution"] == "disabled"
    assert r["timing_ms"]["dynamic"] is None and r["verdict"] != "BLOCK" and code == 0


def test_dynamic_results_are_deterministic(tmp_path):
    root, base = _fixture_b(tmp_path)
    _, r1, _ = verify_exec(root, base)
    _, r2, _ = verify_exec(root, base)
    c1, c2 = contract_result(r1, "normalize.sum.v1"), contract_result(r2, "normalize.sum.v1")
    for k in ("property_result", "witness", "original_witness", "confirmations", "probes", "evidence_ids", "finding_id"):
        assert c1[k] == c2[k], k


def test_revision_invalidates_old_evidence_and_is_audited(tmp_path):
    """T38: a new predicate or domain never inherits earlier passing evidence; history is kept."""
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE, "demo/other.py": "X = 1\n"}, exec_config(require_coverage=False))
    declare(root, normalize_contract(), tmp_path)
    write(root, {"demo/other.py": "X = 2\n"})
    _, r1, _ = verify_exec(root, base)
    assert contract_result(r1, "normalize.sum.v1")["property_result"] == "TESTED"
    _, lst, _ = contracts_cli(root, "list")
    (s1,) = lst["contracts"]
    assert s1["revision"] == 1 and s1["evidence_for_current_revision"] == 2 and s1["latest_evidence"]["result_label"] in ("TESTED",)
    # Revising with identical semantics is refused (nothing to revise).
    same = tmp_path / "same.json"
    same.write_text(json.dumps(normalize_contract()))
    code, _, err = contracts_cli(root, "revise", "normalize.sum.v1", "--file", str(same), "--reason", "no change")
    assert code == 4 and "nothing to revise" in err
    # A new tolerance is a new revision with no evidence.
    rev = tmp_path / "rev.json"
    rev.write_text(json.dumps(normalize_contract(predicate={**normalize_contract()["predicate"], "atol": 1e-6})))
    code, out, err = contracts_cli(root, "revise", "normalize.sum.v1", "--file", str(rev), "--reason", "intentional API change: looser tolerance")
    assert code == 0 and out["revision"] == 2 and out["authority"] == "DECLARED" and out["stale_evidence"] == 2
    _, lst, _ = contracts_cli(root, "list")
    (s2,) = lst["contracts"]
    assert (s2["revision"], s2["evidence_for_current_revision"], s2["latest_evidence"], s2["stale_evidence_from_other_revisions"]) == (2, 0, None, 2)
    assert [h["kind"] for h in s2["history"]] == ["declare", "revise"]
    assert s2["semantic_digest"] != s1["semantic_digest"]
    _, r2, _ = verify_exec(root, base)
    c2 = contract_result(r2, "normalize.sum.v1")
    assert c2["revision"] == 2 and c2["semantic_digest"] == s2["semantic_digest"]
    _, show, _ = contracts_cli(root, "show", "normalize.sum.v1")
    revs = {e["revision"] for e in show["evidence"]}
    assert revs == {1, 2}  # old evidence kept as history, never rewritten
    # A domain change is a revision as well.
    dom = tmp_path / "dom.json"
    dom.write_text(json.dumps(normalize_contract(precondition={**normalize_contract()["precondition"],
                                                                "args": normalize_contract()["precondition"]["args"][:1]
                                                                + [{"kind": "all_between", "value": XS, "lower": 0.0, "upper": 50.0}]
                                                                + normalize_contract()["precondition"]["args"][2:]},
                                                  predicate={**normalize_contract()["predicate"], "atol": 1e-6})))
    code, out, _ = contracts_cli(root, "revise", "normalize.sum.v1", "--file", str(dom), "--reason", "narrower domain")
    assert code == 0 and out["revision"] == 3
    _, lst, _ = contracts_cli(root, "list")
    assert lst["contracts"][0]["evidence_for_current_revision"] == 0


def test_contract_revision_invalidates_only_the_target_summary(tmp_path):
    """T23 contract dependency: a contract change re-keys its target symbol's static summary only."""
    files = {"demo/math_ops.py": NORMALIZE_BASE, "demo/calc.py": "def f(a: int):\n    return 10 // (a + 1)\n"}
    root, base = repo(tmp_path, files, exec_config(require_coverage=False))

    def full():
        code, out, err = run_cli(["verify", str(root), "--base", base, "--format", "json", "--mode", "full"])
        return json.loads(out)["patch"]["cache"]["candidate"]

    full()
    warm = full()
    assert warm["misses"] == 0 and warm["hits"] == 2
    declare(root, normalize_contract(), tmp_path)
    after = full()
    assert after["hits"] == 1 and after["misses"] == 1  # only demo/math_ops.py::normalize was re-analyzed
    assert full()["misses"] == 0
    rev = tmp_path / "rev.json"
    rev.write_text(json.dumps(normalize_contract(predicate={**normalize_contract()["predicate"], "atol": 1e-6})))
    code, _, _ = contracts_cli(root, "revise", "normalize.sum.v1", "--file", str(rev), "--reason", "looser tolerance")
    assert code == 0
    assert full()["misses"] == 1  # the new revision re-keys the target summary
    code, _, _ = contracts_cli(root, "retire", "normalize.sum.v1", "--reason", "API removed")
    assert code == 0
    # With no active contract the key is again the original contract-free key, whose complete entry is reused (sound).
    assert full() == warm


def test_without_required_coverage_an_undecided_contract_is_advisory(tmp_path):
    cfg = exec_config(require_coverage=False).replace("max_probes = 24", "max_probes = 2").replace("probe_wall_seconds = 3", "probe_wall_seconds = 0.5")
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, cfg)
    declare(root, normalize_contract(examples=[]), tmp_path)
    write(root, {"demo/math_ops.py": "def normalize(xs):\n    while True:\n        pass\n"})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert c["property_result"] == "UNKNOWN" and c["required"] is False and r["analysis_plan"]["required_contracts"] == []
    assert r["verdict"] == "WARN" and code == 0  # disclosed, never PASS


def test_changed_dependency_locks_make_the_comparison_undetermined(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE, "requirements.txt": "numpy==1.0\n"}, exec_config())
    declare(root, normalize_contract(), tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH, "requirements.txt": "numpy==2.0\n"})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert (c["property_result"], c["patch_relation"], c["severity"]) == ("OBSERVED_FAILURE", "UNDETERMINED", "WARNING")
    assert "dependency lock files differ" in c["reasons"][-1]
    assert code == 2 and r["verdict"] == "INCOMPLETE"  # an intended dependency change is not silently a regression


def test_evidence_is_recorded_only_for_consistent_runs(tmp_path, monkeypatch):
    import sqlite3

    import invariantguard.verify as verify_mod

    root, base = _fixture_b(tmp_path)
    monkeypatch.setattr(verify_mod, "verify_unchanged", lambda snap: ["demo/math_ops.py"])  # every attempt sees an edit
    code, r, _ = verify_exec(root, base)
    assert r["run_outcome"] == "INCOMPLETE" and contract_result(r, "normalize.sum.v1")["evidence_ids"]
    db = sqlite3.connect(root / ".guard" / "state.sqlite")
    try:
        assert db.execute("SELECT COUNT(*) FROM contract_evidence").fetchone()[0] == 0
    finally:
        db.close()

