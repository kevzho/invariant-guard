"""Phase 4: `guard verify --replay` end to end (CLI, git base, unisolated runner; one Docker case)."""

from __future__ import annotations

import json

import pytest

from ..conftest import run_cli
from .contract_helpers import exec_config
from .gitrepo import make_repo, write

MOD = '''def clamp(x, lo=0, hi=10):
    return max(lo, min(hi, x))


def chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def total(xs):
    s = 0
    for x in xs:
        s += x
    return s
'''
TESTS = '''from demo.mod import clamp, chunks, total

def test_mod():
    assert clamp(5) == 5
    assert chunks([1, 2, 3, 4, 5], 2)
    assert total([1, 2, 3]) == 6
'''


def _repo(tmp_path, cfg=None):
    files = {"demo/__init__.py": "", "demo/mod.py": MOD, "tests/test_mod.py": TESTS}
    return make_repo(tmp_path, files, cfg if cfg is not None else exec_config())


def _verify(root, base, *extra):
    code, out, err = run_cli(["verify", str(root), "--base", base, "--format", "json", *extra])
    assert out.strip(), err
    return code, json.loads(out), err


def _schema_ok(r):
    from invariantguard.models.schema import SchemaValidationUnavailable, validation_errors

    try:
        assert validation_errors("report", r) == []
        for e in r["evidence"]:
            assert validation_errors("evidence", e) == [], e["kind"]
        for f in r["findings"]:
            assert validation_errors("finding", f) == []
    except SchemaValidationUnavailable:
        pass


def test_unintended_change_warns_with_a_replayable_witness(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": MOD.replace("range(0, len(xs), n)", "range(0, len(xs) - 1, n)")})
    code, r, _ = _verify(root, base, "--execute", "--replay", "--no-generate")
    _schema_ok(r)
    (f,) = [f for f in r["findings"] if f["property_result"] == "OBSERVED_DIFFERENCE"]
    assert (f["authority"], f["patch_relation"], f["scope_validity"], f["severity"]) == ("INFERRED", "CHANGED", "EXECUTED", "WARNING")
    b = f["behavior"]
    assert b["witness"]["call"] == "chunks([1, 2, 3, 4, 5], 2)" and b["witness"]["patch"] == "returned [[1, 2], [3, 4]]"
    assert "Was this intended?" in b["question"] and not b["acknowledged_by_patch_tests"]
    assert r["verdict"] == "WARN" and code == 0
    ev = next(e for e in r["evidence"] if e["evidence_id"] in f["evidence_ids"])
    assert ev["kind"] == "REPLAY" and ev["replay"]["isolated"] is False and ev["replay"]["harness"] == "ig-replay-1"


def test_refactor_without_behavior_change_passes(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": MOD.replace("    s = 0\n    for x in xs:\n        s += x\n    return s", "    return sum(xs)")})
    code, r, _ = _verify(root, base, "--execute", "--replay", "--no-generate")
    _schema_ok(r)
    assert [f for f in r["findings"] if f["property_result"] == "OBSERVED_DIFFERENCE"] == []
    assert r["replay"]["status"] == "COMPLETE"
    total = next(t for t in r["replay"]["targets"] if t["target"] == "demo/mod.py::total")
    assert total["executed"] and total["classes"].get("same", 0) >= 1
    assert r["verdict"] in ("PASS", "WARN") and code == 0  # WARN only for the unisolated-runner capability note


def test_change_acknowledged_by_a_new_test_is_info(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": MOD.replace("return max(lo, min(hi, x))", "return lo if x is None else max(lo, min(hi, x))"),
                 "tests/test_none.py": "from demo.mod import clamp\n\ndef test_none():\n    assert clamp(None) == 0\n"})
    code, r, _ = _verify(root, base, "--execute", "--replay", "--no-generate")
    _schema_ok(r)
    (f,) = [f for f in r["findings"] if f["property_result"] == "OBSERVED_DIFFERENCE"]
    assert f["severity"] == "INFO" and f["behavior"]["acknowledged_by_patch_tests"] and f["behavior"]["difference_class"] == "removed_exception"


def test_untested_public_change_is_incomplete(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": MOD + "\n\ndef fresh(a):\n    return a\n"})
    code, r, _ = _verify(root, base, "--execute", "--replay", "--no-generate")
    assert r["replay"]["status"] in ("COMPLETE", "NOTHING_TO_REPLAY")  # an added symbol has no base to compare
    write(root, {"demo/mod.py": MOD.replace("def total(xs):", "def untested(a):\n    return a + 1\n\n\ndef total(xs):")})
    root2, base2 = make_repo(tmp_path, {"demo/__init__.py": "", "demo/mod.py": MOD + "\n\ndef untested(a):\n    return a\n", "tests/test_mod.py": TESTS},
                             exec_config(), name="repo2")
    write(root2, {"demo/mod.py": MOD + "\n\ndef untested(a):\n    return a + 1\n"})
    code, r, _ = _verify(root2, base2, "--execute", "--replay", "--no-generate")
    _schema_ok(r)
    assert r["verdict"] == "INCOMPLETE" and code == 2
    assert any("untested" in x and "no executed input" in x for x in r["verdict_reasons"])


def test_replay_requires_execute_and_a_git_base(tmp_path):
    root, base = _repo(tmp_path)
    code, out, err = run_cli(["verify", str(root), "--base", base, "--format", "json", "--replay"])
    assert code == 4 and "--execute" in err


def test_runner_unavailable_is_incomplete_not_pass(tmp_path):
    cfg = exec_config("docker").replace('image = "python:3.13-slim"', 'image = "invariantguard/does-not-exist:0"')
    root, base = _repo(tmp_path, cfg)
    write(root, {"demo/mod.py": MOD.replace("return s", "return s + 0")})
    code, r, _ = _verify(root, base, "--execute", "--replay")
    _schema_ok(r)
    assert r["replay"]["status"] == "UNAVAILABLE" and r["verdict"] == "INCOMPLETE" and code == 2


def test_replay_in_docker(tmp_path):
    from invariantguard.execution.profiles import parse_profile
    from invariantguard.execution.runner import DockerBackend

    if not DockerBackend(parse_profile("x", {"backend": "docker", "image": "python:3.13-slim", "allowed_targets": ["*::*"]})).status().available:
        pytest.skip("docker backend unavailable here")
    root, base = _repo(tmp_path, exec_config("docker"))
    write(root, {"demo/mod.py": MOD.replace("range(0, len(xs), n)", "range(0, len(xs) - 1, n)")})
    code, r, _ = _verify(root, base, "--execute", "--replay")
    _schema_ok(r)
    (f,) = [f for f in r["findings"] if f["property_result"] == "OBSERVED_DIFFERENCE"]
    ev = next(e for e in r["evidence"] if e["evidence_id"] in f["evidence_ids"])
    assert ev["replay"]["isolated"] is True and ev["replay"]["backend"] == "docker"
    assert "site_packages" in json.dumps(r["replay"]["targets"])  # generation reported as skipped, not silently dropped
    assert r["verdict"] == "WARN" and code == 0


def test_mcp_verify_patch_replay(tmp_path):
    from invariantguard.execution.profiles import parse_profile
    from invariantguard.execution.runner import DockerBackend
    from invariantguard.service import AgentService

    root, base = _repo(tmp_path, exec_config("docker"))
    write(root, {"demo/mod.py": MOD.replace("range(0, len(xs), n)", "range(0, len(xs) - 1, n)")})
    svc = AgentService.from_specs([f"repo={root}"], ["iso"], max_timeout_s=600)
    err = svc.call("verify_patch", {"repository": "repo", "base": base, "replay": True})
    assert err["status"] == "INVALID_REQUEST" and "run_profile" in err["message"]
    if not DockerBackend(parse_profile("x", {"backend": "docker", "image": "python:3.13-slim", "allowed_targets": ["*::*"]})).status().available:
        pytest.skip("docker backend unavailable here")
    r = svc.call("verify_patch", {"repository": "repo", "base": base, "run_profile": "iso", "replay": True})
    (f,) = [f for f in r["findings"] if f["property_result"] == "OBSERVED_DIFFERENCE"]
    assert f["behavior"]["witness"]["call"] == "chunks([1, 2, 3, 4, 5], 2)" and "Was this intended?" in f["behavior"]["question"]
    assert r["replay"]["status"] == "COMPLETE" and r["verdict"] == "WARN"
