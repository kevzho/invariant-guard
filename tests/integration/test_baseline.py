"""T20/T27: explicit, immutable baselines (Git and non-Git) and their use by `guard verify`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ..conftest import run_cli
from .gitrepo import config, finding, git, make_repo, tree_state, verify, write

CALC = "def denominator(x):\n    return x + 1\n\n\ndef scaled(x):\n    if x < 0:\n        return 0.0\n    return 100 / denominator(x)\n"
OTHER = "def legacy(a):\n    return 1 / (a - a)\n"


def _plain(tmp_path: Path) -> Path:
    """A non-Git project (a `.git` marker stops config discovery but is not a repository)."""
    root = tmp_path / "plain"
    root.mkdir()
    (root / ".git").mkdir()
    write(root, {"calc.py": CALC, "other.py": OTHER, ".guard/config.toml": config("calc.py::scaled", "other.py::legacy")})
    return root


def _create(root: Path, label: str, *extra: str) -> tuple[int, dict, str]:
    code, out, err = run_cli(["baseline", "create", str(root), "--label", label, "--reason", "test baseline", *extra])
    return code, (json.loads(out) if out.strip() else {}), err


def test_non_git_baseline_with_retained_source_gives_full_attribution(tmp_path):
    root = _plain(tmp_path)
    code, b, _ = _create(root, "v1", "--retain-source")
    assert code == 0 and b["baseline"]["source_retained"] is True and b["replay_source"] == "retained"
    write(root, {"calc.py": CALC.replace("return x + 1", "return x")})
    code, r, _ = verify(root, "baseline:v1", "--mode", "full")
    assert r["patch"]["base"]["kind"] == "baseline" and r["patch"]["base"]["model_available"] is True
    f = finding(r, "calc.py::scaled::division.zero_denominator#0")
    assert (f["patch_relation"], f["severity"], f["witness"]) == ("INTRODUCED", "ERROR", {"x": 0})
    assert finding(r, "other.py::legacy::division.zero_denominator#0")["patch_relation"] == "PRE_EXISTING"
    assert code == 1 and r["base_snapshot"] == b["baseline"]["snapshot_id"]


def test_non_git_baseline_without_source_is_honest_about_attribution(tmp_path):
    root = _plain(tmp_path)
    code, b, _ = _create(root, "v1")
    assert code == 0 and b["baseline"]["source_retained"] is False and "not retained" in b["replay_source"]
    assert not (root / ".guard" / "snapshots" / "source").exists()  # no source copies without explicit retention
    write(root, {"calc.py": CALC.replace("return x + 1", "return x")})
    code, r, _ = verify(root, "baseline:v1", "--mode", "full")
    assert r["patch"]["base"]["model_available"] is False
    f = finding(r, "calc.py::scaled::division.zero_denominator#0")
    assert f["patch_relation"] == "UNDETERMINED" and f["comparison"]["match"] == "base_unavailable"
    # An unchanged symbol with an identical validity key keeps its stored base result: pre-existing.
    assert finding(r, "other.py::legacy::division.zero_denominator#0")["patch_relation"] == "PRE_EXISTING"
    assert code == 2 and r["run_outcome"] == "INCOMPLETE"  # unattributed exact failure: never PASS
    assert any("not retained" in u for u in r["patch"]["unresolved_changes"])
    inv = {i["symbol_id"] for i in r["patch"]["slice"]["invalidated"]}
    assert inv == {"calc.py::denominator", "calc.py::scaled"}  # key-based slice without base source


def test_git_baseline_records_a_commit_and_reads_it_without_checkout(tmp_path):
    root, base = make_repo(tmp_path, {"calc.py": CALC}, config("calc.py::scaled"))
    code, b, _ = _create(root, "release-1", "--rev", "HEAD")
    assert code == 0 and b["baseline"]["git_commit"] == base and b["baseline"]["source_retained"] is False
    git(root, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "later")
    write(root, {"calc.py": CALC.replace("return x + 1", "return x")})
    before = tree_state(root)
    code, r, _ = verify(root, "baseline:release-1")
    assert tree_state(root) == before
    assert r["patch"]["base"]["commit"] == base and finding(r, "calc.py::scaled::division.zero_denominator#0")["patch_relation"] == "INTRODUCED"


def test_baselines_are_immutable_and_never_silently_updated(tmp_path):
    root = _plain(tmp_path)
    assert _create(root, "v1", "--retain-source")[0] == 0
    db_path = root / ".guard" / "state.sqlite"
    rows_before = sqlite3.connect(db_path).execute("SELECT * FROM baselines").fetchall()
    # Re-creating the label is refused.
    code, _, err = _create(root, "v1")
    assert code == 4 and "immutable" in err
    # A passing verify does not touch the baseline table.
    code, r, _ = verify(root, "baseline:v1")
    assert code == 0 and r["verdict"] == "PASS"
    assert sqlite3.connect(db_path).execute("SELECT * FROM baselines").fetchall() == rows_before
    # Even direct SQL cannot update or delete a baseline.
    db = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("UPDATE baselines SET snapshot_id = 'x' WHERE label = 'v1'")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        db.execute("DELETE FROM baselines WHERE label = 'v1'")
    db.close()
    # An intentional new baseline is a new, explicit record that names what it supersedes.
    code, b2, _ = _create(root, "v2", "--supersedes", "v1")
    assert code == 0 and b2["baseline"]["supersedes"] == "v1"
    code, out, _ = run_cli(["baseline", "list", str(root)])
    assert [x["label"] for x in json.loads(out)["baselines"]] == ["v1", "v2"]
    code, out, _ = run_cli(["baseline", "show", "v1", str(root)])
    assert json.loads(out)["reason"] == "test baseline"


def test_baseline_usage_errors(tmp_path):
    root = _plain(tmp_path)
    assert _create(root, "bad label!")[0] == 4
    assert run_cli(["baseline", "create", str(root), "--label", "x", "--reason", " "])[0] == 4
    assert _create(root, "x", "--rev", "HEAD")[0] == 4  # not a Git work tree
    assert _create(root, "y", "--supersedes", "nope")[0] == 4
    code, _, err = run_cli(["verify", str(root), "--base", "baseline:missing", "--format", "json"])
    assert code == 4 and "no saved baseline" in err


def test_corrupted_retained_source_is_an_error_not_a_pass(tmp_path):
    root = _plain(tmp_path)
    assert _create(root, "v1", "--retain-source")[0] == 0
    for blob in (root / ".guard" / "snapshots" / "source").iterdir():
        blob.write_bytes(b"def legacy(a):\n    return 1\n")
    code, out, err = run_cli(["verify", str(root), "--base", "baseline:v1", "--format", "json"])
    assert code == 3 and out == "" and "missing or corrupted" in err
