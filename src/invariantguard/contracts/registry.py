"""Contract lifecycle, authority and immutable revisions (M3 T31, docs/06, docs/09).

Layout (under the state directory, default ``<root>/.guard``)::

    contracts/<contract_id>/r0001.json          immutable revision documents (reviewable JSON)
    contracts/<contract_id>/events/e0001.json   immutable authority events

Files are created exclusively (never overwritten) and indexed in ``state.sqlite``
together with their sha256 digests; the SQLite tables are append-only. The JSON files
are the reviewable content and SQLite is the index: an indexed file whose bytes changed
is an integrity error (the contract is unusable, which makes a required check
INCOMPLETE); a file that was never indexed through a ``guard contracts`` command is
reported and ignored until ``guard contracts import`` indexes it explicitly.

Authority is an event log, independent of evidence (docs/06 "Authority evolves
independently"): ``propose`` (template/model -> INFERRED), ``declare`` (maintainer ->
DECLARED), ``approve`` (INFERRED/REJECTED -> APPROVED), ``reject`` (INFERRED -> REJECTED),
``revise`` (a new revision; a maintainer revision of an APPROVED/DECLARED contract keeps
that authority), ``retire`` (-> RETIRED), ``import``. Approving never creates evidence and
evidence never changes authority. Evidence binds to a revision's *semantic digest*, so a
new predicate, domain, profile or target starts without evidence; older evidence is kept
as history (T38).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.spans import digest_json, sha256_bytes
from ..storage.store import Store, StoreError, _now
from .dsl import Rejection
from .model import check_document_shape, semantic_digest

REV_RE = re.compile(r"^r(\d{4,})\.json$")
EVT_RE = re.compile(r"^e(\d{4,})\.json$")
ACTIVE = ("APPROVED", "DECLARED", "INFERRED")
REQUIRED_AUTHORITIES = ("APPROVED", "DECLARED")


class RegistryError(Exception):
    """Invalid lifecycle operation (CLI exit 4)."""


@dataclass
class ContractState:
    contract_id: str
    revisions: dict[int, dict[str, Any]] = field(default_factory=dict)
    revision_digests: dict[int, str] = field(default_factory=dict)  # file digests
    semantic: dict[int, str] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    integrity_errors: list[str] = field(default_factory=list)

    @property
    def current_revision(self) -> int:
        return self.events[-1]["revision"] if self.events else max(self.revisions, default=0)

    @property
    def authority(self) -> str:
        return self.events[-1]["to_authority"] if self.events else "INFERRED"

    @property
    def doc(self) -> dict[str, Any]:
        return self.revisions[self.current_revision]

    @property
    def scope(self) -> str:
        return self.doc["scope"]

    @property
    def ok(self) -> bool:
        return not self.integrity_errors and self.current_revision in self.revisions

    def reviewed(self, revision: int) -> bool:
        return any(e["revision"] == revision and e["kind"] in ("declare", "approve", "reject", "revise") for e in self.events)


@dataclass
class RegistryView:
    contracts: dict[str, ContractState]
    diagnostics: list[dict[str, Any]]
    indexed: int

    def active(self) -> list[ContractState]:
        return [c for c in sorted(self.contracts.values(), key=lambda c: c.contract_id) if c.authority in ACTIVE]


def _write_exclusive(path: Path, doc: dict[str, Any]) -> str:
    data = (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return sha256_bytes(data)


class ContractRegistry:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.dir = store.dir / "contracts"

    # -- reading -------------------------------------------------------------------------------
    def load(self) -> RegistryView:
        revs, evs = self.store.contract_rows()
        states: dict[str, ContractState] = {}
        diagnostics: list[dict[str, Any]] = []
        indexed_files: set[Path] = set()
        for cid, rev, fdig, sem, scope, _ in revs:
            st = states.setdefault(cid, ContractState(cid))
            path = self.dir / cid / f"r{rev:04d}.json"
            indexed_files.add(path)
            raw = self._read(path)
            if raw is None:
                st.integrity_errors.append(f"revision file {path.name} is missing")
                continue
            if sha256_bytes(raw) != fdig:
                st.integrity_errors.append(f"revision file {path.name} was modified after it was recorded (digest mismatch); revisions are immutable")
                continue
            doc = json.loads(raw)
            st.revisions[rev] = doc
            st.revision_digests[rev] = fdig
            st.semantic[rev] = sem
        for cid, seq, rev, kind, frm, to, reason, fdig, created in evs:
            st = states.setdefault(cid, ContractState(cid))
            path = self.dir / cid / "events" / f"e{seq:04d}.json"
            indexed_files.add(path)
            raw = self._read(path)
            if raw is None or sha256_bytes(raw) != fdig:
                st.integrity_errors.append(f"event file {path.name} is missing or was modified (digest mismatch)")
                continue
            st.events.append({"seq": seq, "revision": rev, "kind": kind, "from_authority": frm, "to_authority": to, "reason": reason, "created_at": created})
        if self.dir.is_dir():
            for d in sorted(self.dir.iterdir()):
                if not d.is_dir():
                    continue
                files = [p for p in d.glob("r*.json")] + [p for p in (d / "events").glob("e*.json")] if (d / "events").is_dir() else list(d.glob("r*.json"))
                unindexed = sorted(p.relative_to(self.dir).as_posix() for p in files if p not in indexed_files)
                if unindexed:
                    diagnostics.append({"kind": "contract_files_unindexed", "symbol_id": None,
                                        "message": f"contract files not recorded through `guard contracts` are ignored until `guard contracts import`: {unindexed[:5]}"})
        for st in states.values():
            for e in st.integrity_errors:
                diagnostics.append({"kind": "contract_integrity_error", "symbol_id": None, "message": f"{st.contract_id}: {e}"})
        return RegistryView(states, diagnostics, len(revs))

    @staticmethod
    def _read(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except OSError:
            return None

    # -- writing -------------------------------------------------------------------------------
    def _next_seq(self, st: ContractState | None) -> int:
        return (max((e["seq"] for e in st.events), default=0) + 1) if st else 1

    def _record(self, cid: str, *, revision_doc: dict[str, Any] | None, revision: int, kind: str, frm: str | None, to: str, reason: str,
                seq: int, sem: str | None = None) -> None:
        if not reason.strip():
            raise RegistryError("a non-empty --reason is required (every lifecycle change is audited)")
        written: list[Path] = []
        db = self.store.db
        db.execute("BEGIN IMMEDIATE")
        try:
            now = _now()
            if revision_doc is not None:
                rpath = self.dir / cid / f"r{revision:04d}.json"
                if rpath.exists():
                    raise RegistryError(f"{rpath} already exists; revisions are immutable")
                fdig = _write_exclusive(rpath, revision_doc)
                written.append(rpath)
                assert sem is not None
                self.store.add_contract_revision(cid, revision, fdig, sem, revision_doc["scope"], now)
            else:
                fdig = self.load().contracts[cid].revision_digests.get(revision, "")
            event = {"schema_version": "1.0", "contract_id": cid, "seq": seq, "revision": revision, "kind": kind, "from_authority": frm,
                     "to_authority": to, "reason": reason.strip(), "created_at": now, "revision_file_digest": fdig}
            epath = self.dir / cid / "events" / f"e{seq:04d}.json"
            if epath.exists():
                raise RegistryError(f"{epath} already exists; events are append-only")
            edig = _write_exclusive(epath, event)
            written.append(epath)
            self.store.add_contract_event(cid, seq, revision, kind, frm, to, reason.strip(), edig, now)
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            for p in written:
                try:
                    p.unlink()
                except OSError:
                    pass
            raise

    def _state(self, cid: str) -> ContractState:
        view = self.load()
        st = view.contracts.get(cid)
        if st is None:
            raise RegistryError(f"no contract named {cid!r}")
        if not st.ok:
            raise RegistryError(f"contract {cid!r} has integrity errors: {'; '.join(st.integrity_errors)}")
        return st

    def create(self, doc: dict[str, Any], semantic: str, *, kind: str, reason: str) -> None:
        """A new contract (declare by a maintainer, or propose from a template/model)."""
        cid = doc["contract_id"]
        if cid in self.load().contracts or (self.dir / cid).exists():
            raise RegistryError(f"contract {cid!r} already exists; use `guard contracts revise {cid}` for a new revision")
        to = doc["authority"]
        if kind == "declare" and to != "DECLARED":
            raise RegistryError("declare creates DECLARED contracts")
        if kind == "propose" and to != "INFERRED":
            raise RegistryError("proposals are INFERRED; only a maintainer command can approve them")
        check_document_shape(doc)
        self._record(cid, revision_doc=doc, revision=1, kind=kind, frm=None, to=to, reason=reason, seq=1, sem=semantic)

    def set_authority(self, cid: str, action: str, reason: str) -> dict[str, Any]:
        st = self._state(cid)
        cur = st.authority
        allowed = {"approve": ({"INFERRED", "REJECTED"}, "APPROVED"), "reject": ({"INFERRED"}, "REJECTED"),
                   "retire": ({"APPROVED", "DECLARED", "INFERRED"}, "RETIRED")}
        sources, to = allowed[action]
        if cur not in sources:
            hint = " (an approved or declared contract changes through `revise` or `retire`)" if action == "reject" and cur in REQUIRED_AUTHORITIES else ""
            raise RegistryError(f"cannot {action} {cid!r}: its authority is {cur}{hint}")
        self._record(cid, revision_doc=None, revision=st.current_revision, kind=action, frm=cur, to=to, reason=reason, seq=self._next_seq(st))
        return {"contract_id": cid, "revision": st.current_revision, "from_authority": cur, "to_authority": to}

    def revise(self, cid: str, doc: dict[str, Any], semantic: str, reason: str) -> dict[str, Any]:
        st = self._state(cid)
        cur = st.authority
        if cur in ("RETIRED",):
            raise RegistryError(f"{cid!r} is RETIRED; declare a new contract instead")
        if semantic == st.semantic.get(st.current_revision):
            raise RegistryError(f"the new revision of {cid!r} has the same scope, precondition, predicate, profile and examples; nothing to revise")
        to = cur if cur in REQUIRED_AUTHORITIES else "INFERRED"
        rev = max(st.revisions) + 1
        self._record(cid, revision_doc=doc, revision=rev, kind="revise", frm=cur, to=to, reason=reason, seq=self._next_seq(st), sem=semantic)
        return {"contract_id": cid, "revision": rev, "supersedes": st.current_revision, "authority": to,
                "stale_evidence": len([e for e in self.store.contract_evidence(cid) if e["revision"] != rev])}

    def import_unindexed(self, reason: str) -> list[dict[str, Any]]:
        """Index contract directories that exist as files but were never recorded here."""
        if not reason.strip():
            raise RegistryError("a non-empty --reason is required")
        view = self.load()
        out = []
        if not self.dir.is_dir():
            return out
        for d in sorted(self.dir.iterdir()):
            cid = d.name
            if not d.is_dir() or cid in view.contracts:
                continue
            revs = sorted((int(m.group(1)), p) for p in d.glob("r*.json") if (m := REV_RE.match(p.name)))
            evts = sorted((int(m.group(1)), p) for p in (d / "events").glob("e*.json") if (m := EVT_RE.match(p.name))) if (d / "events").is_dir() else []
            if [n for n, _ in revs] != list(range(1, len(revs) + 1)) or not revs:
                raise RegistryError(f"{cid}: revision files must be r0001.json .. rNNNN.json without gaps")
            if [n for n, _ in evts] != list(range(1, len(evts) + 1)) or not evts:
                raise RegistryError(f"{cid}: event files must be events/e0001.json .. without gaps")
            db = self.store.db
            db.execute("BEGIN IMMEDIATE")
            try:
                now = _now()
                last = None
                for n, p in revs:
                    raw = p.read_bytes()
                    doc = json.loads(raw)
                    try:
                        check_document_shape(doc)
                    except Rejection as exc:
                        raise RegistryError(f"{cid}/{p.name}: {exc}") from None
                    if doc["contract_id"] != cid or doc["revision"] != n:
                        raise RegistryError(f"{cid}/{p.name}: contract_id/revision do not match the file name")
                    # The semantic digest of an imported file uses the raw (not yet re-validated) terms.
                    sem = semantic_digest(doc["scope"], doc["precondition"], doc["predicate"], doc["input_profile"], doc.get("examples", []))
                    self.store.add_contract_revision(cid, n, sha256_bytes(raw), sem, doc["scope"], now)
                for n, p in evts:
                    raw = p.read_bytes()
                    e = json.loads(raw)
                    if e.get("contract_id") != cid or e.get("seq") != n or e.get("to_authority") not in ("APPROVED", "DECLARED", "INFERRED", "REJECTED", "RETIRED"):
                        raise RegistryError(f"{cid}/events/{p.name}: malformed event")
                    self.store.add_contract_event(cid, n, int(e["revision"]), str(e["kind"]), e.get("from_authority"), e["to_authority"],
                                                  str(e.get("reason", "")) or "imported", sha256_bytes(raw), now)
                    last = e
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            assert last is not None
            st = self.load().contracts[cid]
            self._record(cid, revision_doc=None, revision=st.current_revision, kind="import", frm=last["to_authority"], to=last["to_authority"],
                         reason=reason, seq=self._next_seq(st))
            out.append({"contract_id": cid, "revisions": len(revs), "events": len(evts), "authority": last["to_authority"]})
        return out

    # -- evidence -------------------------------------------------------------------------------
    def record_evidence(self, ev: dict[str, Any], side: str) -> None:
        digest = self.store.put_json(ev, "contract_evidence")
        self.store.add_contract_evidence({"evidence_id": ev["evidence_id"], "contract_id": ev["contract"]["contract_id"],
                                          "revision": ev["contract"]["revision"], "semantic_digest": ev["contract"]["semantic_digest"],
                                          "run_id": ev["run_id"], "side": side, "snapshot_id": ev["snapshot_id"], "result_label": ev["result_label"],
                                          "artifact_digest": digest, "environment_digest": ev["execution"].get("environment_digest"),
                                          "run_profile_digest": ev["execution"].get("run_profile_digest"), "created_at": ev["created_at"]})

    def summary(self, st: ContractState) -> dict[str, Any]:
        rev = st.current_revision
        evidence = self.store.contract_evidence(st.contract_id)
        sem = st.semantic.get(rev)
        current = [e for e in evidence if e["revision"] == rev and e["semantic_digest"] == sem]
        stale = [e for e in evidence if not (e["revision"] == rev and e["semantic_digest"] == sem)]
        doc = st.revisions.get(rev, {})
        if st.reviewed(rev):
            lifecycle = "REVIEWED"
        elif current:
            lifecycle = "CHECKED"
        else:
            lifecycle = doc.get("lifecycle_state", "CANDIDATE")
        latest = current[-1] if current else None
        return {
            "contract_id": st.contract_id, "revision": rev, "scope": doc.get("scope"), "authority": st.authority, "lifecycle_state": lifecycle,
            "input_profile": doc.get("input_profile"), "semantic_digest": sem, "revision_file_digest": st.revision_digests.get(rev),
            "integrity": "ok" if st.ok else "error", "integrity_errors": st.integrity_errors,
            "latest_evidence": ({k: latest[k] for k in ("evidence_id", "result_label", "side", "run_id", "snapshot_id", "created_at")} if latest else None),
            "evidence_for_current_revision": len(current), "stale_evidence_from_other_revisions": len(stale),
            "history": [{k: e[k] for k in ("seq", "revision", "kind", "from_authority", "to_authority", "reason", "created_at")} for e in st.events],
            "provenance_kinds": sorted({p.get("kind") for p in doc.get("provenance") or [] if isinstance(p, dict)}),
            **({"proposer": prop} if (prop := proposer_of(doc)) is not None else {}),
        }


def proposer_of(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Disclosure of a remote-model or agent proposer from a revision's provenance (None otherwise).

    It travels with the contract into reports and evidence, so every record that used a
    remote proposer states the provider, host, model and that a context packet left the machine."""
    prov = [p for p in doc.get("provenance") or [] if isinstance(p, dict)]
    kinds = sorted({str(p.get("kind")) for p in prov})
    if not ({"remote_model", "agent_proposed"} & set(kinds)) and not any(p.get("egress") for p in prov):
        return None
    egress = next((dict(p["egress"]) for p in prov if isinstance(p.get("egress"), dict)), None)
    client = next((dict(p["client"]) for p in prov if isinstance(p.get("client"), dict)), None)
    return {"kinds": kinds, "data_egress": egress, "client": client}


def contract_scope_digests(view: RegistryView | None) -> dict[str, str]:
    """symbol_id -> digest of the active contract revisions scoped to it (validity keys, T23/T38)."""
    if view is None:
        return {}
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for st in view.contracts.values():
        if not st.ok or st.authority not in ACTIVE:
            continue
        rev = st.current_revision
        by_scope.setdefault(st.scope, []).append({"id": st.contract_id, "revision": rev, "semantic": st.semantic.get(rev), "authority": st.authority})
    return {s: digest_json(sorted(v, key=lambda x: x["id"])) for s, v in by_scope.items()}


__all__ = ["proposer_of", "ContractRegistry", "RegistryView", "ContractState", "RegistryError", "contract_scope_digests", "ACTIVE", "REQUIRED_AUTHORITIES", "StoreError"]
