"""Differential contract checking: same witness, base and candidate (M3 T35-T37, docs/06).

For every active contract (APPROVED, DECLARED or INFERRED; never REJECTED/RETIRED):

1. validate the current revision against the candidate snapshot (and the base);
2. admit probe inputs: examples, boundary and seeded random inputs that satisfy the
   input profile *and* the precondition (others are counted as excluded, never run);
3. run the same probes in both snapshots in the configured runner (each probe in a fresh
   child process inside a fresh container per batch) and evaluate the exact approved
   predicate on the host, on decoded built-in values only;
4. classify each input: base pass + candidate fail = regression; both fail = pre-existing
   (or an invalid specification); base fail + candidate pass = resolved; anything the
   runner could not establish (import error, timeout, crash, missing baseline) is
   UNKNOWN or an UNDETERMINED comparison, never a defect;
5. shrink a counterexample: candidates are strictly simpler, must satisfy profile and
   precondition (the domain is never left), and must keep the same classification;
6. confirm the original and the minimized witness in fresh runs (base once, candidate
   twice, so a patch failure is repeated); only a confirmed base pass + candidate fail
   under comparable environments is OBSERVED_REGRESSION.

Passing probes give TESTED (finite, executed; never a proof). Evidence records carry the
run profile digest, the environment identity, the inputs, the observations and the
predicate evaluations; IDs are content-addressed (timings excluded).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .. import ENGINE_NAME, ENGINE_VERSION
from ..execution.profiles import RunProfileConfig
from ..execution.runner import Backend, BackendStatus, Workspace, validate_observations
from ..models.spans import canonical_json, short_hex
from .dsl import EvalEnv, Rejection, evaluate
from .model import TypedContract, validate_revision
from .profiles import admit_inputs, complexity, domain_hints, shrink_candidates
from .registry import REQUIRED_AUTHORITIES, ContractRegistry, ContractState, RegistryView
from .values import ValueError_, decode, encode, to_plain

PROPERTY_ID = "contract.behavior"
QUANTIFIER_FOUND = "exists an admitted input (same witness executed in base and candidate)"
SHRINK_BATCH = 24


def obligation_id(scope: str, contract_id: str) -> str:
    return f"{scope}::{PROPERTY_ID}#{int(short_hex(contract_id, 8), 16)}"


@dataclass
class Side:
    label: str  # base | candidate
    snapshot_id: str | None
    files: dict[str, bytes] | None
    note: str = ""
    locks: list[dict[str, str]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.files is not None


@dataclass
class ExecContext:
    backend: Backend
    status: BackendStatus
    profile: RunProfileConfig
    run_profile_doc: dict[str, Any]
    run_profile_digest: str
    run_id: str
    created_at: str
    registry: ContractRegistry | None
    store_witnesses: bool = True
    workspaces: dict[str, Workspace] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def workspace(self, side: Side) -> Workspace:
        with self._lock:  # base and candidate batches run concurrently
            ws = self.workspaces.get(side.label)
            if ws is None:
                assert side.files is not None
                ws = Workspace(side.files, side.label)
                self.workspaces[side.label] = ws
            return ws

    def close(self) -> None:
        for ws in self.workspaces.values():
            ws.close()
        self.workspaces.clear()


@dataclass
class ContractsOutcome:
    section: dict[str, Any]
    findings: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    incomplete_reasons: list[str] = field(default_factory=list)
    extra_reasons: list[str] = field(default_factory=list)
    coverage: dict[str, int] = field(default_factory=dict)
    execution_label: str = "disabled"
    dynamic_ms: float | None = None
    severities: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warn_reasons: list[str] = field(default_factory=list)  # advisory, verdict-affecting (WARN)


# -- observation evaluation ---------------------------------------------------------------------
def _canon(node: Any) -> str:
    return canonical_json(node).decode("utf-8")


def _exc_text(e: dict[str, Any]) -> str:
    where = ""
    frames = e.get("frames") or []
    if frames:
        f = frames[-1]
        where = f" at {f.get('path')}:{f.get('line')} in {f.get('function')}"
    return f"{e.get('type')}: {str(e.get('message'))[:200]}{where}"


def evaluate_observation(tc: TypedContract, args: dict[str, Any], obs: dict[str, Any] | None, runner_error: str | None) -> dict[str, Any]:
    """PASS / FAIL / UNKNOWN for one probe in one snapshot (host-side, pure)."""
    if obs is None:
        return {"verdict": "UNKNOWN", "kind": "runner_error", "reason": runner_error or "no observation"}
    status = obs.get("status")
    if status != "ok":
        detail = obs.get("detail") if isinstance(obs.get("detail"), dict) else {}
        return {"verdict": "UNKNOWN", "kind": status, "reason": f"{status} (runner/environment outcome, not a program defect): {_exc_text(detail)}"}
    results = obs.get("results")
    reps = tc.repeat["repeats"] if tc.repeat else 1
    if type(results) is not list or len(results) != reps or not all(isinstance(r, dict) for r in results):
        return {"verdict": "UNKNOWN", "kind": "malformed", "reason": "malformed observation (unexpected result list)"}
    raised = [r for r in results if r.get("kind") == "raised"]
    if raised:
        exc = raised[0].get("exception") if isinstance(raised[0].get("exception"), dict) else {}
        idx = results.index(raised[0])
        if exc.get("type") == "MemoryError":
            # The runner caps memory (cgroup/RLIMIT); exhausting it is a resource outcome of this
            # environment, not evidence about the contract (docs/03; audit A3-03).
            return {"verdict": "UNKNOWN", "kind": "resource_limit",
                    "reason": f"resource_limit (runner/environment outcome, not a program defect): {_exc_text(exc)}"}
        return {"verdict": "FAIL", "kind": "raised", "reason": f"the call raised {_exc_text(exc)}" + (f" (repeat {idx + 1} of {reps})" if reps > 1 else ""),
                "exception": {"type": exc.get("type"), "message": str(exc.get("message"))[:300], "frames": (exc.get("frames") or [])[-5:]}}
    try:
        values = [decode(r["value"]) for r in results]
        before = {k: decode(v) for k, v in (obs.get("before") or {}).items()}
        after = {k: decode(v) for k, v in (obs.get("after") or {}).items()}
    except (ValueError_, KeyError, TypeError) as exc:
        return {"verdict": "UNKNOWN", "kind": "malformed", "reason": f"malformed observation: {exc}"}
    env = EvalEnv(args=args, result=values[0], has_result=True, before=before, after=after,
                  before_enc={k: _canon(v) for k, v in (obs.get("before") or {}).items()},
                  after_enc={k: _canon(v) for k, v in (obs.get("after") or {}).items()},
                  repeat_encodings=[_canon(r["value"]) for r in results])
    r = evaluate(tc.predicate, env)
    verdict = {"TRUE": "PASS", "FALSE": "FAIL"}.get(r.status, "UNKNOWN")
    out = {"verdict": verdict, "kind": "returned", "reason": "predicate holds" if verdict == "PASS" else f"predicate {r.reason}" if verdict == "FAIL" else r.reason,
           "result": to_plain(values[0]), "trace": r.trace}
    if "after_arg" in tc.references or "unchanged_argument" in tc.references:
        out["arguments_after_call"] = to_plain(after)
    if tc.repeat:
        out["distinct_results"] = len(set(env.repeat_encodings or []))
    return out


def _classify(b: dict[str, Any] | None, c: dict[str, Any]) -> str:
    cv = c["verdict"]
    bv = b["verdict"] if b is not None else "UNAVAILABLE"
    if cv == "UNKNOWN":
        return "unknown"
    if cv == "FAIL":
        return {"PASS": "regression", "FAIL": "pre_existing"}.get(bv, "fail_undetermined")
    return {"FAIL": "resolved", "PASS": "pass_both"}.get(bv, "pass_candidate")


# -- runner calls -----------------------------------------------------------------------------------
def _batch(ctx: ExecContext, side: Side, tc: TypedContract, probes: list[tuple[str, dict[str, Any]]]) -> tuple[dict[str, dict[str, Any]], str | None, dict[str, Any] | None]:
    ws = ctx.workspace(side)
    t = tc.target
    seed = tc.repeat["seed"] if tc.repeat else ctx.profile.seed
    reps = tc.repeat["repeats"] if tc.repeat else 1
    req = ctx.backend.probe_request(ws, {**t.call_spec(),
                                         "probes": [{"id": pid, "args": {k: encode(v) for k, v in args.items()}, "repeats": reps, "seed": seed} for pid, args in probes]})
    run = ctx.backend.run(ws, req)
    if run.response is None:
        return {}, run.error, None
    err = validate_observations(run.response, [pid for pid, _ in probes])
    if err:
        return {}, err, run.response.get("identity")
    return {o["id"]: o for o in run.response["observations"]}, None, run.response.get("identity")


def _evaluate_batch(ctx: ExecContext, side: Side, tc: TypedContract | None, probes: list[tuple[str, dict[str, Any]]], why_unavailable: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any], str | None]:
    if tc is None or not side.available:
        return {pid: {"verdict": "UNKNOWN", "kind": "unavailable", "reason": why_unavailable} for pid, _ in probes}, {}, why_unavailable
    obs, err, ident = _batch(ctx, side, tc, probes)
    out = {}
    raw = {}
    for pid, args in probes:
        o = obs.get(pid)
        out[pid] = evaluate_observation(tc, args, o, err)
        if o is not None:
            raw[pid] = o
    return out, {"identity": ident, "observations": raw}, err


# -- the check ----------------------------------------------------------------------------------------
def _witness(args: dict[str, Any]) -> dict[str, Any]:
    return {k: to_plain(v) for k, v in args.items()}


def check_one(ctx: ExecContext, st: ContractState, base: Side, cand: Side, locks_differ: bool) -> dict[str, Any]:
    """Run the differential check for one contract; returns the report entry (+ private keys)."""
    doc = st.doc
    required = st.authority in REQUIRED_AUTHORITIES
    entry: dict[str, Any] = _entry_header(st, required)
    try:
        tc = validate_revision(doc, cand.files or {}, cand.snapshot_id or "", python_path=ctx.profile.python_path)
    except Rejection as exc:
        entry.update(status="INVALID_CONTRACT" if exc.code not in ("UNKNOWN_SYMBOL", "UNSUPPORTED_TARGET") else "TARGET_UNAVAILABLE",
                     reasons=[f"{exc.code}: {exc.message}"] + (["a deleted or renamed target needs an explicit `guard contracts revise` or `retire`"]
                                                               if exc.code == "UNKNOWN_SYMBOL" else []))
        return entry
    entry["semantic_digest"] = tc.semantic_digest
    entry["source_span"] = tc.target.def_span.to_json()
    if not ctx.profile.allows(tc.scope):
        entry.update(status="NOT_ALLOWED", reasons=[f"run profile {ctx.profile.name!r} does not allowlist target {tc.scope} (allowed_targets)"])
        return entry
    denied = [n for n in tc.input_profile.names if n not in ctx.profile.input_profiles]
    if denied:
        entry.update(status="NOT_ALLOWED", reasons=[f"run profile {ctx.profile.name!r} does not allow input profile {', '.join(denied)}"])
        return entry
    base_tc: TypedContract | None = None
    base_why = base.note or "baseline snapshot is not available for replay"
    if base.available:
        try:
            base_tc = validate_revision(doc, base.files or {}, base.snapshot_id or "", python_path=ctx.profile.python_path)
            if base_tc.target.params != tc.target.params:
                base_why = f"the target's parameters changed ({list(base_tc.target.params)} -> {list(tc.target.params)}); the same witness cannot be replayed"
                base_tc = None
        except Rejection as exc:
            base_why = f"the contract does not apply to the base snapshot: {exc.code}: {exc.message}"
    entry["base_replay"] = "available" if base_tc is not None else base_why
    seed = int(tc.semantic_digest.split(":", 1)[1][:12], 16) ^ (ctx.profile.seed & 0xFFFFFFFFFFFF)
    params = list(tc.target.params)
    adm = admit_inputs(tc.input_profile, params, tc.precondition_holds, domain_hints(tc.precondition, params), seed=seed,
                       limit=ctx.profile.max_probes, examples=tc.examples)
    probes = [(f"p{i:04d}", args) for i, (args, _) in enumerate(adm.inputs)]
    origin = {f"p{i:04d}": o for i, (_, o) in enumerate(adm.inputs)}
    entry["probes"] = {"attempted_inputs": adm.attempts, "admitted": len(adm.inputs), "excluded_by_domain": len(adm.excluded), "seed": seed}
    entry["excluded_examples"] = [{"input": _witness(a), "origin": o, "why": w} for a, o, w in adm.excluded if o in ("example", "boundary")][:6]
    runs_before = ctx.backend.runs
    with ThreadPoolExecutor(max_workers=2) as pool:
        fc = pool.submit(_evaluate_batch, ctx, cand, tc, probes, "")
        fb = pool.submit(_evaluate_batch, ctx, base, base_tc, probes, base_why)
        c_eval, c_raw, c_err = fc.result()
        b_eval, b_raw, b_err = fb.result()
    classes = {pid: _classify(b_eval.get(pid), c_eval[pid]) for pid, _ in probes}
    counts = {k: sum(1 for v in classes.values() if v == k) for k in ("regression", "pre_existing", "resolved", "pass_both", "pass_candidate", "fail_undetermined", "unknown")}
    cv = [c_eval[p]["verdict"] for p, _ in probes]
    bv = [b_eval[p]["verdict"] for p, _ in probes]
    entry["probes"].update({"candidate_pass": cv.count("PASS"), "candidate_fail": cv.count("FAIL"), "candidate_unknown": cv.count("UNKNOWN"),
                            "base_pass": bv.count("PASS"), "base_fail": bv.count("FAIL"), "base_unknown": bv.count("UNKNOWN"), **counts})
    reasons: list[str] = []
    if c_err:
        reasons.append(f"candidate runner error: {c_err}")
    if b_err and base_tc is not None:
        reasons.append(f"base runner error: {b_err}")
    target_class = next((k for k in ("regression", "pre_existing", "fail_undetermined") if counts[k]), None)
    evidence_c = {"inputs": probes, "evals": c_eval, "raw": c_raw, "side": cand, "tc": tc}
    evidence_b = {"inputs": probes, "evals": b_eval, "raw": b_raw, "side": base, "tc": base_tc}
    entry["_evidence_parts"] = (evidence_b, evidence_c)
    if target_class is None:
        executed = cv.count("PASS")
        if executed == 0:
            entry.update(status="CHECKED", property_result="UNKNOWN", scope_validity=None,
                         reasons=reasons + [f"no candidate probe produced a decidable observation ({cv.count('UNKNOWN')} unknown of {len(probes)})"]
                         + sorted({c_eval[p]['reason'] for p, _ in probes})[:3])
        else:
            entry.update(status="CHECKED", property_result="TESTED", scope_validity="EXECUTED",
                         quantifier=f"for the {executed} executed admitted input(s) only (finite probes; not a proof)",
                         reasons=reasons + [f"{executed} admitted input(s) satisfied the contract in the candidate"
                                            + (f"; {cv.count('UNKNOWN')} undecided" if cv.count("UNKNOWN") else "")])
            if counts["resolved"]:
                first = next(p for p, _ in probes if classes[p] == "resolved")
                entry["resolved"] = {"count": counts["resolved"], "witness": _witness(dict(probes)[first]), "base": b_eval[first]}
        entry["_runs"] = ctx.backend.runs - runs_before
        return entry
    # A counterexample exists: shrink within the domain, then confirm in fresh runs.
    first = next(p for p, _ in probes if classes[p] == target_class)
    orig_args = dict(probes)[first]
    cur_args = orig_args
    rounds = 0
    seen = {_canon(encode(orig_args))}
    if target_class in ("regression", "pre_existing"):
        while rounds < ctx.profile.max_shrink_rounds:
            cands = []
            for p in params:
                for v in shrink_candidates(tc.input_profile.of(p), cur_args[p]):
                    a = dict(cur_args)
                    a[p] = v
                    key = _canon(encode(a))
                    if key in seen or any(tc.input_profile.check(q, a[q]) for q in params):
                        continue
                    seen.add(key)
                    if tc.precondition_holds(a)[0] is True:  # never leave the contract domain
                        cands.append(a)
            if not cands:
                break
            cands.sort(key=lambda a: [complexity(a[p]) for p in params])
            cands = cands[:SHRINK_BATCH]
            rounds += 1
            sp = [(f"s{rounds:02d}-{i:02d}", a) for i, a in enumerate(cands)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                fc = pool.submit(_evaluate_batch, ctx, cand, tc, sp, "")
                fb = pool.submit(_evaluate_batch, ctx, base, base_tc, sp, base_why)
                sc, _, _ = fc.result()
                sb, _, _ = fb.result()
            nxt = next((a for pid, a in sp if _classify(sb.get(pid), sc[pid]) == target_class), None)
            if nxt is None:
                break
            cur_args = nxt
    entry["probes"]["shrink_rounds"] = rounds
    conf_probes = [("c-original", orig_args)] + ([("c-minimized", cur_args)] if cur_args is not orig_args else [])
    with ThreadPoolExecutor(max_workers=3) as pool:
        f1 = pool.submit(_evaluate_batch, ctx, base, base_tc, conf_probes, base_why)
        f2 = pool.submit(_evaluate_batch, ctx, cand, tc, conf_probes, "")
        f3 = pool.submit(_evaluate_batch, ctx, cand, tc, conf_probes, "")
        cb, cb_raw, _ = f1.result()
        cc1, cc1_raw, _ = f2.result()
        cc2, cc2_raw, _ = f3.result()
    wid = conf_probes[-1][0]
    confirmations = [{"probe": pid, "witness": _witness(a), "base": cb[pid]["verdict"], "candidate_run_1": cc1[pid]["verdict"],
                      "candidate_run_2": cc2[pid]["verdict"]} for pid, a in conf_probes]
    entry["confirmations"] = confirmations
    cand_fail_twice = cc1[wid]["verdict"] == "FAIL" and cc2[wid]["verdict"] == "FAIL"
    base_v = cb[wid]["verdict"]
    entry["witness"] = _witness(cur_args)
    entry["original_witness"] = _witness(orig_args)
    entry["observations"] = {"base": cb[wid], "candidate": cc1[wid], "candidate_repeat": {"verdict": cc2[wid]["verdict"], "reason": cc2[wid]["reason"]}}
    entry["_confirm"] = (cb_raw, cc1_raw, cc2_raw, conf_probes)
    entry["status"] = "CHECKED"
    entry["scope_validity"] = "EXECUTED"
    entry["quantifier"] = QUANTIFIER_FOUND
    if not cand_fail_twice:
        entry.update(property_result="OBSERVED_FAILURE", patch_relation="UNDETERMINED",
                     reasons=reasons + [f"the candidate failure did not reproduce in both fresh runs ({cc1[wid]['verdict']}, {cc2[wid]['verdict']}): "
                                        "observed instability or a runner problem, not a confirmed regression"])
    elif target_class == "regression" and base_v == "PASS":
        if locks_differ:
            entry.update(property_result="OBSERVED_FAILURE", patch_relation="UNDETERMINED",
                         reasons=reasons + ["base passes and candidate fails on the same witness, but the dependency lock files differ between the "
                                            "snapshots and both ran in the same prepared environment; the difference may be an intended dependency change"])
        else:
            entry.update(property_result="OBSERVED_REGRESSION", patch_relation="INTRODUCED",
                         reasons=reasons + [f"same witness: base satisfies the contract, candidate violates it ({cc1[wid]['reason']}); "
                                            "confirmed in fresh runs (base once, candidate twice)"])
    elif target_class == "pre_existing" and base_v == "FAIL":
        entry.update(property_result="OBSERVED_FAILURE", patch_relation="PRE_EXISTING",
                     reasons=reasons + ["the base already violates the contract on the same witness: pre-existing defect or invalid specification "
                                        "(not an introduced regression)"])
    elif base_v == "UNKNOWN" or base_tc is None:
        entry.update(property_result="OBSERVED_FAILURE", patch_relation="UNDETERMINED",
                     reasons=reasons + [f"the candidate violates the contract, but the baseline could not be evaluated: {cb[wid]['reason']}"])
    else:
        entry.update(property_result="OBSERVED_FAILURE", patch_relation="UNDETERMINED",
                     reasons=reasons + [f"base verdict changed between the batch and the confirmation run ({base_v}); comparison undetermined"])
    entry["_runs"] = ctx.backend.runs - runs_before
    return entry


def _entry_header(st: ContractState, required: bool) -> dict[str, Any]:
    from .registry import proposer_of

    doc = st.doc if st.ok else {}
    out = {"contract_id": st.contract_id, "revision": st.current_revision, "semantic_digest": st.semantic.get(st.current_revision),
           "scope": doc.get("scope") or "unknown.py::unknown", "authority": st.authority, "input_profile": doc.get("input_profile"),
           "required": required, "status": "NOT_RUN", "property_result": "UNKNOWN", "scope_validity": None, "patch_relation": None,
           "severity": None, "finding_id": None, "quantifier": None, "reasons": [], "evidence_ids": []}
    prop = proposer_of(doc) if doc else None
    if prop is not None:  # remote-model or agent proposer disclosure (DECISIONS I-105/I-106)
        out["proposer"] = prop
    return out


# -- evidence -----------------------------------------------------------------------------------------------
def _side_evidence(ctx: ExecContext, entry: dict[str, Any], part: dict[str, Any], side_label: str, extra_confirm: dict[str, Any] | None) -> dict[str, Any] | None:
    side: Side = part["side"]
    tc: TypedContract | None = part["tc"]
    if side.snapshot_id is None:
        return None
    evals = part["evals"]
    verdicts = [evals[p]["verdict"] for p, _ in part["inputs"]]
    if side_label == "candidate":
        label = entry["property_result"]
    else:
        label = "OBSERVED_FAILURE" if "FAIL" in verdicts else ("TESTED" if "PASS" in verdicts else "UNKNOWN")
    store_inputs = ctx.store_witnesses and ctx.profile.witness_storage
    probes = []
    for pid, args in part["inputs"]:
        e = evals[pid]
        probes.append({"probe": pid, "input": {k: encode(v) for k, v in args.items()} if store_inputs else {"redacted": short_hex(encode(args))},
                       "verdict": e["verdict"], "reason": e["reason"][:300], **({"result": e["result"]} if "result" in e and store_inputs else {})})
    ident = (part["raw"] or {}).get("identity")
    span = tc.target.def_span.to_json() if tc is not None else None
    body = {
        "schema_version": "1.0",
        "kind": "EXECUTION",
        "engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION, "ir_version": "n/a (execution)"},
        "snapshot_id": side.snapshot_id,
        "symbol_id": entry["scope"],
        "property_id": PROPERTY_ID,
        "obligation_id": obligation_id(entry["scope"], entry["contract_id"]),
        "result_label": label,
        "domain": tc.domain_text if tc else "contract domain (target unavailable in this snapshot)",
        "assumptions": _assumptions(ctx),
        "precision": "EXECUTED" if label != "UNKNOWN" else None,
        "method": "PROBE",
        "source_span": span,
        "details": [f"{side_label} side of contract {entry['contract_id']} r{entry['revision']}"] + entry.get("reasons", [])[:3],
        "solver_runs": [],
        "witness": entry.get("witness") if side_label == "candidate" and store_inputs and label in ("OBSERVED_REGRESSION", "OBSERVED_FAILURE") else None,
        "witness_validation": "VALIDATED" if entry.get("confirmations") else "NOT_APPLICABLE",
        "contract": {"contract_id": entry["contract_id"], "revision": entry["revision"], "semantic_digest": entry["semantic_digest"],
                     "authority": entry["authority"], **({"proposer": entry["proposer"]} if entry.get("proposer") else {})},
        "execution": {
            "side": side_label,
            "backend": ctx.status.backend,
            "isolated": ctx.status.isolated,
            "capabilities": [c.to_json() | {"detail": None} for c in ctx.status.capabilities],
            "environment_digest": ctx.status.environment_digest,
            "environment": {k: v for k, v in ctx.status.environment.items() if k in ("image", "image_id", "repo_digests", "docker_server", "architecture", "harness_digest")},
            "interpreter": ident,
            "run_profile_digest": ctx.run_profile_digest,
            "dependency_locks": side.locks,
            "probes": probes,
            "confirmations": extra_confirm,
            "witness_storage": "stored" if store_inputs else "redacted (inputs replaced by digests; not replayable)",
        },
    }
    if span is None:
        return None
    ev_id = "ev-" + short_hex(body)
    return {**body, "evidence_id": ev_id, "run_id": ctx.run_id, "created_at": ctx.created_at}


def _assumptions(ctx: ExecContext) -> list[str]:
    st = ctx.status
    if st.isolated:
        iso = "executed in a fresh container per batch (" + ", ".join(f"{c.name}={c.status}" for c in st.capabilities) + ")"
    else:
        iso = "executed UNISOLATED on the host (explicit allow_unisolated profile); isolation capabilities NOT_ENFORCED"
    return [iso, "observations are finite executions of the admitted inputs only; they do not prove the contract for other inputs",
            "base and candidate ran the same inputs in the same prepared environment"]


# -- entry point ------------------------------------------------------------------------------------------------
def run_contract_checks(*, view: RegistryView, registry: ContractRegistry | None, base: Side, cand: Side, execute: bool,
                        ctx: ExecContext | None, unavailable_reason: str | None, execution_section: dict[str, Any],
                        require_coverage: bool = True) -> ContractsOutcome:
    """Check every active contract. Evidence is returned, not stored: the caller records it
    (``record_contract_evidence``) only after the run's snapshot passed its consistency check."""
    t0 = time.perf_counter()
    active = view.active()
    broken = [c for c in view.contracts.values() if not c.ok]
    results: list[dict[str, Any]] = []
    out = ContractsOutcome(section={})
    locks_differ = [(d["path"], d["sha256"]) for d in base.locks] != [(d["path"], d["sha256"]) for d in cand.locks] if base.available else False
    for st in sorted(broken, key=lambda c: c.contract_id):
        e = _entry_header(st, st.authority in REQUIRED_AUTHORITIES)
        e.update(status="INTEGRITY_ERROR", reasons=st.integrity_errors)
        results.append(e)
    try:
        _check_all(active, execute, ctx, unavailable_reason, base, cand, locks_differ, results, out)
    finally:
        if ctx is not None:
            ctx.close()  # temporary workspaces are removed even on cancellation or engine faults
    if not require_coverage:  # analysis.require_coverage = false: no contract check is required coverage
        for e in results:
            e["required"] = False
    return _finish(results, out, ctx, registry, cand, active, execution_section, view, execute, unavailable_reason, t0)


def record_contract_evidence(registry: ContractRegistry, evidence: list[dict[str, Any]]) -> int:
    for ev in evidence:
        registry.record_evidence(ev, ev["execution"]["side"])
    return len(evidence)


def _check_all(active, execute, ctx, unavailable_reason, base, cand, locks_differ, results, out) -> None:
    for st in active:
        if not st.ok:
            continue
        if not execute:
            e = _entry_header(st, False)
            e.update(status="NOT_RUN", property_result="UNVERIFIED", reasons=["dynamic contract checks were not requested (use --execute --run-profile NAME)"])
            results.append(e)
            continue
        if ctx is None:
            e = _entry_header(st, st.authority in REQUIRED_AUTHORITIES)
            e.update(status="RUNNER_UNAVAILABLE", reasons=[f"RUNNER_UNAVAILABLE: {unavailable_reason}", "no unisolated fallback is attempted"])
            results.append(e)
            continue
        try:
            e = check_one(ctx, st, base, cand, locks_differ)
        except Exception as exc:  # noqa: BLE001 - a checker fault is an engine error for this contract
            e = _entry_header(st, st.authority in REQUIRED_AUTHORITIES)
            e.update(status="ENGINE_ERROR", reasons=[f"engine error while checking: {type(exc).__name__}: {exc}"])
            out.errors.append({"symbol_id": e["scope"], "stage": "contract_check", "error_type": type(exc).__name__, "message": str(exc)[:500]})
        results.append(e)


def _finish(results, out, ctx, registry, cand, active, execution_section, view, execute, unavailable_reason, t0) -> ContractsOutcome:
    # Evidence, findings and policy inputs.
    req_total = req_resolved = 0
    for e in results:
        parts = e.pop("_evidence_parts", None)
        confirm = e.pop("_confirm", None)
        runs = e.pop("_runs", None)
        if runs is not None:
            e.setdefault("probes", {})["runner_invocations"] = runs
        if parts is not None and ctx is not None:
            for part, label in zip(parts, ("base", "candidate")):
                conf = None
                if confirm is not None:
                    cb_raw, cc1_raw, cc2_raw, conf_probes = confirm
                    conf = {"probes": [{"probe": pid, "input": {k: encode(v) for k, v in a.items()}} for pid, a in conf_probes]
                            if ctx.store_witnesses and ctx.profile.witness_storage else "redacted",
                            "runs": e.get("confirmations")}
                ev = _side_evidence(ctx, e, part, label, conf)
                if ev is None:
                    continue
                out.evidence.append(ev)
                e["evidence_ids"].append(ev["evidence_id"])
        sev = severity_for_contract(e)
        e["severity"] = sev
        if e["required"]:
            req_total += 1
            if e["property_result"] != "UNKNOWN":
                req_resolved += 1
        if sev is not None:
            out.severities.append(sev)
            f = _finding(e, cand)
            if f is not None:
                e["finding_id"] = f["finding_id"]
                out.findings.append(f)
    unknown_required = [e for e in results if e["required"] and e["property_result"] == "UNKNOWN"]
    undetermined_required = [e for e in results if e["required"] and e.get("patch_relation") == "UNDETERMINED"]
    if unknown_required:
        statuses = sorted({e["status"] for e in unknown_required})
        out.incomplete_reasons.append(f"{len(unknown_required)} required contract check(s) did not produce evidence ({', '.join(statuses)})")
    if undetermined_required:
        out.incomplete_reasons.append(f"{len(undetermined_required)} required contract violation(s) could not be attributed to the patch (comparison UNDETERMINED)")
    advisory_unknown = [e for e in results if not e["required"] and e["property_result"] == "UNKNOWN"]
    if advisory_unknown:
        out.warn_reasons.append(f"{len(advisory_unknown)} INFERRED contract check(s) undecided (advisory)")
    if execute and ctx is None and active:
        out.warn_reasons.append(f"capability unavailable: execution: RUNNER_UNAVAILABLE ({unavailable_reason}); no unisolated fallback")
    not_run = [e for e in results if e["status"] == "NOT_RUN"]
    if not_run:
        out.extra_reasons.append(f"{len(not_run)} contract(s) not checked: dynamic execution was not requested")
    tested = [e for e in results if e["property_result"] == "TESTED"]
    if tested:
        out.extra_reasons.append(f"{len(tested)} contract(s) TESTED on finite executed probes (not proofs)")
    out.coverage = {"contracts_active": len(active), "required_contract_checks": req_total, "resolved_required_contract_checks": req_resolved,
                    "contracts_not_run": len(not_run)}
    out.section = {"execution": execution_section, "registry": {"indexed_contracts": len(view.contracts), "active": len(active),
                                                                "diagnostics": view.diagnostics},
                   "policy": "regression of an APPROVED/DECLARED contract blocks; a change to an INFERRED contract warns; pre-existing violations warn; "
                             "required checks without evidence make the run INCOMPLETE",
                   "results": results}
    if execute:
        out.dynamic_ms = round((time.perf_counter() - t0) * 1000, 3)
        out.execution_label = ("isolated" if ctx.status.isolated else "unisolated") if ctx is not None else "unavailable"
    return out


def severity_for_contract(e: dict[str, Any]) -> str | None:
    """docs/03 rows for contracts: approved/declared replayed regression BLOCKs; inferred change WARNs."""
    r, rel, auth = e["property_result"], e.get("patch_relation"), e["authority"]
    if r == "OBSERVED_REGRESSION":
        return "ERROR" if auth in REQUIRED_AUTHORITIES and rel == "INTRODUCED" else "WARNING"
    if r == "OBSERVED_FAILURE":
        return "WARNING"
    if r == "TESTED" and e.get("resolved"):
        return "INFO"
    return None


def _finding(e: dict[str, Any], cand: Side) -> dict[str, Any] | None:
    if not e["evidence_ids"]:
        return None
    oid = obligation_id(e["scope"], e["contract_id"])
    resolved = e["property_result"] == "TESTED" and e.get("resolved")
    relation = "RESOLVED" if resolved else (e.get("patch_relation") or "UNDETERMINED")
    witness = e.get("witness") if not resolved else e["resolved"]["witness"]
    obs = e.get("observations") or {}
    if resolved:
        msg = (f"contract {e['contract_id']} r{e['revision']} ({e['authority']}): the base violated it on admitted input {witness}; the candidate satisfies it "
               f"on all {e['probes'].get('candidate_pass', 0)} executed probes (RESOLVED)")
    elif e["property_result"] == "OBSERVED_REGRESSION":
        msg = (f"contract {e['contract_id']} r{e['revision']} ({e['authority']}) regressed on admitted input {witness}: base: {obs['base']['reason']}"
               f"{_res(obs['base'])}; candidate: {obs['candidate']['reason']}{_res(obs['candidate'])}")
    else:
        msg = f"contract {e['contract_id']} r{e['revision']} ({e['authority']}) violated on admitted input {witness}: {obs.get('candidate', {}).get('reason', '')}"
        if relation == "PRE_EXISTING":
            msg += " (the base violates it on the same input: pre-existing or invalid specification)"
    span = e.get("source_span")
    if span is None:
        return None
    return {
        "finding_id": "fnd-" + short_hex([cand.snapshot_id, oid, e["semantic_digest"], e["property_result"], witness, relation]),
        "obligation_id": oid,
        "symbol_id": e["scope"],
        "property_id": PROPERTY_ID,
        "property_result": e["property_result"],
        "scope_validity": e["scope_validity"],
        "authority": e["authority"],
        "patch_relation": relation,
        "severity": e["severity"],
        "source_span": span,
        "message": msg,
        "quantifier": e.get("quantifier") or QUANTIFIER_FOUND,
        "domain": f"contract precondition over input profile {e['input_profile']}",
        "domain_source": "CONTRACT_PRECONDITION",
        "assumptions": ["contract authority " + e["authority"] + ("" if e["authority"] in REQUIRED_AUTHORITIES else " (advisory: intent not approved)")],
        "path_constraints": [],
        "reasons": e["reasons"] or ["see contract result"],
        "witness": witness,
        "witness_validated": bool(e.get("confirmations")) and not resolved,
        "runtime_mode": None,
        "evidence_ids": list(e["evidence_ids"]),
        "replay_available": witness is not None,
        "contract": {"contract_id": e["contract_id"], "revision": e["revision"], "semantic_digest": e["semantic_digest"]},
        "comparison": {"base_obligation_id": oid, "base_result": None if resolved else _base_label(obs), "match": "contract_scope",
                       "witness_in_base": _in_base(obs, resolved), "reason": e["reasons"][-1] if e["reasons"] else "contract comparison"},
    }


def _res(o: dict[str, Any]) -> str:
    return f" (returned {o['result']})" if "result" in o else ""


def _base_label(obs: dict[str, Any]) -> str | None:
    v = (obs.get("base") or {}).get("verdict")
    return {"PASS": "TESTED", "FAIL": "OBSERVED_FAILURE"}.get(v)


def _in_base(obs: dict[str, Any], resolved: bool) -> str:
    if resolved:
        return "FAILED_SAME_SITE"
    v = (obs.get("base") or {}).get("verdict")
    return {"PASS": "PASSED", "FAIL": "FAILED_SAME_SITE"}.get(v, "NOT_EVALUATED")


__all__ = ["run_contract_checks", "record_contract_evidence", "check_one", "ExecContext", "Side", "ContractsOutcome", "evaluate_observation", "severity_for_contract",
           "obligation_id", "PROPERTY_ID"]
