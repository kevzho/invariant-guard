"""Dynamic falsification of discovered candidates on one snapshot (M4 T43, docs/06 "Discovery and falsification").

Used by ``guard contracts discover --execute --run-profile NAME`` for *every* validated
candidate, whatever its source (template or local model). It reuses the M3 runner and
the exact predicate evaluator of the contract checker:

1. admit inputs (examples/boundary inputs first, then profile boundaries and seeded random
   inputs); each must satisfy the input profile and the precondition;
2. run them in the configured runner against the captured snapshot and evaluate the
   candidate's predicate on the host;
3. on a violation, shrink within the domain (simpler inputs that still satisfy profile and
   precondition and still violate), then re-run the minimized counterexample in a fresh
   runner invocation;
4. REFUTED only when the fresh run violates again (OBSERVED_FAILURE, EXECUTED, finite);
   TESTED when executed probes passed (never a proof); UNKNOWN when nothing decidable
   was observed (import error, timeout, opaque values, ...).

This is discovery evidence about the *current* code only: there is no baseline, so no
patch relation and no regression claim. Results are reported by ``discover``; durable
contract evidence is still produced by ``guard verify --execute``.
"""

from __future__ import annotations

import time
from typing import Any

from .checker import ExecContext, Side, _canon, _evaluate_batch, _witness
from .model import TypedContract
from .profiles import admit_inputs, check_value, complexity, domain_hints, shrink_candidates
from .values import encode

SHRINK_BATCH = 16


def falsify(ctx: ExecContext, tc: TypedContract, side: Side) -> dict[str, Any]:
    t0 = time.perf_counter()
    runs0 = ctx.backend.runs
    out: dict[str, Any] = {"status": "UNKNOWN", "property_result": "UNKNOWN", "scope_validity": None, "quantifier": None, "reasons": []}
    if not ctx.profile.allows(tc.scope):
        out.update(status="NOT_ALLOWED", reasons=[f"run profile {ctx.profile.name!r} does not allowlist target {tc.scope}"])
        return out
    if tc.input_profile.name not in ctx.profile.input_profiles:
        out.update(status="NOT_ALLOWED", reasons=[f"run profile {ctx.profile.name!r} does not allow input profile {tc.input_profile.name}"])
        return out
    params = list(tc.target.params)
    seed = int(tc.semantic_digest.split(":", 1)[1][:12], 16) ^ (ctx.profile.seed & 0xFFFFFFFFFFFF)
    adm = admit_inputs(tc.input_profile, params, tc.precondition_holds, domain_hints(tc.precondition, params), seed=seed,
                       limit=ctx.profile.max_probes, examples=tc.examples)
    probes = [(f"p{i:04d}", a) for i, (a, _) in enumerate(adm.inputs)]
    evals, _, err = _evaluate_batch(ctx, side, tc, probes, "")
    verdicts = [evals[p]["verdict"] for p, _ in probes]
    out["probes"] = {"attempted_inputs": adm.attempts, "admitted": len(probes), "excluded_by_domain": len(adm.excluded), "seed": seed,
                     "pass": verdicts.count("PASS"), "fail": verdicts.count("FAIL"), "unknown": verdicts.count("UNKNOWN")}
    if err:
        out["reasons"].append(f"runner error: {err}")
    failing = [(p, a) for p, a in probes if evals[p]["verdict"] == "FAIL"]
    if not failing:
        if verdicts.count("PASS"):
            out.update(status="TESTED", property_result="TESTED", scope_validity="EXECUTED",
                       quantifier=f"for the {verdicts.count('PASS')} executed admitted input(s) only (finite probes; not a proof)")
            out["reasons"].append(f"{verdicts.count('PASS')} admitted input(s) satisfied the candidate" +
                                  (f"; {verdicts.count('UNKNOWN')} undecided" if verdicts.count("UNKNOWN") else ""))
        else:
            out["reasons"].append(f"no probe produced a decidable observation ({verdicts.count('UNKNOWN')} unknown of {len(probes)})")
            out["reasons"].extend(sorted({evals[p]["reason"] for p, _ in probes})[:3])
        out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        out["runner_invocations"] = ctx.backend.runs - runs0
        return out
    first_id, orig = failing[0]
    cur = orig
    rounds = 0
    seen = {_canon(encode(orig))}
    while rounds < ctx.profile.max_shrink_rounds:
        cands = []
        for p in params:
            for v in shrink_candidates(tc.input_profile, cur[p]):
                a = dict(cur)
                a[p] = v
                key = _canon(encode(a))
                if key in seen or any(check_value(tc.input_profile, a[q]) for q in params):
                    continue
                seen.add(key)
                if tc.precondition_holds(a)[0] is True:  # never leave the candidate's domain
                    cands.append(a)
        if not cands:
            break
        cands.sort(key=lambda a: [complexity(a[p]) for p in params])
        rounds += 1
        sp = [(f"s{rounds:02d}-{i:02d}", a) for i, a in enumerate(cands[:SHRINK_BATCH])]
        se, _, _ = _evaluate_batch(ctx, side, tc, sp, "")
        nxt = next((a for pid, a in sp if se[pid]["verdict"] == "FAIL"), None)
        if nxt is None:
            break
        cur = nxt
    conf, _, _ = _evaluate_batch(ctx, side, tc, [("c-minimized", cur)], "")
    c = conf["c-minimized"]
    out["counterexample"] = {"input": _witness(cur), "original_input": _witness(orig), "shrink_rounds": rounds,
                             "first_observation": {k: v for k, v in evals[first_id].items() if k in ("verdict", "reason", "result", "exception")},
                             "confirmation": {k: v for k, v in c.items() if k in ("verdict", "reason", "result", "exception")}}
    if c["verdict"] == "FAIL":
        out.update(status="REFUTED", property_result="OBSERVED_FAILURE", scope_validity="EXECUTED",
                   quantifier="exists an admitted input (executed on the current snapshot; confirmed in a fresh run)")
        out["reasons"].append(f"counterexample {_witness(cur)}: {c['reason']}")
    else:
        out.update(status="UNSTABLE", property_result="UNKNOWN",
                   quantifier=None)
        out["reasons"].append(f"the violation did not reproduce in a fresh run ({c['verdict']}: {c['reason']}); instability or a runner problem")
    out["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    out["runner_invocations"] = ctx.backend.runs - runs0
    return out


__all__ = ["falsify"]
