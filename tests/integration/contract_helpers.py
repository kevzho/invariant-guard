"""Shared fixtures for M3 contract tests (throwaway Git repositories under tmp_path)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..conftest import run_cli
from .gitrepo import git, make_repo, write

NORMALIZE_BASE = "def normalize(xs):\n    total = sum(xs)\n    return [x / total for x in xs]\n"
NORMALIZE_PATCH = "def normalize(xs):\n    total = sum(xs) + 1.0\n    return [x / total for x in xs]\n"
SORTED_BASE = "def sorted_copy(xs):\n    return sorted(xs)\n"
SORTED_PATCH = "def sorted_copy(xs):\n    xs.sort()\n    return xs\n"

XS = {"kind": "arg", "name": "xs"}
POSITIVE_SUM = {"kind": "and", "args": [
    {"kind": "gt", "left": {"kind": "len", "value": XS}, "right": {"kind": "literal", "value": 0}},
    {"kind": "all_between", "value": XS, "lower": 0.0, "upper": 100.0},
    {"kind": "gt", "left": {"kind": "sum", "value": XS}, "right": {"kind": "literal", "value": 0.0}},
]}
SUMS_TO_ONE = {"kind": "approx_eq", "left": {"kind": "sum", "value": {"kind": "result"}}, "right": {"kind": "literal", "value": 1.0},
               "atol": 1e-9, "rtol": 1e-9, "require_finite": True}


def normalize_contract(cid: str = "normalize.sum.v1", **over: Any) -> dict[str, Any]:
    c = {"contract_id": cid, "scope": "demo/math_ops.py::normalize", "precondition": POSITIVE_SUM, "predicate": SUMS_TO_ONE,
         "input_profile": "bounded_builtin_numeric_list_v1", "examples": [{"xs": [1.0, 1.0]}, {"xs": [0.0, 0.0]}]}
    c.update(over)
    return c


def unchanged_contract(cid: str = "sorted_copy.pure.v1") -> dict[str, Any]:
    return {"contract_id": cid, "scope": "demo/listy.py::sorted_copy", "precondition": {"kind": "literal", "value": True},
            "predicate": {"kind": "unchanged_argument", "name": "xs"}, "input_profile": "bounded_builtin_int_list_v1", "examples": [{"xs": [2, 1]}]}


def exec_config(backend: str = "unisolated", *, extra_profile: str = "", require_coverage: bool = True, extra: str = "") -> str:
    lines = ['schema_version = "1.0"', "[analysis]", "quick_budget_ms = 30000", "full_budget_ms = 60000",
             f"require_coverage = {'true' if require_coverage else 'false'}", extra, "[execution]", "enabled = true",
             f'profile = "{"local" if backend == "unisolated" else "iso"}"', f"allow_unisolated = {'true' if backend == 'unisolated' or 'unisolated' in extra_profile else 'false'}"]
    if backend == "unisolated":
        lines += ["[execution.profiles.local]", 'backend = "unisolated"', 'allowed_targets = ["demo/*.py::*"]', "max_probes = 24", "probe_wall_seconds = 3"]
    else:
        lines += ["[execution.profiles.iso]", 'backend = "docker"', 'image = "python:3.13-slim"', 'allowed_targets = ["demo/*.py::*"]', "max_probes = 32"]
    lines.append(extra_profile)
    return "\n".join(lines) + "\n"


def repo(tmp_path: Path, files: dict[str, str], cfg: str, name: str = "repo") -> tuple[Path, str]:
    return make_repo(tmp_path, files, cfg, name=name)


def declare(root: Path, contract: dict[str, Any], tmp_path: Path, reason: str = "maintainer declares the intended behavior") -> dict[str, Any]:
    f = tmp_path / f"{contract['contract_id']}.json"
    f.write_text(json.dumps(contract), encoding="utf-8")
    code, out, err = run_cli(["contracts", "declare", "--file", str(f), "--reason", reason, "--path", str(root)])
    assert code == 0, err
    return json.loads(out)


def contracts_cli(root: Path, *args: str) -> tuple[int, Any, str]:
    code, out, err = run_cli(["contracts", *args, "--path", str(root)])
    return code, (json.loads(out) if out.strip().startswith(("{", "[")) else out), err


def verify_exec(root: Path, base: str, *extra: str, profile: str | None = None) -> tuple[int, dict[str, Any], str]:
    argv = ["verify", str(root), "--base", base, "--format", "json", "--execute"]
    if profile:
        argv += ["--run-profile", profile]
    code, out, err = run_cli(argv + list(extra))
    assert out.strip(), f"no JSON (exit {code}); stderr: {err}"
    assert code != 3, err  # a schema-invalid report would be exit 3
    return code, json.loads(out), err


def contract_result(report: dict[str, Any], cid: str) -> dict[str, Any]:
    for r in report["contracts"]["results"]:
        if r["contract_id"] == cid:
            return r
    raise KeyError(cid)


def evidence_by_id(report: dict[str, Any], ev_id: str) -> dict[str, Any]:
    return next(e for e in report["evidence"] if e["evidence_id"] == ev_id)


def side_evidence(report: dict[str, Any], result: dict[str, Any], side: str) -> dict[str, Any]:
    for i in result["evidence_ids"]:
        e = evidence_by_id(report, i)
        if e["execution"]["side"] == side:
            return e
    raise KeyError(side)


def probe_with_input(ev: dict[str, Any], encoded_input: dict[str, Any]) -> dict[str, Any]:
    return next(p for p in ev["execution"]["probes"] if p["input"] == encoded_input)


__all__ = ["NORMALIZE_BASE", "NORMALIZE_PATCH", "SORTED_BASE", "SORTED_PATCH", "normalize_contract", "unchanged_contract", "exec_config", "repo",
           "declare", "contracts_cli", "verify_exec", "contract_result", "side_evidence", "probe_with_input", "git", "write", "POSITIVE_SUM", "SUMS_TO_ONE"]
