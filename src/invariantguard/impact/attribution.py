"""Patch relation classification (docs/03, docs/05 "Differential attribution", docs/07).

A candidate obligation is matched to the base obligation of the matched base symbol with
the same property and structural anchor. A validated candidate witness is then evaluated
against the *base* model with the IR evaluator (the same concrete input in both models):

=====================  ====================================================  ============
witness in base        base obligation                                        relation
=====================  ====================================================  ============
fails at the same site (any)                                                  PRE_EXISTING
passes / returns       PROVED_UNSAFE or POSSIBLY_UNSAFE (a known defect)      CHANGED
passes / returns       PROVED_SAFE, UNKNOWN or no such site in base           INTRODUCED
stuck (unmodeled)      PROVED_SAFE under the same declared domain             INTRODUCED
fails elsewhere,       -                                                      UNDETERMINED
raises otherwise,
stuck otherwise,
signature changed
=====================  ====================================================  ============

Candidate symbols with no base counterpart are INTRODUCED. An uncertain rename match,
or a base without an analyzable model (manifest-only baseline), gives UNDETERMINED,
unless the symbol's validity key is identical to the base's (then the analysis inputs and
therefore the results are identical: PRE_EXISTING). A base failure whose candidate
counterpart is PROVED_SAFE, removed, or passes on the base witness is RESOLVED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..analysis.obligations import ObligationOutcome, site_obligation_id
from ..ir.evaluator import UNMODELED, evaluate
from ..ir.model import Function, Kind
from ..models.enums import PatchRelation, PropertyResult, ScopeValidity
from ..orchestration import Relation

_FAILING = (PropertyResult.PROVED_UNSAFE, PropertyResult.POSSIBLY_UNSAFE)


@dataclass
class BaseModel:
    """What is known about the base for attribution."""

    available: bool  # base IR/analysis exists (Git revision or retained source)
    functions: dict[str, Function] = field(default_factory=dict)
    outcomes: dict[str, ObligationOutcome] = field(default_factory=dict)  # base obligation id -> outcome
    keys: dict[str, str] = field(default_factory=dict)  # base symbol -> validity key
    stored: dict[str, dict[str, Any]] = field(default_factory=dict)  # manifest-only: base obligation id -> stored result
    note: str = ""

    _index: dict[tuple[str, str, str], tuple[str, ObligationOutcome | None, dict[str, Any] | None]] | None = None

    def outcome_by_anchor(self, symbol_id: str, property_id: str, anchor: str) -> tuple[str | None, ObligationOutcome | None, dict[str, Any] | None]:
        if self._index is None:
            idx: dict[tuple[str, str, str], tuple[str, ObligationOutcome | None, dict[str, Any] | None]] = {}
            for oid, rec in self.stored.items():
                idx[(rec["symbol_id"], rec["property_id"], rec["anchor"])] = (oid, None, rec)
            for oid, o in self.outcomes.items():
                idx[(o.symbol_id, o.site.property_id, o.site.anchor)] = (oid, o, None)
            self._index = idx
        return self._index.get((symbol_id, property_id, anchor), (None, None, None))


def _args_for(fn: Function, witness: dict[str, Any]) -> dict[str, Any] | None:
    names = {p.name for p in fn.params}
    if not set(witness) <= names:
        return None
    args: dict[str, Any] = {}
    for p in fn.params:
        if p.name in witness:
            v = witness[p.name]
            if isinstance(v, list) and p.type.kind is Kind.TUPLE:
                v = tuple(v)
            elif isinstance(v, list):
                v = list(v)
            args[p.name] = v
        else:
            args[p.name] = UNMODELED
    return args


def eval_in(fn: Function | None, property_id: str, anchor: str, witness: dict[str, Any] | None, runtime_mode: str | None) -> str:
    """Outcome of running ``witness`` through ``fn``'s IR at the site with ``anchor``."""
    if fn is None or witness is None:
        return "NOT_EVALUATED"
    args = _args_for(fn, witness)
    if args is None:
        return "SIGNATURE_CHANGED"
    ev = evaluate(fn, args, assertions_enabled=runtime_mode != "python -O (assertions disabled)")
    target = next((s for s in fn.sites if s.property_id == property_id and s.anchor == anchor), None)
    if ev.kind == "REACHED_FAILURE":
        return "FAILED_SAME_SITE" if target is not None and ev.instr_result == target.result_id else "FAILED_OTHER_SITE"
    if ev.kind == "RETURNED":
        return "PASSED"
    if ev.kind in ("RAISED", "RAISED_OTHER"):
        return "RAISED_OTHER"
    if (target is None and ev.stuck_attrs.get("anchor") == anchor and ev.stuck_attrs.get("op") == "ATTRIBUTE"
            and ev.stuck_attrs.get("receiver_is_none") is False and property_id.startswith("none.")):
        # The same attribute load executed with a non-None receiver (it is no obligation here).
        return "PASSED"
    return "STUCK"


def classify(o: ObligationOutcome, cand_key: str | None, match: tuple[str, str] | None, uncertain: str | None, base: BaseModel) -> Relation:
    """Relation of one candidate obligation outcome (only meaningful for non-safe results)."""
    comp: dict[str, Any] = {"base_obligation_id": None, "base_result": None, "match": "none", "witness_in_base": "NOT_EVALUATED"}

    def rel(r: PatchRelation, reason: str) -> Relation:
        comp["reason"] = reason
        return Relation(r, comp)

    if uncertain is not None:
        comp["match"] = "uncertain_rename"
        return rel(PatchRelation.UNDETERMINED, f"possible rename of {uncertain} (uncertain match): both findings are kept; relation not determined")
    if match is None:
        if o.result in _FAILING:
            return rel(PatchRelation.INTRODUCED, "symbol has no counterpart in the base snapshot (new code)")
        return rel(PatchRelation.UNDETERMINED, "new symbol; no claim to attribute")
    base_sym, how = match
    comp["match"] = how
    boid, bout, brec = base.outcome_by_anchor(base_sym, o.site.property_id, o.site.anchor)
    comp["base_obligation_id"] = boid
    base_result = bout.result if bout is not None else (PropertyResult(brec["result"]) if brec is not None else None)
    comp["base_result"] = base_result.value if base_result is not None else None
    same_key = cand_key is not None and base.keys.get(base_sym) == cand_key
    if not base.available:
        comp["match"] = "base_unavailable"
        if same_key and base_result is o.result:
            return rel(PatchRelation.PRE_EXISTING, "identical validity key and identical stored base result (base source not retained)")
        return rel(PatchRelation.UNDETERMINED, f"base model unavailable ({base.note}); witness not evaluated against the base")
    if o.result is PropertyResult.UNKNOWN:
        if same_key:
            return rel(PatchRelation.PRE_EXISTING, "identical validity key: the same abstention exists in the base")
        return rel(PatchRelation.UNDETERMINED, "no claim (UNKNOWN); nothing to attribute")
    if o.result is PropertyResult.POSSIBLY_UNSAFE:
        if base_result is PropertyResult.PROVED_SAFE or (boid is None and base_sym in base.functions):
            return rel(PatchRelation.INTRODUCED, "base obligation was PROVED_SAFE (or absent); the candidate admits a possible failure")
        if same_key and base_result in _FAILING:
            return rel(PatchRelation.PRE_EXISTING, "identical validity key: the same possible failure exists in the base")
        return rel(PatchRelation.UNDETERMINED, "overapproximate result without a validated witness; base comparison inconclusive")
    # PROVED_UNSAFE: replay the validated witness in the base model.
    fn = base.functions.get(base_sym)
    outcome = eval_in(fn, o.site.property_id, o.site.anchor, o.witness, o.runtime_mode) if fn is not None else "STUCK"
    comp["witness_in_base"] = outcome
    if outcome == "FAILED_SAME_SITE":
        return rel(PatchRelation.PRE_EXISTING, f"the same witness {o.witness} also fails at the matched operation in the base")
    if outcome == "PASSED":
        if base_result in _FAILING:
            return rel(PatchRelation.CHANGED, f"the base already had a failure here, but not for witness {o.witness}: the violating domain grew")
        return rel(PatchRelation.INTRODUCED, f"the same witness {o.witness} runs without this failure in the base")
    if outcome == "STUCK" and base_result is PropertyResult.PROVED_SAFE and bout is not None and bout.scope in (ScopeValidity.EXACT_MODEL, ScopeValidity.CONDITIONAL):
        return rel(PatchRelation.INTRODUCED, "the base obligation is PROVED_SAFE for every admitted input (base evaluation of the witness was not possible)")
    reasons = {
        "FAILED_OTHER_SITE": "the witness fails at a different operation in the base",
        "RAISED_OTHER": "the base raises a different exception for this witness",
        "STUCK": "the base model cannot evaluate this witness (unmodeled values or effects)",
        "SIGNATURE_CHANGED": "the base function has different parameters",
        "NOT_EVALUATED": "the base symbol has no analyzable model",
    }
    return rel(PatchRelation.UNDETERMINED, reasons.get(outcome, outcome))


def resolved(base_out: ObligationOutcome, cand_fn: Function | None, cand_out: ObligationOutcome | None, cand_symbol: str | None) -> Relation | None:
    """RESOLVED (or UNDETERMINED) relation for a failing base obligation."""
    if base_out.result not in _FAILING:
        return None
    comp: dict[str, Any] = {"base_obligation_id": site_obligation_id(base_out.symbol_id, base_out.site), "base_result": base_out.result.value,
                            "match": "anchor", "witness_in_base": "FAILED_SAME_SITE" if base_out.witness_validated else "NOT_EVALUATED"}
    if cand_symbol is None:
        comp["match"] = "none"
        comp["reason"] = "the symbol was removed"
        return Relation(PatchRelation.RESOLVED, comp)
    if cand_out is not None:
        if cand_out.result is PropertyResult.PROVED_SAFE:
            comp["reason"] = "the matched candidate obligation is PROVED_SAFE"
            return Relation(PatchRelation.RESOLVED, comp)
        return None  # still failing or unknown: reported from the candidate side
    if base_out.witness is not None and base_out.witness_validated and cand_fn is not None:
        outcome = eval_in(cand_fn, base_out.site.property_id, base_out.site.anchor, base_out.witness, base_out.runtime_mode)
        comp["witness_in_candidate"] = outcome
        if outcome == "PASSED":
            comp["reason"] = "the operation was removed and the base witness runs without failure in the candidate"
            return Relation(PatchRelation.RESOLVED, comp)
    comp["reason"] = "the operation has no matched candidate counterpart; resolution not established"
    return Relation(PatchRelation.UNDETERMINED, comp)
