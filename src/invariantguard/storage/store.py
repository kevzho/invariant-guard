"""Local state under ``.guard/`` (docs/09 "Persistent layout").

* ``state.sqlite`` owns identities, relationships and run transactions.
* ``artifacts/`` holds immutable JSON artifacts addressed by the sha256 of their
  canonical bytes; every read re-verifies the digest (a corrupted file is a miss).
* ``snapshots/`` holds snapshot manifests and, only when explicitly requested, retained
  source blobs (content-addressed).

Cache entries are written in two phases. During a run they are ``pending`` and tied to
the run; only ``finalize_run(ok=True)``, called after the snapshot consistency check
passed, marks them ``complete`` in one transaction. Lookups read only ``complete``
entries, so interrupted, failed or inconsistent runs never contribute reusable results.

Baselines are append-only: SQLite triggers abort any UPDATE or DELETE on the table, so a
baseline can never be silently replaced (docs/07 "Baseline lifecycle").
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.spans import canonical_json, sha256_bytes

STATE_SCHEMA_VERSION = 3
# v2 (M3): append-only contract revisions, authority events and contract evidence.
# v3 (M5): append-only index of finished verify reports and their finding IDs, so evidence
#          can be fetched by ID (``guard explain``, MCP ``get_finding``/``explain_finding``).

_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS artifacts (
        digest TEXT PRIMARY KEY, kind TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, command TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running', 'complete', 'failed')),
        started_at TEXT NOT NULL, finished_at TEXT, base_snapshot TEXT, candidate_snapshot TEXT, note TEXT)""",
    """CREATE TABLE IF NOT EXISTS cache_entries (
        cache_key TEXT NOT NULL, run_id TEXT NOT NULL, kind TEXT NOT NULL, symbol_id TEXT NOT NULL,
        artifact_digest TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'complete')),
        created_at TEXT NOT NULL, PRIMARY KEY (cache_key, run_id))""",
    "CREATE INDEX IF NOT EXISTS cache_by_key ON cache_entries (cache_key, status)",
    """CREATE TABLE IF NOT EXISTS baselines (
        label TEXT PRIMARY KEY, created_at TEXT NOT NULL, reason TEXT NOT NULL, snapshot_id TEXT NOT NULL,
        manifest_digest TEXT NOT NULL, results_digest TEXT, git_commit TEXT, source_retained INTEGER NOT NULL,
        supersedes TEXT, engine_version TEXT NOT NULL, contract_set TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS baselines_immutable_update BEFORE UPDATE ON baselines
        BEGIN SELECT RAISE(ABORT, 'baselines are immutable; create a new label'); END""",
    """CREATE TRIGGER IF NOT EXISTS baselines_immutable_delete BEFORE DELETE ON baselines
        BEGIN SELECT RAISE(ABORT, 'baselines are immutable; create a new label'); END""",
]

# Schema v2 (M3). Every table is append-only: triggers abort UPDATE and DELETE, so a
# revision, an authority decision or a piece of evidence can be superseded only by a new
# row, never rewritten (docs/03 "Immutable evidence ... must not be rewritten").
_SCHEMA_V2 = [
    """CREATE TABLE IF NOT EXISTS contract_revisions (
        contract_id TEXT NOT NULL, revision INTEGER NOT NULL, file_digest TEXT NOT NULL, semantic_digest TEXT NOT NULL,
        scope TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (contract_id, revision))""",
    """CREATE TABLE IF NOT EXISTS contract_events (
        contract_id TEXT NOT NULL, seq INTEGER NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL,
        from_authority TEXT, to_authority TEXT NOT NULL, reason TEXT NOT NULL, file_digest TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (contract_id, seq))""",
    """CREATE TABLE IF NOT EXISTS contract_evidence (
        evidence_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, revision INTEGER NOT NULL, semantic_digest TEXT NOT NULL,
        run_id TEXT NOT NULL, side TEXT NOT NULL, snapshot_id TEXT NOT NULL, result_label TEXT NOT NULL, artifact_digest TEXT NOT NULL,
        environment_digest TEXT, run_profile_digest TEXT, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS contract_evidence_by_contract ON contract_evidence (contract_id, revision)",
] + [
    f"""CREATE TRIGGER IF NOT EXISTS {t}_immutable_{op.lower()} BEFORE {op} ON {t}
        BEGIN SELECT RAISE(ABORT, '{t} rows are append-only'); END"""
    for t in ("contract_revisions", "contract_events", "contract_evidence") for op in ("UPDATE", "DELETE")
]

# Schema v3 (M5). The report body is an immutable artifact (content-addressed); these rows
# only index it. Append-only like every other evidence table.
_SCHEMA_V3 = [
    """CREATE TABLE IF NOT EXISTS run_reports (
        run_id TEXT PRIMARY KEY, command TEXT NOT NULL, report_digest TEXT NOT NULL, verdict TEXT NOT NULL, run_outcome TEXT NOT NULL,
        base_snapshot TEXT, candidate_snapshot TEXT NOT NULL, scope TEXT NOT NULL, created_at TEXT NOT NULL, seq INTEGER NOT NULL UNIQUE)""",
    """CREATE TABLE IF NOT EXISTS report_findings (
        finding_id TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY (finding_id, run_id))""",
    "CREATE INDEX IF NOT EXISTS report_findings_by_finding ON report_findings (finding_id)",
] + [
    f"""CREATE TRIGGER IF NOT EXISTS {t}_immutable_{op.lower()} BEFORE {op} ON {t}
        BEGIN SELECT RAISE(ABORT, '{t} rows are append-only'); END"""
    for t in ("run_reports", "report_findings") for op in ("UPDATE", "DELETE")
]


class StoreError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class Baseline:
    label: str
    created_at: str
    reason: str
    snapshot_id: str
    manifest_digest: str
    results_digest: str | None
    git_commit: str | None
    source_retained: bool
    supersedes: str | None
    engine_version: str
    contract_set: str

    def to_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Store:
    def __init__(self, state_dir: Path) -> None:
        self.dir = state_dir
        self.artifacts = state_dir / "artifacts"
        self.snapshots = state_dir / "snapshots"
        try:
            for d in (self.dir, self.artifacts, self.snapshots, state_dir / "cache", state_dir / "runs"):
                d.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(state_dir / "state.sqlite"), timeout=30, isolation_level=None)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self._migrate()
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(f"cannot open state directory {state_dir}: {exc}") from None

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _SCHEMA:
                self.db.execute(stmt)
            row = self.db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            version = int(row[0]) if row is not None else None
            if version is not None and version > STATE_SCHEMA_VERSION:
                raise StoreError(f"state.sqlite schema version {version} is newer than this engine supports ({STATE_SCHEMA_VERSION}); no downgrade exists")
            if version is not None and version < 1:
                raise StoreError(f"state.sqlite schema version {version} is not supported")
            # Migrations 1 -> 2 -> 3 are purely additive (new append-only tables).
            for stmt in _SCHEMA_V2 + _SCHEMA_V3:
                self.db.execute(stmt)
            if version is None:
                self.db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(STATE_SCHEMA_VERSION),))
            elif version < STATE_SCHEMA_VERSION:
                self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(STATE_SCHEMA_VERSION),))
                self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (f"migrated_{version}_to_{STATE_SCHEMA_VERSION}", _now()))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # -- artifacts ------------------------------------------------------------------------
    def _artifact_path(self, digest: str) -> Path:
        hexd = digest.split(":", 1)[1]
        return self.artifacts / hexd[:2] / f"{hexd}.json"

    def put_json(self, obj: Any, kind: str) -> str:
        data = canonical_json(obj)
        digest = sha256_bytes(data)
        path = self._artifact_path(digest)
        if not path.exists():
            _atomic_write(path, data)
        self.db.execute("INSERT OR IGNORE INTO artifacts (digest, kind, size, created_at) VALUES (?, ?, ?, ?)", (digest, kind, len(data), _now()))
        return digest

    def get_json(self, digest: str) -> Any | None:
        """The artifact, or None if missing or corrupted (digest mismatch)."""
        try:
            data = self._artifact_path(digest).read_bytes()
        except (OSError, ValueError, IndexError):
            return None
        if sha256_bytes(data) != digest:
            return None
        try:
            return json.loads(data)
        except ValueError:
            return None

    # -- retained source -------------------------------------------------------------------
    def put_source(self, raw: bytes) -> str:
        digest = sha256_bytes(raw)
        path = self.snapshots / "source" / digest.split(":", 1)[1]
        if not path.exists():
            _atomic_write(path, raw)
        return digest

    def get_source(self, digest: str) -> bytes | None:
        try:
            raw = (self.snapshots / "source" / digest.split(":", 1)[1]).read_bytes()
        except (OSError, IndexError):
            return None
        return raw if sha256_bytes(raw) == digest else None

    def put_manifest(self, manifest: dict[str, Any]) -> str:
        digest = self.put_json(manifest, "snapshot_manifest")
        _atomic_write(self.snapshots / (manifest["snapshot_id"].split(":", 1)[1] + ".json"), canonical_json(manifest))
        return digest

    # -- runs and cache ----------------------------------------------------------------------
    def begin_run(self, run_id: str, command: str, base_snapshot: str | None, candidate_snapshot: str | None) -> None:
        self.db.execute("INSERT INTO runs (run_id, command, status, started_at, base_snapshot, candidate_snapshot) VALUES (?, ?, 'running', ?, ?, ?)",
                        (run_id, command, _now(), base_snapshot, candidate_snapshot))

    def stage_entry(self, run_id: str, cache_key: str, kind: str, symbol_id: str, payload: dict[str, Any]) -> None:
        digest = self.put_json(payload, kind)
        self.db.execute(
            "INSERT OR REPLACE INTO cache_entries (cache_key, run_id, kind, symbol_id, artifact_digest, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (cache_key, run_id, kind, symbol_id, digest, _now()),
        )

    def finalize_run(self, run_id: str, ok: bool, note: str | None = None) -> int:
        """Publish the run's pending entries if ``ok``; returns the number published."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            n = 0
            if ok:
                n = self.db.execute("UPDATE cache_entries SET status = 'complete' WHERE run_id = ? AND status = 'pending'", (run_id,)).rowcount
            self.db.execute("UPDATE runs SET status = ?, finished_at = ?, note = ? WHERE run_id = ?", ("complete" if ok else "failed", _now(), note, run_id))
            self.db.execute("COMMIT")
            return n
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def lookup(self, cache_key: str, kind: str) -> dict[str, Any] | None:
        rows = self.db.execute(
            "SELECT artifact_digest FROM cache_entries WHERE cache_key = ? AND kind = ? AND status = 'complete' ORDER BY created_at DESC",
            (cache_key, kind),
        ).fetchall()
        for (digest,) in rows:
            payload = self.get_json(digest)
            if isinstance(payload, dict) and payload.get("cache_key") == cache_key:
                return payload
        return None

    def run_status(self, run_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return row[0] if row else None

    # -- reports (M5) ----------------------------------------------------------------------------
    def record_report(self, report: dict[str, Any], scope: str) -> str:
        """Store ``report`` as an immutable artifact and index it with its finding IDs."""
        digest = self.put_json(report, "report")
        ids = sorted({f["finding_id"] for f in report.get("findings", [])} | {c["finding_id"] for c in (report.get("contracts") or {}).get("results", [])
                                                                            if c.get("finding_id")})
        self.db.execute("BEGIN IMMEDIATE")
        try:
            seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_reports").fetchone()[0]
            self.db.execute("INSERT INTO run_reports (run_id, command, report_digest, verdict, run_outcome, base_snapshot, candidate_snapshot, scope, created_at, seq) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (report["run_id"], report["command"], digest, report["verdict"], report["run_outcome"], report.get("base_snapshot"),
                             report["candidate_snapshot"], scope, _now(), seq))
            for fid in ids:
                self.db.execute("INSERT OR IGNORE INTO report_findings (finding_id, run_id) VALUES (?, ?)", (fid, report["run_id"]))
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError:
            self.db.execute("ROLLBACK")
            raise StoreError(f"a report for run {report['run_id']} is already recorded; reports are immutable") from None
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return digest

    _REPORT_COLS = ("run_id", "command", "report_digest", "verdict", "run_outcome", "base_snapshot", "candidate_snapshot", "scope", "created_at", "seq")

    def report_rows(self, *, run_id: str | None = None, finding_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """Indexed reports, newest first; optionally those of one run or containing one finding."""
        q = "SELECT " + ", ".join("r." + c for c in self._REPORT_COLS) + " FROM run_reports r"
        args: list[Any] = []
        conds = []
        if finding_id is not None:
            q += " JOIN report_findings f ON f.run_id = r.run_id"
            conds.append("f.finding_id = ?")
            args.append(finding_id)
        if run_id is not None:
            conds.append("r.run_id = ?")
            args.append(run_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY r.seq DESC"
        if limit is not None:
            q += " LIMIT ?"
            args.append(int(limit))
        return [dict(zip(self._REPORT_COLS, r)) for r in self.db.execute(q, args).fetchall()]

    # -- baselines ---------------------------------------------------------------------------
    def create_baseline(self, b: Baseline) -> None:
        try:
            self.db.execute(
                "INSERT INTO baselines (label, created_at, reason, snapshot_id, manifest_digest, results_digest, git_commit, source_retained, supersedes, engine_version, contract_set) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (b.label, b.created_at, b.reason, b.snapshot_id, b.manifest_digest, b.results_digest, b.git_commit, int(b.source_retained),
                 b.supersedes, b.engine_version, b.contract_set),
            )
        except sqlite3.IntegrityError:
            raise StoreError(f"baseline {b.label!r} already exists; baselines are immutable (choose a new label, optionally --supersedes {b.label})") from None

    def get_baseline(self, label: str) -> Baseline | None:
        row = self.db.execute(
            "SELECT label, created_at, reason, snapshot_id, manifest_digest, results_digest, git_commit, source_retained, supersedes, engine_version, contract_set "
            "FROM baselines WHERE label = ?", (label,)).fetchone()
        if row is None:
            return None
        return Baseline(row[0], row[1], row[2], row[3], row[4], row[5], row[6], bool(row[7]), row[8], row[9], row[10])

    def list_baselines(self) -> list[Baseline]:
        labels = [r[0] for r in self.db.execute("SELECT label FROM baselines ORDER BY created_at, label").fetchall()]
        return [b for b in (self.get_baseline(x) for x in labels) if b is not None]

    # -- contracts (M3) ------------------------------------------------------------------------
    def add_contract_revision(self, contract_id: str, revision: int, file_digest: str, semantic_digest: str, scope: str, created_at: str) -> None:
        try:
            self.db.execute("INSERT INTO contract_revisions (contract_id, revision, file_digest, semantic_digest, scope, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                            (contract_id, revision, file_digest, semantic_digest, scope, created_at))
        except sqlite3.IntegrityError:
            raise StoreError(f"contract {contract_id!r} revision {revision} already exists; revisions are immutable") from None

    def add_contract_event(self, contract_id: str, seq: int, revision: int, kind: str, from_authority: str | None, to_authority: str, reason: str,
                           file_digest: str, created_at: str) -> None:
        try:
            self.db.execute("INSERT INTO contract_events (contract_id, seq, revision, kind, from_authority, to_authority, reason, file_digest, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (contract_id, seq, revision, kind, from_authority, to_authority, reason, file_digest, created_at))
        except sqlite3.IntegrityError:
            raise StoreError(f"contract {contract_id!r} event {seq} already exists; events are append-only") from None

    def contract_rows(self) -> tuple[list[tuple], list[tuple]]:
        revs = self.db.execute("SELECT contract_id, revision, file_digest, semantic_digest, scope, created_at FROM contract_revisions ORDER BY contract_id, revision").fetchall()
        evs = self.db.execute("SELECT contract_id, seq, revision, kind, from_authority, to_authority, reason, file_digest, created_at FROM contract_events "
                              "ORDER BY contract_id, seq").fetchall()
        return revs, evs

    def add_contract_evidence(self, row: dict[str, Any]) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO contract_evidence (evidence_id, contract_id, revision, semantic_digest, run_id, side, snapshot_id, result_label, "
            "artifact_digest, environment_digest, run_profile_digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["evidence_id"], row["contract_id"], row["revision"], row["semantic_digest"], row["run_id"], row["side"], row["snapshot_id"],
             row["result_label"], row["artifact_digest"], row.get("environment_digest"), row.get("run_profile_digest"), row["created_at"]))
        return cur.rowcount == 1

    def contract_evidence(self, contract_id: str | None = None) -> list[dict[str, Any]]:
        q = ("SELECT evidence_id, contract_id, revision, semantic_digest, run_id, side, snapshot_id, result_label, artifact_digest, environment_digest, "
             "run_profile_digest, created_at FROM contract_evidence")
        rows = self.db.execute(q + (" WHERE contract_id = ?" if contract_id else "") + " ORDER BY created_at, evidence_id",
                               (contract_id,) if contract_id else ()).fetchall()
        keys = ("evidence_id", "contract_id", "revision", "semantic_digest", "run_id", "side", "snapshot_id", "result_label", "artifact_digest",
                "environment_digest", "run_profile_digest", "created_at")
        return [dict(zip(keys, r)) for r in rows]

