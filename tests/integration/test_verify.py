"""M2 acceptance: `guard verify` on throwaway Git repositories (docs/07, docs/11 M2 gate).

Every repository is created under pytest's tmp_path. Witnesses are replayed in isolated
CPython processes against both the base revision (read with `git show`) and the
candidate file, so attribution claims are checked against real execution.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ..conftest import run_cli
from .gitrepo import config, finding, git, make_repo, tree_state, verify, write

REPLAY = r"""
import json, runpy, sys
ns = runpy.run_path(sys.argv[1])
try:
    print(json.dumps({"ok": True, "value": repr(ns[sys.argv[2]](**json.loads(sys.argv[3])))}))
except BaseException as e:
    import traceback
    tb = traceback.extract_tb(e.__traceback__)[-1]
    print(json.dumps({"ok": False, "exc": type(e).__name__, "line": tb.lineno}))
"""


def replay(tmp_path: Path, source: str, func: str, args: dict, tag: str) -> dict:
    f = tmp_path / f"replay_{tag}.py"
    f.write_text(source)
    p = subprocess.run([sys.executable, "-I", "-c", REPLAY, str(f), func, json.dumps(args)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


CALC_BASE = "def denominator(x):\n    return x + 1\n\n\ndef scaled(x):\n    if x < 0:\n        return 0.0\n    return 100 / denominator(x)\n"
CALC_CAND = CALC_BASE.replace("return x + 1", "return x")


# -- Fixture D: changed callee, unchanged caller (docs/13) ------------------------------------------
def test_fixture_d_changed_callee_invalidates_unchanged_caller(tmp_path):
    root, base = make_repo(tmp_path, {"calc.py": CALC_BASE}, config("calc.py::scaled"))
    code0, r0, _ = verify(root, base)
    assert code0 == 0 and r0["verdict"] == "PASS" and r0["findings"] == []
    write(root, {"calc.py": CALC_CAND})
    code, r, _ = verify(root, base)
    assert code == 1 and r["verdict"] == "BLOCK" and r["run_outcome"] == "COMPLETE"
    inv = {i["symbol_id"]: i["reason"] for i in r["patch"]["slice"]["invalidated"]}
    assert inv["calc.py::denominator"] == "definition changed"
    assert inv["calc.py::scaled"].startswith("calls calc.py::denominator")  # unchanged caller, invalidated through the graph
    f = finding(r, "calc.py::scaled::division.zero_denominator#0")
    assert (f["property_result"], f["scope_validity"], f["patch_relation"], f["severity"]) == ("PROVED_UNSAFE", "EXACT_MODEL", "INTRODUCED", "ERROR")
    assert f["witness"] == {"x": 0}
    assert f["comparison"]["witness_in_base"] == "PASSED" and f["comparison"]["base_result"] == "PROVED_SAFE"
    # Same witness against both real versions: the base returns, the candidate raises at the caller's division.
    base_src = git(root, "show", f"{base}:calc.py") + "\n"
    assert replay(tmp_path, base_src, "scaled", f["witness"], "base") == {"ok": True, "value": "100.0"}
    cand = replay(tmp_path, CALC_CAND, "scaled", f["witness"], "cand")
    assert cand["exc"] == "ZeroDivisionError" and cand["line"] == f["source_span"]["start_line"]


def test_fixture_d_cross_module_callee_is_conditional_and_invalidates_importer(tmp_path):
    files = {
        "helpers.py": "def denominator(x):\n    return x + 1\n",
        "app.py": "from helpers import denominator\n\n\ndef scaled(x):\n    if x < 0:\n        return 0.0\n    return 100 / denominator(x)\n",
    }
    root, base = make_repo(tmp_path, files, config("app.py::scaled"))
    write(root, {"helpers.py": "def denominator(x):\n    return x\n"})
    code, r, _ = verify(root, base)
    assert r["patch"]["files"]["modified"] == ["helpers.py"]
    inv = {i["symbol_id"]: i["reason"] for i in r["patch"]["slice"]["invalidated"]}
    assert "app.py::scaled" in inv and r["patch"]["slice"]["widened_by_key_check"] == []
    f = finding(r, "app.py::scaled::division.zero_denominator#0")
    # Import resolution (sys.path, shadowing) is an assumption, so the failure is CONDITIONAL: advisory, not a block.
    assert (f["property_result"], f["scope_validity"], f["patch_relation"], f["severity"]) == ("PROVED_UNSAFE", "CONDITIONAL", "INTRODUCED", "WARNING")
    assert any("resolves to helpers.py" in a for a in f["assumptions"])
    assert code == 0 and r["verdict"] == "WARN"



def test_fixture_d_changed_callee_invalidates_unchanged_method_caller(tmp_path):
    """I-130: a method is an ordinary call-graph node, so an unchanged method that calls a changed
    module function is re-analyzed, and its free name resolves to the module global (not class scope)."""
    src = "def denominator(x):\n    return x + 1\n\n\nclass Report:\n    denominator = None\n\n    def scaled(self):\n        return 100 / denominator(0)\n"
    root, base = make_repo(tmp_path, {"calc.py": src}, config())
    code0, r0, _ = verify(root, base)
    assert code0 == 0 and r0["verdict"] == "PASS"
    write(root, {"calc.py": src.replace("return x + 1", "return x")})
    code, r, _ = verify(root, base)
    inv = {i["symbol_id"]: i["reason"] for i in r["patch"]["slice"]["invalidated"]}
    assert inv["calc.py::Report.scaled"].startswith("calls calc.py::denominator")
    f = finding(r, "calc.py::Report.scaled::division.zero_denominator#0")
    assert (f["property_result"], f["patch_relation"]) == ("PROVED_UNSAFE", "INTRODUCED")
    assert f["comparison"]["base_result"] == "PROVED_SAFE"
    assert code == 1 and r["verdict"] == "BLOCK"

# -- staged / unstaged / untracked / deleted / renamed --------------------------------------------------
def test_staged_unstaged_untracked_deleted_and_renamed_files_participate(tmp_path):
    files = {
        "keep.py": "def ok(a):\n    if a != 0:\n        return 1 / a\n    return 0\n",
        "gone.py": "def bad():\n    return 1 / 0\n",
        "moved.py": "def helper():\n    return 1 // 0\n",
        "staged.py": "def s(a):\n    if a > 0:\n        return 10 % a\n    return 0\n",
    }
    root, base = make_repo(tmp_path, files, config("keep.py::ok", "staged.py::s"))
    git(root, "rm", "-q", "gone.py")  # staged deletion
    git(root, "mv", "moved.py", "moved2.py")  # staged rename (identical bytes)
    write(root, {"staged.py": "def s(a):\n    if a >= 0:\n        return 10 % a\n    return 0\n"})
    git(root, "add", "staged.py")  # staged modification ...
    write(root, {"staged.py": "def s(a):\n    if a >= -1:\n        return 10 % a\n    return 0\n"})  # ... then modified again (unstaged)
    write(root, {"keep.py": "def ok(a):\n    return 1 / a\n"})  # unstaged modification
    write(root, {"fresh.py": "def fresh(xs: list[int]):\n    return xs[0]\n"})  # untracked
    write(root, {"ignored_build.py": "def junk():\n    return 1 / 0\n", ".gitignore": "ignored_build.py\n.guard/cache/\n.guard/runs/\n.guard/artifacts/\n.guard/snapshots/\n.guard/state.sqlite*\n"})
    code, r, _ = verify(root, base)
    pf = r["patch"]["files"]
    assert pf["added"] == ["fresh.py"] and pf["deleted"] == ["gone.py"] and pf["renamed"] == [{"from": "moved.py", "to": "moved2.py"}]
    assert sorted(pf["modified"]) == ["keep.py", "staged.py"]
    gs = pf["git_states"]
    assert gs["fresh.py"] == ["untracked"] and gs["gone.py"] == ["deleted_staged"] and gs["keep.py"] == ["unstaged"]
    assert gs["staged.py"] == ["staged", "unstaged"] and gs["moved2.py"] == ["staged_new"]
    assert "ignored_build.py" not in {f["path"] for f in r["snapshot"]["files"]}  # git-ignored untracked files are not candidate source
    # The candidate is the working tree (unstaged bytes win over the index).
    s = finding(r, "staged.py::s::division.zero_denominator#0")
    assert s["witness"] == {"a": 0} and s["patch_relation"] == "INTRODUCED"
    assert finding(r, "keep.py::ok::division.zero_denominator#0")["patch_relation"] == "INTRODUCED"
    assert finding(r, "fresh.py::fresh::index.out_of_range#0")["patch_relation"] == "INTRODUCED"  # new untracked code
    assert finding(r, "moved2.py::helper::division.zero_denominator#0")["patch_relation"] == "PRE_EXISTING"  # renamed, same bug
    resolved = finding(r, "gone.py::bad::division.zero_denominator#0")
    assert resolved["patch_relation"] == "RESOLVED" and resolved["severity"] == "INFO"
    assert resolved["source_span"]["snapshot_id"] == r["base_snapshot"]  # historical evidence stays tied to the base
    assert code == 1


# -- relations ------------------------------------------------------------------------------------------
def test_pre_existing_changed_and_full_mode(tmp_path):
    files = {
        "old.py": "def legacy(a):\n    return 1 / (a - a)\n",
        "f.py": "def f(a, b):\n    if a == 1:\n        return 1 / (b - 2)\n    return 0\n",
    }
    root, base = make_repo(tmp_path, files, config("old.py::legacy", "f.py::f"))
    write(root, {"f.py": "def f(a, b):\n    if a == 2:\n        return 1 / (b - 2)\n    return 0\n"})
    code, r, _ = verify(root, base)
    f = finding(r, "f.py::f::division.zero_denominator#0")
    # The base already failed at this operation (a=1, b=2) but not for the new witness: the violating domain moved.
    assert f["patch_relation"] == "CHANGED" and f["witness"] == {"a": 2, "b": 2} and f["severity"] == "ERROR"
    assert f["comparison"]["witness_in_base"] == "PASSED" and f["comparison"]["base_result"] == "PROVED_UNSAFE"
    assert code == 1
    # Quick mode does not re-verify the untouched legacy bug; full mode reports it as pre-existing (not a block).
    assert all(x["symbol_id"] != "old.py::legacy" for x in r["obligation_results"])
    _, full, _ = verify(root, base, "--mode", "full")
    legacy = finding(full, "old.py::legacy::division.zero_denominator#0")
    assert legacy["patch_relation"] == "PRE_EXISTING" and legacy["severity"] == "WARNING"
    assert legacy["comparison"]["witness_in_base"] == "FAILED_SAME_SITE"
    assert full["analysis_plan"]["mode"] == "full" and full["coverage"]["required_obligations"] == 2


def test_resolved_when_candidate_proves_safe(tmp_path):
    root, base = make_repo(tmp_path, {"m.py": "def f(a, b):\n    return 1 / (a - b)\n"}, config("m.py::f"))
    write(root, {"m.py": "def f(a, b):\n    if a == b:\n        return 0.0\n    return 1 / (a - b)\n"})
    code, r, _ = verify(root, base)
    assert code == 0 and r["verdict"] == "PASS"
    res = [f for f in r["findings"] if f["patch_relation"] == "RESOLVED"]
    assert len(res) == 1 and res[0]["severity"] == "INFO" and res[0]["comparison"]["reason"].startswith("the matched candidate obligation is PROVED_SAFE")


def test_certain_and_uncertain_symbol_renames(tmp_path):
    files = {"m.py": "def area(a):\n    return 1 / (a - a)\n\n\ndef ratio(a, b):\n    return a\n"}
    root, base = make_repo(tmp_path, files, config("m.py::area", "m.py::ratio", "m.py::surface", "m.py::quotient"))
    # `area` renamed with an identical body (certain); `ratio` renamed and changed (uncertain).
    write(root, {"m.py": "def surface(a):\n    return 1 / (a - a)\n\n\ndef quotient(a, b):\n    return 1 / (a - b)\n"})
    code, r, _ = verify(root, base)
    syms = r["patch"]["symbols"]
    assert {"from": "m.py::area", "to": "m.py::surface", "match": "anchor_renamed_symbol"} in syms["renamed"]
    assert syms["uncertain_renames"] == [{"from": "m.py::ratio", "to": "m.py::quotient"}]
    assert finding(r, "m.py::surface::division.zero_denominator#0")["patch_relation"] == "PRE_EXISTING"
    q = finding(r, "m.py::quotient::division.zero_denominator#0")
    assert q["patch_relation"] == "UNDETERMINED" and q["comparison"]["match"] == "uncertain_rename"
    # An exact failure that cannot be attributed is required work left undone: INCOMPLETE, never PASS.
    assert r["run_outcome"] == "INCOMPLETE" and code == 2
    assert any("could not be attributed" in x for x in r["verdict_reasons"])


# -- T23: global / type / config / lock dependencies ----------------------------------------------------
def test_changed_global_limit_rechecks_dependent_index(tmp_path):
    files = {"lim.py": "LIMIT = 2\n\n\ndef pick():\n    xs = [10, 20, 30]\n    return xs[LIMIT]\n"}
    root, base = make_repo(tmp_path, files)
    write(root, {"lim.py": "LIMIT = 3\n\n\ndef pick():\n    xs = [10, 20, 30]\n    return xs[LIMIT]\n"})
    code, r, _ = verify(root, base)
    inv = {i["symbol_id"]: i["reason"] for i in r["patch"]["slice"]["invalidated"]}
    assert inv["lim.py::pick"].startswith("reads lim.py::LIMIT")  # `pick` itself is unchanged
    f = finding(r, "lim.py::pick::index.out_of_range#0")
    assert (f["property_result"], f["scope_validity"], f["patch_relation"]) == ("PROVED_UNSAFE", "EXACT_MODEL", "INTRODUCED")
    assert any("LIMIT" in a for a in f["assumptions"]) and code == 1


def test_type_dependency_rebinding_int_invalidates_annotated_function(tmp_path):
    root, base = make_repo(tmp_path, {"t.py": "def inv(a: int):\n    if a != 0:\n        return 1 / a\n    return 0\n"})
    write(root, {"t.py": "int = float\n\n\ndef inv(a: int):\n    if a != 0:\n        return 1 / a\n    return 0\n"})
    _, r, _ = verify(root, base)
    inv = {i["symbol_id"] for i in r["patch"]["slice"]["invalidated"]}
    assert "t.py::inv" in inv
    o = next(x for x in r["obligation_results"] if x["symbol_id"] == "t.py::inv")
    assert o["property_result"] == "UNKNOWN"  # `int` no longer names the builtin: the annotation is not trusted


def test_config_domain_change_invalidates_only_dependent_summary(tmp_path):
    files = {"a.py": "def f(a, b):\n    return 1 / (a - b)\n", "b.py": "def g(x):\n    return 2 // (x - x + 1)\n"}
    root, base = make_repo(tmp_path, files, config("b.py::g"))
    code1, r1, _ = verify(root, base, "--mode", "full")
    assert r1["patch"]["cache"]["candidate"]["staged"] == 2
    write(root, {".guard/config.toml": config("b.py::g", "a.py::f")})
    _, r2, _ = verify(root, base, "--mode", "full")
    c = r2["patch"]["cache"]["candidate"]
    assert c["hits"] == 1 and c["misses"] == 1  # only a.py::f has a new key (its declared domain changed)
    f = next(x for x in r2["obligation_results"] if x["symbol_id"] == "a.py::f")
    assert f["scope_validity"] == "EXACT_MODEL" and f["domain_source"] == "CONFIG_DECLARED"
    assert any("configuration differs" in u for u in r2["patch"]["unresolved_changes"])


def test_dependency_lock_change_invalidates_every_summary(tmp_path):
    files = {"a.py": "def f(a):\n    return 1\n", "b.py": "def g(x):\n    return 2\n", "requirements.txt": "z3-solver==5.1.0.0\n"}
    root, base = make_repo(tmp_path, files)
    write(root, {"requirements.txt": "z3-solver==4.15.0.0\n"})
    _, r, _ = verify(root, base)
    assert r["patch"]["slice"]["global_change"] and "locks" in r["patch"]["slice"]["global_change"]
    assert r["patch"]["slice"]["affected_symbols"] == 2
    assert r["snapshot"]["dependency_locks"][0]["path"] == "requirements.txt"


# -- None-flow and bounds through verify ---------------------------------------------------------------
def test_none_flow_through_changed_helper(tmp_path):
    files = {"n.py": "def lookup(code):\n    return code\n\n\ndef bits(code):\n    v = lookup(code)\n    return v.bit_length()\n"}
    cfg = config("n.py::bits", profiles={"n.py::bits": "python_optional_exact_builtin_int"})
    root, base = make_repo(tmp_path, files, cfg)
    write(root, {"n.py": "def lookup(code):\n    if code is None:\n        return 0\n    return code\n\n\ndef bits(code):\n    v = lookup(code)\n    return v.bit_length()\n"})
    code, r, _ = verify(root, base)
    res = [f for f in r["findings"] if f["property_id"] == "none.attribute_access"]
    assert [f["patch_relation"] for f in res] == ["RESOLVED"]  # the helper now maps None to 0
    assert code == 0


# -- no checkout, no mutation, no silent baseline update ---------------------------------------------------
def test_verify_never_mutates_the_working_tree_or_git_state(tmp_path):
    root, base = make_repo(tmp_path, {"calc.py": CALC_BASE}, config("calc.py::scaled"))
    write(root, {"calc.py": CALC_CAND, "new.py": "def n():\n    return 1\n"})
    git(root, "add", "new.py")
    # `git status` (run by this test, not by guard) may refresh .git/index, so take it first.
    head_before, status_before = git(root, "rev-parse", "HEAD"), git(root, "status", "--porcelain")
    before = tree_state(root)
    for mode in ("quick", "full"):
        verify(root, base, "--mode", mode)
    assert tree_state(root) == before  # every byte and mtime outside .guard state, including all of .git
    assert git(root, "rev-parse", "HEAD") == head_before and git(root, "status", "--porcelain") == status_before
    created = {p.relative_to(root).as_posix().split("/")[1] for p in (root / ".guard").rglob("*") if p.is_file() and p.name != "config.toml"}
    assert created <= {"state.sqlite", "state.sqlite-wal", "state.sqlite-shm", "artifacts", "snapshots", "cache", "runs"}


def test_external_state_dir_leaves_repository_byte_identical(tmp_path):
    root, base = make_repo(tmp_path, {"calc.py": CALC_BASE}, config("calc.py::scaled"))
    write(root, {"calc.py": CALC_CAND})
    before = tree_state(root, skip_state=False)
    code, _, _ = verify(root, base, "--state-dir", str(tmp_path / "state"))
    assert code == 1 and tree_state(root, skip_state=False) == before
    assert (tmp_path / "state" / "state.sqlite").is_file()


def test_base_is_never_inferred_and_errors_are_usage_errors(tmp_path):
    root, base = make_repo(tmp_path, {"a.py": "def f():\n    return 1\n"})
    code, _, err = run_cli(["verify", str(root), "--format", "json"])
    assert code == 4 and "--base" in err
    code, _, err = run_cli(["verify", str(root), "--base", "no-such-ref", "--format", "json"])
    assert code == 4 and "does not name a commit" in err
    code, _, err = run_cli(["verify", str(root), "--base", "--output=x", "--format", "json"])
    assert code == 4
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("def f():\n    return 1\n")
    code, _, err = run_cli(["verify", str(plain), "--base", "HEAD", "--format", "json"])
    assert code == 4 and "baseline:LABEL" in err


def test_verify_restarts_once_then_reports_incomplete(tmp_path, monkeypatch):
    import invariantguard.orchestration as orch

    root, base = make_repo(tmp_path, {"m.py": "def f(a):\n    return 1\n"})
    write(root, {"m.py": "def f(a):\n    return 0\n"})  # a change, so the slice is not empty
    real = orch.check_function
    n = {"i": 0}

    def editing(fn, *a, **k):
        n["i"] += 1
        (root / "m.py").write_text(f"def f(a):\n    return {n['i']}\n")
        return real(fn, *a, **k)

    monkeypatch.setattr(orch, "check_function", editing)
    code, r, err = verify(root, base, "--no-cache")
    assert code == 2 and r["run_outcome"] == "INCOMPLETE" and "restarting once" in err
    assert {s["kind"] for s in r["coverage"]["skipped_checks"]} == {"source_changed_during_run"}


def test_quick_budget_exhaustion_is_incomplete_and_offers_full_mode(tmp_path):
    src = "def f(a, b):\n    if a * a * a + b * b * b == 33:\n        return 1 / (a - b + 7)\n    return 0\n"
    cfg = config("m.py::f", extra="witness_search = false").replace("quick_budget_ms = 30000", "quick_budget_ms = 1")
    root, base = make_repo(tmp_path, {"m.py": "def f(a, b):\n    return 0\n"}, cfg)
    write(root, {"m.py": src})
    code, r, _ = verify(root, base, "--no-cache")
    assert code == 2 and r["run_outcome"] == "INCOMPLETE"
    assert any("--mode full" in x for x in r["verdict_reasons"])
