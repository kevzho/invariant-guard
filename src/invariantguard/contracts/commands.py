"""``guard contracts ...`` and ``guard runner ...`` command implementations (M3).

Lifecycle commands are explicit maintainer actions with a recorded reason. None of them
runs repository code: validation resolves the target from a fresh capture of the working
tree with the AST only. ``guard runner setup`` is the only command that may use the
network (to pull the configured image); ``guard runner probe`` runs the capability probe
container without any repository code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config, ConfigError
from ..orchestration import SEMANTICS_PROFILE, CheckOptions, UsageError, now_utc, resolve_inputs
from ..snapshots.capture import capture
from ..storage.store import Store, StoreError
from .dsl import Rejection
from .model import load_document, validate_revision
from .registry import ContractRegistry, RegistryError
from .templates import candidate_batch, discover, validate_proposals

SEMANTIC_FIELDS = ("scope", "precondition", "predicate", "input_profile", "examples")


@dataclass
class Ctx:
    root: Path
    cfg: Config
    store: Store
    registry: ContractRegistry

    def close(self) -> None:
        self.store.close()


def open_ctx(path: Path, state_dir: Path | None, root: Path | None, config_path: Path | None = None, no_config_search: bool = False) -> Ctx:
    r, cfg = resolve_inputs(CheckOptions(path=path, root=root, config_path=config_path, no_config_search=no_config_search))
    sd = state_dir.resolve() if state_dir is not None else r / ".guard"
    try:
        store = Store(sd)
    except StoreError as exc:
        raise UsageError(str(exc)) from None
    return Ctx(r, cfg, store, ContractRegistry(store))


def _python_path(cfg: Config) -> list[str]:
    name = cfg.execution.get("profile")
    prof = cfg.run_profiles.get(name) if name else None
    return list(prof.python_path) if prof is not None else ["."]


def _snapshot_files(ctx: Ctx) -> tuple[dict[str, bytes], str]:
    snap = capture(ctx.root, [ctx.root], ctx.cfg.exclude, ctx.cfg.digest, SEMANTICS_PROFILE)
    return {f.path: f.raw for f in snap.files}, snap.snapshot_id


def _read_file(file: Path) -> dict[str, Any]:
    try:
        raw = file.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {file}: {exc.strerror}") from None
    try:
        doc = load_document(raw)
    except Rejection as exc:
        raise UsageError(f"{file}: {exc}") from None
    if type(doc) is not dict:
        raise UsageError(f"{file}: a contract file is a JSON object")
    return doc


def _build(src: dict[str, Any], *, contract_id: str, revision: int, authority: str, lifecycle: str, supersedes: int | None, reason: str,
           action: str) -> dict[str, Any]:
    missing = [k for k in ("scope", "precondition", "predicate", "input_profile") if k not in src]
    if missing:
        raise UsageError(f"the contract file is missing {missing}")
    unknown = set(src) - set(SEMANTIC_FIELDS) - {"provenance", "contract_id", "schema_version", "revision", "authority", "lifecycle_state",
                                                   "supersedes", "reason", "created_at"}
    if unknown:
        raise UsageError(f"unknown field(s) in the contract file: {sorted(unknown)}")
    prov = list(src.get("provenance") or [])
    prov.append({"kind": "maintainer_approval" if action != "declare" else "declaration", "reference": f"guard contracts {action}: {reason.strip()[:200]}"})
    doc = {"schema_version": "1.0", "contract_id": contract_id, "revision": revision, "scope": src["scope"], "authority": authority,
           "lifecycle_state": lifecycle, "precondition": src["precondition"], "predicate": src["predicate"], "input_profile": src["input_profile"],
           "provenance": prov, "supersedes": supersedes, "reason": reason.strip(), "created_at": now_utc()}
    if src.get("examples"):
        doc["examples"] = src["examples"]
    return doc


def _validated(ctx: Ctx, doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    files, snap_id = _snapshot_files(ctx)
    try:
        tc = validate_revision(doc, files, snap_id, python_path=_python_path(ctx.cfg))
    except Rejection as exc:
        raise UsageError(f"contract rejected ({exc.code} at {exc.path or '<root>'}): {exc.message}") from None
    return tc.semantic_digest, {"admissible_witness": tc.admissible_witness, "search": tc.search, "target": tc.target.to_json(), "snapshot_id": snap_id}


def cmd_list(ctx: Ctx) -> dict[str, Any]:
    view = ctx.registry.load()
    return {"schema_version": "1.0", "contracts": [ctx.registry.summary(st) for st in sorted(view.contracts.values(), key=lambda s: s.contract_id)],
            "diagnostics": view.diagnostics}


def cmd_show(ctx: Ctx, cid: str) -> dict[str, Any]:
    view = ctx.registry.load()
    st = view.contracts.get(cid)
    if st is None:
        raise UsageError(f"no contract named {cid!r}")
    out = ctx.registry.summary(st)
    out["revisions"] = {str(k): v for k, v in sorted(st.revisions.items())}
    out["evidence"] = ctx.store.contract_evidence(cid)
    return out


def cmd_declare(ctx: Ctx, file: Path, reason: str) -> dict[str, Any]:
    src = _read_file(file)
    cid = src.get("contract_id")
    if not isinstance(cid, str):
        raise UsageError("the contract file needs a contract_id")
    doc = _build(src, contract_id=cid, revision=1, authority="DECLARED", lifecycle="REVIEWED", supersedes=None, reason=reason, action="declare")
    sem, info = _validated(ctx, doc)
    try:
        ctx.registry.create(doc, sem, kind="declare", reason=reason)
    except (RegistryError, Rejection, StoreError) as exc:
        raise UsageError(str(exc)) from None
    return {"declared": cid, "revision": 1, "authority": "DECLARED", "semantic_digest": sem, "validation": info}


def cmd_authority(ctx: Ctx, action: str, cid: str, reason: str) -> dict[str, Any]:
    try:
        return ctx.registry.set_authority(cid, action, reason)
    except (RegistryError, StoreError) as exc:
        raise UsageError(str(exc)) from None


def cmd_revise(ctx: Ctx, cid: str, file: Path, reason: str) -> dict[str, Any]:
    src = _read_file(file)
    if src.get("contract_id", cid) != cid:
        raise UsageError(f"the file names contract {src.get('contract_id')!r}, not {cid!r}")
    view = ctx.registry.load()
    st = view.contracts.get(cid)
    if st is None:
        raise UsageError(f"no contract named {cid!r}")
    if not st.ok:
        raise UsageError(f"contract {cid!r} has integrity errors: {st.integrity_errors}")
    authority = st.authority if st.authority in ("APPROVED", "DECLARED") else "INFERRED"
    doc = _build(src, contract_id=cid, revision=max(st.revisions) + 1, authority=authority,
                 lifecycle="REVIEWED" if authority in ("APPROVED", "DECLARED") else "VALIDATED", supersedes=st.current_revision, reason=reason, action="revise")
    sem, info = _validated(ctx, doc)
    try:
        out = ctx.registry.revise(cid, doc, sem, reason)
    except (RegistryError, StoreError) as exc:
        raise UsageError(str(exc)) from None
    out["validation"] = info
    out["note"] = "evidence of earlier revisions is kept as history and is not inherited by this revision"
    return out


def cmd_import(ctx: Ctx, reason: str) -> dict[str, Any]:
    try:
        return {"imported": ctx.registry.import_unindexed(reason)}
    except (RegistryError, StoreError, ValueError) as exc:
        raise UsageError(str(exc)) from None


@dataclass
class DiscoverOptions:
    dry_run: bool = False
    model: str | None = None  # None (templates only) | "local" | "remote"
    execute: bool = False
    run_profile: str | None = None
    symbols: list[str] | None = None
    max_functions: int | None = None
    show_context: bool = False
    templates: bool = True
    agent_batches: list[Any] | None = None  # agent-proposed candidate batches (semantic/agent.py)
    agent_client: dict[str, str] | None = None  # the proposer's self-reported identity (from the transport)
    authorized_profile_digest: str | None = None  # MCP: the run profile digest pinned at server start


def check_model_request(model: str, sem: dict[str, Any]) -> None:
    """``--model local`` must never send a packet off this machine; ``--model remote`` needs a
    configured bring-your-own-key provider. Neither falls back to the other."""
    if not sem.get("enabled"):
        raise UsageError(f"--model {model} needs [semantic] enabled = true in .guard/config.toml (nothing is contacted otherwise)")
    provider = sem.get("provider", "loopback")
    if model == "local":
        if provider != "loopback":
            from ..semantic.remote import DestinationError, validate_provider_url

            try:
                dest = validate_provider_url(sem.get("base_url"), remote_allowed=bool(sem.get("remote_allowed")))
            except DestinationError as exc:
                raise UsageError(f"semantic.base_url: {exc}") from None
            if not dest.loopback:
                raise UsageError(f"--model local refuses the configured {provider} provider at {dest.host}: it is not a loopback destination "
                                 "(context would leave this machine); use --model remote to send it there explicitly")
    elif model == "remote":
        if provider == "loopback":
            raise UsageError("--model remote needs semantic.provider = 'openai_compatible' or 'anthropic' with base_url, model_id and api_key_env; "
                             "the loopback model is used with --model local (there is no automatic switch)")
    else:
        raise UsageError(f"--model must be 'local' or 'remote', not {model!r}")


def _candidate_json(p: Any) -> dict[str, Any]:
    fals = p.extra.get("falsification")
    out = {"contract_id": p.doc["contract_id"], "scope": p.doc["scope"], "source": p.extra.get("source", "template"), "template": p.template,
            "status": p.status, "reason": p.reason, "rejection_code": p.extra.get("rejection_code"), "rationale": p.rationale,
            "assumptions": p.assumptions, "provenance": p.doc.get("provenance", []), "input_profile": p.doc.get("input_profile"),
            "precondition": p.doc.get("precondition"), "predicate": p.doc.get("predicate"),
            "examples": p.doc.get("examples", []), "semantic_digest": p.semantic_digest, "source_span": p.source_span,
            "admissible_witness": p.extra.get("admissible_witness"), "nonvacuity": p.extra.get("nonvacuity"), "meaning_digest": p.extra.get("meaning_digest"),
            "boundary_inputs_dropped": p.extra.get("boundary_dropped", []), "falsification": fals}
    egress = next((x.get("egress") for x in p.doc.get("provenance") or [] if isinstance(x, dict) and x.get("egress")), None)
    if egress is not None:
        out["data_egress"] = egress
    if p.extra.get("source") == "agent_proposed":
        out["evidence_ids"] = list(p.extra.get("evidence_ids", []))
        out["batch_index"] = p.extra.get("batch_index")
        out["candidate_index"] = p.extra.get("index")
    return out


def cmd_discover(ctx: Ctx, dry_run: bool | DiscoverOptions = False) -> tuple[dict[str, Any], int]:
    """Template (and optionally local-model) discovery through one mechanical pipeline.

    Returns (output, exit code): 0; 2 (INCOMPLETE) when a requested capability (the
    local model or the runner) was unavailable or the working tree changed during the run;
    3 (ERROR) when checking a candidate hit an engine fault (audit A4-02).
    """
    from .templates import target_functions

    opts = dry_run if isinstance(dry_run, DiscoverOptions) else DiscoverOptions(dry_run=bool(dry_run))
    if opts.run_profile and not opts.execute:
        raise UsageError("--run-profile is only meaningful with --execute")
    t_start = time.perf_counter()
    log: list[str] = []
    incomplete: list[str] = []
    files, snap_id = _snapshot_files(ctx)
    created = now_utc()
    pp = _python_path(ctx.cfg)
    sem = ctx.cfg.semantic
    if opts.model is not None:
        check_model_request(opts.model, sem)
    t0 = time.perf_counter()
    proposals, skipped = discover(files, snap_id, created, pp) if opts.templates else ([], [])
    for p in proposals:
        p.extra["source"] = "template"
    template_ms = (time.perf_counter() - t0) * 1000
    infos, _ = target_functions(files, snap_id, pp)
    if opts.symbols:
        wanted = set(opts.symbols)
        unknown = wanted - {i.symbol_id for i in infos}
        if unknown:
            raise UsageError(f"--symbol: not a contract target with an established input profile: {sorted(unknown)}")
        infos = [i for i in infos if i.symbol_id in wanted]
        proposals = [p for p in proposals if p.doc["scope"] in wanted]
    limit = opts.max_functions or int(sem.get("max_functions", 64))
    view = ctx.registry.load()
    model_section: dict[str, Any] = {"requested": False, "status": "DISABLED", "reason": "templates only (no --model)"}
    model_props: list[Any] = []
    md = None
    if opts.model is not None:
        from ..semantic.context import ContextPacketBuilder
        from ..semantic.discovery import make_reasoner, run_model_discovery

        if len(infos) > limit:
            log.append(f"model discovery limited to the first {limit} of {len(infos)} target functions (semantic.max_functions / --max-functions)")
        existing = {}
        for st in view.contracts.values():
            if st.ok:
                existing.setdefault(st.scope, []).append(st.contract_id)
        from ..semantic.endpoint import EndpointError
        from ..semantic.reasoner import ReasonerUnavailable

        try:
            reasoner = make_reasoner(sem)
        except (EndpointError, ReasonerUnavailable, ValueError) as exc:  # destination policy refused: a configuration error, nothing sent
            raise UsageError(f"model proposer refused: {exc}") from None
        md = run_model_discovery(reasoner, infos[:limit], ContextPacketBuilder(files, snap_id, pp), created_at=created, sem=sem,
                                 existing_ids=existing, store=ctx.store, retain_raw=bool(ctx.cfg.privacy.get("store_raw_model_context", False)), log=log)
        model_props = md.proposals
        model_section = md.section(opts.show_context)
        if md.status != "AVAILABLE":
            incomplete.append(f"{opts.model} model discovery {md.status}: {md.reason}")
    agent_props: list[Any] = []
    agent_section: dict[str, Any] = {"requested": False}
    agent_packets: list[tuple[Any, list[Any]]] = []
    if opts.agent_batches is not None:
        from ..semantic.agent import BatchRejection, load_batches, normalize_client, proposals_for_batch
        from ..semantic.context import ContextPacketBuilder

        client = normalize_client(opts.agent_client)
        existing_a: dict[str, list[str]] = {}
        for st in view.contracts.values():
            if st.ok:
                existing_a.setdefault(st.scope, []).append(st.contract_id)
        builder = ContextPacketBuilder(files, snap_id, pp)
        profiles = {i.symbol_id: i.profile for i in infos}
        batch_reports: list[dict[str, Any]] = []
        try:
            raw_batches = load_batches(opts.agent_batches)
        except BatchRejection as exc:
            raise UsageError(f"{exc.code}: {exc.message}") from None
        for bi, raw in enumerate(raw_batches):
            props_b, rep, bp = proposals_for_batch(raw, builder, profiles, sem, existing_a, created, client)
            rep["batch_index"] = bi
            for pr in props_b:
                pr.extra["batch_index"] = bi
            batch_reports.append(rep)
            agent_props.extend(props_b)
            if bp is not None and props_b:
                agent_packets.append((bp, props_b))
        agent_section = {"requested": True, "client": client, "client_identity_note": "self-reported by the client; not authenticated",
                         "batches": batch_reports,
                         "authority_note": "agent proposals are INFERRED at most: never approved, never evidence or proof; agents cannot approve or revise"}
    all_props = proposals + model_props + agent_props
    t1 = time.perf_counter()
    validate_proposals(all_props, files, snap_id, pp)  # the same mechanical pipeline for both sources
    _dedupe(all_props, view)
    validation_ms = (time.perf_counter() - t1) * 1000
    fals_section: dict[str, Any] = {"requested": opts.execute, "status": "DISABLED",
                                    "reason": "dynamic falsification not requested (--execute --run-profile NAME)"}
    engine_errors: list[str] = []
    if opts.execute:
        fals_section = _falsify_all(ctx, all_props, files, snap_id, opts, log, incomplete)
        # An engine fault while checking a candidate is ERROR, never a complete run (audit A4-02).
        engine_errors = [f"{p.doc.get('contract_id')}: " + "; ".join(p.extra["falsification"].get("reasons", []))[:300]
                         for p in all_props if (p.extra.get("falsification") or {}).get("status") == "ENGINE_ERROR"]
    # The snapshot must not have changed while discovery ran (docs/10); otherwise nothing is recorded.
    _, snap_after = _snapshot_files(ctx)
    changed = snap_after != snap_id
    if changed:
        incomplete.append("the working tree changed during discovery; no candidate was recorded")
    for pr in all_props:
        if pr.status != "validated":
            continue
        existing_st = view.contracts.get(pr.doc["contract_id"])
        if existing_st is not None:
            same = existing_st.semantic.get(existing_st.current_revision) == pr.semantic_digest
            pr.status = "exists" if same else "exists_different"
            pr.reason = ("already recorded with the same semantics" if same else
                         "a contract with this id exists with different semantics; discovery never revises it (use `guard contracts revise`)")
            continue
        if opts.dry_run or changed:
            pr.status = "would_propose" if opts.dry_run else "not_recorded"
            continue
        src = pr.extra.get("source", "template")
        why = (f"template {pr.template}: {pr.rationale}" if src == "template" else
               f"{src.replace('_', ' ')} proposal (not evidence): {pr.rationale}")
        try:
            ctx.registry.create(pr.doc, pr.semantic_digest or "", kind="propose", reason=why[:500])
            pr.status = "proposed"
        except (RegistryError, Rejection, StoreError) as exc:
            pr.status, pr.reason = "rejected", str(exc)
    if agent_props and not opts.dry_run and not changed:
        # Content-addressed record of each falsified agent candidate (its discovery evidence).
        for pr in agent_props:
            fals = pr.extra.get("falsification")
            if fals:
                pr.extra["evidence_ids"] = [ctx.store.put_json({"kind": "discovery_falsification", "snapshot_id": snap_id,
                                                                 "contract_id": pr.doc["contract_id"], "semantic_digest": pr.semantic_digest,
                                                                 "provenance": pr.doc.get("provenance"), "status": pr.status,
                                                                 "falsification": fals}, "discovery_falsification")]
    batches = []
    if md is not None:
        egress = model_section.get("data_egress") if md.source_kind == "remote_model" else None
        for sym, bp in md.packets.items():
            mine = [p for p in model_props if p.extra.get("packet") is bp]
            fe = next((f.get("egress") for f in md.functions if f.get("symbol_id") == sym and f.get("egress")), None)
            b = candidate_batch(mine, snap_id, source=md.source_kind, model_identity=md.capabilities.identity.to_batch_json()  # type: ignore[union-attr]
                                if md.capabilities and md.capabilities.identity else None, context_packet_digest=bp.digest,
                                data_egress=fe if egress is not None or fe is not None else None)
            batches.append(b)
    agent_batches_out = [candidate_batch(mine, snap_id, source="agent_proposed", model_identity=None, context_packet_digest=bp.digest,
                                         proposer_client=agent_section.get("client")) for bp, mine in agent_packets]
    counts: dict[str, dict[str, int]] = {}
    for p in all_props:
        c = counts.setdefault(p.extra.get("source", "template"), {})
        c[p.status] = c.get(p.status, 0) + 1
    out = {
        "schema_version": "1.0", "snapshot_id": snap_id,
        "mode": ("templates (deterministic)" if opts.templates else "no templates") + (f" + {opts.model} model" if opts.model else "")
                + (" + agent proposals" if opts.agent_batches is not None else "") + (" + dynamic falsification" if opts.execute else ""),
        "authority_note": "proposals are INFERRED; approval is a separate `guard contracts approve ID --reason` command; model and agent output is never evidence",
        "run_outcome": "ERROR" if engine_errors else ("INCOMPLETE" if incomplete else "COMPLETE"),
        "incomplete_reasons": incomplete,
        "engine_errors": engine_errors,
        "counts": counts,
        "candidates": [_candidate_json(p) for p in all_props],
        "skipped": skipped,
        "candidate_batch": candidate_batch(proposals, snap_id),
        "model_candidate_batches": batches,
        "model": model_section,
        "agent": agent_section,
        "agent_candidate_batches": agent_batches_out,
        "falsification": fals_section,
        "timing_ms": {"templates": round(template_ms, 3), "model": round(md.model_ms, 3) if md else 0.0, "validation": round(validation_ms, 3),
                      "falsification": fals_section.get("elapsed_ms", 0.0), "total": round((time.perf_counter() - t_start) * 1000, 3)},
        "log": log,
    }
    return out, (3 if engine_errors else 2 if incomplete else 0)


def cmd_context(root: Path, cfg: Config, symbol: str, state_dir: Path | None = None) -> dict[str, Any]:
    """``guard contracts context SYMBOL`` / MCP ``get_context_packet``: the exact bounded
    ContextPacket a model would receive, plus the candidate schema and DSL reference.
    Reads source with the AST only; contacts nothing and writes nothing (an absent state
    directory is not created)."""
    from ..semantic.agent import context_for
    from ..semantic.context import ContextError, ContextPacketBuilder
    from .templates import target_functions

    snap = capture(root, [root], cfg.exclude, cfg.digest, SEMANTICS_PROFILE)
    files, snap_id = {f.path: f.raw for f in snap.files}, snap.snapshot_id
    pp = _python_path(cfg)
    infos, skipped = target_functions(files, snap_id, pp)
    info = next((i for i in infos if i.symbol_id == symbol), None)
    if info is None:
        why = next((x.get("reason") for x in skipped if x.get("symbol_id") == symbol), None)
        raise UsageError(f"{symbol[:200]} is not a contract target" + (f": {why}" if why else " (no module-level function with that id and an "
                                                                                                "established input profile)"))
    sd = state_dir if state_dir is not None else root / ".guard"
    existing: list[str] = []
    if (sd / "state.sqlite").is_file():
        try:
            store = Store(sd)
        except StoreError as exc:
            raise UsageError(str(exc)) from None
        try:
            existing = sorted(st.contract_id for st in ContractRegistry(store).load().contracts.values() if st.ok and st.scope == symbol)
        finally:
            store.close()
    try:
        _, resp = context_for(ContextPacketBuilder(files, snap_id, pp), symbol, info.profile, cfg.semantic, existing)
    except ContextError as exc:
        raise UsageError(f"{exc.code}: {exc.message}") from None
    return {"schema_version": "1.0", "snapshot_id": snap_id, "input_profile_source": info.profile_source, **resp}


def cmd_propose(ctx: Ctx, batches: Any, *, client: dict[str, str] | None, execute: bool = False, run_profile: str | None = None,
                dry_run: bool = False, authorized_profile_digest: str | None = None) -> tuple[dict[str, Any], int]:
    """``guard contracts propose --file F`` / MCP ``propose_contracts``: agent candidates through
    the shared pipeline (no templates, no model). Never approves; records INFERRED only."""
    out, code = cmd_discover(ctx, DiscoverOptions(dry_run=dry_run, execute=execute, run_profile=run_profile, templates=False, agent_batches=batches,
                                                  agent_client=client, authorized_profile_digest=authorized_profile_digest))
    accepted = [c for c in out["candidates"] if c["status"] in ("proposed", "would_propose", "exists")]
    out["summary"] = {"batches": len(out["agent"].get("batches", [])), "candidates": len(out["candidates"]), "accepted": len(accepted),
                      "rejected": len(out["candidates"]) - len(accepted),
                      "accepted_means": "schema-valid, typed, non-vacuous and not refuted on the current snapshot; recorded as INFERRED (not approved)"}
    return out, code


def _meaning(scope: str, pre: Any, post: Any, profile: str) -> str:
    from ..models.spans import digest_json
    from .dsl import canonicalize

    return digest_json([scope, canonicalize(pre), canonicalize(post), profile])


def _dedupe(props: list[Any], view: Any) -> None:
    """Same scope + canonical precondition/predicate + profile = the same claim, whatever the
    source or the examples: keep the first (templates come first) or the recorded contract."""
    recorded = {}
    for st in view.contracts.values():
        if st.ok:
            d = st.doc
            recorded[_meaning(d["scope"], d["precondition"], d["predicate"], d["input_profile"])] = st.contract_id
    seen: dict[str, str] = {}
    for pr in props:
        if pr.status != "validated":
            continue
        tc = pr.extra["typed"]
        key = _meaning(tc.scope, tc.precondition, tc.predicate, tc.input_profile.name)
        pr.extra["meaning_digest"] = key
        if key in recorded and recorded[key] != pr.doc["contract_id"]:
            pr.status, pr.reason = "duplicate", f"same claim as recorded contract {recorded[key]}"
        elif key in seen:
            pr.status, pr.reason = "duplicate", f"same claim as candidate {seen[key]} in this run"
        else:
            seen[key] = pr.doc["contract_id"]


def _falsify_all(ctx: Ctx, props: list[Any], files: dict[str, bytes], snap_id: str, opts: DiscoverOptions, log: list[str],
                 incomplete: list[str]) -> dict[str, Any]:
    from ..verify import _prepare_execution
    from .checker import ExecContext, Side
    from .falsify import falsify

    todo = [p for p in props if p.status == "validated"]
    dyn = _prepare_execution(opts, ctx.cfg, None, log, needed=bool(todo))
    section = dyn.section()
    section["note"] = "falsification runs every validated candidate (template or model) on the current snapshot; it is discovery evidence only"
    if not todo:
        return section
    if dyn.status is None or dyn.unavailable_reason is not None:
        incomplete.append(f"dynamic falsification RUNNER_UNAVAILABLE: {dyn.unavailable_reason}; no unisolated fallback")
        for p in todo:
            p.extra["falsification"] = {"status": "RUNNER_UNAVAILABLE", "property_result": "UNVERIFIED", "reasons": [dyn.unavailable_reason]}
        return section
    ectx = ExecContext(backend=dyn.backend, status=dyn.status, profile=dyn.profile, run_profile_doc=dyn.run_profile_doc or {},
                       run_profile_digest=dyn.run_profile_digest or "", run_id="discover-" + snap_id[7:23], created_at=now_utc(), registry=None)
    side = Side("candidate", snap_id, files)
    t0 = time.perf_counter()
    try:
        for p in todo:
            try:
                r = falsify(ectx, p.extra["typed"], side)
            except Exception as exc:  # noqa: BLE001 - a checker fault is an engine error for this candidate only
                r = {"status": "ENGINE_ERROR", "property_result": "UNKNOWN", "reasons": [f"{type(exc).__name__}: {exc}"]}
            p.extra["falsification"] = r
            if r["status"] == "REFUTED":
                p.status, p.reason = "refuted", "counterexample on the current snapshot: " + "; ".join(r["reasons"][-1:])
    finally:
        ectx.close()
    section["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 3)
    section["results"] = {k: sum(1 for p in todo if p.extra["falsification"]["status"] == k)
                          for k in ("TESTED", "REFUTED", "UNKNOWN", "UNSTABLE", "NOT_ALLOWED", "ENGINE_ERROR")}
    return section


def _profile(cfg: Config, name: str | None) -> Any:
    try:
        if name:
            p = cfg.run_profiles.get(name)
            if p is None:
                raise ConfigError(f"run profile {name!r} is not defined in [execution.profiles] (known: {sorted(cfg.run_profiles)})")
            return p
        return cfg.run_profile(None)
    except ConfigError as exc:
        raise UsageError(str(exc)) from None


def cmd_runner_setup(path: Path, root: Path | None, name: str | None, config_path: Path | None = None) -> dict[str, Any]:
    from ..execution.runner import RunnerError, make_backend

    _, cfg = resolve_inputs(CheckOptions(path=path, root=root, config_path=config_path))
    prof = _profile(cfg, name)
    backend = make_backend(prof)
    if prof.backend != "docker":
        st = backend.status()
        return {"profile": prof.name, "backend": prof.backend, "status": st.to_json(), "note": "the unisolated backend needs no image; nothing was prepared"}
    try:
        info = backend.setup()  # type: ignore[attr-defined]
    except RunnerError as exc:
        raise UsageError(f"runner setup failed: {exc}") from None
    return {"profile": prof.name, "backend": "docker", "prepared": info,
            "note": "image prepared explicitly; scans use it by image ID with --pull never and never download"}


def cmd_runner_probe(path: Path, root: Path | None, name: str | None, config_path: Path | None = None) -> tuple[dict[str, Any], bool]:
    from ..execution.runner import make_backend

    _, cfg = resolve_inputs(CheckOptions(path=path, root=root, config_path=config_path))
    prof = _profile(cfg, name)
    st = make_backend(prof).status()
    return {"profile": prof.name, **st.to_json()}, st.available


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


__all__ = ["DiscoverOptions", "check_model_request", "cmd_context", "cmd_propose", "open_ctx", "cmd_list", "cmd_show", "cmd_declare", "cmd_authority", "cmd_revise", "cmd_import", "cmd_discover", "cmd_runner_setup",
           "cmd_runner_probe", "dumps"]
