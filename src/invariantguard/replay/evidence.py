"""Map a differential-replay result to findings, evidence and policy inputs (docs/03).

One confirmed cluster (changed symbol, replayed target, difference class) becomes one
finding:

* ``property_id`` ``behavior.base_equivalence``: an implicit property, never declared;
* ``property_result`` ``OBSERVED_DIFFERENCE``, ``scope_validity`` ``EXECUTED`` (finite
  inputs, all recorded), ``authority`` ``INFERRED``, ``patch_relation`` ``CHANGED``;
* ``severity`` WARNING, or INFO when the difference is only an exception message or a
  witness comes from a test or doctest that only the patch contains (the patch
  acknowledges the change). A difference never blocks by itself: only a DECLARED or
  APPROVED property can make it a regression.

A changed public symbol without any input that executed on both sides is required work
left undone (INCOMPLETE). Replay requested but not performed (runner unavailable, base
source unavailable) is INCOMPLETE, never PASS.
"""

from __future__ import annotations

import ast
from typing import Any

from ..models.spans import LineIndex, short_hex
from .engine import ReplayResult

PROPERTY_ID = "behavior.base_equivalence"
QUANTIFIER = "exists a recorded input (the same input executed on base and patch; reproduced in fresh runs)"


def obligation_id(symbol: str, target: str, cls: str) -> str:
    return f"{symbol}::{PROPERTY_ID}#{int(short_hex([target, cls], 8), 16)}"


def _span(files: dict[str, bytes], symbol: str, snapshot_id: str) -> dict[str, Any] | None:
    path, qual = symbol.split("::", 1)
    raw = files.get(path)
    if raw is None:
        return None
    try:
        tree = ast.parse(raw)
    except SyntaxError:
        return None
    scope: list[ast.stmt] = tree.body
    node: ast.AST | None = None
    for p in qual.split("."):
        node = next((s for s in scope if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and s.name == p), None)
        if node is None:
            return None
        scope = node.body
    return LineIndex(raw).node_span(path, snapshot_id, node).to_json()


def _question(c: Any) -> str:
    w = c.witness
    via = "" if c.relation in ("direct", "constructor") else f" (observed through {c.target.split('::', 1)[1]}, {c.relation.replace('_', ' ')})"
    return f"Your patch changed the behavior of {c.symbol.split('::', 1)[1]}{via}: `{w.call}` -- base {w.base}; patch {w.patch}. Was this intended?"


def build(res: ReplayResult, *, cand_files: dict[str, bytes], cand_snapshot_id: str, base_snapshot_id: str | None, run_id: str, created_at: str,
          backend_status: Any, run_profile_digest: str | None, engine_version: str) -> dict[str, Any]:
    """Findings, evidence records, report section, coverage and policy reasons."""
    findings: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    env = {}
    harness, harness_digest, interp = "ig-replay-1", "", None
    for label, runs in res.runs.items():
        if label == "generation":
            continue
        for r in runs:
            env[label] = r.get("environment_digest")
            harness_digest = r.get("harness_digest") or harness_digest
            interp = interp or r.get("identity")
    warn = info = 0
    for c in res.clusters:
        span = _span(cand_files, c.symbol, cand_snapshot_id)
        if span is None:
            res.errors.append(f"no source span for {c.symbol}")
            continue
        oid = obligation_id(c.symbol, c.target, c.cls)
        w = c.witness
        severity = "INFO" if (c.cls == "exception_message" or c.acknowledged) else "WARNING"
        warn += severity == "WARNING"
        info += severity == "INFO"
        examples = [{"call": e.call, "input_origin": e.origin, "base": e.base, "patch": e.patch} for e in sorted(c.examples, key=lambda e: (len(e.call), e.call))[:6]]
        body = {
            "schema_version": "1.0", "kind": "REPLAY",
            "engine": {"name": "invariantguard", "version": engine_version, "ir_version": "n/a (execution)"},
            "snapshot_id": cand_snapshot_id, "symbol_id": c.symbol, "property_id": PROPERTY_ID, "obligation_id": oid,
            "result_label": "OBSERVED_DIFFERENCE", "domain": "recorded inputs (harvested from tests/doctests, perturbed defaults, generated in the boundary)",
            "assumptions": _assumptions(backend_status), "precision": "EXECUTED", "method": "PROBE", "source_span": span,
            "details": [f"{len(c.examples)} differing input(s), class {c.cls}, target {c.target} ({c.relation})"],
            "solver_runs": [], "witness": None, "witness_validation": "VALIDATED",
            "replay": {"backend": backend_status.backend, "isolated": bool(backend_status.isolated), "base_snapshot_id": base_snapshot_id,
                       "candidate_snapshot_id": cand_snapshot_id, "environment_digests": env, "harness": harness, "harness_digest": harness_digest,
                       "run_profile_digest": run_profile_digest, "interpreter": interp, "target": c.target, "difference_class": c.cls,
                       "examples": examples, "runs": {"base": 3, "patch": 3}},
        }
        ev_id = "ev-" + short_hex(body)
        evidence.append({**body, "evidence_id": ev_id, "run_id": run_id, "created_at": created_at})
        reasons = [f"same input, different observable outcome ({c.cls.replace('_', ' ')}); reproduced in fresh runs (base 2+1, patch 1+2)"]
        if c.acknowledged:
            reasons.append("a witness comes from a test or doctest that only the patch contains: the patch acknowledges this change (INFO)")
        elif c.cls == "exception_message":
            reasons.append("only the exception message differs (INFO)")
        reasons.append("advisory: base equivalence is not a declared property; a difference blocks only through a DECLARED or APPROVED contract")
        msg = f"behavior of {c.symbol} changed ({c.cls.replace('_', ' ')}): {w.call} -> base {w.base}; patch {w.patch}"
        findings.append({
            "finding_id": "fnd-" + short_hex([cand_snapshot_id, oid, w.call, w.base, w.patch]),
            "obligation_id": oid, "symbol_id": c.symbol, "property_id": PROPERTY_ID, "property_result": "OBSERVED_DIFFERENCE",
            "scope_validity": "EXECUTED", "authority": "INFERRED", "patch_relation": "CHANGED", "severity": severity, "source_span": span,
            "message": msg[:2000], "quantifier": QUANTIFIER, "domain": "recorded inputs (see evidence)", "domain_source": "NONE",
            "assumptions": ["the replayed inputs are representative of intended use; a difference on an invalid input may be irrelevant"],
            "path_constraints": [], "reasons": reasons, "witness": None, "witness_validated": True, "runtime_mode": None,
            "evidence_ids": [ev_id], "replay_available": True,
            "behavior": {
                "difference_class": c.cls, "changed_symbol": c.symbol, "observed_through": {"target": c.target, "relation": c.relation},
                "witness": {"call": w.call, "input_origin": w.origin, "base": w.base, "patch": w.patch},
                "examples_in_cluster": len(c.examples), "input_origins": sorted({e.origin for e in c.examples}),
                "acknowledged_by_patch_tests": c.acknowledged, "confirmed_runs": {"base": 3, "patch": 3},
                "question": _question(c),
                "if_intended": "state it in the patch rationale (a test or doctest in the patch that exercises it makes this INFO)",
                "if_not_intended": "fix the patch and re-run verify with --replay: the same input is replayed",
            },
        })
    cov = res.coverage()
    incomplete = [f"replay: changed public symbol {g['symbol_id']} has no executed input ({g['reason']})" for g in cov["required_gaps"]]
    warn_reasons = []
    if warn:
        warn_reasons.append(f"replay: {warn} unacknowledged behavior change(s) (advisory: confirm each was intended)")
    extra = []
    if info:
        extra.append(f"replay: {info} behavior change(s) acknowledged by the patch's own tests or affecting only exception messages (INFO)")
    if res.unconfirmed:
        extra.append(f"replay: {res.unconfirmed} difference(s) did not reproduce in fresh runs and were not reported")
    section = {"requested": True, "status": "INCOMPLETE" if incomplete else ("COMPLETE" if res.targets else "NOTHING_TO_REPLAY"),
               "property_id": PROPERTY_ID, "targets": [t.to_json() for t in res.targets], "coverage": cov, "clusters": len(res.clusters),
               "findings": len(findings), "probes": res.probes, "unconfirmed": res.unconfirmed, "runs": res.runs, "errors": res.errors,
               "timing_ms": res.timing_ms, "base_snapshot_id": base_snapshot_id, "candidate_snapshot_id": cand_snapshot_id}
    return {"findings": findings, "evidence": evidence, "section": section, "coverage": cov, "incomplete_reasons": incomplete,
            "warn_reasons": warn_reasons, "extra_reasons": extra}


def unavailable(reason: str) -> dict[str, Any]:
    """Replay was requested but could not run: required work undone."""
    section = {"requested": True, "status": "UNAVAILABLE", "reason": reason}
    return {"findings": [], "evidence": [], "section": section, "coverage": {"status": "UNAVAILABLE", "reason": reason},
            "incomplete_reasons": [f"replay requested but not performed: {reason}"], "warn_reasons": [], "extra_reasons": []}


def _assumptions(st: Any) -> list[str]:
    out = [f"executed in the {st.backend} runner ({'isolated; capabilities verified from inside the boundary' if st.isolated else 'UNISOLATED: not a sandbox'})",
           "observations compared on the observable view (private attributes, concrete iterator types and timing ignored)",
           "both base runs agreed (nondeterministic inputs are excluded)"]
    return out
