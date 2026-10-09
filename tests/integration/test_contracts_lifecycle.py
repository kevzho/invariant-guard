"""Contract lifecycle, authority and immutable revisions (M3 T31) plus integrity checks of the files."""

import json
import shutil
import sqlite3

import pytest

from invariantguard.storage.store import STATE_SCHEMA_VERSION, Store, _SCHEMA

from .contract_helpers import (
    NORMALIZE_BASE,
    NORMALIZE_PATCH,
    contract_result,
    contracts_cli,
    declare,
    exec_config,
    normalize_contract,
    repo,
    verify_exec,
    write,
)


def _propose(root, contract):
    """What `guard contracts discover` does: an INFERRED revision recorded through the registry."""
    from invariantguard.contracts.model import validate_revision
    from invariantguard.contracts.registry import ContractRegistry

    doc = {"schema_version": "1.0", "revision": 1, "authority": "INFERRED", "lifecycle_state": "VALIDATED", "supersedes": None,
           "provenance": [{"kind": "template", "reference": "test"}], "reason": "proposed", "created_at": "2026-10-04T00:00:00Z", **contract}
    store = Store(root / ".guard")
    try:
        sem = validate_revision(doc, {"demo/math_ops.py": (root / "demo/math_ops.py").read_bytes()}, "sha256:" + "0" * 64).semantic_digest
        ContractRegistry(store).create(doc, sem, kind="propose", reason="template proposal")
    finally:
        store.close()


def _evidence_rows(root):
    db = sqlite3.connect(root / ".guard" / "state.sqlite")
    try:
        return db.execute("SELECT COUNT(*) FROM contract_evidence").fetchone()[0]
    finally:
        db.close()


def test_authority_transitions_are_explicit_and_audited(tmp_path):
    root, _ = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config())
    _propose(root, normalize_contract())
    code, lst, _ = contracts_cli(root, "list")
    (c,) = lst["contracts"]
    assert (c["authority"], c["lifecycle_state"], c["revision"]) == ("INFERRED", "VALIDATED", 1)
    # Invalid transitions are refused (exit 4); every valid one needs a reason and is recorded.
    assert contracts_cli(root, "retire", "nope", "--reason", "x")[0] == 4
    code, out, _ = contracts_cli(root, "approve", "normalize.sum.v1", "--reason", "maintainer reviewed the intent")
    assert code == 0 and out == {"contract_id": "normalize.sum.v1", "revision": 1, "from_authority": "INFERRED", "to_authority": "APPROVED"}
    code, _, err = contracts_cli(root, "reject", "normalize.sum.v1", "--reason", "x")
    assert code == 4 and "revise" in err  # an approved contract changes through revise or retire, not reject
    assert contracts_cli(root, "approve", "normalize.sum.v1", "--reason", "again")[0] == 4
    assert contracts_cli(root, "retire", "normalize.sum.v1", "--reason", "API removed")[0] == 0
    _, lst, _ = contracts_cli(root, "list")
    (c,) = lst["contracts"]
    assert c["authority"] == "RETIRED" and [h["kind"] for h in c["history"]] == ["propose", "approve", "retire"]
    assert all(h["reason"] for h in c["history"])
    files = sorted(p.relative_to(root / ".guard" / "contracts").as_posix() for p in (root / ".guard" / "contracts").rglob("*.json"))
    assert files == ["normalize.sum.v1/events/e0001.json", "normalize.sum.v1/events/e0002.json", "normalize.sum.v1/events/e0003.json",
                     "normalize.sum.v1/r0001.json"]


def test_reject_keeps_history_and_excludes_from_policy(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(require_coverage=False))
    _propose(root, normalize_contract())
    assert contracts_cli(root, "reject", "normalize.sum.v1", "--reason", "not intended behavior")[0] == 0
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    code, r, _ = verify_exec(root, base)
    assert r["contracts"]["results"] == [] and r["contracts"]["registry"]["active"] == 0  # rejected: no policy effect
    _, lst, _ = contracts_cli(root, "list")
    assert lst["contracts"][0]["authority"] == "REJECTED"


def test_approval_is_separate_from_evidence(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(require_coverage=False))
    _propose(root, normalize_contract())
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    # Evidence does not change authority: a regression found for an INFERRED contract stays INFERRED (and only warns).
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert c["property_result"] == "OBSERVED_REGRESSION" and c["authority"] == "INFERRED" and r["verdict"] == "WARN"
    n = _evidence_rows(root)
    assert n == 2
    _, lst, _ = contracts_cli(root, "list")
    assert lst["contracts"][0]["authority"] == "INFERRED" and lst["contracts"][0]["lifecycle_state"] == "CHECKED"
    # Approval does not create evidence; the next check of the same revision blocks under the new authority.
    assert contracts_cli(root, "approve", "normalize.sum.v1", "--reason", "intended behavior")[0] == 0
    assert _evidence_rows(root) == n
    _, lst, _ = contracts_cli(root, "list")
    assert lst["contracts"][0]["lifecycle_state"] == "REVIEWED" and lst["contracts"][0]["evidence_for_current_revision"] == 2
    code, r2, _ = verify_exec(root, base)
    assert contract_result(r2, "normalize.sum.v1")["severity"] == "ERROR" and r2["verdict"] == "BLOCK" and code == 1


def test_revisions_events_and_evidence_are_append_only(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(require_coverage=False))
    declare(root, normalize_contract(), tmp_path)
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    verify_exec(root, base)
    code, _, err = contracts_cli(root, "declare", "--file", str(tmp_path / "normalize.sum.v1.json"), "--reason", "again")
    assert code == 4 and "already exists" in err
    db = sqlite3.connect(root / ".guard" / "state.sqlite")
    try:
        for stmt in ("UPDATE contract_revisions SET semantic_digest = 'x'", "DELETE FROM contract_revisions",
                     "UPDATE contract_events SET to_authority = 'APPROVED'", "DELETE FROM contract_events",
                     "UPDATE contract_evidence SET result_label = 'TESTED'", "DELETE FROM contract_evidence"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(stmt)
    finally:
        db.close()


def test_tampered_revision_file_is_an_integrity_error_and_never_used(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config())
    declare(root, normalize_contract(), tmp_path)
    f = root / ".guard" / "contracts" / "normalize.sum.v1" / "r0001.json"
    doc = json.loads(f.read_text())
    doc["predicate"]["atol"] = 1.0  # a hand edit that would make the regression disappear
    f.write_text(json.dumps(doc))
    _, lst, _ = contracts_cli(root, "list")
    assert lst["contracts"][0]["integrity"] == "error" and "modified after it was recorded" in lst["contracts"][0]["integrity_errors"][0]
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    code, r, _ = verify_exec(root, base)
    c = contract_result(r, "normalize.sum.v1")
    assert c["status"] == "INTEGRITY_ERROR" and c["property_result"] == "UNKNOWN" and c["required"]
    assert code == 2 and r["verdict"] == "INCOMPLETE"  # never PASS on a tampered approved contract
    assert any("(INTEGRITY_ERROR)" in x for x in r["verdict_reasons"])
    assert contracts_cli(root, "approve", "normalize.sum.v1", "--reason", "x")[0] == 4


def test_unindexed_files_are_ignored_until_imported(tmp_path):
    root, base = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config(require_coverage=False))
    declare(root, normalize_contract(), tmp_path)
    src = root / ".guard" / "contracts" / "normalize.sum.v1"
    dst = root / ".guard" / "contracts" / "copied.v1"
    shutil.copytree(src, dst)
    for p in dst.rglob("*.json"):
        d = json.loads(p.read_text())
        d["contract_id"] = "copied.v1"
        p.write_text(json.dumps(d))
    _, lst, _ = contracts_cli(root, "list")
    assert [c["contract_id"] for c in lst["contracts"]] == ["normalize.sum.v1"]
    assert any(d["kind"] == "contract_files_unindexed" and "copied.v1" in d["message"] for d in lst["diagnostics"])
    write(root, {"demo/math_ops.py": NORMALIZE_PATCH})
    _, r, _ = verify_exec(root, base)
    assert [x["contract_id"] for x in r["contracts"]["results"]] == ["normalize.sum.v1"]
    code, out, _ = contracts_cli(root, "import", "--reason", "reviewed the files committed by a teammate")
    assert code == 0 and out["imported"] == [{"contract_id": "copied.v1", "revisions": 1, "events": 1, "authority": "DECLARED"}]
    _, lst, _ = contracts_cli(root, "list")
    assert [h["kind"] for h in next(c for c in lst["contracts"] if c["contract_id"] == "copied.v1")["history"]] == ["declare", "import"]


def test_invalid_declarations_write_nothing(tmp_path):
    root, _ = repo(tmp_path, {"demo/math_ops.py": NORMALIZE_BASE}, exec_config())
    bad = normalize_contract(predicate={"kind": "le", "left": {"kind": "len", "value": {"kind": "arg", "name": "xs"}}, "right": {"kind": "literal", "value": 5}})
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(bad))
    code, _, err = contracts_cli(root, "declare", "--file", str(f), "--reason", "x")
    assert code == 4 and "VACUOUS" in err
    f.write_text(json.dumps(normalize_contract(scope="demo/math_ops.py::missing")))
    code, _, err = contracts_cli(root, "declare", "--file", str(f), "--reason", "x")
    assert code == 4 and "UNKNOWN_SYMBOL" in err
    f.write_text('{"contract_id": "x", "scope": "demo/math_ops.py::normalize", "precondition": {"kind": "eval", "code": "1"}, '
                 '"predicate": {"kind": "result"}, "input_profile": "bounded_builtin_numeric_list_v1"}')
    code, _, err = contracts_cli(root, "declare", "--file", str(f), "--reason", "x")
    assert code == 4 and "UNKNOWN_NODE" in err
    assert not (root / ".guard" / "contracts").exists() or not any((root / ".guard" / "contracts").iterdir())


def test_state_schema_v1_migrates_additively(tmp_path):
    sd = tmp_path / "state"
    sd.mkdir()
    db = sqlite3.connect(sd / "state.sqlite")
    for stmt in _SCHEMA:
        db.execute(stmt)
    db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
    db.execute("INSERT INTO baselines (label, created_at, reason, snapshot_id, manifest_digest, results_digest, git_commit, source_retained, supersedes, "
               "engine_version, contract_set) VALUES ('b1', 't', 'r', 's', 'm', NULL, NULL, 0, NULL, '0.2.0', 'none')")
    db.commit()
    db.close()
    store = Store(sd)
    try:
        assert store.db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == str(STATE_SCHEMA_VERSION)
        assert store.get_baseline("b1") is not None and store.contract_rows() == ([], [])
        assert store.db.execute("SELECT COUNT(*) FROM meta WHERE key = ?", (f"migrated_1_to_{STATE_SCHEMA_VERSION}",)).fetchone()[0] == 1
        assert store.report_rows() == []  # v3 (M5) report index exists after the additive migration
    finally:
        store.close()
    db = sqlite3.connect(sd / "state.sqlite")
    db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(STATE_SCHEMA_VERSION + 1),))
    db.commit()
    db.close()
    from invariantguard.storage.store import StoreError

    with pytest.raises(StoreError, match="newer"):
        Store(sd)
