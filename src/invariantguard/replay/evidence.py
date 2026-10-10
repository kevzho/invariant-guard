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
          backend_status: Any, run_profile_digest: str | None, engine_version: str, accepted: dict[str, Any] | None = None,
          declared: list[Any] | None = None) -> dict[str, Any]:
    """Findings, evidence records, report section, coverage and policy reasons.

    ``accepted`` maps ``decisions.accept_key`` to active accept decisions; ``declared`` holds
    the doctest and pin results (``declared.DeclaredResult``)."""
    from .decisions import accept_key

    accepted = accepted or {}
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
        decision = accepted.get(accept_key(c.symbol, c.target, c.cls, w.call, w.base, w.patch))
        severity = "INFO" if (c.cls == "exception_message" or c.acknowledged or decision is not None) else "WARNING"
        warn += severity == "WARNING"
        info += severity == "INFO"
        examples = [_example(e) for e in sorted(c.examples, key=lambda e: (len(e.call), e.call))[:6]]
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
        if decision is not None:
            reasons.append(f"accepted as intended by maintainer decision {decision.decision_id} ({decision.created_at}): {decision.reason}"[:500])
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
                **({"accepted_by": decision.decision_id} if decision is not None else {}),
                "question": _question(c),
                "if_intended": "state it in the patch rationale (a test or doctest in the patch that exercises it makes this INFO)",
                "if_not_intended": "fix the patch and re-run verify with --replay: the same input is replayed",
            },
        })
    blocking = 0
    for dr in declared or []:
        for reg in dr.regressions:
            f, ev = _declared_finding(reg, cand_files, cand_snapshot_id, base_snapshot_id, run_id, created_at, backend_status, env, harness, harness_digest,
                                      run_profile_digest, interp, engine_version)
            if f is None:
                res.errors.append(f"no source span for declared behavior {reg.get('path')}::{reg.get('owner') or reg.get('target')}")
                continue
            findings.append(f)
            evidence.append(ev)
            blocking += f["severity"] == "ERROR"
    cov = res.coverage()
    incomplete = [f"replay: changed public symbol {g['symbol_id']} has no executed input ({g['reason']})" for g in cov["required_gaps"]]
    for dr in declared or []:
        incomplete += [f"replay: {x}" for x in dr.incomplete]
    warn_reasons = []
    if warn:
        warn_reasons.append(f"replay: {warn} unacknowledged behavior change(s) (advisory: confirm each was intended)")
    extra = []
    if info:
        extra.append(f"replay: {info} behavior change(s) acknowledged by the patch's own tests or affecting only exception messages (INFO)")
    if res.unconfirmed:
        extra.append(f"replay: {res.unconfirmed} difference(s) did not reproduce in fresh runs and were not reported")
    if blocking:
        extra.append(f"replay: {blocking} declared or approved behavior(s) regressed (doctest or pinned behavior; blocking)")
    section = {"requested": True, "status": "INCOMPLETE" if incomplete else ("COMPLETE" if res.targets or declared else "NOTHING_TO_REPLAY"),
               "declared": {k: v for k, v in zip(("doctests", "pins"), [d.stats for d in (declared or [])])},
               "property_id": PROPERTY_ID, "targets": [t.to_json() for t in res.targets], "coverage": cov, "clusters": len(res.clusters),
               "findings": len(findings), "probes": res.probes, "unconfirmed": res.unconfirmed, "runs": res.runs, "errors": res.errors,
               "timing_ms": res.timing_ms, "base_snapshot_id": base_snapshot_id, "candidate_snapshot_id": cand_snapshot_id}
    return {"findings": findings, "evidence": evidence, "section": section, "coverage": cov, "incomplete_reasons": incomplete,
            "warn_reasons": warn_reasons, "extra_reasons": extra}


MAX_EXAMPLE_BYTES = 128 * 1024


def _example(e: Any) -> dict[str, Any]:
    """Rendered example plus its recorded probe and observations (dropped when too large; then it cannot be pinned)."""
    import json

    out = {"call": e.call, "input_origin": e.origin, "base": e.base, "patch": e.patch}
    if e.probe is not None and e.observations is not None:
        extra = {"probe": e.probe, "observations": e.observations}
        if len(json.dumps(extra, sort_keys=True)) <= MAX_EXAMPLE_BYTES:
            out.update(extra)
    return out


def _declared_finding(reg: dict[str, Any], cand_files: dict[str, bytes], cand_snapshot_id: str, base_snapshot_id: str | None, run_id: str, created_at: str,
                      st: Any, env: dict[str, Any], harness: str, harness_digest: str, run_profile_digest: str | None, interp: Any,
                      engine_version: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """An OBSERVED_REGRESSION of a doctest example (DECLARED) or a pin (APPROVED)."""
    if reg["kind"] == "doctest":
        symbol = f"{reg['path']}::{reg['owner']}"
        prop, authority = "behavior.doctest", "DECLARED"
        ident = [reg["test"], reg["source"], reg["want"]]
        cid = "doctest." + short_hex(ident, 16)
        relation, severity = "INTRODUCED", "ERROR"
        call, expected = reg["source"].strip(), reg["want"].strip()
        msg = (f"doctest of {reg['test']} regressed: `{call}` expects {expected!r}; base passes, patch gives {reg['got_patch'].strip()!r} "
               f"(an unchanged example in the repository: declared behavior)")
        span = _span(cand_files, symbol, cand_snapshot_id) if reg["owner"] != "<module>" else _module_span(cand_files, reg["path"], cand_snapshot_id)
    else:
        symbol = reg["target"]
        prop, authority = "behavior.pinned", "APPROVED"
        ident = [reg["decision_id"]]
        cid = reg["decision_id"]
        relation = reg["relation"]
        severity = "ERROR" if relation == "INTRODUCED" else "WARNING"
        call, expected = reg["call"], reg["expected"]
        msg = (f"pinned behavior {reg['decision_id']} of {symbol} changed: `{call}` must give {expected}; base {reg['got_base']}; patch {reg['got_patch']}"
               + ("" if relation == "INTRODUCED" else " (the base differs too: pre-existing)"))
        span = _span(cand_files, symbol, cand_snapshot_id)
    oid = f"{symbol}::{prop}#{int(short_hex(ident, 8), 16)}"
    contract = {"contract_id": cid, "revision": 1, "semantic_digest": "sha256:" + short_hex(ident, 64), "authority": authority}
    detail = {"kind": reg["kind"], "call": call, "expected": expected, "base": reg["got_base"].strip()[:2000], "patch": reg["got_patch"].strip()[:2000]}
    body = {
        "schema_version": "1.0", "kind": "REPLAY", "engine": {"name": "invariantguard", "version": engine_version, "ir_version": "n/a (execution)"},
        "snapshot_id": cand_snapshot_id, "symbol_id": symbol, "property_id": prop, "obligation_id": oid, "result_label": "OBSERVED_REGRESSION",
        "domain": "the recorded example input", "assumptions": _assumptions(st), "precision": "EXECUTED", "method": "PROBE",
        "source_span": span or {}, "details": [msg[:500]], "solver_runs": [], "witness": {}, "witness_validation": "VALIDATED",
        "contract": contract,
        "replay": {"backend": st.backend, "isolated": bool(st.isolated), "base_snapshot_id": base_snapshot_id, "candidate_snapshot_id": cand_snapshot_id,
                   "environment_digests": env, "harness": harness, "harness_digest": harness_digest, "run_profile_digest": run_profile_digest,
                   "interpreter": interp, "target": symbol, "difference_class": reg["kind"], "examples": [{"call": call, "input_origin": reg["kind"],
                   "base": detail["base"], "patch": detail["patch"]}], "runs": {"base": 2 if reg["kind"] == "doctest" else 1, "patch": 2}, "declared": detail},
    }
    ev_id = "ev-" + short_hex(body)
    ev = {**body, "evidence_id": ev_id, "run_id": run_id, "created_at": created_at}
    if span is None:
        return None, ev
    reasons = [f"{authority} behavior: " + ("an unchanged doctest example of the repository" if reg["kind"] == "doctest"
                                            else f"pinned by maintainer decision {reg['decision_id']}: {reg.get('reason', '')}"[:300]),
               "the same input ran on base and patch in the runner; reproduced (patch twice" + (", base twice)" if reg["kind"] == "doctest" else ")")]
    f = {
        "finding_id": "fnd-" + short_hex([cand_snapshot_id, oid, detail]), "obligation_id": oid, "symbol_id": symbol, "property_id": prop,
        "property_result": "OBSERVED_REGRESSION", "scope_validity": "EXECUTED", "authority": authority, "patch_relation": relation, "severity": severity,
        "source_span": span, "message": msg[:2000], "quantifier": "exists the recorded example input (executed on base and patch)",
        "domain": "the recorded example input", "domain_source": "NONE", "assumptions": [], "path_constraints": [], "reasons": reasons,
        "witness": {}, "witness_validated": True, "runtime_mode": None, "evidence_ids": [ev_id], "replay_available": True,
        "contract": {k: contract[k] for k in ("contract_id", "revision", "semantic_digest")}, "declared": detail,
    }
    return f, ev


def _module_span(files: dict[str, bytes], path: str, snapshot_id: str) -> dict[str, Any] | None:
    raw = files.get(path)
    if raw is None:
        return None
    return LineIndex(raw).span(path, snapshot_id, 1, 0, 1, 0).to_json()


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
