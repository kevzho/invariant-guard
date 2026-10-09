"""M5 agent service (shared by the MCP server and `guard explain`): T50-T52 without the MCP SDK.

Covers root/path/profile restriction, the absence of approval/baseline/exec tools, cancellation
that never publishes cache entries, evidence fetching with staleness, explanations and schema
compatibility of everything returned.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from invariantguard import cancellation
from invariantguard.models.schema import validation_errors
from invariantguard.orchestration import UsageError
from invariantguard.service import NOT_EXPOSED, TOOL_SPECS, TOOLS, AgentService, ServiceError, validate_args

from ..conftest import run_cli
from .gitrepo import config, git, make_repo, write

SAFE = "def ratio(a: int, b: int):\n    d = a - b\n    if d == 0:\n        return 0.0\n    return 100 / d\n"
UNSAFE = "def ratio(a: int, b: int):\n    d = a - b\n    return 100 / d\n"
RATIO = "demo/math_ops.py::ratio"


def _repo(tmp_path: Path, cfg: str | None = None, name: str = "repo") -> tuple[Path, str]:
    root, base = make_repo(tmp_path, {"demo/math_ops.py": SAFE}, cfg if cfg is not None else config(RATIO), name=name)
    return root, base


def _svc(*roots: Path, profiles: tuple[str, ...] = (), timeout: float = 600.0) -> AgentService:
    return AgentService.from_specs([f"{r.name}={r}" for r in roots], list(profiles), max_timeout_s=timeout)


# -- T51: roots, paths, tools ---------------------------------------------------------------------------
def test_root_specs_are_validated(tmp_path):
    with pytest.raises(UsageError, match="absolute"):
        AgentService.from_specs(["relative/path"], [])
    with pytest.raises(UsageError, match="does not exist"):
        AgentService.from_specs([str(tmp_path / "missing")], [])
    (tmp_path / "a").mkdir()
    with pytest.raises(UsageError, match="duplicate"):
        AgentService.from_specs([f"x={tmp_path / 'a'}", f"y={tmp_path / 'a'}"], [])
    with pytest.raises(UsageError, match="alias"):
        AgentService.from_specs([f"bad alias={tmp_path / 'a'}"], [])
    with pytest.raises(UsageError, match="at least one"):
        AgentService.from_specs([], [])


def test_only_configured_roots_and_inside_paths_are_served(tmp_path):
    root, base = _repo(tmp_path)
    other, _ = _repo(tmp_path, name="other")
    (root / "escape").symlink_to(other, target_is_directory=True)
    svc = _svc(root)
    ok = svc.call("check_scope", {"repository": str(root)})  # the exact root path is accepted like its alias
    assert ok["status"] == "OK" and ok["repository"] == "repo"
    for repo_arg in (str(other), str(root / "demo"), "other", str(tmp_path)):
        r = svc.call("verify_patch", {"repository": repo_arg, "base": base})
        assert r["status"] == "NOT_AUTHORIZED", repo_arg
    for path_arg, status in (("../other", "NOT_AUTHORIZED"), ("escape", "NOT_AUTHORIZED"), (str(other), "INVALID_REQUEST"), ("nope", "INVALID_REQUEST")):
        r = svc.call("verify_patch", {"repository": "repo", "base": base, "path": path_arg})
        assert r["status"] == status, (path_arg, r)
    r = svc.call("verify_patch", {"repository": "repo", "base": "--output=/tmp/x"})
    assert r["status"] == "INVALID_REQUEST"
    assert git(root, "status", "--porcelain") == "?? escape"  # nothing in the repository was changed by any call


def test_tool_set_has_no_approval_baseline_or_execution_tools():
    assert TOOLS == ("verify_patch", "check_scope", "get_contracts", "get_finding", "explain_finding", "get_context_packet", "propose_contracts",
                     "get_capabilities")
    forbidden = ("approve", "revise", "reject", "retire", "declare", "import", "baseline", "setup", "discover", "exec", "run_command", "shell")
    for name in TOOLS:
        assert not any(w in name for w in forbidden)
        assert TOOL_SPECS[name]["input_schema"]["additionalProperties"] is False
    # Not even through argument smuggling: no tool accepts a flag that would execute or approve.
    all_args = {k for spec in TOOL_SPECS.values() for k in spec["input_schema"]["properties"]}
    assert all_args == {"repository", "base", "mode", "path", "run_profile", "detail", "timeout_s", "no_cache", "finding_id", "run_id", "symbol",
                        "context_packet_digest", "candidates", "dry_run"}
    # propose_contracts takes candidates only: no authority, approval, provenance or client-identity argument
    assert set(TOOL_SPECS["propose_contracts"]["input_schema"]["properties"]) == {"repository", "symbol", "context_packet_digest", "candidates",
                                                                                   "run_profile", "dry_run", "timeout_s"}
    assert set(NOT_EXPOSED) >= {"baseline create", "arbitrary command execution", "contracts discover"}
    for bad in ("approve_contract", "baseline_create", "contracts", "__init__", "_root"):
        with pytest.raises(ServiceError) as ei:
            validate_args(bad, {})
        assert ei.value.status == "INVALID_REQUEST"


def test_contract_authority_is_unchanged_by_every_tool(tmp_path):
    from .contract_helpers import declare, normalize_contract

    root, base = make_repo(tmp_path, {"demo/math_ops.py": SAFE + "\n\n" + "def normalize(xs):\n    total = sum(xs)\n    return [x / total for x in xs]\n"},
                           config(RATIO))
    declare(root, normalize_contract(), tmp_path)
    svc = _svc(root)
    before = svc.call("get_contracts", {"repository": "repo"})
    assert before["status"] == "OK" and [c["authority"] for c in before["contracts"]] == ["DECLARED"]
    write(root, {"demo/math_ops.py": UNSAFE})
    for tool, args in (("verify_patch", {"base": base}), ("verify_patch", {"base": base, "mode": "full"}), ("check_scope", {}), ("get_capabilities", None)):
        svc.call(tool, {"repository": "repo", **args} if args is not None else {})
    after = svc.call("get_contracts", {"repository": "repo"})
    assert [(c["contract_id"], c["authority"], c["lifecycle_state"], c["revision"]) for c in after["contracts"]] == \
           [(c["contract_id"], c["authority"], c["lifecycle_state"], c["revision"]) for c in before["contracts"]]
    with sqlite3.connect(root / ".guard" / "state.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM contract_events").fetchone()[0] == 1  # only the maintainer's declare
        assert db.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 0


def test_config_is_read_only_from_the_root_never_a_parent(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    write(outer, {".guard/config.toml": config("inner/demo/math_ops.py::ratio")})
    inner = outer / "inner"
    inner.mkdir()
    write(inner, {"demo/math_ops.py": UNSAFE})  # not a Git repository: plain discovery would walk up to outer/.guard
    code, out, _ = run_cli(["check", str(inner), "--format", "json"])
    plain = json.loads(out)
    assert any(f["scope_validity"] == "EXACT_MODEL" for f in plain["findings"])  # the CLI found the parent's declared domain
    r = AgentService.from_specs([f"inner={inner}"], []).call("check_scope", {"repository": "inner"})
    assert r["status"] == "OK" and r["config"]["digest"] is None
    assert [f["scope_validity"] for f in r["findings"]] == ["CONDITIONAL"]  # annotation-only domain: the parent config was not used


def _profile_cfg(backend: str = "docker", memory: int = 256) -> str:
    unis = "true" if backend == "unisolated" else "false"
    img = 'image = "python:3.13-slim"\n' if backend == "docker" else ""
    return config(RATIO) + (f'[execution]\nenabled = true\nprofile = "iso"\nallow_unisolated = {unis}\n'
                            f'[execution.profiles.iso]\nbackend = "{backend}"\n{img}allowed_targets = ["demo/*.py::*"]\nmemory_mb = {memory}\n')


def test_run_profiles_must_be_preauthorized_isolated_and_unchanged(tmp_path):
    root, base = _repo(tmp_path, _profile_cfg())
    svc_none = _svc(root)
    r = svc_none.call("verify_patch", {"repository": "repo", "base": base, "run_profile": "iso"})
    assert r["status"] == "NOT_AUTHORIZED" and "not preauthorized" in r["message"]
    svc = _svc(root, profiles=("iso",))
    assert svc.roots["repo"].profiles.keys() == {"iso"}
    svc._authorize_profile(svc.roots["repo"], "iso")  # unchanged definition: authorized
    write(root, {".guard/config.toml": _profile_cfg(memory=4096)})  # the repository edits the preauthorized profile
    r = svc.call("verify_patch", {"repository": "repo", "base": base, "run_profile": "iso"})
    assert r["status"] == "NOT_AUTHORIZED" and "changed since the server started" in r["message"]
    root2, base2 = _repo(tmp_path, _profile_cfg("unisolated"), name="unis")
    svc2 = AgentService.from_specs([f"unis={root2}"], ["iso"])
    assert svc2.roots["unis"].profiles == {} and "not isolated" in svc2.roots["unis"].profile_notes["iso"]
    r = svc2.call("verify_patch", {"repository": "unis", "base": base2, "run_profile": "iso"})
    assert r["status"] == "NOT_AUTHORIZED" and "not isolated" in r["message"]
    caps = svc.call("get_capabilities", {})
    assert caps["status"] == "OK" and caps["server"]["roots"][0]["run_profiles"] == ["iso"]


# -- T52: cancellation and stale evidence -----------------------------------------------------------------
def _many_functions(n: int) -> str:
    return "\n\n".join(f"def f{i}(a: int, b: int):\n    d = a - b + {i}\n    return 100 / d\n" for i in range(n))


def test_cancelled_verify_publishes_no_cache_entries_evidence_or_report(tmp_path, monkeypatch):
    from invariantguard import orchestration
    from invariantguard.verify import VerifyOptions, run_verify

    root, base = make_repo(tmp_path, {"demo/many.py": _many_functions(12)}, config(*[f"demo/many.py::f{i}" for i in range(12)]))
    write(root, {"demo/many.py": _many_functions(12).replace("+ 3", "+ 0")})
    token = cancellation.CancelToken()
    real = orchestration.check_function
    calls = {"n": 0}

    def cancelling(*a, **k):
        calls["n"] += 1
        if calls["n"] == 4:
            token.cancel("test cancellation")
        return real(*a, **k)

    monkeypatch.setattr(orchestration, "check_function", cancelling)
    with cancellation.bound(token), pytest.raises(cancellation.Cancelled, match="test cancellation"):
        run_verify(VerifyOptions(base=base, mode="full", path=root, root=root))
    assert calls["n"] == 4  # stopped at the next per-symbol safe point
    with sqlite3.connect(root / ".guard" / "state.sqlite") as db:
        runs = db.execute("SELECT status, note FROM runs").fetchall()
        assert runs == [("failed", "cancelled: test cancellation")]
        assert db.execute("SELECT COUNT(*) FROM cache_entries WHERE status = 'complete'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM run_reports").fetchone()[0] == 0
    monkeypatch.setattr(orchestration, "check_function", real)
    res = run_verify(VerifyOptions(base=base, mode="full", path=root, root=root))
    cache = res.report["patch"]["cache"]
    assert cache["candidate"]["hits"] == 0 and cache["base"]["hits"] == 0  # nothing from the cancelled run was reused
    assert res.report["verdict"] == "BLOCK" and res.report_digest is not None


def test_cancellation_kills_a_running_process(tmp_path):
    from invariantguard.execution.process import minimal_env, run_argv

    token = cancellation.CancelToken()
    threading.Timer(0.3, token.cancel, args=("stop",)).start()
    t0 = time.monotonic()
    with cancellation.bound(token), pytest.raises(cancellation.Cancelled):
        run_argv(["/bin/sleep", "30"], stdin=b"", env=minimal_env(), timeout_s=60, max_stdout=1024)
    assert time.monotonic() - t0 < 5
    with cancellation.bound(token), pytest.raises(cancellation.Cancelled):  # never starts a process once cancelled
        run_argv(["/bin/echo", "x"], stdin=b"", env=minimal_env(), timeout_s=5, max_stdout=1024)


def test_findings_are_fetchable_and_marked_stale_after_the_repair(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/math_ops.py": UNSAFE})
    svc = _svc(root)
    v = svc.call("verify_patch", {"repository": "repo", "base": base, "detail": "full"})
    assert v["status"] == "OK" and v["verdict"] == "BLOCK" and v["exit_code"] == 1
    assert validation_errors("report", v["report"]) == []
    (f,) = [x for x in v["findings"] if x["patch_relation"] == "INTRODUCED"]
    assert f["property_result"] == "PROVED_UNSAFE" and f["witness_validated"] and f["witness"]["a"] == f["witness"]["b"]
    g = svc.call("get_finding", {"repository": "repo", "finding_id": f["finding_id"]})
    assert g["status"] == "OK" and g["freshness"]["evidence_status"] == "CURRENT" and g["run"]["run_id"] == v["run_id"]
    assert g["run"]["report_digest"] == v["report_digest"] and g["run"]["source"] == "state_index"
    assert validation_errors("finding", g["finding"]) == []
    assert g["evidence"] and all(validation_errors("evidence", e) == [] for e in g["evidence"]) and not g["missing_evidence_ids"]
    assert g["obligation_result"]["obligation_id"] == g["finding"]["obligation_id"]
    e = svc.call("explain_finding", {"repository": "repo", "finding_id": f["finding_id"]})
    assert e["status"] == "OK" and "PROVED_UNSAFE" in e["text"] and "does not establish that any production caller" in e["text"]
    assert e["explanation"]["witness"] == f["witness"] and "no model" in e["explanation"]["explainer"]

    write(root, {"demo/math_ops.py": SAFE})  # the repair
    g2 = svc.call("get_finding", {"repository": "repo", "finding_id": f["finding_id"]})
    assert g2["status"] == "STALE_SNAPSHOT" and g2["freshness"]["evidence_status"] == "STALE"
    assert g2["freshness"]["current_snapshot"] != g2["freshness"]["evidence_snapshot"]
    v2 = svc.call("verify_patch", {"repository": "repo", "base": base})
    assert v2["status"] == "OK" and v2["verdict"] == "PASS" and v2["findings"] == [] and v2["candidate_snapshot"] != v["candidate_snapshot"]
    g3 = svc.call("get_finding", {"repository": "repo", "finding_id": f["finding_id"]})
    assert g3["status"] == "STALE_SNAPSHOT" and g3["freshness"]["superseded_by_run"] == v2["run_id"]
    n = svc.call("get_finding", {"repository": "repo", "finding_id": "fnd-" + "0" * 32})
    assert n["status"] == "NOT_FOUND"
    bad = svc.call("get_finding", {"repository": "repo", "finding_id": "../../etc/passwd"})
    assert bad["status"] == "INVALID_REQUEST"


def test_check_scope_findings_live_in_the_session(tmp_path):
    root, _ = _repo(tmp_path)
    write(root, {"demo/math_ops.py": UNSAFE})
    svc = _svc(root)
    c = svc.call("check_scope", {"repository": "repo", "path": "demo"})
    assert c["status"] == "OK" and c["verdict"] == "BLOCK" and c["scope"] == "demo"
    fid = c["findings"][0]["finding_id"]
    g = svc.call("get_finding", {"repository": "repo", "finding_id": fid})
    assert g["status"] == "OK" and g["run"]["source"] == "session" and g["freshness"]["evidence_status"] == "CURRENT"
    assert _svc(root).call("get_finding", {"repository": "repo", "finding_id": fid})["status"] == "NOT_FOUND"  # another session


def test_stale_snapshot_during_a_run_is_an_explicit_status(tmp_path, monkeypatch):
    from invariantguard import verify as verify_mod

    root, base = _repo(tmp_path)
    write(root, {"demo/math_ops.py": UNSAFE})
    real = verify_mod.verify_unchanged
    n = {"i": 0}

    def edit_then_check(snap):
        n["i"] += 1
        (root / "demo" / "math_ops.py").write_text(UNSAFE + f"\n# edit {n['i']}\n")  # an agent keeps editing during every attempt
        return real(snap)

    monkeypatch.setattr(verify_mod, "verify_unchanged", edit_then_check)
    r = _svc(root).call("verify_patch", {"repository": "repo", "base": base})
    # BLOCK outranks INCOMPLETE in the docs/09 policy (the captured snapshot really had the failure); never PASS.
    assert n["i"] == 2 and r["status"] == "STALE_SNAPSHOT" and r["verdict"] in ("BLOCK", "INCOMPLETE") and r["run_outcome"] == "INCOMPLETE"
    assert "call verify_patch again" in r["message"]
    assert any(s["kind"] == "source_changed_during_run" for s in r["coverage"]["skipped_checks"])
    with sqlite3.connect(root / ".guard" / "state.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM cache_entries WHERE status = 'complete'").fetchone()[0] == 0


def test_unknown_required_work_is_incomplete_not_pass_through_the_service(tmp_path):
    src = "def imported_ratio(a, b):\n    d = external_transform(a, b)\n    return 100 / d\n"
    root, base = make_repo(tmp_path, {"demo/u.py": "def imported_ratio(a, b):\n    return 0\n"}, config("demo/u.py::imported_ratio"))
    write(root, {"demo/u.py": src})
    r = _svc(root).call("verify_patch", {"repository": "repo", "base": base})
    assert r["status"] == "OK" and r["verdict"] == "INCOMPLETE" and r["exit_code"] == 2 and r["coverage"]["unknown_obligations"]


def test_guard_explain_reads_recorded_cli_reports(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/math_ops.py": UNSAFE})
    code, out, _ = run_cli(["verify", str(root), "--base", base, "--format", "json"])
    rep = json.loads(out)
    fid = next(f["finding_id"] for f in rep["findings"] if f["patch_relation"] == "INTRODUCED")
    code, out, err = run_cli(["explain", fid, "--path", str(root), "--format", "json"])
    assert code == 0, err
    doc = json.loads(out)
    assert doc["run"]["run_id"] == rep["run_id"] and doc["freshness"]["evidence_status"] == "CURRENT"
    assert doc["explanation"]["patch_relation"]["value"] == "INTRODUCED"
    code, out, _ = run_cli(["explain", fid, "--path", str(root)])
    assert code == 0 and "witness:" in out and "to resolve:" in out
    code, _, err = run_cli(["explain", "fnd-" + "1" * 32, "--path", str(root)])
    assert code == 4 and "NOT_FOUND" in err
    with sqlite3.connect(root / ".guard" / "state.sqlite") as db, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM run_reports")
