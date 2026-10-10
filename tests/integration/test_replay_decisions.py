"""Phase 5: intent workflow for replayed behavior changes (accept, pin, retire) and doctests as declared behavior."""

from __future__ import annotations

import json
import sqlite3

import pytest

from ..conftest import run_cli
from .contract_helpers import exec_config
from .gitrepo import make_repo, write

MOD = '''def chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def label(n):
    """Short label.

    >>> label(3)
    'n=3'
    """
    return f"n={n}"
'''
TESTS = '''from demo.mod import chunks, label

def test_mod():
    assert chunks([1, 2, 3, 4, 5], 2)
    assert label(3)
'''
BUG = MOD.replace("range(0, len(xs), n)", "range(0, len(xs) - 1, n)")


def _repo(tmp_path):
    return make_repo(tmp_path, {"demo/__init__.py": "", "demo/mod.py": MOD, "tests/test_mod.py": TESTS}, exec_config())


def _verify(root, base):
    code, out, err = run_cli(["verify", str(root), "--base", base, "--format", "json", "--execute", "--replay", "--no-generate"])
    assert out.strip(), err
    r = json.loads(out)
    from invariantguard.models.schema import SchemaValidationUnavailable, validation_errors

    try:
        assert validation_errors("report", r) == []
        for e in r["evidence"]:
            assert validation_errors("evidence", e) == [], e.get("kind")
    except SchemaValidationUnavailable:
        pass
    return code, r


def _behavior(root, *args):
    code, out, err = run_cli(["behavior", *args, "--path", str(root)])
    return code, (json.loads(out) if out.strip().startswith("{") else out), err


def _diff(r, sym="chunks"):
    return [f for f in r["findings"] if f["property_result"] == "OBSERVED_DIFFERENCE" and f["symbol_id"].endswith(sym)]


def test_accept_turns_the_same_difference_into_info_and_retire_restores_it(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": BUG})
    _, r = _verify(root, base)
    (f,) = _diff(r)
    assert f["severity"] == "WARNING"
    code, _, err = _behavior(root, "accept", f["finding_id"], "--reason", "")
    assert code == 4 and "reason" in err
    code, d, _ = _behavior(root, "accept", f["finding_id"], "--reason", "the last partial chunk is dropped on purpose")
    assert code == 0 and d["decision"]["kind"] == "accept"
    code, _, err = _behavior(root, "accept", f["finding_id"], "--reason", "again")
    assert code == 4 and "already accepted" in err
    _, r2 = _verify(root, base)
    (f2,) = _diff(r2)
    assert f2["severity"] == "INFO" and f2["behavior"]["accepted_by"] == d["decision"]["decision_id"]
    assert any("accepted as intended" in x for x in f2["reasons"])
    code, _, _ = _behavior(root, "retire", d["decision"]["decision_id"], "--reason", "changed my mind")
    assert code == 0
    _, r3 = _verify(root, base)
    assert _diff(r3)[0]["severity"] == "WARNING"
    code, lst, _ = _behavior(root, "list", "--format", "json")
    assert [x["kind"] for x in lst["decisions"]] == ["accept", "retire"] and lst["decisions"][0]["active"] is False


def test_a_different_witness_is_not_covered_by_an_acceptance(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": BUG})
    _, r = _verify(root, base)
    _behavior(root, "accept", _diff(r)[0]["finding_id"], "--reason", "intended")
    write(root, {"demo/mod.py": MOD.replace("range(0, len(xs), n)", "range(1, len(xs), n)")})
    _, r2 = _verify(root, base)
    assert _diff(r2)[0]["severity"] == "WARNING"


def test_pinned_behavior_blocks_a_later_change(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": BUG})
    _, r = _verify(root, base)
    (f,) = _diff(r)
    code, d, err = _behavior(root, "pin", f["finding_id"], "--side", "base", "--reason", "chunks keeps the partial last chunk")
    assert code == 0, err
    _, r2 = _verify(root, base)
    (reg,) = [x for x in r2["findings"] if x["property_result"] == "OBSERVED_REGRESSION"]
    assert (reg["authority"], reg["patch_relation"], reg["severity"], reg["property_id"]) == ("APPROVED", "INTRODUCED", "ERROR", "behavior.pinned")
    assert reg["declared"]["kind"] == "pin" and "[[1, 2], [3, 4], [5]]" in reg["declared"]["expected"]
    assert r2["verdict"] == "BLOCK"
    # A refactor that keeps the pinned behavior passes the pin.
    write(root, {"demo/mod.py": MOD.replace("return [xs", "return list([xs").replace("n)]\n", "n)])\n", 1)})
    code, r3 = _verify(root, base)
    assert not [x for x in r3["findings"] if x["property_result"] == "OBSERVED_REGRESSION"] and r3["verdict"] != "BLOCK"


def test_only_literal_witnesses_can_be_pinned(tmp_path):
    tests = "from demo.mod import chunks\n\ndef test_x():\n    data = [1, 2, 3, 4, 5]\n    assert chunks(data, 2)\n"
    root, base = make_repo(tmp_path, {"demo/__init__.py": "", "demo/mod.py": MOD, "tests/test_mod.py": tests}, exec_config())
    write(root, {"demo/mod.py": BUG})
    _, r = _verify(root, base)
    (f,) = _diff(r)
    assert "data =" in f["behavior"]["witness"]["call"]
    code, _, err = _behavior(root, "pin", f["finding_id"], "--side", "base", "--reason", "x")
    assert code == 4 and "literal" in err


def test_a_broken_unchanged_doctest_blocks_and_an_edited_one_does_not(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": MOD.replace('return f"n={n}"', 'return f"N={n}"')})
    code, r = _verify(root, base)
    (reg,) = [x for x in r["findings"] if x["property_result"] == "OBSERVED_REGRESSION"]
    assert (reg["authority"], reg["property_id"], reg["severity"]) == ("DECLARED", "behavior.doctest", "ERROR")
    assert reg["declared"]["call"] == "label(3)" and "'N=3'" in reg["declared"]["patch"]
    assert r["verdict"] == "BLOCK" and code == 1
    write(root, {"demo/mod.py": MOD.replace('return f"n={n}"', 'return f"N={n}"').replace("'n=3'", "'N=3'")})
    code, r2 = _verify(root, base)
    assert not [x for x in r2["findings"] if x["property_result"] == "OBSERVED_REGRESSION"]
    assert r2["replay"]["declared"]["doctests"]["unchanged"] == 0  # the example itself changed: new intent, not a regression


def test_decisions_are_append_only(tmp_path):
    root, base = _repo(tmp_path)
    write(root, {"demo/mod.py": BUG})
    _, r = _verify(root, base)
    _behavior(root, "accept", _diff(r)[0]["finding_id"], "--reason", "intended")
    db = sqlite3.connect(str(root / ".guard" / "state.sqlite"))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE behavior_decisions SET reason = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM behavior_decisions")


def test_no_mcp_tool_makes_decisions():
    from invariantguard.service import NOT_EXPOSED, TOOLS

    assert "behavior accept/pin/retire" in NOT_EXPOSED and not any("behavior" in t or "accept" in t or "pin" in t for t in TOOLS)
