"""Throwaway Git repositories for M2 tests (always under pytest's tmp_path)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from ..conftest import run_cli

GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_ENV)
    env["GIT_CONFIG_GLOBAL"] = os.devnull  # ignore the developer's global config (e.g. LFS filters)
    p = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args], cwd=repo, env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout.strip()


def config(*symbols: str, extra: str = "", profiles: dict[str, str] | None = None) -> str:
    lines = ['schema_version = "1.0"', "[analysis]", "quick_budget_ms = 30000", "full_budget_ms = 60000", extra]
    for s in symbols:
        prof = (profiles or {}).get(s, "python_exact_builtin_ints")
        lines += ["[[domains]]", f'symbol = "{s}"', f'profile = "{prof}"', 'authority = "DECLARED"', 'reason = "test fixture"']
    return "\n".join(lines) + "\n"


IGNORE = ".guard/cache/\n.guard/runs/\n.guard/artifacts/\n.guard/snapshots/\n.guard/state.sqlite*\n"


def write(root: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        p = root / rel
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def make_repo(tmp_path: Path, files: dict[str, str], cfg: str | None = None, name: str = "repo") -> tuple[Path, str]:
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "-q")
    write(root, {".gitignore": IGNORE, **files})
    if cfg is not None:
        write(root, {".guard/config.toml": cfg})
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root, git(root, "rev-parse", "HEAD")


def verify(root: Path, base: str, *extra: str) -> tuple[int, dict, str]:
    code, out, err = run_cli(["verify", str(root), "--base", base, "--format", "json", *extra])
    assert out.strip(), f"no JSON (exit {code}); stderr: {err}"
    return code, json.loads(out), err


def finding(report: dict, obligation_id: str) -> dict:
    for f in report["findings"]:
        if f["obligation_id"] == obligation_id:
            return f
    raise KeyError(f"{obligation_id} not in findings: {[f['obligation_id'] for f in report['findings']]}")


def tree_state(root: Path, skip_state: bool = True) -> dict[str, tuple]:
    """Bytes, mode and mtime of every file (including .git); optionally skipping .guard state."""
    out: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if skip_state and (rel_dir == ".guard" or rel_dir.startswith(".guard/")):
            dirnames[:] = []
            names = [n for n in filenames if n == "config.toml"]
        else:
            names = filenames
        for n in names:
            p = Path(dirpath) / n
            st = p.lstat()
            out[(Path(rel_dir) / n).as_posix()] = (st.st_mode, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    return out
