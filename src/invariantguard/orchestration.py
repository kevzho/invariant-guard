"""Static analysis orchestration shared by ``guard check`` and ``guard verify``.

Pipeline: resolve root/config -> capture immutable snapshot -> parse + lower (no imports)
-> fix the AnalysisPlan (required obligations) -> interval/path analysis + optional SMT
-> verify the snapshot did not change (restart once, else INCOMPLETE) -> policy ->
schema-valid report.

``guard check`` is single-snapshot and stateless (it writes nothing). ``guard verify``
(``verify.py``) reuses ``lower_snapshot``/``analyze_lowered``/``build_report`` for a base
and a candidate snapshot.
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import ENGINE_NAME, ENGINE_VERSION, REPORT_SCHEMA_VERSION, cancellation
from .analysis.obligations import (
    PROPERTY_ID,
    Budget,
    ObligationOutcome,
    check_function,
    failure_name,
    site_obligation_id,
    unsupported_outcome,
)
from .config import Config, ConfigError, find_config, load_config
from .frontends.python import PYTHON_GRAMMAR, ParseFailure, ProgramIndex, domain_decls, lower_module, parse_source
from .ir.model import IR_SCHEMA_VERSION, DIVISION_PROPERTY, INDEX_PROPERTY, NONE_PROPERTY, Function, ModuleIR, ObligationSite, check_ir_version
from .models.enums import AnalysisMethod, Authority, DomainSource, PatchRelation, PropertyResult, Severity, SolverStatus
from .models.spans import short_hex
from .policy.verdict import POLICY_ID, PolicyInput, evaluate, severity_for
from .snapshots.capture import Snapshot, capture, verify_unchanged
from .solver.z3_backend import SolverCapability, Z3Backend, probe_z3

SEMANTICS_PROFILE = f"python{PYTHON_GRAMMAR}-cpython-builtin-v4"
MAX_ATTEMPTS = 2  # capture once, restart once on a mid-run edit, then report INCOMPLETE


class UsageError(Exception):
    """Invalid CLI usage or configuration (exit 4)."""


@dataclass
class CheckOptions:
    path: Path
    root: Path | None = None
    config_path: Path | None = None
    no_solver: bool = False
    timeout_ms: int | None = None
    no_config_search: bool = False  # MCP: use only <root>/.guard/config.toml (never a parent directory's)


@dataclass
class CheckResult:
    report: dict[str, Any]
    exit_code: int
    log: list[str] = field(default_factory=list)
    report_digest: str | None = None  # set when the report was indexed in the state directory (verify)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


_now = now_utc


def resolve_inputs(opts: CheckOptions) -> tuple[Path, Config]:
    path = opts.path
    if not path.exists():
        raise UsageError(f"path does not exist: {path}")
    if opts.config_path is not None:
        if not opts.config_path.is_file():
            raise UsageError(f"config file does not exist: {opts.config_path}")
        cfg_path: Path | None = opts.config_path.resolve()
    elif opts.no_config_search:
        cfg_path = None
    else:
        cfg_path = find_config(path)
    try:
        cfg = load_config(cfg_path) if cfg_path is not None else Config()
    except ConfigError as exc:
        raise UsageError(f"invalid configuration: {exc}") from None
    if opts.root is not None:
        root = opts.root.resolve()
    elif cfg_path is not None:
        root = cfg_path.parent.parent
    else:
        root = path.resolve() if path.is_dir() else path.resolve().parent
    try:
        path.resolve().relative_to(root)
    except ValueError:
        raise UsageError(f"path {path} is outside the repository root {root}") from None
    if opts.timeout_ms is not None:
        if opts.timeout_ms <= 0:
            raise UsageError("--timeout-ms must be positive")
        cfg.analysis.per_query_timeout_ms = opts.timeout_ms
    return root, cfg


# -- solver ---------------------------------------------------------------------------------
@dataclass
class SolverSetup:
    state: str  # configured | unavailable | disabled
    status: SolverStatus
    detail: str
    warnings: list[str]
    identity: str  # part of summary validity keys

    def backend(self, cfg: Config) -> Z3Backend | None:
        if self.state != "configured":
            return None
        return Z3Backend(cfg.analysis.per_query_timeout_ms, rlimit=cfg.analysis.per_query_rlimit)


def setup_solver(cfg: Config, no_solver: bool) -> SolverSetup:
    if no_solver or not cfg.analysis.solver_enabled:
        detail = "disabled by " + ("--no-solver" if no_solver else "analysis.solver_enabled = false")
        return SolverSetup("disabled", SolverStatus.DISABLED, detail, [], "disabled")
    cap: SolverCapability = probe_z3()
    if cap.available:
        return SolverSetup("configured", SolverStatus.SAT, f"z3 {cap.version}", [], f"z3 {cap.version}")
    detail = f"z3 unavailable ({cap.reason})"
    return SolverSetup("unavailable", SolverStatus.UNAVAILABLE, detail, [f"solver: {detail}"], "unavailable")


# -- lowering -------------------------------------------------------------------------------
@dataclass
class Lowered:
    snap: Snapshot
    modules: list[ModuleIR]
    program: ProgramIndex
    skipped: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    errored_sites: list[tuple[str, ObligationSite, str]]
    domains: dict[str, Any]
    t_ms: float = 0.0
    scoped: bool = False  # only some files were lowered (guard verify, I-143); the rest are unaffected

    def functions(self) -> dict[str, Function]:
        return {fn.symbol_id: fn for m in self.modules for fn in m.functions}

    def unsupported(self) -> dict[str, Any]:
        return {u.symbol_id: u for m in self.modules for u in m.unsupported_symbols}

    def requested_symbols(self) -> list[str]:
        return sorted({s for m in self.modules for s in m.requested_symbols})

    def all_sites(self) -> list[tuple[str, ObligationSite]]:
        out: list[tuple[str, ObligationSite]] = []
        for m in self.modules:
            for fn in m.functions:
                out.extend((fn.symbol_id, s) for s in fn.sites)
            for u in m.unsupported_symbols:
                out.extend((u.symbol_id, s) for s in u.division_sites)
        out.extend((sym, s) for sym, s, _ in self.errored_sites)
        return out


def lower_snapshot(snap: Snapshot, cfg: Config, only: set[str] | None = None) -> Lowered:
    """Lower every captured file, or only the paths in ``only`` (the others stay available for resolution)."""
    t0 = time.perf_counter()
    require = cfg.analysis.require_coverage
    domains = cfg.domain_map()
    skipped: list[dict[str, Any]] = [
        {"kind": "excluded_path", "path": u["path"], "symbol_id": None, "reason": u["reason"], "required": require}
        for u in snap.uncaptured
    ]
    errors: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    errored_sites: list[tuple[str, ObligationSite, str]] = []
    raw = {f.path: f.raw for f in snap.files}
    # Modules are parsed on demand into a bounded cache (cross-module resolution may need a
    # module before its own turn); each file's bytes come from the immutable snapshot.
    max_nodes = cfg.analysis.max_file_nodes
    program = ProgramIndex(set(raw), lambda path: parse_source(path, raw[path], max_nodes))
    modules: list[ModuleIR] = []
    for f in snap.files:
        if only is not None and f.path not in only:
            continue
        ctx = program.context(f.path)
        if ctx is None:
            exc = program.failures[f.path]
            where = f" (line {exc.line})" if exc.line else ""
            kind = "resource_limit" if exc.kind == "resource_limit" else "parse_failure"
            skipped.append({"kind": kind, "path": f.path, "symbol_id": None, "reason": f"{exc.kind}: {exc.message}{where}", "required": require})
            continue
        fr = lower_module(ctx.parsed, snap.snapshot_id, domains, program)
        check_ir_version(fr.module)
        fr.module.semantics_profile = SEMANTICS_PROFILE
        modules.append(fr.module)
        diagnostics.extend({"kind": d["kind"], "symbol_id": d.get("symbol_id"), "message": d["message"]} for d in fr.diagnostics)
        for e in fr.function_errors:
            sites = e.pop("_sites", [])
            errors.append(e)
            for s in sites:
                errored_sites.append((e["symbol_id"], s, f"engine error during {e['stage']}: {e['error_type']}: {e['message']}"))
    captured_paths = {f.path for f in snap.files if only is None or f.path in only}
    known_symbols = {fn.symbol_id for m in modules for fn in m.functions} | {u.symbol_id for m in modules for u in m.unsupported_symbols}
    for sym in sorted(domains):
        if sym.split("::", 1)[0] in captured_paths and sym not in known_symbols:
            diagnostics.append({"kind": "domain_unmatched", "symbol_id": sym, "message": "declared domain names a symbol that was not found in the captured source"})
    return Lowered(snap, modules, program, skipped, errors, diagnostics, errored_sites, domains, (time.perf_counter() - t0) * 1000, scoped=only is not None)


def domain_reason(domains: dict[str, Any], symbol_id: str) -> str | None:
    decls = domain_decls(domains.get(symbol_id))
    if not decls:
        return None
    return "; ".join(d.reason for d in decls)


# -- analysis -------------------------------------------------------------------------------
@dataclass
class Analysis:
    outcomes: list[ObligationOutcome]
    engine_error_ids: set[str]
    analyzed_symbols: int
    errors: list[dict[str, Any]]
    analyzed: set[str] = field(default_factory=set)  # analyzed in this run
    reused: set[str] = field(default_factory=set)  # taken from a valid cache entry
    t_ms: float = 0.0


class UnitCache:
    """Interface used by ``analyze_lowered`` (implemented by ``storage.cache``)."""

    def get(self, symbol_id: str, fn: Function) -> list[ObligationOutcome] | None:  # pragma: no cover - interface
        return None

    def stage(self, symbol_id: str, fn: Function, outcomes: list[ObligationOutcome]) -> None:  # pragma: no cover - interface
        return None


def analyze_lowered(
    lowered: Lowered,
    cfg: Config,
    backend: Any,
    solver_status: SolverStatus,
    budget: Budget,
    *,
    select: set[str] | None = None,
    cache: UnitCache | None = None,
) -> Analysis:
    t0 = time.perf_counter()
    outcomes: list[ObligationOutcome] = []
    engine_error_ids: set[str] = set()
    errors: list[dict[str, Any]] = []
    analyzed = 0
    fresh: set[str] = set()
    reused: set[str] = set()
    errored_sites = list(lowered.errored_sites)
    for m in lowered.modules:
        for u in m.unsupported_symbols:
            if select is not None and u.symbol_id not in select:
                continue
            outcomes.extend(unsupported_outcome(u.symbol_id, s, u.reason) for s in u.division_sites)
        for fn in m.functions:
            if select is not None and fn.symbol_id not in select:
                continue
            cancellation.check()  # per-symbol safe point (T52); unwinds past per-symbol error isolation
            cached = cache.get(fn.symbol_id, fn) if cache is not None else None
            if cached is not None:
                outcomes.extend(cached)
                reused.add(fn.symbol_id)
                analyzed += 1
                continue
            try:
                res = check_function(fn, backend, solver_status, budget, domain_reason(lowered.domains, fn.symbol_id))
            except Exception as exc:  # noqa: BLE001 - engine failure, reported as ERROR
                errors.append({"symbol_id": fn.symbol_id, "stage": "analysis", "error_type": type(exc).__name__, "message": str(exc)})
                for s in fn.sites:
                    errored_sites.append((fn.symbol_id, s, f"engine error during analysis: {type(exc).__name__}: {exc}"))
                continue
            analyzed += 1
            fresh.add(fn.symbol_id)
            outcomes.extend(res)
            if cache is not None:
                cache.stage(fn.symbol_id, fn, res)
    for sym, site, reason in errored_sites:
        if select is not None and sym not in select:
            continue
        o = unsupported_outcome(sym, site, reason)
        o.reasons = [reason]
        o.domain = "not established (engine error)"
        engine_error_ids.add(site_obligation_id(sym, site))
        outcomes.append(o)
    return Analysis(outcomes, engine_error_ids, analyzed, errors, fresh, reused, (time.perf_counter() - t0) * 1000)


def make_plan(*, command: str, mode: str, base_snapshot: str | None, snap: Snapshot, requested: list[str], required_ids: list[str],
              cfg: Config, solver_state: str, budget_ms: int, invalidated: list[str] | None = None, unresolved: list[str] | None = None,
              properties: list[str] | None = None) -> dict[str, Any]:
    budgets = {
        "per_query_timeout_ms": cfg.analysis.per_query_timeout_ms,
        "quick_budget_ms": budget_ms,
        "per_query_rlimit": cfg.analysis.per_query_rlimit,
    }
    return {
        "schema_version": "1.0",
        "plan_id": "plan-" + short_hex([command, mode, base_snapshot, snap.snapshot_id, required_ids, budgets, solver_state]),
        "command": command,
        "mode": mode,
        "base_snapshot": base_snapshot,
        "candidate_snapshot": snap.snapshot_id,
        "requested_symbols": requested,
        "required_properties": properties or [DIVISION_PROPERTY, INDEX_PROPERTY, NONE_PROPERTY],
        "required_obligations": required_ids if cfg.analysis.require_coverage else [],
        "require_coverage": cfg.analysis.require_coverage,
        "budgets": budgets,
        "solver": {"enabled": solver_state != "disabled", "available": solver_state == "configured"},
        "invalidated_summaries": sorted(invalidated or []),
        "unresolved_changes": list(unresolved or []),
        "created_before_analysis": True,
    }


# -- guard check ------------------------------------------------------------------------------
def run_check(opts: CheckOptions) -> CheckResult:
    root, cfg = resolve_inputs(opts)
    log: list[str] = []
    restart_note: dict[str, Any] | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        report, code, changed = _check_once(opts, root, cfg, log, final=attempt == MAX_ATTEMPTS, restart_note=restart_note)
        if changed and attempt < MAX_ATTEMPTS:
            log.append(f"source changed during the run ({', '.join(changed[:5])}); restarting once on a fresh capture")
            restart_note = {"kind": "snapshot_restarted", "symbol_id": None,
                            "message": f"the working tree changed during the first attempt ({', '.join(changed[:5])}); the run was restarted once on a fresh capture"}
            continue
        return CheckResult(report, code, log)
    raise AssertionError("unreachable")


def _check_once(opts: CheckOptions, root: Path, cfg: Config, log: list[str], *, final: bool, restart_note: dict[str, Any] | None):
    run_id = "run-" + uuid.uuid4().hex
    created_at = now_utc()
    solver = setup_solver(cfg, opts.no_solver)

    t0 = time.perf_counter()
    snap = capture(root, [opts.path], cfg.exclude, cfg.digest, SEMANTICS_PROFILE)
    t_capture = (time.perf_counter() - t0) * 1000
    log.append(f"captured {len(snap.files)} file(s), snapshot {snap.snapshot_id[:19]}…")
    cancellation.check()

    lowered = lower_snapshot(snap, cfg)
    cancellation.check()
    sites = lowered.all_sites()
    required_ids = sorted(site_obligation_id(sym, s) for sym, s in sites if s.required)
    planned_all = sorted(site_obligation_id(sym, s) for sym, s in sites)
    plan = make_plan(command="check", mode=cfg.analysis.mode, base_snapshot=None, snap=snap, requested=lowered.requested_symbols(),
                     required_ids=required_ids, cfg=cfg, solver_state=solver.state, budget_ms=cfg.analysis.quick_budget_ms)

    backend = solver.backend(cfg)
    budget = Budget(deadline=time.monotonic() + cfg.analysis.quick_budget_ms / 1000.0, per_query_timeout_ms=cfg.analysis.per_query_timeout_ms, witness_search=cfg.analysis.witness_search,
                    assume_no_str_rmod_override=cfg.analysis.assume_no_str_rmod_override)
    analysis = analyze_lowered(lowered, cfg, backend, solver.status, budget)
    errors = lowered.errors + analysis.errors
    produced = sorted(site_obligation_id(o.symbol_id, o.site) for o in analysis.outcomes)
    if produced != planned_all:
        errors.append({"symbol_id": None, "stage": "coverage", "error_type": "PlanMismatch", "message": "obligation results do not match the analysis plan"})

    skipped = list(lowered.skipped)
    changed = verify_unchanged(snap)
    if changed and not final:
        return None, 0, changed
    for p in changed:
        skipped.append({"kind": "source_changed_during_run", "path": p, "symbol_id": None,
                        "reason": "file content or the file set changed after capture (also after one restart); results for this snapshot are incomplete", "required": True})
    diagnostics = list(lowered.diagnostics) + ([restart_note] if restart_note else [])
    report = build_report(
        command="check", run_id=run_id, created_at=created_at, cfg=cfg, snap=snap, plan=plan, modules=lowered.modules,
        outcomes=analysis.outcomes, engine_error_ids=analysis.engine_error_ids, skipped=skipped, errors=errors,
        diagnostics=diagnostics, analyzed_symbols=analysis.analyzed_symbols, requested_symbols=lowered.requested_symbols(),
        solver_state=solver.state, solver_detail=solver.detail, capability_warnings=solver.warnings,
        timing={"capture": round(t_capture, 3), "static": round(lowered.t_ms + analysis.t_ms, 3)},
    )
    code = report.pop("_exit_code")
    return report, code, []


# -- report -----------------------------------------------------------------------------------
def _message(o: ObligationOutcome) -> str:
    text, operand = o.site.text, o.site.divisor_text
    prop = o.site.base_property
    via = ""
    if o.site.via:
        via = " (reached through call(s) to " + " -> ".join(c["callee"] for c in o.site.via) + ")"
    if o.result is PropertyResult.PROVED_UNSAFE:
        mode = f" ({o.runtime_mode})" if o.runtime_mode else ""
        if prop == INDEX_PROPERTY:
            what = f"index `{operand}` is out of range for `{o.site.subject_text}`"
        elif prop == NONE_PROPERTY:
            what = f"receiver `{operand}` is None"
        else:
            what = f"divisor `{operand}` is 0"
        return f"`{text}` raises {failure_name(o.site)} for admitted input {o.witness}{mode}{via}: {what}"
    if o.result is PropertyResult.POSSIBLY_UNSAFE:
        return f"`{text}` may raise {failure_name(o.site)}{via} (overapproximate model; candidate witness {o.witness} not validated)"
    if o.result is PropertyResult.PROVED_SAFE:
        return f"`{text}` cannot raise {failure_name(o.site)}{via}"
    label = {DIVISION_PROPERTY: "zero-divisor safety", INDEX_PROPERTY: "index-bounds safety", NONE_PROPERTY: "None-receiver safety"}.get(prop, "safety")
    return f"{label} of `{text}`{via} is UNKNOWN: {o.reasons[-1] if o.reasons else 'no reason recorded'}"


@dataclass
class Relation:
    relation: PatchRelation
    comparison: dict[str, Any]


def evidence_body(o: ObligationOutcome, snap_id: str, oid: str, kind: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "kind": kind,
        "engine": {"name": ENGINE_NAME, "version": ENGINE_VERSION, "ir_version": IR_SCHEMA_VERSION},
        "snapshot_id": snap_id,
        "symbol_id": o.symbol_id,
        "property_id": o.site.property_id,
        "obligation_id": oid,
        "result_label": o.result.value,
        "domain": o.domain,
        "assumptions": o.assumptions,
        "precision": o.scope.value if o.scope else None,
        "method": o.method.value,
        "source_span": o.site.span.to_json(),
        "details": o.reasons,
        "solver_runs": o.solver_runs,
        "witness": o.witness,
        "witness_validation": "VALIDATED" if o.witness_validated else ("NOT_VALIDATED" if o.witness is not None else "NOT_APPLICABLE"),
    }


def evidence_kind(o: ObligationOutcome, is_engine_error: bool) -> str:
    if is_engine_error:
        return "ENGINE_ERROR"
    if o.solver_runs:
        return "SOLVER"
    if o.method is AnalysisMethod.WITNESS_SEARCH:
        return "WITNESS_SEARCH"
    if o.method is AnalysisMethod.UNSUPPORTED:
        return "UNSUPPORTED"
    return "STATIC_ANALYSIS"


def make_evidence(o: ObligationOutcome, snap_id: str, run_id: str, created_at: str, is_engine_error: bool) -> dict[str, Any]:
    oid = site_obligation_id(o.symbol_id, o.site)
    body = evidence_body(o, snap_id, oid, evidence_kind(o, is_engine_error))
    # Content address excludes timing fields so identical analyses get identical IDs.
    ev_id = "ev-" + short_hex({**body, "solver_runs": [{k: v for k, v in r.items() if k != "elapsed_ms"} for r in o.solver_runs]})
    return {**body, "evidence_id": ev_id, "run_id": run_id, "created_at": created_at}


def build_report(*, command: str, run_id: str, created_at: str, cfg: Config, snap: Snapshot, plan: dict[str, Any], modules: list[ModuleIR],
                 outcomes: list[ObligationOutcome], engine_error_ids: set[str], skipped: list[dict[str, Any]], errors: list[dict[str, Any]],
                 diagnostics: list[dict[str, Any]], analyzed_symbols: int, requested_symbols: list[str], solver_state: str, solver_detail: str,
                 capability_warnings: list[str], timing: dict[str, float], base_snapshot: str | None = None,
                 relations: dict[str, Relation] | None = None,
                 required_fn: Callable[[ObligationOutcome], bool] | None = None,
                 severity_fn: Callable[[ObligationOutcome, Relation | None], Severity | None] | None = None,
                 extra_findings: list[dict[str, Any]] | None = None, extra_sections: dict[str, Any] | None = None,
                 extra_reasons: list[str] | None = None, coverage_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    require = cfg.analysis.require_coverage
    outcomes = sorted(outcomes, key=lambda o: (o.site.span.path, o.symbol_id, o.site.property_id, o.site.ordinal))
    obligation_results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    evidence_ids: set[str] = set()
    unknown_obligations: list[dict[str, Any]] = []
    severities: list[Severity] = []
    solver_ms = 0.0
    for o in outcomes:
        oid = site_obligation_id(o.symbol_id, o.site)
        span = o.site.span.to_json()
        required = (required_fn(o) if required_fn is not None else o.site.required) and require
        if o.reused_evidence is not None:
            ev = o.reused_evidence
        else:
            ev = make_evidence(o, snap.snapshot_id, run_id, created_at, oid in engine_error_ids)
            for r in o.solver_runs:
                solver_ms += r["elapsed_ms"]
        if ev["evidence_id"] not in evidence_ids:
            evidence.append(ev)
            evidence_ids.add(ev["evidence_id"])
        ev_id = ev["evidence_id"]
        rel = relations.get(oid) if relations is not None else None
        sev = severity_fn(o, rel) if severity_fn is not None else severity_for(o.result, o.scope)
        if o.result is PropertyResult.UNKNOWN and not o.site.required:
            sev = None  # advisory reachable-call evidence: unknowns are not findings
        if sev in (Severity.ERROR, Severity.WARNING) and o.site.handled_by:
            sev = Severity.INFO  # the failure is caught by a handler that does not re-raise (I-133)
        finding_id = None
        if sev is not None:
            severities.append(sev)
            finding_id = "fnd-" + short_hex([snap.snapshot_id, oid, o.result.value, o.witness, rel.relation.value if rel else None])
            f = {
                "finding_id": finding_id,
                "obligation_id": oid,
                "symbol_id": o.symbol_id,
                "property_id": o.site.property_id,
                "property_result": o.result.value,
                "scope_validity": o.scope.value if o.scope else None,
                "authority": o.authority.value,
                "patch_relation": (rel.relation if rel else PatchRelation.UNDETERMINED).value,
                "severity": sev.value,
                "source_span": span,
                "message": _message(o),
                "quantifier": o.quantifier,
                "domain": o.domain,
                "domain_source": o.domain_source.value,
                "assumptions": o.assumptions,
                "path_constraints": o.path_constraints,
                "reasons": o.reasons,
                "witness": o.witness,
                "witness_validated": o.witness_validated,
                "runtime_mode": o.runtime_mode,
                "evidence_ids": [ev_id],
                "replay_available": False,
            }
            if rel is not None:
                f["comparison"] = rel.comparison
            findings.append(f)
        if o.result is PropertyResult.UNKNOWN and o.site.required:
            unknown_obligations.append({"obligation_id": oid, "symbol_id": o.symbol_id, "reason": o.reasons[-1] if o.reasons else "unknown", "required": required})
        r = {
            "obligation_id": oid,
            "symbol_id": o.symbol_id,
            "property_id": o.site.property_id,
            "operation": o.site.op.value,
            "operation_text": o.site.text,
            "divisor_text": o.site.divisor_text,
            "property_result": o.result.value,
            "scope_validity": o.scope.value if o.scope else None,
            "authority": o.authority.value,
            "domain": o.domain,
            "domain_source": o.domain_source.value,
            "assumptions": o.assumptions,
            "quantifier": o.quantifier,
            "method": o.method.value,
            "reasons": o.reasons,
            "source_span": span,
            "required": required,
            "witness": o.witness,
            "witness_validated": o.witness_validated,
            "path_constraints": o.path_constraints,
            "evidence_ids": [ev_id],
            "finding_id": finding_id,
            "runtime_mode": o.runtime_mode,
            "divisor_fact": o.divisor_fact,
            "anchor": o.site.anchor,
        }
        if o.site.via:
            r["via"] = [{"callee": c["callee"], "call": c["call"], "call_span": c["call_span"]} for c in o.site.via]
        if o.reused_evidence is not None:
            r["evidence_reused"] = True
        if rel is not None:
            r["patch_relation"] = rel.relation.value
        obligation_results.append(r)
    for f in extra_findings or []:
        findings.append(f)
        severities.append(Severity(f["severity"]))
    unknown_required = sum(1 for u in unknown_obligations if u["required"])
    unknown_advisory = len(unknown_obligations) - unknown_required
    skipped_required = sum(1 for s in skipped if s["required"])
    skipped_advisory = len(skipped) - skipped_required
    decision = evaluate(PolicyInput(
        severities=severities,
        unknown_required=unknown_required,
        skipped_required=skipped_required,
        engine_errors=len(errors),
        capability_warnings=capability_warnings,
        unknown_advisory=unknown_advisory,
        skipped_advisory=skipped_advisory,
        files_analyzed=len(modules) + len(skipped),
        extra_reasons=list(extra_reasons or []),
        incomplete_reasons=[r for r in (extra_sections or {}).get("_incomplete_reasons", [])],
        warn_reasons=list((extra_sections or {}).get("_warn_reasons", [])),
        contract_errors=sum(1 for f in extra_findings or [] if f.get("contract") is not None and f["severity"] == Severity.ERROR.value),
    ))
    unsupported_symbols = [
        {"symbol_id": u.symbol_id, "kind": u.kind, "reason": u.reason, "division_sites": len(u.division_sites)}
        for m in modules for u in m.unsupported_symbols
    ]
    required_total = len(plan["required_obligations"])
    resolved_required = sum(1 for r in obligation_results if r["required"] and r["property_result"] != PropertyResult.UNKNOWN.value)
    coverage = {
        "requested_symbols": len(requested_symbols),
        "analyzed_symbols": analyzed_symbols,
        "required_obligations": required_total,
        "resolved_required_obligations": resolved_required,
        "files_captured": len(snap.files),
        "files_analyzed": len(modules),
        "unknown_obligations": unknown_obligations,
        "skipped_checks": skipped,
        "unsupported_symbols": unsupported_symbols,
        "advisory_obligations": sum(1 for o in outcomes if not o.site.required),
    }
    coverage.update(coverage_extra or {})
    timing_ms = {"capture": timing["capture"], "static": timing["static"], "solver": round(solver_ms, 3), "dynamic": None, "model": None}
    for k, v in timing.items():
        if k not in timing_ms:
            timing_ms[k] = v
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": {"name": ENGINE_NAME, "engine_version": ENGINE_VERSION, "ir_version": IR_SCHEMA_VERSION,
                 "python": sys.version.split()[0], "grammar": PYTHON_GRAMMAR},
        "command": command,
        "run_id": run_id,
        "base_snapshot": base_snapshot,
        "candidate_snapshot": snap.snapshot_id,
        "snapshot": snap.manifest(),
        "analysis_plan": plan,
        "run_outcome": decision.run_outcome.value,
        "verdict": decision.verdict.value,
        "verdict_reasons": decision.reasons,
        "policy_id": POLICY_ID,
        "coverage": coverage,
        "timing_ms": timing_ms,
        "findings": findings,
        "obligation_results": obligation_results,
        "evidence": evidence,
        "capabilities": {
            "solver": solver_state,
            "solver_detail": solver_detail,
            "execution": "disabled",
            "model": "disabled",
            "frontends": [f"python (stdlib ast, grammar {PYTHON_GRAMMAR})"],
        },
        "diagnostics": diagnostics,
        "errors": [{"symbol_id": e.get("symbol_id"), "stage": e["stage"], "error_type": e["error_type"], "message": e["message"]} for e in errors],
        "_exit_code": decision.exit_code,
    }
    for k, v in (extra_sections or {}).items():
        if not k.startswith("_"):
            report[k] = v
    return report


__all__ = ["CheckOptions", "CheckResult", "UsageError", "run_check", "Authority", "DomainSource", "Function", "PROPERTY_ID"]
