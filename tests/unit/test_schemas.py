"""Schema validity: valid, invalid, unknown-enum and placeholder-ID cases for every schema."""

import copy

import pytest

from invariantguard.models.schema import SCHEMA_NAMES, load_schema, validation_errors

H = "0123456789abcdef" * 4
SNAP = "sha256:" + H
SPAN = {"path": "pkg/m.py", "start_line": 3, "start_column": 11, "end_line": 3, "end_column": 18, "start_byte": 40, "end_byte": 47, "snapshot_id": SNAP}
NODE_LIT = {"kind": "literal", "value": 0}
PRED = {"kind": "approx_eq", "left": {"kind": "sum", "value": {"kind": "result"}}, "right": {"kind": "literal", "value": 1.0}, "atol": 1e-9, "rtol": 1e-9, "require_finite": True}
PRE = {"kind": "and", "args": [{"kind": "gt", "left": {"kind": "len", "value": {"kind": "arg", "name": "xs"}}, "right": NODE_LIT},
                                {"kind": "all_between", "value": {"kind": "arg", "name": "xs"}, "lower": 0.0, "upper": 100.0}]}
FINDING = {
    "finding_id": "fnd-" + H[:32], "obligation_id": "pkg/m.py::ratio::division.zero_denominator#0", "symbol_id": "pkg/m.py::ratio",
    "property_id": "division.zero_denominator", "property_result": "PROVED_UNSAFE", "scope_validity": "EXACT_MODEL",
    "authority": "DECLARED", "patch_relation": "UNDETERMINED", "severity": "ERROR", "source_span": SPAN, "message": "m",
    "quantifier": "exists admitted function input", "domain": "a, b: exact Python built-in integers", "domain_source": "CONFIG_DECLARED",
    "assumptions": ["domain explicitly admitted in configuration"], "path_constraints": ["d == a - b", "d == 0"], "reasons": ["r"],
    "witness": {"a": 1, "b": 1}, "witness_validated": True, "runtime_mode": None, "evidence_ids": ["ev-" + H[:32]], "replay_available": False,
}
OBL = {
    "obligation_id": "pkg/m.py::ratio::division.zero_denominator#0", "symbol_id": "pkg/m.py::ratio", "property_id": "division.zero_denominator",
    "operation": "DIV", "operation_text": "100 / d", "divisor_text": "d", "property_result": "PROVED_SAFE", "scope_validity": "EXACT_MODEL",
    "authority": "DECLARED", "domain": "a, b", "domain_source": "CONFIG_DECLARED", "assumptions": [], "quantifier": "for all admitted function inputs",
    "method": "INTERVAL", "reasons": ["divisor excludes 0"], "source_span": SPAN, "required": True, "witness": None, "witness_validated": False,
    "path_constraints": [], "evidence_ids": ["ev-" + H[:32]], "finding_id": None, "runtime_mode": None, "divisor_fact": "[1, +inf]",
}
EVIDENCE = {
    "schema_version": "1.0", "evidence_id": "ev-" + H[:32], "kind": "SOLVER", "engine": {"name": "invariantguard", "version": "0.1.0", "ir_version": "ir-0.1"},
    "run_id": "run-" + H[:32], "snapshot_id": SNAP, "symbol_id": "pkg/m.py::ratio", "property_id": "division.zero_denominator",
    "obligation_id": "pkg/m.py::ratio::division.zero_denominator#0", "result_label": "PROVED_UNSAFE", "domain": "a, b", "assumptions": [],
    "precision": "EXACT_MODEL", "method": "SMT", "source_span": SPAN, "created_at": "2026-10-04T12:00:00.000000Z", "details": ["SAT"],
    "solver_runs": [{"solver": "z3", "solver_version": "5.1.0", "query_digest": SNAP, "response": "SAT", "reason_unknown": None, "elapsed_ms": 1.5,
                     "timeout_ms": 100, "rlimit": None, "exact_encoding": True, "assertions_constraint": "any"}],
    "witness": {"a": 1, "b": 1}, "witness_validation": "VALIDATED",
}
SNAPSHOT = {"schema_version": "1.0", "snapshot_id": SNAP, "kind": "working_tree_capture", "root_label": "demo", "scope": ["."],
            "files": [{"path": "pkg/m.py", "sha256": SNAP, "size_bytes": 10}], "excluded": [], "config_digest": None,
            "semantics_profile": "python3.13-cpython-builtin-v1", "dependency_lock_digests": [], "source_retained": False}
PLAN = {"schema_version": "1.0", "plan_id": "plan-" + H[:32], "command": "check", "mode": "quick", "base_snapshot": None, "candidate_snapshot": SNAP,
        "requested_symbols": ["pkg/m.py::ratio"], "required_properties": ["division.zero_denominator"],
        "required_obligations": ["pkg/m.py::ratio::division.zero_denominator#0"], "require_coverage": True,
        "budgets": {"per_query_timeout_ms": 100, "quick_budget_ms": 2000, "per_query_rlimit": 0}, "solver": {"enabled": True, "available": True},
        "invalidated_summaries": [], "unresolved_changes": [], "created_before_analysis": True}
CONTRACT = {"schema_version": "1.0", "contract_id": "normalize.sum.v1", "revision": 1, "scope": "demo/math_ops.py::normalize", "authority": "APPROVED",
            "lifecycle_state": "REVIEWED", "precondition": PRE, "predicate": PRED, "input_profile": "bounded_builtin_numeric_list_v1",
            "provenance": [{"kind": "maintainer_approval", "reference": "contract-review-1"}], "supersedes": None, "reason": "r",
            "created_at": "2026-10-04T12:00:00Z"}
BATCH = {"schema_version": "1.0", "batch_id": "batch-" + H[:32], "snapshot_id": SNAP, "source": "template", "model_identity": None,
         "context_packet_digest": None, "candidates": [{"candidate_id": "cand-" + H[:16], "scope": "demo/m.py::f", "precondition": PRE,
         "predicate": PRED, "input_profile": "p", "source_refs": [SPAN], "rationale": "r", "uncertainty": "u", "unsupported_dependencies": [], "boundary_inputs": []}]}
PACKET = {"schema_version": "1.0", "snapshot_id": SNAP, "symbol_ids": ["demo/m.py::f"], "source_spans": [SPAN], "source_digest": SNAP,
          "snippets": [{"span": SPAN, "text": "def f(): ...", "trusted": False}], "trusted_static_facts": [], "candidate_domain": "",
          "existing_contract_ids": [], "unsupported_constructs": [], "resource_budget": {"max_bytes": 8000, "max_tokens": 2000}}
PROFILE = {"schema_version": "1.0", "profile_id": "isolated-python", "allowed_targets": ["demo/m.py::f"], "input_templates": ["t"],
           "interpreter": {"implementation": "cpython", "version": "3.13.9"}, "environment_lock_digest": SNAP,
           "isolation": {"backend": "container", "required_capabilities": ["deny_network"]}, "network_policy": "deny", "writable_paths": [],
           "limits": {"cpu_seconds": 5, "memory_mb": 512, "wall_seconds": 10, "max_processes": 4}, "env_allowlist": [],
           "max_sizes": {"input_bytes": 1000, "output_bytes": 1000, "artifact_bytes": 1000}, "baseline_environment": "b",
           "candidate_environment": "c", "source_retention": False, "witness_storage": True, "allow_unisolated": False}

VALID = {
    "source_span": SPAN, "finding": FINDING, "obligation_result": OBL, "evidence": EVIDENCE, "snapshot": SNAPSHOT, "analysis_plan": PLAN,
    "contract_revision": CONTRACT, "candidate_batch": BATCH, "context_packet": PACKET, "run_profile": PROFILE,
}


def test_all_schemas_load_and_are_valid_draft_2020_12():
    import jsonschema

    for name in SCHEMA_NAMES:
        jsonschema.Draft202012Validator.check_schema(load_schema(name))


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_payloads(name):
    assert validation_errors(name, VALID[name]) == []


@pytest.mark.parametrize("name", sorted(VALID))
def test_missing_required_field_and_unknown_field_rejected(name):
    payload = copy.deepcopy(VALID[name])
    first = sorted(payload)[0]
    del payload[first]
    assert validation_errors(name, payload)
    payload = copy.deepcopy(VALID[name])
    payload["unexpected_forward_field"] = 1
    assert validation_errors(name, payload)


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("finding", "property_result", "PROBABLY_FINE"),
        ("finding", "scope_validity", "ALMOST_EXACT"),
        ("finding", "authority", "MODEL_SAYS_SO"),
        ("finding", "patch_relation", "MAYBE"),
        ("finding", "severity", "CRITICAL"),
        ("obligation_result", "method", "VIBES"),
        ("evidence", "kind", "LLM_OPINION"),
        ("evidence", "witness_validation", "TRUST_ME"),
        ("analysis_plan", "mode", "turbo"),
        ("run_profile", "network_policy", "allow"),
    ],
)
def test_unknown_enum_values_rejected(name, field, value):
    payload = copy.deepcopy(VALID[name])
    payload[field] = value
    assert validation_errors(name, payload)


@pytest.mark.parametrize(
    "name,field,value",
    [
        ("finding", "finding_id", "finding-example"),
        ("evidence", "run_id", "run-example"),
        ("evidence", "snapshot_id", "sha256:candidate-example"),
        ("evidence", "evidence_id", "evidence-example"),
        ("analysis_plan", "candidate_snapshot", "sha256:base-example"),
    ],
)
def test_placeholder_ids_rejected(name, field, value):
    payload = copy.deepcopy(VALID[name])
    payload[field] = value
    assert validation_errors(name, payload)


def test_semantic_constraints():
    # A finding can never be PROVED_SAFE; a PROVED_UNSAFE needs a validated witness and a scope.
    f = copy.deepcopy(FINDING)
    f["property_result"] = "PROVED_SAFE"
    assert validation_errors("finding", f)
    f = copy.deepcopy(FINDING)
    f["witness"] = None
    assert validation_errors("finding", f)
    f = copy.deepcopy(FINDING)
    f["witness_validated"] = False
    assert validation_errors("finding", f)
    f = copy.deepcopy(FINDING)
    f["scope_validity"] = None
    assert validation_errors("finding", f)
    f["property_result"] = "UNKNOWN"
    f["severity"] = "INFO"
    f["witness"] = None
    assert validation_errors("finding", f) == []  # UNKNOWN makes no scoped claim
    # assumptions/coverage-documenting fields are required
    f = copy.deepcopy(FINDING)
    del f["assumptions"]
    assert validation_errors("finding", f)
    # unisolated profile requires explicit allow_unisolated
    p = copy.deepcopy(PROFILE)
    p["isolation"]["backend"] = "unisolated"
    assert validation_errors("run_profile", p)
    p["allow_unisolated"] = True
    assert validation_errors("run_profile", p) == []


def test_source_span_rejects_bad_positions_and_paths():
    for field, value in [("start_line", 0), ("start_column", -1), ("path", "/etc/passwd"), ("path", "../x.py"), ("path", "a/../../x.py")]:
        s = dict(SPAN)
        s[field] = value
        assert validation_errors("source_span", s), (field, value)


def test_dsl_rejects_unknown_and_executable_nodes():
    c = copy.deepcopy(CONTRACT)
    c["predicate"] = {"kind": "eval", "code": "__import__('os').system('true')"}
    assert validation_errors("contract_revision", c)
    c = copy.deepcopy(CONTRACT)
    c["predicate"] = {"kind": "approx_eq", "left": NODE_LIT, "right": NODE_LIT, "atol": 1e-9, "rtol": 1e-9}  # missing finiteness policy
    assert validation_errors("contract_revision", c)
    c = copy.deepcopy(CONTRACT)
    c["predicate"] = {"kind": "literal", "value": "x" * 257}  # string literals are inert, bounded data (DECISIONS I-152)
    assert validation_errors("contract_revision", c)
    c["predicate"] = {"kind": "literal", "value": {"code": "os.system"}}
    assert validation_errors("contract_revision", c)
    c["predicate"] = {"kind": "is_type", "value": NODE_LIT, "type": "builtins.object"}
    assert validation_errors("contract_revision", c)
