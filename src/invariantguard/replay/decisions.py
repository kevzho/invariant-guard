"""Maintainer decisions about behavior changes (pivot phase 5): accept, pin, retire.

Decisions are append-only rows in the state store (``behavior_decisions``, schema v4); a
decision is superseded only by a later ``retire`` row, never edited or deleted. Every
decision is made through the CLI by a person (``guard behavior ...``); no MCP tool
creates one, so an agent can only *ask* (the finding's question) and explain intent in
its patch rationale.

* ``accept``: a confirmed behavior difference was intended. Later replay runs that observe
  the same difference (same changed symbol, target, class, witness call and both outcomes)
  report it as INFO citing the decision. Any other witness or outcome still warns.
* ``pin``: an observed outcome on a recorded literal input becomes APPROVED behavior.
  Later replay runs execute the pinned input on base and candidate; a candidate outcome
  that differs from the pinned one while the base matched is an OBSERVED_REGRESSION
  (APPROVED, INTRODUCED, ERROR: blocks). Pins accept only literal inputs (encoded
  built-in values, optionally a receiver constructed from literals), so a pin never
  depends on repository text that may later change or disappear.
* ``retire``: ends an accept or pin (with a reason).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models.spans import digest_json, short_hex

DECISION_KINDS = ("accept", "pin", "retire")


def accept_key(symbol: str, target: str, cls: str, call: str, base: str, patch: str) -> str:
    return digest_json(["accept", symbol, target, cls, call, base, patch])


@dataclass
class Decision:
    decision_id: str
    kind: str
    symbol_id: str
    key: str  # accept: accept_key(...); pin: pin digest; retire: the retired decision id
    record: dict[str, Any]
    reason: str
    created_at: str
    seq: int

    def to_json(self) -> dict[str, Any]:
        return {"decision_id": self.decision_id, "kind": self.kind, "symbol_id": self.symbol_id, "key": self.key, "reason": self.reason,
                "created_at": self.created_at, "record": self.record}


class DecisionError(ValueError):
    pass


class Decisions:
    """Active decisions computed from the append-only log."""

    def __init__(self, rows: list[Decision]) -> None:
        self.rows = sorted(rows, key=lambda d: d.seq)
        retired = {d.key for d in self.rows if d.kind == "retire"}
        self.active = [d for d in self.rows if d.kind != "retire" and d.decision_id not in retired]

    def accepted(self) -> dict[str, Decision]:
        return {d.key: d for d in self.active if d.kind == "accept"}

    def pins(self) -> list[Decision]:
        return [d for d in self.active if d.kind == "pin"]

    def get(self, decision_id: str) -> Decision | None:
        return next((d for d in self.rows if d.decision_id == decision_id), None)


def load(store: Any) -> Decisions:
    return Decisions([Decision(*r[:4], json.loads(r[4]), *r[5:]) for r in store.behavior_decision_rows()])


def _record(store: Any, kind: str, symbol: str, key: str, record: dict[str, Any], reason: str, created_at: str) -> Decision:
    if not reason or not reason.strip():
        raise DecisionError("a decision needs a non-empty --reason (it is part of the audit trail)")
    did = f"bd-{kind}-" + short_hex([kind, symbol, key, record, reason, created_at], 16)
    seq = store.add_behavior_decision(did, kind, symbol, key, json.dumps(record, sort_keys=True), reason, created_at)
    return Decision(did, kind, symbol, key, record, reason, created_at, seq)


def accept(store: Any, finding: dict[str, Any], reason: str, created_at: str, *, run_id: str | None = None) -> Decision:
    if finding.get("property_result") != "OBSERVED_DIFFERENCE" or "behavior" not in finding:
        raise DecisionError(f"finding {finding.get('finding_id')} is not a behavior difference (OBSERVED_DIFFERENCE); only those can be accepted")
    b = finding["behavior"]
    w = b["witness"]
    key = accept_key(b["changed_symbol"], b["observed_through"]["target"], b["difference_class"], w["call"], w["base"], w["patch"])
    if key in load(store).accepted():
        raise DecisionError("this behavior change is already accepted")
    record = {"finding_id": finding["finding_id"], "run_id": run_id, "changed_symbol": b["changed_symbol"], "target": b["observed_through"]["target"],
              "difference_class": b["difference_class"], "witness": w, "evidence_ids": finding.get("evidence_ids", [])}
    return _record(store, "accept", b["changed_symbol"], key, record, reason, created_at)


def pin(store: Any, finding: dict[str, Any], evidence: list[dict[str, Any]], side: str, reason: str, created_at: str, *, run_id: str | None = None) -> Decision:
    """Pin the ``side`` (base | patch) outcome of the finding's witness as APPROVED behavior."""
    if side not in ("base", "patch"):
        raise DecisionError("--side must be base or patch")
    if finding.get("property_result") != "OBSERVED_DIFFERENCE" or "behavior" not in finding:
        raise DecisionError("only a behavior difference (OBSERVED_DIFFERENCE) can be pinned")
    b = finding["behavior"]
    ex = None
    for ev in evidence:
        if ev.get("evidence_id") in finding.get("evidence_ids", []) and ev.get("kind") == "REPLAY":
            ex = next((e for e in ev["replay"]["examples"] if e["call"] == b["witness"]["call"]), None)
            if ex is not None:
                break
    if ex is None or "probe" not in ex or "observations" not in ex:
        raise DecisionError("the finding's evidence has no recorded probe for its witness (re-run verify --replay with this engine version)")
    probe = ex["probe"]
    if not literal_only(probe):
        raise DecisionError("only witnesses whose inputs are literal values can be pinned (this one uses repository expressions or call steps); "
                            "pin a literal call instead")
    expected = ex["observations"][side]
    record = {"changed_symbol": b["changed_symbol"], "target": b["observed_through"]["target"], "probe": probe, "expected": expected,
              "expected_text": b["witness"][side], "call": b["witness"]["call"], "from_finding": finding["finding_id"], "run_id": run_id, "side": side}
    key = digest_json(["pin", b["observed_through"]["target"], probe, expected])
    if any(d.key == key for d in load(store).pins()):
        raise DecisionError("this behavior is already pinned")
    return _record(store, "pin", b["changed_symbol"], key, record, reason, created_at)


def retire(store: Any, decision_id: str, reason: str, created_at: str) -> Decision:
    ds = load(store)
    d = ds.get(decision_id)
    if d is None or d.kind == "retire":
        raise DecisionError(f"no accept or pin decision {decision_id!r}")
    if d not in ds.active:
        raise DecisionError(f"decision {decision_id} is already retired")
    return _record(store, "retire", d.symbol_id, decision_id, {"retired": decision_id}, reason, created_at)


def literal_only(probe: dict[str, Any]) -> bool:
    """A probe spec whose every input is an encoded value (receiver: literal construction only; no steps)."""
    def lit(a: Any) -> bool:
        return isinstance(a, dict) and set(a) == {"v"}

    if probe.get("steps"):
        return False
    r = probe.get("receiver")
    if r is not None and not (set(r) == {"construct"} and all(lit(a) for a in r["construct"].get("args", []))
                              and all(lit(a) for a in r["construct"].get("kwargs", {}).values())):
        return False
    return all(lit(a) for a in probe.get("args", [])) and all(lit(a) for a in probe.get("kwargs", {}).values())
