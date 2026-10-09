"""Runner boundaries (docs/10 operational acceptance): output limits, unsupported/cyclic values, unauthorized
targets, path traversal, process-group kill and cancellation. Uses the explicit unisolated backend (no daemon)."""

import json
import os
import sqlite3
import sys
import tempfile
import time

import pytest

from invariantguard.execution.process import minimal_env, run_argv
from invariantguard.execution.runner import RunnerError, Workspace

from ..conftest import run_cli
from .contract_helpers import NORMALIZE_BASE, NORMALIZE_PATCH, contract_result, declare, exec_config, normalize_contract, repo, verify_exec, write


def _one(tmp_path, patch, cfg=None, contract=None):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, cfg or exec_config(require_coverage=False))
    declare(root, contract or normalize_contract(examples=[]), tmp_path)
    write(root, {"demo/math_ops.py": patch})
    return root, base


def test_oversized_results_are_output_limit_unknown(tmp_path):
    cfg = exec_config().replace("max_probes = 24", "max_probes = 2\nmax_result_bytes = 1024")
    root, base = _one(tmp_path, "def normalize(xs):\n    return [1.0 / 3] * 5000\n", cfg)
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert c["property_result"] == "UNKNOWN" and any("output_limit" in x for x in c["reasons"]) and code == 2


def test_cyclic_or_custom_results_are_unknown_not_failures(tmp_path):
    patch = "class Box:\n    pass\n\n\ndef normalize(xs):\n    out = [x / sum(xs) for x in xs]\n    out.append(out)\n    return out\n"
    root, base = _one(tmp_path, patch, exec_config(require_coverage=False).replace("max_probes = 24", "max_probes = 3"))
    _, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert c["property_result"] == "UNKNOWN" and "unsupported value" in " ".join(c["reasons"])


def test_unauthorized_target_is_not_run(tmp_path):
    cfg = exec_config().replace('allowed_targets = ["demo/*.py::*"]', 'allowed_targets = ["demo/other.py::*"]')
    root, base = _one(tmp_path, NORMALIZE_PATCH, cfg)
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert c["status"] == "NOT_ALLOWED" and "does not allowlist" in c["reasons"][0] and code == 2 and r["verdict"] == "INCOMPLETE"
    assert any("(NOT_ALLOWED)" in x for x in r["verdict_reasons"])
    assert "probes" not in c


@pytest.mark.parametrize("bad", ["../escape.py", "/etc/evil.py", "a/../../b.py", "nul\x00.py"])
def test_workspace_rejects_path_traversal(bad):
    before = set(os.listdir(tempfile.gettempdir()))
    with pytest.raises(RunnerError):
        Workspace({bad: b"x = 1\n"}, "t")
    assert [d for d in set(os.listdir(tempfile.gettempdir())) - before if d.startswith("ig-")] == []  # no half-built workspace left


def test_timeout_kills_the_whole_process_group(tmp_path):
    pids = tmp_path / "pids"
    code = (f"import os, time\nopen({str(pids)!r}, 'w').write(str(os.getpid()))\npid = os.fork()\n"
            f"if pid == 0:\n    open({str(pids)!r}, 'a').write(' ' + str(os.getpid()))\n    time.sleep(60)\nelse:\n    time.sleep(60)\n")
    t0 = time.monotonic()
    res = run_argv([sys.executable, "-I", "-c", code], stdin=b"", env=minimal_env(), timeout_s=1.0, max_stdout=1024)
    assert res.timed_out and time.monotonic() - t0 < 15
    time.sleep(0.5)
    for pid in map(int, pids.read_text().split()):
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if alive:  # a zombie of an already-killed child is acceptable only if it is not running
            assert os.waitpid(pid, os.WNOHANG) is not None
    assert "IG" not in "".join(minimal_env())


def test_cancellation_cleans_up_and_publishes_nothing(tmp_path, monkeypatch):
    import invariantguard.execution.runner as runner

    root, base = _one(tmp_path, NORMALIZE_PATCH)
    real = runner.UnisolatedBackend.run
    calls = []

    def interrupt(self, ws, req):
        calls.append(req["mode"])
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(self, ws, req)

    monkeypatch.setattr(runner.UnisolatedBackend, "run", interrupt)
    before = set(os.listdir(tempfile.gettempdir()))
    with pytest.raises(KeyboardInterrupt):
        run_cli(["verify", str(root), "--base", base, "--format", "json", "--execute"])
    leaked = [d for d in set(os.listdir(tempfile.gettempdir())) - before if d.startswith("ig-")]
    assert leaked == []
    db = sqlite3.connect(root / ".guard" / "state.sqlite")
    try:
        assert db.execute("SELECT COUNT(*) FROM contract_evidence").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM cache_entries WHERE status = 'complete'").fetchone()[0] == 0
        assert {s for (s,) in db.execute("SELECT status FROM runs")} == {"running"}
    finally:
        db.close()


def test_human_report_lists_contract_results(tmp_path):
    root, base = _one(tmp_path, NORMALIZE_PATCH, contract=normalize_contract())
    code, out, _ = run_cli(["verify", str(root), "--base", base, "--execute"])
    assert code == 1
    assert "contracts: execution AVAILABLE (unisolated, profile local)" in out
    assert "normalize.sum.v1 r1 [DECLARED] CHECKED OBSERVED_REGRESSION INTRODUCED severity=ERROR" in out
    assert "minimized from {'xs': [1.0, 1.0]}" in out
