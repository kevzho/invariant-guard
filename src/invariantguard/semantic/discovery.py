"""Model-backed contract discovery (M4): packets -> local model -> strict parsing.

The output is a list of ``Proposal`` objects in the same shape as template proposals;
the caller (``contracts/commands.cmd_discover``) then runs the shared mechanical pipeline
over both sources. Nothing here validates semantics, records contracts or executes code.

Failure behavior (docs/08 "Privacy and failure behavior"): if the endpoint is down or the
model is absent the result is UNAVAILABLE and deterministic discovery continues; after two
consecutive transport failures the remaining functions are not attempted (no retry
storm, no fallback endpoint). Raw context (packet, prompt, response) is stored only when
``[privacy] store_raw_model_context = true``; by default only digests are kept.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from ..contracts.templates import Proposal, TargetInfo
from ..models.spans import canonical_json
from .candidates import ProposerSource, proposals_from_response
from .context import ContextBudget, ContextError, ContextPacketBuilder
from .endpoint import EndpointError, validate_loopback_endpoint
from .prompt import PROMPT_VERSION, render_prompt, system_prompt_digest
from .reasoner import LocalReasoner, ReasonerCapabilities, ReasonerUnavailable

MAX_CONSECUTIVE_FAILURES = 2


@dataclass
class ModelDiscovery:
    status: str  # AVAILABLE | PARTIAL | UNAVAILABLE
    reason: str
    capabilities: ReasonerCapabilities | None
    functions: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    packets: dict[str, Any] = field(default_factory=dict)  # symbol -> BuiltPacket
    model_ms: float = 0.0
    egress: dict[str, Any] | None = None  # destination disclosure (remote adapters)

    @property
    def source_kind(self) -> str:
        return "remote_model" if self.egress and self.egress.get("context_left_machine") else "local_model"

    def egress_summary(self) -> dict[str, Any]:
        """Data-egress disclosure for the whole run (also when nothing left the machine)."""
        sent = [f["egress"] for f in self.functions if f.get("egress")]
        e = self.egress or {}
        return {"provider": e.get("provider", "loopback"), "host": e.get("host"), "model_id": e.get("model_id"),
                "context_left_machine": bool(e.get("context_left_machine")) and bool(sent),
                "destination_is_loopback": not e.get("context_left_machine", False),
                "packets_sent": len(sent), "context_packet_bytes_total": sum(x["context_packet_bytes"] for x in sent),
                "request_bytes_total": sum(x.get("request_bytes") or 0 for x in sent),
                "packets": [{k: x.get(k) for k in ("context_packet_digest", "context_packet_bytes", "request_bytes", "request_sha256")} for x in sent],
                "warning": e.get("warning"), "api_key": "never stored, logged or reported (read from the environment variable at request time)"}

    def section(self, show_context: bool = False) -> dict[str, Any]:
        caps = self.capabilities
        out = {"requested": True, "status": self.status, "reason": self.reason,
               "capabilities": caps.to_json() if caps else None, "prompt_version": PROMPT_VERSION, "system_prompt_digest": system_prompt_digest(),
               "functions": self.functions, "model_ms": round(self.model_ms, 3), "source": self.source_kind,
               "data_egress": self.egress_summary(),
               "authority_note": "model candidates are proposals only: INFERRED at most, never approved, never evidence or proof"}
        if show_context:
            out["context_packets"] = {s: bp.packet for s, bp in self.packets.items()}
        return out


def make_reasoner(sem: dict[str, Any]) -> LocalReasoner:
    """The configured adapter: Ollama over loopback (default), or a bring-your-own-key provider
    (semantic/remote.py). There is never a fallback from one to the other."""
    if sem.get("provider", "loopback") != "loopback":
        from .remote import make_remote_reasoner

        return make_remote_reasoner(sem)
    from .ollama import OllamaReasoner

    ep = validate_loopback_endpoint(sem.get("endpoint"))
    return OllamaReasoner(ep, sem["model_id"], pinned_digest=sem.get("model_digest") or None, timeout_s=float(sem.get("request_timeout_s", 600)),
                          options={"temperature": sem.get("temperature", 0.0), "seed": sem.get("seed", 0), "num_ctx": sem.get("num_ctx", 8192),
                                   "num_predict": sem.get("num_predict", 1024)},
                          keep_alive=str(sem.get("keep_alive", "10m")))


def run_model_discovery(reasoner: LocalReasoner, infos: list[TargetInfo], builder: ContextPacketBuilder, *, created_at: str,
                        sem: dict[str, Any], existing_ids: dict[str, list[str]] | None = None, store: Any = None, retain_raw: bool = False,
                        log: list[str] | None = None) -> ModelDiscovery:
    log = log if log is not None else []
    try:
        caps = reasoner.capabilities()
    except (EndpointError, ReasonerUnavailable) as exc:  # adapters normally report, not raise
        return ModelDiscovery("UNAVAILABLE", str(exc), None)
    if not caps.available:
        return ModelDiscovery("UNAVAILABLE", caps.reason, caps)
    assert caps.identity is not None
    md = ModelDiscovery("AVAILABLE", caps.reason, caps)
    eprof = getattr(reasoner, "egress_profile", None)
    md.egress = eprof() if callable(eprof) else None
    budget = ContextBudget(int(sem.get("max_context_bytes", 12000)), int(sem.get("max_context_tokens", 4000)))
    max_c = int(sem.get("max_candidates", 3))
    failures = 0
    attempted = ok = 0
    for info in infos:
        f: dict[str, Any] = {"symbol_id": info.symbol_id, "input_profile": info.profile}
        md.functions.append(f)
        if failures >= MAX_CONSECUTIVE_FAILURES:
            f.update(status="NOT_ATTEMPTED", reason=f"stopped after {failures} consecutive endpoint failures")
            continue
        try:
            bp = builder.build(info.symbol_id, info.profile, budget=budget, existing_contract_ids=(existing_ids or {}).get(info.symbol_id, []))
        except ContextError as exc:
            f.update(status="SKIPPED", reason=f"{exc.code}: {exc.message}")
            continue
        md.packets[info.symbol_id] = bp
        prompt = render_prompt(bp, max_c)
        f.update(packet_digest=bp.digest, prompt_digest=prompt.digest, context_bytes=bp.bytes_used, snippets=bp.roles, omitted=bp.omitted)
        attempted += 1
        t0 = time.perf_counter()
        try:
            resp = reasoner.propose_contracts(prompt)
        except (ReasonerUnavailable, EndpointError) as exc:
            failures += 1
            md.model_ms += (time.perf_counter() - t0) * 1000
            f.update(status="FAILED", reason=str(exc))
            if md.egress is not None and getattr(exc, "code", "") not in ("REQUEST_TOO_LARGE", "API_KEY_MISSING", "API_KEY_INVALID", "UNREACHABLE",
                                                                          "UNAVAILABLE", "REQUEST_NOT_ALLOWED"):
                # The request may have reached the provider before it failed: disclose it as sent.
                f["egress"] = {**{k: md.egress[k] for k in ("provider", "host", "model_id", "context_left_machine")},
                               "context_packet_digest": bp.digest, "context_packet_bytes": len(canonical_json(bp.packet))}
            log.append(f"model: {info.symbol_id}: {exc}")
            continue
        failures = 0
        md.model_ms += resp.elapsed_ms
        egress = None
        if md.egress is not None:
            egress = {k: md.egress[k] for k in ("provider", "host", "model_id", "context_left_machine")}
            egress.update(context_packet_digest=bp.digest, context_packet_bytes=len(canonical_json(bp.packet)))
            if resp.request_bytes is not None:
                egress.update(request_bytes=resp.request_bytes, request_sha256=resp.request_sha256)
            f["egress"] = egress
        props, stats = proposals_from_response(resp.text, bp, caps.identity, created_at, max_c, ProposerSource.from_identity(caps.identity, egress))
        f.update(status="ANSWERED", elapsed_ms=resp.elapsed_ms, prompt_tokens=resp.prompt_tokens, output_tokens=resp.output_tokens,
                 done_reason=resp.done_reason, runtime_timing_ms=resp.runtime_timing_ms, request_options=resp.request_options,
                 response_sha256="sha256:" + hashlib.sha256(resp.text.encode("utf-8", "replace")).hexdigest(),
                 response_rejection=stats["response_rejection"], raw_candidates=stats["raw_candidates"])
        if retain_raw and store is not None:
            f["raw_context_artifact"] = store.put_json({"kind": "model_context", "packet": bp.packet, "prompt_version": prompt.prompt_version,
                                                        "system": prompt.system, "user": prompt.user, "response_schema": prompt.response_schema,
                                                        "response": resp.text, "model_identity": caps.identity.to_json()}, "model_context")
        for pr in props:
            pr.extra["packet"] = bp
            pr.extra["function_index"] = len(md.functions) - 1
        md.proposals.extend(props)
        ok += 1
    wanted = sum(1 for f in md.functions if f["status"] in ("ANSWERED", "FAILED", "NOT_ATTEMPTED"))
    if ok < wanted:
        md.status, md.reason = ("PARTIAL" if ok else "UNAVAILABLE"), f"{ok} of {wanted} model request(s) answered ({attempted} attempted)"
    return md


__all__ = ["ModelDiscovery", "make_reasoner", "run_model_discovery"]
