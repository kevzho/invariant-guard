"""CLI integration: JSON/exit contract, stdout/stderr separation, capabilities, init, solver modes."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from invariantguard.models.schema import validation_errors

from ..conftest import DEMO, REPO, check_json, result_for, run_cli, run_guard_subprocess


def test_demo_json_is_schema_valid_and_stdout_is_pure_json():
    code, out, err = run_cli(["check", str(DEMO), "--format", "json"])
    report = json.loads(out)  # stdout is exactly one JSON document
    assert validation_errors("report", report) == []
    assert "guard:" not in out and "guard: verdict" in err
    assert code == 1 and report["verdict"] == "BLOCK" and report["run_outcome"] == "INCOMPLETE"  # BLOCK and incomplete together
    cov = report["coverage"]
    required = [r for r in report["obligation_results"] if r["required"]]
    assert cov["required_obligations"] == len(required) == len(report["analysis_plan"]["required_obligations"])
    # Only handled failures (I-133) are advisory in the demo.
    assert all("handled" in r["reasons"][-1] for r in report["obligation_results"] if not r["required"])
    assert cov["resolved_required_obligations"] + len(cov["unknown_obligations"]) == cov["required_obligations"]
    assert report["base_snapshot"] is None and all(f["patch_relation"] == "UNDETERMINED" for f in report["findings"])
    ev_ids = {e["evidence_id"] for e in report["evidence"]}
    assert all(set(r["evidence_ids"]) <= ev_ids for r in report["obligation_results"])


def test_fixture_a_unsafe_candidate_has_validated_witness():
    code, report, _ = check_json(DEMO / "candidate")
    assert code == 1 and report["verdict"] == "BLOCK" and report["run_outcome"] == "COMPLETE"
    (f,) = report["findings"]
    assert (f["property_result"], f["scope_validity"], f["authority"], f["severity"]) == ("PROVED_UNSAFE", "EXACT_MODEL", "DECLARED", "ERROR")
    w = f["witness"]
    assert set(w) == {"a", "b"} and w["a"] == w["b"] and f["witness_validated"] is True
    assert f["path_constraints"] == ["d == a - b", "d == 0"]
    assert f["source_span"]["path"] == "candidate/ratio.py" and f["source_span"]["start_line"] == 3
    assert f["quantifier"] == "exists admitted function input"
    ev = next(e for e in report["evidence"] if e["evidence_id"] == f["evidence_ids"][0])
    assert ev["kind"] == "SOLVER" and ev["solver_runs"][0]["response"] == "SAT" and ev["witness_validation"] == "VALIDATED"


def test_fixture_a_guarded_baseline_is_proved_safe():
    code, report, _ = check_json(DEMO / "baseline")
    assert code == 0 and report["verdict"] == "PASS" and report["findings"] == []
    r = result_for(report, "baseline/ratio.py::ratio")
    assert (r["property_result"], r["scope_validity"], r["method"]) == ("PROVED_SAFE", "EXACT_MODEL", "INTERVAL")


def test_fixture_c_unknown_call_is_incomplete():
    code, report, _ = check_json(DEMO / "unknown" / "imported_ratio.py")
    assert code == 2 and report["verdict"] == "INCOMPLETE" and report["run_outcome"] == "INCOMPLETE"
    r = result_for(report, "unknown/imported_ratio.py::imported_ratio")
    assert r["property_result"] == "UNKNOWN" and r["witness"] is None and r["scope_validity"] is None
    assert "external_transform" in r["reasons"][-1]
    assert report["coverage"]["unknown_obligations"][0]["obligation_id"] == r["obligation_id"]


def test_annotation_only_never_becomes_unconditional():
    code, report, _ = check_json(DEMO / "adversarial" / "annotation_only.py")
    (f,) = report["findings"]
    assert f["property_result"] == "PROVED_UNSAFE" and f["scope_validity"] == "CONDITIONAL"
    assert f["authority"] == "INFERRED" and f["severity"] == "WARNING" and f["domain_source"] == "ANNOTATION"
    assert any("not enforced" in a for a in f["assumptions"])
    assert code == 0 and report["verdict"] == "WARN"


def test_human_format_on_stdout_and_logs_on_stderr():
    code, out, err = run_cli(["check", str(DEMO / "candidate"), "--format", "human"])
    assert code == 1 and "PROVED_UNSAFE" in out and "verdict=BLOCK" in out
    assert "captured" in err and "captured" not in out



def test_human_format_groups_unknown_by_cause_and_verbose_lists_each():
    code, out, _ = run_cli(["check", str(DEMO), "--format", "human"])
    code_v, out_v, _ = run_cli(["check", str(DEMO), "--format", "human", "--verbose"])
    _, report, _ = check_json(DEMO)
    unknown = [o for o in report["obligation_results"] if o["property_result"] == "UNKNOWN"]
    assert unknown and code == code_v == 1
    # Compact: every non-UNKNOWN result is still listed, and UNKNOWNs are counted, never dropped.
    assert f"UNKNOWN: {len(unknown)} obligation(s) in {len({o['symbol_id'] for o in unknown})} symbol(s)" in out
    assert out.count("  UNKNOWN  `") == 0 and out_v.count("  UNKNOWN  `") == len(unknown)
    for o in report["obligation_results"]:
        if o["property_result"] != "UNKNOWN":
            assert f"{o['source_span']['path']}:{o['source_span']['start_line']}:" in out
    assert out.split("verdict reasons:")[1] == out_v.split("verdict reasons:")[1]
    assert report["candidate_snapshot"] not in out and report["candidate_snapshot"] in out_v


@pytest.mark.parametrize("reason,cause", [
    ("unsupported: for loop (loops are outside the supported subset)", "unsupported for loop"),
    ("unsupported: exception handling (try/except) is outside the supported subset", "unsupported exception handling (try/except)"),
    ("dividend has type object: the operator may dispatch to a user-defined method, so built-in zero-divisor semantics are not established; "
     "it comes from: attribute access .path not modeled", "operand of unmodeled type (from attribute access): the operator may be user code"),
    ("dividend has type object: the operator may dispatch to a user-defined method, so built-in zero-divisor semantics are not established; "
     "parameter 'x' annotated Any and no declared domain",
     "operand of unmodeled type (from a parameter without a declared exact domain): the operator may be user code"),
    ("list xs may be mutated (through an alias) by unknown effects", "list xs may be mutated"),
])
def test_unresolved_cause_labels(reason, cause):
    from invariantguard.cli import unresolved_cause

    assert unresolved_cause(reason) == cause

@pytest.mark.parametrize(
    "argv",
    [
        ["check", "/definitely/not/a/path"],
        ["check", str(DEMO), "--format", "xml"],
        ["check"],
        [],
        ["frobnicate"],
        ["check", str(DEMO), "--timeout-ms", "0"],
    ],
)
def test_invalid_usage_exits_4(argv):
    code, out, _ = run_cli(argv)
    assert code == 4 and out == ""


def test_invalid_config_exits_4(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".guard").mkdir()
    (tmp_path / ".guard" / "config.toml").write_text("[analysis]\nsolver_enabled = 'maybe'\n")
    (tmp_path / "m.py").write_text("def f(a):\n    return 1 / a\n")
    code, out, err = run_cli(["check", str(tmp_path), "--format", "json"])
    assert code == 4 and out == "" and "invalid configuration" in err


def test_init_writes_only_config_and_refuses_overwrite(tmp_path):
    (tmp_path / "keep.py").write_text("x = 1\n")
    code, out, err = run_cli(["init", str(tmp_path)])
    assert code == 0 and ".guard/cache/" in out
    cfg = tmp_path / ".guard" / "config.toml"
    from invariantguard.config import parse_config

    parse_config(cfg.read_bytes())
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == [".guard", ".guard/config.toml", "keep.py"]
    before = cfg.read_bytes()
    code, _, err = run_cli(["init", str(tmp_path)])
    assert code == 4 and "not overwritten" in err and cfg.read_bytes() == before


def test_capabilities_reports_unimplemented_features_honestly():
    code, out, _ = run_cli(["capabilities", "--format", "json"])
    c = json.loads(out)
    assert code == 0 and c["solver"]["available"] is True and c["solver"]["version"]
    # M5: the MCP server is an optional extra; capabilities reports whether its SDK is installed (and never starts a server).
    try:
        import mcp  # noqa: F401

        assert c["mcp"]["status"] == "available (explicit)" and "guard mcp serve --stdio" in c["mcp"]["reason"]
    except ImportError:
        assert c["mcp"]["status"] == "unavailable" and "'.[mcp]'" in c["mcp"]["reason"]
    # M4: the local model is explicit ([semantic] + --model local); capabilities never contacts the endpoint.
    assert c["model"]["status"] == "available (explicit)" and "loopback" in c["model"]["reason"] and "no download" in c["model"]["reason"]
    # M3: execution exists but is explicit (--execute + an enabled run profile); capabilities never contacts the daemon.
    assert c["execution"]["status"] == "available (explicit)" and "--execute" in c["execution"]["reason"]
    # M2 implemented static patch verification; it is reported as available (Git found on this host).
    assert c["patch_verification"]["status"] == "available" and "static" in c["patch_verification"]["reason"]


def test_no_solver_flag_disables_smt_but_keeps_sound_results():
    code, report, _ = check_json(DEMO / "adversarial" / "two_variable_equality.py", "--no-solver")
    assert report["capabilities"]["solver"] == "disabled"
    r = result_for(report, "adversarial/two_variable_equality.py::guarded_difference")
    assert r["property_result"] == "UNKNOWN" and code == 2
    code, report, _ = check_json(DEMO / "candidate", "--no-solver")
    f = report["findings"][0]
    assert f["property_result"] == "PROVED_UNSAFE" and f["witness_validated"] and code == 1
    ev = next(e for e in report["evidence"] if e["evidence_id"] == f["evidence_ids"][0])
    assert ev["kind"] == "WITNESS_SEARCH" and ev["solver_runs"] == []


def test_solver_unavailable_is_distinct_from_disabled(tmp_path):
    stub = tmp_path / "stub" / "z3"
    stub.mkdir(parents=True)
    (stub / "__init__.py").write_text("raise ImportError('z3 deliberately unavailable for this test')\n")
    env = {"PYTHONPATH": str(tmp_path / "stub")}
    p = run_guard_subprocess(["check", str(DEMO / "adversarial" / "two_variable_equality.py"), "--format", "json"], env=env)
    report = json.loads(p.stdout)
    assert report["capabilities"]["solver"] == "unavailable" and "deliberately unavailable" in report["capabilities"]["solver_detail"]
    assert result_for(report, "adversarial/two_variable_equality.py::guarded_difference")["property_result"] == "UNKNOWN"
    assert p.returncode == 2
    p = run_guard_subprocess(["check", str(DEMO / "baseline"), "--format", "json"], env=env)
    report = json.loads(p.stdout)
    # interval analysis still proves the guarded baseline; the missing optional capability is disclosed as WARN
    assert result_for(report, "baseline/ratio.py::ratio")["property_result"] == "PROVED_SAFE"
    assert report["verdict"] == "WARN" and p.returncode == 0
    assert any("capability unavailable" in r for r in report["verdict_reasons"])
    p = run_guard_subprocess(["capabilities", "--format", "json"], env=env)
    assert json.loads(p.stdout)["solver"]["available"] is False


def test_solver_timeout_and_resource_limit_are_unknown_not_safe(project):
    src = {"m.py": "def f(x, y, z):\n    d = x * x * x + y * y * y + z * z * z - 33\n    return 100 / d\n"}
    # Default limits (I-146): the deterministic resource limit decides.
    root = project(src, "m.py::f")
    code, report, _ = check_json(root / "m.py")
    r = result_for(report, "m.py::f")
    ev = next(e for e in report["evidence"] if e["evidence_id"] == r["evidence_ids"][0])
    assert r["property_result"] == "UNKNOWN" and ev["solver_runs"][0]["response"] == "RESOURCE_LIMIT" and code == 2
    # A wall-clock limit only (no rlimit) gives TIMEOUT.
    root = project(src, "m.py::f", extra_analysis="per_query_rlimit = 0\nper_query_timeout_ms = 100")
    code, report, _ = check_json(root / "m.py")
    r = result_for(report, "m.py::f")
    ev = next(e for e in report["evidence"] if e["evidence_id"] == r["evidence_ids"][0])
    assert r["property_result"] == "UNKNOWN" and ev["solver_runs"][0]["response"] == "TIMEOUT" and code == 2
    root = project(src, "m.py::f", extra_analysis="per_query_rlimit = 1000\nper_query_timeout_ms = 10000")
    code, report, _ = check_json(root / "m.py")
    r = result_for(report, "m.py::f")
    ev = next(e for e in report["evidence"] if e["evidence_id"] == r["evidence_ids"][0])
    assert r["property_result"] == "UNKNOWN" and ev["solver_runs"][0]["response"] == "RESOURCE_LIMIT" and code == 2


def test_require_coverage_false_makes_unknown_advisory(project):
    root = project({"m.py": "def f(a, b):\n    return 1 / g(a)\n"}, extra_analysis="require_coverage = false")
    code, report, _ = check_json(root)
    assert report["verdict"] == "WARN" and report["run_outcome"] == "COMPLETE" and code == 0
    assert report["coverage"]["unknown_obligations"][0]["required"] is False


def test_output_is_deterministic_apart_from_run_identity_and_timing():
    def normalized():
        _, report, _ = check_json(DEMO)
        report.pop("run_id")
        report.pop("timing_ms")
        for e in report["evidence"]:
            e.pop("run_id")
            e.pop("created_at")
            for r in e["solver_runs"]:
                r.pop("elapsed_ms")
        return report

    assert normalized() == normalized()


def test_symlink_outside_root_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("def f(a):\n    return 1 / a\n")
    root = tmp_path / "root"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "ok.py").write_text("def g():\n    return 1\n")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "link.py").symlink_to(outside / "secret.py")
    code, report, _ = check_json(root)
    assert [f["path"] for f in report["snapshot"]["files"]] == ["ok.py"]
    assert {e["path"] for e in report["snapshot"]["excluded"]} == {"link", "link.py"}
