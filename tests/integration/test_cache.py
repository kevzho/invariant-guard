"""T24: cache validity and interruption handling (docs/07 "Cache key", docs/02 "Atomicity").

Stale, partial, interrupted or tampered evidence is never reused as a pass.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import invariantguard.orchestration as orch
from invariantguard.storage.store import Store

from .gitrepo import config, finding, make_repo, verify, write

SRC = {
    "calc.py": "def denominator(x):\n    return x + 1\n\n\ndef scaled(x):\n    if x < 0:\n        return 0.0\n    return 100 / denominator(x)\n",
    "other.py": "def ratio(a, b):\n    if a != b:\n        return 1 / (a - b)\n    return 0\n",
}


def _strip(report: dict) -> list:
    """Semantic content of a report (no run ids, timing or cache statistics)."""
    return sorted(
        (o["obligation_id"], o["property_result"], o["scope_validity"], json.dumps(o["witness"]), tuple(o["assumptions"]),
         tuple(o["path_constraints"]), o["source_span"]["start_line"], o["evidence_ids"][0])
        for o in report["obligation_results"]
    )


def _repo(tmp_path):
    root, base = make_repo(tmp_path, SRC, config("calc.py::scaled", "other.py::ratio"))
    write(root, {"calc.py": SRC["calc.py"].replace("return x + 1", "return x"), "other.py": SRC["other.py"].replace("a != b", "a > b")})
    return root, base


def test_warm_run_reuses_complete_entries_with_identical_results(tmp_path):
    root, base = _repo(tmp_path)
    c1, cold, _ = verify(root, base, "--mode", "full")
    c2, warm, _ = verify(root, base, "--mode", "full")
    assert cold["patch"]["cache"]["candidate"]["hits"] == 0 and cold["patch"]["cache"]["published_entries"] == 6
    assert warm["patch"]["cache"]["candidate"]["hits"] == 3 and warm["patch"]["cache"]["base"]["hits"] == 3
    assert c1 == c2 and _strip(cold) == _strip(warm) and cold["verdict"] == warm["verdict"]
    assert all(o.get("evidence_reused") for o in warm["obligation_results"])
    # Reused evidence is the original immutable record (original run and snapshot).
    ev = {e["evidence_id"]: e for e in warm["evidence"]}
    assert all(ev[o["evidence_ids"][0]]["run_id"] == cold["run_id"] for o in warm["obligation_results"])
    _, nocache, _ = verify(root, base, "--mode", "full", "--no-cache")
    assert _strip(nocache) == _strip(cold) and nocache["patch"]["cache"]["enabled"] is False


def test_interrupted_run_never_publishes_entries(tmp_path, monkeypatch):
    root, base = _repo(tmp_path)
    calls = {"n": 0}
    real = orch.check_function

    def interrupt(fn, *a, **k):
        calls["n"] += 1
        if calls["n"] == 4:  # base analyzed (staged), candidate partly analyzed: simulate Ctrl-C
            raise KeyboardInterrupt
        return real(fn, *a, **k)

    monkeypatch.setattr(orch, "check_function", interrupt)
    with pytest.raises(KeyboardInterrupt):
        verify(root, base, "--mode", "full")
    db = sqlite3.connect(root / ".guard" / "state.sqlite")
    statuses = db.execute("SELECT status, COUNT(*) FROM cache_entries GROUP BY status").fetchall()
    runs = db.execute("SELECT status FROM runs").fetchall()
    db.close()
    assert dict(statuses).get("pending", 0) >= 1 and dict(statuses).get("complete", 0) == 0
    assert runs == [("running",)]  # never finalized
    monkeypatch.setattr(orch, "check_function", real)
    _, r, _ = verify(root, base, "--mode", "full")
    assert r["patch"]["cache"]["candidate"]["hits"] == 0 and r["patch"]["cache"]["base"]["hits"] == 0


def test_inconsistent_run_never_publishes_entries(tmp_path, monkeypatch):
    root, base = _repo(tmp_path)
    real = orch.check_function
    n = {"i": 0}

    def editing(fn, *a, **k):
        n["i"] += 1
        (root / "other.py").write_text(SRC["other.py"].replace("a != b", f"a > b + {n['i']}"))
        return real(fn, *a, **k)

    monkeypatch.setattr(orch, "check_function", editing)
    code, r, _ = verify(root, base, "--mode", "full")
    assert r["run_outcome"] == "INCOMPLETE" and r["patch"]["cache"]["published_entries"] == 0
    db = sqlite3.connect(root / ".guard" / "state.sqlite")
    assert db.execute("SELECT COUNT(*) FROM cache_entries WHERE status = 'complete'").fetchone() == (0,)
    assert {s for (s,) in db.execute("SELECT status FROM runs")} == {"failed"}
    db.close()


def test_tampered_artifact_is_a_miss_not_a_pass(tmp_path):
    root, base = _repo(tmp_path)
    _, cold, _ = verify(root, base, "--mode", "full")
    # Flip a stored result to PROVED_SAFE without changing its address: the digest check rejects it.
    store = Store(root / ".guard")
    digests = [d for (d,) in store.db.execute("SELECT artifact_digest FROM cache_entries WHERE symbol_id = 'calc.py::scaled'")]
    store.close()
    for d in digests:
        path = root / ".guard" / "artifacts" / d.split(":")[1][:2] / (d.split(":")[1] + ".json")
        path.write_bytes(path.read_bytes().replace(b'"PROVED_UNSAFE"', b'"PROVED_SAFE"'))
    _, warm, _ = verify(root, base, "--mode", "full")
    f = finding(warm, "calc.py::scaled::division.zero_denominator#0")
    assert f["property_result"] == "PROVED_UNSAFE" and f["patch_relation"] == "INTRODUCED"
    assert warm["patch"]["cache"]["candidate"]["misses"] >= 1


def test_entry_with_mismatched_anchor_is_rejected(tmp_path):
    root, base = _repo(tmp_path)
    verify(root, base, "--mode", "full")
    store = Store(root / ".guard")
    rows = store.db.execute("SELECT cache_key, artifact_digest FROM cache_entries WHERE symbol_id = 'other.py::ratio' AND status = 'complete'").fetchall()
    assert len(rows) == 2  # base and candidate versions of other.py::ratio
    for i, (key, digest) in enumerate(rows):
        payload = store.get_json(digest)
        payload["outcomes"][0]["anchor"] = "division.zero_denominator:0000000000000000#9"
        fake_run = "run-" + f"{i:x}" * 32
        store.stage_entry(fake_run, key, "symbol_analysis/v1", "other.py::ratio", payload)
        store.db.execute("UPDATE cache_entries SET status = 'complete', created_at = '9999-01-01T00:00:00.000000Z' WHERE run_id = ?", (fake_run,))
    store.close()
    _, r, _ = verify(root, base, "--mode", "full")
    assert "other.py::ratio" in r["patch"]["cache"]["candidate"]["rejected"] and "other.py::ratio" in r["patch"]["cache"]["base"]["rejected"]
    o = next(x for x in r["obligation_results"] if x["symbol_id"] == "other.py::ratio")
    assert o["property_result"] == "PROVED_SAFE" and not o.get("evidence_reused")  # recomputed, not taken from the doctored entry


def test_comment_only_edit_reuses_analysis_with_current_spans(tmp_path):
    root, base = _repo(tmp_path)
    verify(root, base, "--mode", "full")
    cand = (root / "calc.py").read_text()
    write(root, {"calc.py": "# a comment\n# another\n" + cand})
    _, r, _ = verify(root, base, "--mode", "full")
    assert r["patch"]["cache"]["candidate"]["hits"] == 3  # no line numbers in validity keys
    f = finding(r, "calc.py::scaled::division.zero_denominator#0")
    assert f["source_span"]["start_line"] == 10 and f["source_span"]["snapshot_id"] == r["candidate_snapshot"]  # remapped
    ev = next(e for e in r["evidence"] if e["evidence_id"] == f["evidence_ids"][0])
    assert ev["source_span"]["start_line"] == 8 and ev["snapshot_id"] != r["candidate_snapshot"]  # historical evidence keeps its span


def test_engine_or_solver_change_invalidates(tmp_path):
    root, base = _repo(tmp_path)
    verify(root, base, "--mode", "full")
    _, r, _ = verify(root, base, "--mode", "full", "--no-solver")
    assert r["patch"]["cache"]["candidate"]["hits"] == 0  # solver identity is part of the key
    _, r2, _ = verify(root, base, "--mode", "full", "--timeout-ms", "250")
    assert r2["patch"]["cache"]["candidate"]["hits"] == 0  # analysis bounds are part of the key


GEN = {
    "m.py": ("def gen(n: int):\n    if n != 0:\n        yield 1\n        yield 10 // n\n\n\n"
             "def caller(xs: list):\n    if len(xs) > 0:\n        g = gen(1)\n        return xs[0]\n\n\n"
             "def other(a: int):\n    return a\n"),
}


def test_cache_from_an_earlier_model_revision_is_not_reused(tmp_path, monkeypatch):
    """Entries computed under earlier modeled semantics (before generators, I-135/I-136) carry
    another model revision in their key, so a warm run recomputes instead of reusing them."""
    import invariantguard.impact.keys as keys

    assert keys.MODEL_REVISION >= 2  # generators came with revision 2
    root, base = make_repo(tmp_path, GEN)
    write(root, {"m.py": GEN["m.py"].replace("return a", "return a + 1")})
    monkeypatch.setattr(keys, "MODEL_REVISION", keys.MODEL_REVISION - 1)
    _, old, _ = verify(root, base, "--mode", "full")
    assert old["patch"]["cache"]["published_entries"] > 0
    monkeypatch.undo()
    _, warm, _ = verify(root, base, "--mode", "full")
    _, nocache, _ = verify(root, base, "--mode", "full", "--no-cache")
    assert warm["patch"]["cache"]["candidate"]["hits"] == 0 and warm["patch"]["cache"]["base"]["hits"] == 0
    assert _strip(warm) == _strip(nocache)
    caller = [o for o in nocache["obligation_results"] if o["symbol_id"] == "m.py::caller"]
    assert [o["property_result"] for o in caller] == ["PROVED_SAFE"]
