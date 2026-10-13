"""Static checks never import/execute the target; engine errors differ from unsupported code."""

import json
import shutil
import sys
from pathlib import Path

import pytest

import invariantguard.analysis.obligations as obligations_mod
import invariantguard.frontends.python.lower as lower_mod
import invariantguard.orchestration as orch

from ..conftest import DEMO, check_json, result_for, run_cli, run_guard_subprocess

SENTINEL = Path(__file__).resolve().parents[1] / "semantic_fixtures" / "python" / "side_effect_sentinel.py"


def _sentinel_project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    shutil.copy(SENTINEL, root / "side_effect_sentinel.py")
    return root


def test_side_effect_sentinel_untouched_subprocess(tmp_path):
    root = _sentinel_project(tmp_path)
    p = run_guard_subprocess(["check", str(root), "--format", "json"], cwd=tmp_path)
    assert not (root / "SENTINEL_TOUCHED").exists(), "static analysis executed the target module"
    report = json.loads(p.stdout)
    r = result_for(report, "side_effect_sentinel.py::ratio")
    # The module was analyzed from bytes: the annotated ratio still yields a conditional witness.
    assert r["property_result"] == "PROVED_UNSAFE" and r["scope_validity"] == "CONDITIONAL"
    assert report["coverage"]["files_analyzed"] == 1


def test_side_effect_sentinel_untouched_in_process(tmp_path):
    root = _sentinel_project(tmp_path)
    before = set(sys.modules)
    code, out, _ = run_cli(["check", str(root), "--format", "json"])
    assert not (root / "SENTINEL_TOUCHED").exists()
    new = set(sys.modules) - before
    assert not any("sentinel" in m for m in new)
    assert json.loads(out)["run_outcome"] == "COMPLETE"


def test_engine_error_in_analysis_is_error_not_unsupported(monkeypatch):
    def boom(self, site):
        raise RuntimeError("injected analyzer fault")

    monkeypatch.setattr(obligations_mod.FunctionChecker, "check_site", boom)
    code, report, _ = check_json(DEMO / "candidate")
    assert code == 3 and report["verdict"] == "ERROR" and report["run_outcome"] == "ERROR"
    assert report["errors"][0]["stage"] == "analysis" and "injected analyzer fault" in report["errors"][0]["message"]
    r = result_for(report, "candidate/ratio.py::ratio")
    assert r["property_result"] == "UNKNOWN" and "engine error" in r["reasons"][0]
    ev = next(e for e in report["evidence"] if e["evidence_id"] == r["evidence_ids"][0])
    assert ev["kind"] == "ENGINE_ERROR"


def test_engine_error_in_lowering_is_error(monkeypatch):
    def boom(self):
        raise RuntimeError("injected lowering fault")

    monkeypatch.setattr(lower_mod.FunctionLowerer, "lower", boom)
    code, report, _ = check_json(DEMO / "baseline")
    assert code == 3 and report["verdict"] == "ERROR" and report["errors"][0]["stage"] == "lowering"
    assert report["coverage"]["required_obligations"] == 1  # the site is still in the denominator


def test_unsupported_construct_is_incomplete_not_error():
    code, report, _ = check_json(DEMO / "adversarial" / "decorated.py")
    assert code == 2 and report["verdict"] == "INCOMPLETE" and report["errors"] == []


def test_witness_disagreement_is_an_engine_error(monkeypatch):
    from invariantguard.ir.evaluator import EvalOutcome

    monkeypatch.setattr(obligations_mod.FunctionChecker, "validate", lambda self, site, args, assertions: EvalOutcome(kind="RETURNED"))
    code, report, _ = check_json(DEMO / "candidate")
    assert code == 3 and report["errors"][0]["error_type"] == "EngineInconsistency"


def test_syntax_error_file_is_incomplete():
    code, report, _ = check_json(DEMO / "adversarial" / "syntax_error.py")
    assert code == 2 and report["verdict"] == "INCOMPLETE"
    (skip,) = report["coverage"]["skipped_checks"]
    assert skip["kind"] == "parse_failure" and "line 1" in skip["reason"]


def test_mid_run_edit_is_detected(monkeypatch, project):
    """An edit during every attempt: one restart, then INCOMPLETE (docs/07). (M1 had no restart.)"""
    root = project({"m.py": "def f(a, b):\n    if a == b:\n        return 0\n    return 1 / (a - b)\n"}, "m.py::f")
    real = orch.check_function
    versions = iter(["def f(a, b):\n    if a != b:\n        return 1 / (a - b)\n    return 0\n",
                     "def f(a, b):\n    if a != b:\n        return 2 / (a - b)\n    return 0\n", "x = 1\n"])

    def editing(fn, *args, **kwargs):
        (root / "m.py").write_text(next(versions))
        return real(fn, *args, **kwargs)

    monkeypatch.setattr(orch, "check_function", editing)
    code, report, err = check_json(root)
    assert code == 2 and report["run_outcome"] == "INCOMPLETE"
    assert report["coverage"]["skipped_checks"][0]["kind"] == "source_changed_during_run"
    assert any(d["kind"] == "snapshot_restarted" for d in report["diagnostics"])
    assert "restarting once" in err


def test_single_mid_run_edit_restarts_on_a_consistent_capture(monkeypatch, project):
    """An edit during the first attempt only: the run restarts once and reports the final content."""
    root = project({"m.py": "def f(a, b):\n    if a == b:\n        return 0\n    return 1 / (a - b)\n"}, "m.py::f")
    real = orch.check_function
    edited = []

    def editing(fn, *args, **kwargs):
        if not edited:
            (root / "m.py").write_text("def f(a, b):\n    return 1 / (a - b)\n")
            edited.append(True)
        return real(fn, *args, **kwargs)

    monkeypatch.setattr(orch, "check_function", editing)
    code, report, _ = check_json(root)
    assert report["coverage"]["skipped_checks"] == []
    assert any(d["kind"] == "snapshot_restarted" for d in report["diagnostics"])
    r = result_for(report, "m.py::f")
    assert r["property_result"] == "PROVED_UNSAFE" and code == 1  # the edited (final) content was analyzed
    assert report["snapshot"]["files"][0]["sha256"] == __import__("invariantguard.models.spans", fromlist=["x"]).sha256_bytes((root / "m.py").read_bytes())


def test_file_added_mid_run_is_detected(monkeypatch, project):
    root = project({"m.py": "def f(a):\n    return 1\n"}, "m.py::f")
    real = orch.check_function
    n = {"i": 0}

    def adding(fn, *args, **kwargs):
        n["i"] += 1
        (root / f"new_{n['i']}.py").write_text("def g():\n    return 1\n")
        return real(fn, *args, **kwargs)

    monkeypatch.setattr(orch, "check_function", adding)
    code, report, _ = check_json(root)
    kinds = [s["kind"] for s in report["coverage"]["skipped_checks"]]
    assert code == 2 and kinds and set(kinds) == {"source_changed_during_run"}
    assert any(s["path"].startswith("new_") for s in report["coverage"]["skipped_checks"])


def test_budget_exhaustion_is_unknown_but_cheap_proofs_remain():
    import time
    from types import SimpleNamespace as NS

    from invariantguard.analysis.obligations import Budget, check_function
    from invariantguard.frontends.python import lower_module, parse_source
    from invariantguard.models.enums import SolverStatus
    from invariantguard.solver.z3_backend import Z3Backend

    src = b"def f(a, b):\n    d = a - b\n    if d != 0:\n        return 1 / d\n    return 1 / (a - b)\n"
    decl = NS(params=None, profile="python_exact_builtin_ints", authority="DECLARED", reason="t")
    fn = lower_module(parse_source("m.py", src), "sha256:" + "0" * 64, {"m.py::f": decl}).module.functions[0]
    out = check_function(fn, Z3Backend(100), SolverStatus.SAT, Budget(deadline=time.monotonic() - 1, per_query_timeout_ms=100), "t")
    assert [o.result.value for o in out] == ["PROVED_SAFE", "UNKNOWN"]
    assert out[1].method.value == "BUDGET"
