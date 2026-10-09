"""Regression tests for the M4/M5 audit (docs/IMPLEMENTATION_STATUS.md, "Audit (M4/M5)").

A5-01: the read-only get_contracts tool created .guard/state.sqlite in a repository without
guard state.
A5-02: a run profile could be swapped between the MCP server's authorization check and the
run's own configuration load (time-of-check/time-of-use), so a repository edit could turn a
preauthorized docker profile into unisolated execution of the target code.
A5-03: identifiers were matched with ``re.match`` and ``$``, which accepts a trailing newline.
"""

from __future__ import annotations

import json
from pathlib import Path

from invariantguard.service import AgentService
from invariantguard.verify import ProfileNotAuthorized, VerifyOptions, run_verify

from .contract_helpers import NORMALIZE_BASE, NORMALIZE_PATCH, declare, normalize_contract
from .gitrepo import make_repo, tree_state, write


def _cfg(backend: str) -> str:
    unis = "true" if backend == "unisolated" else "false"
    img = 'image = "python:3.13-slim"\n' if backend == "docker" else ""
    return ('schema_version = "1.0"\n[analysis]\nquick_budget_ms = 30000\nrequire_coverage = false\n'
            f'[execution]\nenabled = true\nprofile = "iso"\nallow_unisolated = {unis}\n'
            f'[execution.profiles.iso]\nbackend = "{backend}"\n{img}allowed_targets = ["demo/*.py::*"]\n')


def test_a5_01_get_contracts_never_creates_state(tmp_path: Path):
    root = tmp_path / "plain"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "m.py").write_text("def f(a: int):\n    return 1 / a\n", encoding="utf-8")
    svc = AgentService.from_specs([f"plain={root}"], [])
    before = tree_state(root, skip_state=False)
    for tool, args in [("get_contracts", {"repository": "plain"}), ("check_scope", {"repository": "plain"}), ("get_capabilities", {}),
                       ("get_finding", {"repository": "plain", "finding_id": "fnd-0123456789abcdef"}),
                       ("explain_finding", {"repository": "plain", "finding_id": "fnd-0123456789abcdef"})]:
        r = svc.call(tool, args)
        assert r["status"] in ("OK", "NOT_FOUND"), r
        if tool == "get_contracts":
            assert r["contracts"] == [] and "nothing was created" in r["diagnostics"][0]
    assert tree_state(root, skip_state=False) == before
    assert not (root / ".guard").exists()


def test_a5_02_profile_swapped_after_authorization_never_executes(tmp_path: Path):
    marker = tmp_path / "TARGET-CODE-RAN"
    patch = f"open({str(marker)!r}, 'w').close()\n" + NORMALIZE_PATCH
    root, base = make_repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, _cfg("docker"), name="app")
    declare(root, normalize_contract(), tmp_path)
    write(root, {"demo/math_ops.py": patch})
    svc = AgentService.from_specs([f"app={root}"], ["iso"])
    assert svc.roots["app"].profiles.keys() == {"iso"}
    original = svc._authorize_profile

    def racing(entry, name):
        original(entry, name)  # the check passes on the preauthorized docker definition ...
        write(root, {".guard/config.toml": _cfg("unisolated")})  # ... then the repository swaps the profile

    svc._authorize_profile = racing  # type: ignore[method-assign]
    r = svc.call("verify_patch", {"repository": "app", "base": base, "run_profile": "iso"})
    assert r["status"] == "NOT_AUTHORIZED", r
    assert "preauthorized" in r["message"] and "nothing was executed" in r["message"]
    assert not marker.exists(), "target code ran through a profile that was not preauthorized"


def test_a5_02_verify_checks_the_digest_against_its_own_configuration(tmp_path: Path):
    root, base = make_repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, _cfg("docker"), name="app")
    opts = VerifyOptions(base=base, path=root, root=root, execute=True, run_profile="iso", no_config_search=True,
                         config_path=root / ".guard" / "config.toml", authorized_profile_digest="sha256:" + "0" * 64)
    try:
        run_verify(opts)
    except ProfileNotAuthorized as exc:
        assert "nothing was executed" in str(exc)
    else:
        raise AssertionError("a run with a non-matching preauthorized digest must be refused")


def test_a5_03_identifiers_with_a_trailing_newline_are_rejected(tmp_path: Path):
    root, _ = make_repo(tmp_path, {"demo/m.py": "x = 1\n"}, None, name="app")
    svc = AgentService.from_specs([f"app={root}"], [])
    for args in ({"repository": "app", "finding_id": "fnd-0123456789abcdef\n"},
                 {"repository": "app", "finding_id": "fnd-0123456789abcdef", "run_id": "run-0123456789abcdef\n"}):
        r = svc.call("get_finding", args)
        assert r["status"] == "INVALID_REQUEST", json.dumps(r)
    try:
        AgentService.from_specs([f"app\n={root}"], [])
    except Exception as exc:  # noqa: BLE001 - UsageError
        assert "alias" in str(exc)
    else:
        raise AssertionError("an alias with a trailing newline must be rejected")


def test_mcp_static_tools_never_import_the_target(tmp_path: Path):
    """verify_patch without a run profile, check_scope, get_finding and explain_finding (which recapture the
    tree for freshness) analyze source only: a module that writes a marker and exits at import is never run."""
    marker = tmp_path / "IMPORTED"
    head = f"open({str(marker)!r}, 'w').close()\nraise SystemExit('static analysis must never execute this module')\n\n\n"
    safe = head + "def ratio(a: int, b: int):\n    d = a - b\n    if d == 0:\n        return 0.0\n    return 100 / d\n"
    unsafe = head + "def ratio(a: int, b: int):\n    d = a - b\n    return 100 / d\n"
    root, base = make_repo(tmp_path, {"demo/math_ops.py": safe}, None, name="app")
    write(root, {"demo/math_ops.py": unsafe})
    svc = AgentService.from_specs([f"app={root}"], [])
    for mode in ("quick", "full"):
        v = svc.call("verify_patch", {"repository": "app", "base": base, "mode": mode})
        assert v["status"] == "OK" and v["verdict"] in ("WARN", "BLOCK", "INCOMPLETE"), v
        fids = [f["finding_id"] for f in v["findings"] if f["property_result"] == "PROVED_UNSAFE"]
        assert fids, v["findings"]
        assert svc.call("get_finding", {"repository": "app", "finding_id": fids[0]})["status"] == "OK"
        assert svc.call("explain_finding", {"repository": "app", "finding_id": fids[0]})["status"] == "OK"
    c = svc.call("check_scope", {"repository": "app"})
    assert c["status"] == "OK" and any(f["property_result"] == "PROVED_UNSAFE" for f in c["findings"]), c
    assert not marker.exists(), "an MCP tool imported the repository's code"


def test_a4_02_falsification_engine_fault_is_error_not_complete(tmp_path: Path, monkeypatch):
    """A candidate whose falsification hits an engine fault made discovery COMPLETE with exit 0."""
    from invariantguard.contracts import falsify as falsify_mod

    from ..conftest import run_cli
    from .contract_helpers import exec_config

    lib = "def total(xs: list[int]) -> int:\n    return sum(xs)\n"
    root, _ = make_repo(tmp_path, {"demo/lib.py": lib}, exec_config("unisolated"), name="app")

    def broken(*a, **k):
        raise RuntimeError("injected checker fault")

    monkeypatch.setattr(falsify_mod, "falsify", broken)
    code, out, err = run_cli(["contracts", "discover", "--path", str(root), "--execute", "--dry-run"])
    doc = json.loads(out)
    assert doc["counts"]["template"].get("would_propose", 0) >= 1, doc["counts"]
    assert code == 3 and doc["run_outcome"] == "ERROR", (code, doc["run_outcome"])
    assert doc["engine_errors"] and "injected checker fault" in doc["engine_errors"][0]
    assert "ERROR: falsification engine fault" in err
