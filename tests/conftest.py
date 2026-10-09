from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "examples" / "python_demo"
VENV_PY = Path(sys.executable)


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in-process; returns (exit_code, stdout, stderr)."""
    from invariantguard.cli import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:  # argparse --help/--version
            code = exc.code if isinstance(exc.code, int) else 0
    return code, out.getvalue(), err.getvalue()


def run_guard_subprocess(argv: list[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.pop("PYTHONPATH", None)
    if env:
        e.update(env)
    return subprocess.run([str(VENV_PY), "-m", "invariantguard.cli", *argv], capture_output=True, text=True, cwd=cwd, env=e, timeout=120)


def check_json(path: Path, *extra: str) -> tuple[int, dict, str]:
    code, out, err = run_cli(["check", str(path), "--format", "json", *extra])
    assert out.strip(), f"no JSON on stdout (exit {code}); stderr: {err}"
    return code, json.loads(out), err


def write_project(root: Path, files: dict[str, str], config: str | None = None) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if config is not None:
        (root / ".guard").mkdir(exist_ok=True)
        (root / ".guard" / "config.toml").write_text(config, encoding="utf-8")
    return root


def domain_config(*symbols: str, extra_analysis: str = "") -> str:
    lines = ['schema_version = "1.0"', "[analysis]", "quick_budget_ms = 20000", extra_analysis]
    for s in symbols:
        lines += ["[[domains]]", f'symbol = "{s}"', 'profile = "python_exact_builtin_ints"', 'authority = "DECLARED"', 'reason = "test fixture"']
    return "\n".join(lines) + "\n"


def result_for(report: dict, symbol_id: str, ordinal: int = 0) -> dict:
    oid = f"{symbol_id}::division.zero_denominator#{ordinal}"
    for r in report["obligation_results"]:
        if r["obligation_id"] == oid:
            return r
    raise KeyError(f"{oid} not in report: {[r['obligation_id'] for r in report['obligation_results']]}")


@pytest.fixture
def project(tmp_path: Path):
    def make(files: dict[str, str], *domains: str, extra_analysis: str = "") -> Path:
        root = tmp_path / "proj"
        root.mkdir(exist_ok=True)
        (root / ".git").mkdir(exist_ok=True)  # stop config discovery at this root
        return write_project(root, files, domain_config(*domains, extra_analysis=extra_analysis))

    return make


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Tests run offline: any in-process socket connection attempt fails the test."""
    import socket

    def refuse(self, address, *args, **kwargs):
        raise AssertionError(f"network access attempted during tests: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: refuse(None, a))
