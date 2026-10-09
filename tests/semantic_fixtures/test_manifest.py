"""Every manifest case must produce its independently derived expected result."""

import json
from pathlib import Path

import pytest

from ..conftest import REPO, check_json, result_for

MANIFEST = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
CASES = MANIFEST["cases"]


def test_manifest_cases_have_required_metadata():
    keys = {"case_id", "language", "language_version", "path", "symbol_id", "baseline_source", "patch_source", "property", "domain",
            "expected", "expected_without_solver", "scope", "ground_truth", "environment_lock", "seed", "origin", "license"}
    ids = set()
    for c in CASES:
        assert keys <= set(c), c["case_id"]
        assert c["ground_truth"]["method"] and c["ground_truth"]["detail"]
        assert (REPO / c["path"]).is_file()
        assert c["case_id"] not in ids
        ids.add(c["case_id"])
    # clean safe cases, defects and unsupported cases are all represented
    results = {c["expected"]["property_result"] for c in CASES}
    assert {"PROVED_SAFE", "PROVED_UNSAFE", "POSSIBLY_UNSAFE", "UNKNOWN"} <= results


def _check(case, *extra):
    code, report, _ = check_json(REPO / case["path"], *extra)
    oid = f"{case['symbol_id']}::{case['property']}#{case['obligation_ordinal']}"
    r = next((x for x in report["obligation_results"] if x["obligation_id"] == oid), None)
    assert r is not None, (oid, [x["obligation_id"] for x in report["obligation_results"]])
    return code, report, r


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_case_with_solver(case):
    _, _, r = _check(case)
    exp = case["expected"]
    assert r["property_result"] == exp["property_result"], r["reasons"]
    assert r["scope_validity"] == exp["scope_validity"]
    if "runtime_mode" in exp:
        assert r["runtime_mode"] == exp["runtime_mode"]
    if "expected_witness" in case:
        assert r["witness"] == case["expected_witness"]
    if r["property_result"] == "PROVED_UNSAFE":
        assert r["witness_validated"] and r["path_constraints"]
    if r["property_result"] == "UNKNOWN":
        assert r["witness"] is None


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_case_without_solver(case):
    _, report, r = _check(case, "--no-solver")
    exp = case["expected_without_solver"]
    assert report["capabilities"]["solver"] == "disabled"
    assert r["property_result"] == exp["property_result"], r["reasons"]
    assert r["scope_validity"] == exp["scope_validity"]


def test_patch_cases_point_at_existing_tests():
    for c in MANIFEST["patch_cases"] + MANIFEST["dynamic_cases"]:
        path, _, name = c["test"].partition("::")
        assert (REPO / path).is_file() and f"def {name}(" in (REPO / path).read_text(), c["case_id"]
        assert c["ground_truth"]["method"] and c["expected"]
    assert MANIFEST["deferred_cases"] == []


@pytest.mark.parametrize("case", MANIFEST["skip_cases"], ids=[c["case_id"] for c in MANIFEST["skip_cases"]])
def test_skip_cases(case):
    code, report, _ = check_json(REPO / case["path"])
    assert report["run_outcome"] == case["expected_run_outcome"]
    assert [s["kind"] for s in report["coverage"]["skipped_checks"]] == [case["expected_skip"]]


def test_dynamic_suites_from_docs12_are_all_covered():
    ids = {c["case_id"] for c in MANIFEST["dynamic_cases"]}
    assert {"fixture-b-positive-sum-regression", "all-zero-excluded", "both-versions-fail", "fixture-e-purity-regression", "controlled-repeatability",
            "shrinking-preserves-domain", "missing-baseline-environment", "isolation-denial"} <= ids
