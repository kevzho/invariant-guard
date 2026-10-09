"""Fixture D (docs/13) end to end with `guard verify`, in a throwaway Git repository.

    .venv/bin/python examples/patch_demo/run_demo.py [--keep DIR]

The script creates a temporary Git repository (never this one), commits the baseline
(`denominator(x)` returns `x + 1`; the caller `scaled` divides by it under `x >= 0`),
changes only the callee to return `x`, then runs the real CLI:

    guard verify --base <baseline commit> --format json

and prints the slice, the finding, its witness and how the same witness behaves in the
base. Nothing in the output is hard-coded: it is read from the JSON report. The caller's
source never changes; the dependency graph invalidates it because its callee changed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = '''def denominator(x):
    return x + 1


def scaled(x):
    if x < 0:
        return 0.0
    return 100 / denominator(x)
'''
CONFIG = '''schema_version = "1.0"

[analysis]
quick_budget_ms = 5000

[[domains]]
symbol = "calc.py::scaled"
profile = "python_exact_builtin_ints"
authority = "DECLARED"
reason = "demo public API admits exact built-in integer inputs"
'''


def git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@example.invalid",
           "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "demo@example.invalid"}
    return subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=Path, default=None, help="create the demo repository here and keep it")
    args = ap.parse_args()
    root = args.keep or Path(tempfile.mkdtemp(prefix="ig-patch-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    try:
        (root / ".guard").mkdir(exist_ok=True)
        (root / ".guard" / "config.toml").write_text(CONFIG)
        (root / ".gitignore").write_text(".guard/cache/\n.guard/runs/\n.guard/artifacts/\n.guard/snapshots/\n.guard/state.sqlite*\n")
        (root / "calc.py").write_text(BASE)
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "baseline: guarded divisor")
        base = git(root, "rev-parse", "HEAD")
        (root / "calc.py").write_text(BASE.replace("return x + 1", "return x"))  # only the callee changes
        guard = Path(sys.executable).parent / "guard"
        p = subprocess.run([str(guard), "verify", str(root), "--base", base, "--format", "json"], capture_output=True, text=True)
        sys.stderr.write(p.stderr)
        report = json.loads(p.stdout)
        print(f"repository: {root}")
        print(f"base {base[:12]} -> verdict {report['verdict']} (exit {p.returncode}), run_outcome {report['run_outcome']}")
        for inv in report["patch"]["slice"]["invalidated"]:
            print(f"  invalidated {inv['symbol_id']}: {inv['reason']}")
        for f in report["findings"]:
            c = f.get("comparison", {})
            print(f"  {f['patch_relation']} {f['severity']} {f['property_result']} [{f['scope_validity']}] {f['obligation_id']}")
            print(f"    {f['message']}")
            print(f"    witness {f['witness']}; same witness in base: {c.get('witness_in_base')} ({c.get('reason')})")
        return 0 if p.returncode in (0, 1, 2) else p.returncode
    finally:
        if args.keep is None:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
