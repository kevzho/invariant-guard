"""Run profiles and fail-closed isolation (M3 T33): explicit authorization, RUNNER_UNAVAILABLE -> INCOMPLETE,
no unisolated fallback, and capability checks that gate any target import.

These tests need no Docker daemon: a missing CLI, and a fake ``docker`` executable that answers
like a daemon whose capability probe fails, stand in for unavailable or non-conforming backends.
"""

import json
import os
import stat

from ..conftest import run_cli
from .contract_helpers import NORMALIZE_BASE, NORMALIZE_PATCH, contract_result, declare, exec_config, normalize_contract, repo, write

UNISOLATED_EXTRA = '[execution.profiles.local]\nbackend = "unisolated"\nallowed_targets = ["demo/*.py::*"]\n'


def _docker_repo(tmp_path, docker_path, *, allow_unisolated=True, inferred=False):
    cfg = exec_config("docker", extra_profile=f'docker = "{docker_path}"\n' + (UNISOLATED_EXTRA if allow_unisolated else ""))
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, cfg)
    declare(root, normalize_contract(), tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    return root, base


def _fake_docker(tmp_path, *, capabilities_ok=False, image_present=True):
    log = tmp_path / "docker-calls.log"
    caps = {k: {"verified": capabilities_ok or k != "deny_network"} for k in
            ("deny_network", "readonly_source", "scratch_only_writes", "no_host_credentials", "resource_limits")}
    caps["interpreter"] = {"verified": True}
    resp = json.dumps({"protocol": "ig-probe-1", "mode": "capabilities", "identity": {"version": "3.13.0"}, "capabilities": caps})
    inspect = json.dumps({"Id": "sha256:" + "a" * 64, "RepoDigests": [], "Os": "linux", "Architecture": "arm64", "Config": {"Env": ["PATH=/usr/bin"]}})
    script = tmp_path / "docker"
    script.write_text(f"""#!/bin/sh
echo "$@" >> '{log}'
case "$1" in
  info) echo '"29.0.0-fake"' ;;
  image) {"echo '" + inspect + "'" if image_present else "echo 'Error: No such image' >&2; exit 1"} ;;
  run) cat > /dev/null; echo '{resp}' ;;
  pull) echo 'pull must never happen during a scan' >&2; exit 9 ;;
  *) ;;
esac
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, log


def test_execute_requires_an_enabled_run_profile(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, 'schema_version = "1.0"\n')
    code, _, err = run_cli(["verify", str(root), "--base", base, "--execute", "--run-profile", "anything"])
    assert code == 4 and "not enabled" in err
    code, _, err = run_cli(["verify", str(root), "--base", base, "--run-profile", "anything"])
    assert code == 4 and "--execute" in err
    root2, base2 = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(), name="repo2")
    code, _, err = run_cli(["verify", str(root2), "--base", base2, "--execute", "--run-profile", "missing"])
    assert code == 4 and "not defined" in err


def test_unisolated_needs_explicit_allow(tmp_path):
    cfg = exec_config().replace("allow_unisolated = true", "allow_unisolated = false")
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, cfg)
    code, _, err = run_cli(["verify", str(root), "--base", base, "--execute"])
    assert code == 4 and "allow_unisolated = true" in err


def test_missing_docker_is_runner_unavailable_incomplete_without_fallback(tmp_path, monkeypatch):
    import invariantguard.execution.runner as runner

    def no_fallback(*a, **k):
        raise AssertionError("fell back to unisolated execution")

    monkeypatch.setattr(runner.UnisolatedBackend, "run", no_fallback)
    root, base = _docker_repo(tmp_path, "/nonexistent/bin/docker")
    code, out, err = run_cli(["verify", str(root), "--base", base, "--format", "json", "--execute", "--run-profile", "iso"])
    r = json.loads(out)
    c = contract_result(r, "normalize.sum.v1")
    assert (c["status"], c["property_result"], c["required"]) == ("RUNNER_UNAVAILABLE", "UNKNOWN", True)
    assert any("no unisolated fallback" in x for x in c["reasons"])
    ex = r["contracts"]["execution"]
    assert ex["status"] == "RUNNER_UNAVAILABLE" and "docker CLI not found" in ex["reason"]
    assert all(cap["status"] == "NOT_PROBED" for cap in ex["capabilities"])
    assert r["capabilities"]["execution"] == "unavailable"
    # The static engine still runs; the static finding here is UNKNOWN (division inside a comprehension), so the run is
    # INCOMPLETE, never PASS, and the contract regression is not claimed.
    assert code == 2 and r["verdict"] == "INCOMPLETE" and r["run_outcome"] == "INCOMPLETE"
    assert any("required contract check(s) did not produce evidence (RUNNER_UNAVAILABLE)" in x for x in r["verdict_reasons"])
    assert "RUNNER_UNAVAILABLE" in err


def test_failed_capability_probe_fails_closed_before_any_import(tmp_path):
    fake, log = _fake_docker(tmp_path, capabilities_ok=False)
    root, base = _docker_repo(tmp_path, str(fake), allow_unisolated=False)
    code, out, _ = run_cli(["verify", str(root), "--base", base, "--format", "json", "--execute"])
    r = json.loads(out)
    ex = r["contracts"]["execution"]
    assert ex["status"] == "RUNNER_UNAVAILABLE" and "deny_network" in ex["reason"]
    assert {c["name"]: c["status"] for c in ex["capabilities"]}["deny_network"] == "FAILED"
    calls = log.read_text().splitlines()
    runs = [c for c in calls if c.startswith("run ")]
    assert len(runs) == 1  # only the capability probe container; no probe of target code was started
    assert "--network none" in runs[0] and "--read-only" in runs[0] and "--pull never" in runs[0] and "--cap-drop ALL" in runs[0]
    assert not any(c.startswith("pull") for c in calls)
    assert code == 2 and contract_result(r, "normalize.sum.v1")["status"] == "RUNNER_UNAVAILABLE"


def test_image_is_never_pulled_during_a_scan(tmp_path):
    fake, log = _fake_docker(tmp_path, image_present=False)
    root, base = _docker_repo(tmp_path, str(fake), allow_unisolated=False)
    code, out, _ = run_cli(["verify", str(root), "--base", base, "--format", "json", "--execute"])
    r = json.loads(out)
    assert "guard runner setup" in r["contracts"]["execution"]["reason"] and "never pull" in r["contracts"]["execution"]["reason"]
    assert not any(c.startswith(("pull", "run ")) for c in log.read_text().splitlines())
    assert code == 2


def test_runner_probe_command_fails_closed(tmp_path):
    fake, _ = _fake_docker(tmp_path, capabilities_ok=False)
    root, _ = _docker_repo(tmp_path, str(fake), allow_unisolated=False)
    code, out, err = run_cli(["runner", "probe", "--path", str(root)])
    assert code == 2 and json.loads(out)["status"] == "RUNNER_UNAVAILABLE" and "RUNNER_UNAVAILABLE" in err
    fake_ok, _ = _fake_docker(tmp_path / "ok" if (tmp_path / "ok").mkdir() is None else tmp_path, capabilities_ok=True)
    cfg = (root / ".guard" / "config.toml").read_text().replace(str(fake), str(fake_ok))
    (root / ".guard" / "config.toml").write_text(cfg)
    code, out, _ = run_cli(["runner", "probe", "--path", str(root)])
    assert code == 0 and json.loads(out)["status"] == "AVAILABLE"


def test_inferred_contracts_are_advisory_when_the_runner_is_unavailable(tmp_path):
    from invariantguard.contracts.model import validate_revision
    from invariantguard.contracts.registry import ContractRegistry
    from invariantguard.storage.store import Store

    cfg = exec_config("docker", extra_profile='docker = "/nonexistent/docker"\n', require_coverage=False)
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, cfg)
    doc = {"schema_version": "1.0", "revision": 1, "authority": "INFERRED", "lifecycle_state": "VALIDATED", "supersedes": None,
           "provenance": [{"kind": "template", "reference": "t"}], "reason": "r", "created_at": "2026-10-04T00:00:00Z", **normalize_contract()}
    store = Store(root / ".guard")
    try:
        ContractRegistry(store).create(doc, validate_revision(doc, {"demo/math_ops.py": NORMALIZE_BASE.encode()}, "sha256:" + "0" * 64).semantic_digest,
                                       kind="propose", reason="t")
    finally:
        store.close()
    code, out, _ = run_cli(["verify", str(root), "--base", base, "--format", "json", "--execute"])
    r = json.loads(out)
    assert contract_result(r, "normalize.sum.v1")["required"] is False
    assert r["analysis_plan"]["required_contracts"] == []
    assert any("INFERRED contract check(s) undecided" in x for x in r["verdict_reasons"]) and r["verdict"] == "WARN" and code == 0


def test_runner_setup_is_explicit_and_reports_profile(tmp_path):
    fake, log = _fake_docker(tmp_path, capabilities_ok=True)
    root, _ = _docker_repo(tmp_path, str(fake), allow_unisolated=False)
    code, _, err = run_cli(["runner", "setup", "--path", str(root)])
    # The fake CLI refuses `pull` (exit 9): setup reports the failure as a usage error and records nothing.
    assert code == 4 and "pull" in err
    assert any(c.startswith("pull -- python:3.13-slim") for c in log.read_text().splitlines())


def test_env_of_the_docker_cli_is_minimal(tmp_path, monkeypatch):
    """The docker CLI process (not only the container) gets an explicit environment without the caller's secrets."""
    from invariantguard.execution.profiles import parse_profile
    from invariantguard.execution.runner import DockerBackend

    monkeypatch.setenv("IG_TEST_SECRET", "s3cr3t")
    b = DockerBackend(parse_profile("x", {"backend": "docker", "image": "python:3.13-slim", "allowed_targets": ["a.py::*"]}))
    env = b._env()
    assert "IG_TEST_SECRET" not in env and set(env) <= {"PATH", "LANG", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY"}
    assert b.container_env() == {"HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"}
    assert os.environ["IG_TEST_SECRET"] == "s3cr3t"
