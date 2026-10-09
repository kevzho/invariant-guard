"""Fixtures B and E (docs/13) end to end with `guard verify --execute`, in a throwaway Git repository.

    .venv/bin/python examples/contract_demo/run_demo.py [--backend docker|unisolated] [--setup] [--keep DIR]

The script creates a temporary Git repository (never this one) with the baseline
`normalize(xs)` and `sorted_copy(xs)`, records two maintainer-declared contracts with
`guard contracts declare` (positive-sum normalization sums to 1; sorted_copy leaves its
argument unchanged), applies the candidate patches (`sum(xs) + 1.0`; `xs.sort()`), and runs
the real CLI:

    guard verify --base <baseline commit> --format json --execute --run-profile demo

It then restores the baseline behavior (the repair) and verifies again. Every value printed
comes from the JSON reports. The docker backend needs the image prepared explicitly: pass
`--setup` to run `guard runner setup --run-profile demo` first (the only step that may use
the network). If Docker or the image is unavailable the report says RUNNER_UNAVAILABLE and the
run is INCOMPLETE: there is no fallback.
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

NORMALIZE = "def normalize(xs):\n    total = sum(xs)\n    return [x / total for x in xs]\n"
SORTED = "def sorted_copy(xs):\n    return sorted(xs)\n"
XS = {"kind": "arg", "name": "xs"}
CONTRACTS = [
    {"contract_id": "normalize.sum.v1", "scope": "demo/math_ops.py::normalize",
     "precondition": {"kind": "and", "args": [
         {"kind": "gt", "left": {"kind": "len", "value": XS}, "right": {"kind": "literal", "value": 0}},
         {"kind": "all_between", "value": XS, "lower": 0.0, "upper": 100.0},
         {"kind": "gt", "left": {"kind": "sum", "value": XS}, "right": {"kind": "literal", "value": 0.0}}]},
     "predicate": {"kind": "approx_eq", "left": {"kind": "sum", "value": {"kind": "result"}}, "right": {"kind": "literal", "value": 1.0},
                   "atol": 1e-9, "rtol": 1e-9, "require_finite": True},
     "input_profile": "bounded_builtin_numeric_list_v1", "examples": [{"xs": [1.0, 1.0]}, {"xs": [0.0, 0.0]}]},
    {"contract_id": "sorted_copy.pure.v1", "scope": "demo/listy.py::sorted_copy", "precondition": {"kind": "literal", "value": True},
     "predicate": {"kind": "unchanged_argument", "name": "xs"}, "input_profile": "bounded_builtin_int_list_v1", "examples": [{"xs": [2, 1]}]},
]


def config(backend: str) -> str:
    prof = ('backend = "docker"\nimage = "python:3.13-slim"\n' if backend == "docker" else 'backend = "unisolated"\n')
    return ('schema_version = "1.0"\n\n[analysis]\nquick_budget_ms = 10000\nrequire_coverage = false\n\n'
            f'[execution]\nenabled = true\nprofile = "demo"\nallow_unisolated = {"true" if backend == "unisolated" else "false"}\n\n'
            f'[execution.profiles.demo]\n{prof}allowed_targets = ["demo/*.py::*"]\n')


def git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@example.invalid",
           "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "demo@example.invalid"}
    return subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True).stdout.strip()


def guard(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(Path(sys.executable).parent / "guard"), *args], capture_output=True, text=True)


def show(title: str, p: subprocess.CompletedProcess) -> None:
    r = json.loads(p.stdout)
    ex = r["contracts"]["execution"]
    print(f"\n== {title}: verdict {r['verdict']} (exit {p.returncode}), run_outcome {r['run_outcome']}")
    print(f"   runner: {ex['status']} backend={ex['backend']} isolated={ex['isolated']}; " + ", ".join(f"{c['name']}={c['status']}" for c in ex["capabilities"]))
    for c in r["contracts"]["results"]:
        print(f"   {c['contract_id']} r{c['revision']} [{c['authority']}] {c['status']} {c['property_result']} {c['patch_relation'] or ''} "
              f"severity={c['severity']}")
        pr = c.get("probes") or {}
        if pr:
            print(f"     probes: admitted {pr.get('admitted')}, excluded by domain {pr.get('excluded_by_domain')}, candidate pass/fail/unknown "
                  f"{pr.get('candidate_pass')}/{pr.get('candidate_fail')}/{pr.get('candidate_unknown')}, runner invocations {pr.get('runner_invocations')}")
        if c.get("witness") is not None:
            print(f"     witness {c['witness']} (first observed {c['original_witness']})")
            o = c["observations"]
            print(f"     base: {o['base']['reason']} {o['base'].get('result', '')}")
            print(f"     candidate: {o['candidate']['reason']} {o['candidate'].get('result', '')}")
        for x in c.get("excluded_examples", [])[:1]:
            print(f"     excluded: {x['input']} ({x['why']})")
    print("   reasons: " + "; ".join(r["verdict_reasons"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=("docker", "unisolated"), default="docker")
    ap.add_argument("--keep", type=Path, default=None)
    ap.add_argument("--setup", action="store_true", help="run `guard runner setup` first (explicit image preparation)")
    args = ap.parse_args()
    root = args.keep or Path(tempfile.mkdtemp(prefix="ig-contract-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    try:
        (root / ".guard").mkdir(exist_ok=True)
        (root / ".guard" / "config.toml").write_text(config(args.backend))
        (root / ".gitignore").write_text(".guard/cache/\n.guard/runs/\n.guard/artifacts/\n.guard/snapshots/\n.guard/state.sqlite*\n")
        (root / "demo").mkdir(exist_ok=True)
        (root / "demo" / "math_ops.py").write_text(NORMALIZE)
        (root / "demo" / "listy.py").write_text(SORTED)
        git(root, "init", "-q", "-b", "main")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "baseline")
        base = git(root, "rev-parse", "HEAD")
        for c in CONTRACTS:
            f = root.parent / f"{root.name}-{c['contract_id']}.json"
            f.write_text(json.dumps(c))
            p = guard("contracts", "declare", "--file", str(f), "--reason", "demo: maintainer-declared intended behavior", "--path", str(root))
            f.unlink()
            if p.returncode != 0:
                sys.stderr.write(p.stderr)
                return p.returncode
        print(f"repository: {root}\nbase: {base[:12]}; contracts declared: {[c['contract_id'] for c in CONTRACTS]}")
        if args.setup:
            p = guard("runner", "setup", "--run-profile", "demo", "--path", str(root))
            print("runner setup:", "ok" if p.returncode == 0 else f"failed (exit {p.returncode}): {p.stderr.strip()}")
        (root / "demo" / "math_ops.py").write_text(NORMALIZE.replace("sum(xs)", "sum(xs) + 1.0"))
        (root / "demo" / "listy.py").write_text("def sorted_copy(xs):\n    xs.sort()\n    return xs\n")
        show("candidate patch", guard("verify", str(root), "--base", base, "--format", "json", "--execute"))
        (root / "demo" / "math_ops.py").write_text(NORMALIZE)
        (root / "demo" / "listy.py").write_text(SORTED)
        show("after the repair", guard("verify", str(root), "--base", base, "--format", "json", "--execute"))
        return 0
    finally:
        if args.keep is None:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
