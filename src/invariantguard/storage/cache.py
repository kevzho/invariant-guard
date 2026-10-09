"""Per-symbol analysis cache over ``Store`` (docs/07: reuse only completed, valid evidence).

An entry stores the outcomes of one function plus their original evidence records. It is
reused only when

* its validity key equals the key of the freshly lowered symbol,
* its payload re-verifies (artifact digest, embedded key), and
* every stored outcome binds to a current obligation site with the same property,
  ordinal and structural anchor (otherwise the entry is rejected and the symbol is
  analyzed again).

Results that depend on time (budget exhaustion, solver timeout/resource limit/unknown)
or that came from an engine error are never cached. Entries become visible only after
their run finished with a consistent snapshot (``Store.finalize_run``).
"""

from __future__ import annotations

from typing import Any

from ..analysis.obligations import ObligationOutcome
from ..ir.model import Function
from ..models.enums import AnalysisMethod, Authority, DomainSource, PropertyResult, ScopeValidity
from ..orchestration import UnitCache, make_evidence
from .store import Store

CACHE_KIND = "symbol_analysis/v1"
_TRANSIENT_SOLVER = {"TIMEOUT", "RESOURCE_LIMIT", "UNKNOWN"}


def cacheable(outcomes: list[ObligationOutcome]) -> bool:
    for o in outcomes:
        if o.method is AnalysisMethod.BUDGET:
            return False
        if any(r.get("response") in _TRANSIENT_SOLVER for r in o.solver_runs):
            return False
    return True


def _encode(o: ObligationOutcome, ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "property_id": o.site.property_id, "ordinal": o.site.ordinal, "anchor": o.site.anchor,
        "result": o.result.value, "method": o.method.value, "scope": o.scope.value if o.scope else None,
        "authority": o.authority.value, "domain_source": o.domain_source.value, "domain": o.domain,
        "assumptions": o.assumptions, "reasons": o.reasons, "witness": o.witness, "witness_validated": o.witness_validated,
        "path_constraints": o.path_constraints, "solver_runs": o.solver_runs, "quantifier": o.quantifier,
        "runtime_mode": o.runtime_mode, "divisor_fact": o.divisor_fact, "evidence": ev,
    }


class SymbolCache(UnitCache):
    def __init__(self, store: Store, keys: dict[str, str], *, run_id: str, snapshot_id: str, created_at: str,
                 read: bool = True, write: bool = True) -> None:
        self.store = store
        self.keys = keys
        self.run_id = run_id
        self.snapshot_id = snapshot_id
        self.created_at = created_at
        self.read = read
        self.write = write
        self.hits = 0
        self.misses = 0
        self.staged = 0
        self.uncacheable = 0
        self.rejected: list[str] = []

    def get(self, symbol_id: str, fn: Function) -> list[ObligationOutcome] | None:
        key = self.keys.get(symbol_id)
        if not self.read or key is None:
            return None
        payload = self.store.lookup(key, CACHE_KIND)
        if payload is None:
            self.misses += 1
            return None
        out = self._decode(payload, fn)
        if out is None:
            self.rejected.append(symbol_id)
            self.misses += 1
            return None
        self.hits += 1
        return out

    def _decode(self, payload: dict[str, Any], fn: Function) -> list[ObligationOutcome] | None:
        sites = {(s.property_id, s.ordinal): s for s in fn.sites}
        items = payload.get("outcomes")
        if payload.get("symbol_id") != fn.symbol_id or not isinstance(items, list) or len(items) != len(sites):
            return None
        out: list[ObligationOutcome] = []
        try:
            for it in items:
                site = sites.get((it["property_id"], it["ordinal"]))
                if site is None or site.anchor != it["anchor"]:
                    return None
                out.append(ObligationOutcome(
                    symbol_id=fn.symbol_id, site=site, result=PropertyResult(it["result"]), method=AnalysisMethod(it["method"]),
                    scope=ScopeValidity(it["scope"]) if it["scope"] else None, authority=Authority(it["authority"]),
                    domain_source=DomainSource(it["domain_source"]), domain=it["domain"], assumptions=list(it["assumptions"]),
                    reasons=list(it["reasons"]), witness=it["witness"], witness_validated=bool(it["witness_validated"]),
                    path_constraints=list(it["path_constraints"]), solver_runs=list(it["solver_runs"]), quantifier=it["quantifier"],
                    runtime_mode=it["runtime_mode"], divisor_fact=it["divisor_fact"], reused_evidence=dict(it["evidence"]),
                ))
        except (KeyError, ValueError, TypeError):
            return None
        if {(o.site.property_id, o.site.ordinal) for o in out} != set(sites):
            return None
        return out

    def stage(self, symbol_id: str, fn: Function, outcomes: list[ObligationOutcome]) -> None:
        key = self.keys.get(symbol_id)
        if not self.write or key is None:
            return
        if not cacheable(outcomes):
            self.uncacheable += 1
            return
        items = [_encode(o, make_evidence(o, self.snapshot_id, self.run_id, self.created_at, False)) for o in outcomes]
        payload = {"cache_key": key, "symbol_id": symbol_id, "outcomes": items}
        self.store.stage_entry(self.run_id, key, CACHE_KIND, symbol_id, payload)
        self.staged += 1

    def stats(self) -> dict[str, Any]:
        return {"hits": self.hits, "misses": self.misses, "staged": self.staged, "uncacheable": self.uncacheable, "rejected": sorted(self.rejected)}
