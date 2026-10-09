"""Strict parsing of model output into contract proposals (M4 T40/T43, docs/08 "Candidate response").

Model text is untrusted. It is decoded as JSON with limits, then every candidate is
checked field by field *before* it joins the shared mechanical pipeline
(``contracts.templates.validate_proposals``: revision validation + non-vacuity), exactly
like a template candidate. This stage adds only the checks that are specific to model
output:

* MALFORMED_JSON / MALFORMED_RESPONSE: not one JSON object ``{"candidates": [...]}``
  (size, nesting and non-finite constants are limited);
* TOO_MANY_CANDIDATES: candidates beyond the requested maximum are dropped;
* MALFORMED_CANDIDATE / FORBIDDEN_FIELD: wrong types, or fields outside the response
  schema such as ``authority``, ``approved``, ``proof`` or ``evidence`` (a model cannot set
  authority, lifecycle or evidence: every accepted candidate is INFERRED);
* UNTYPED_TEXT: a string where a DSL node is expected, or string data in any node field
  other than ``kind``/``name``/``comparison`` (Python, SMT or prose is never evaluated);
* PROFILE_MISMATCH: an input profile other than the packet's;
* INVALID_SOURCE_REF: a source reference outside the snippets actually sent.

Boundary inputs are plain values; each must give exactly the target's parameters and
belong to the input profile, otherwise it is dropped (reported) and the candidate stays.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..contracts.dsl import Rejection
from ..contracts.model import _example_value
from ..contracts.profiles import PROFILES, check_value
from ..contracts.templates import Proposal, _cid
from ..models.spans import canonical_json, short_hex
from .context import BuiltPacket
from .prompt import PROMPT_VERSION
from .reasoner import ModelIdentity

MAX_RESPONSE_CHARS = 256 * 1024
MAX_BOUNDARY_INPUTS = 4
CANDIDATE_FIELDS = frozenset({"precondition", "predicate", "input_profile", "source_ref", "rationale", "uncertainty", "unsupported_dependencies",
                              "boundary_inputs"})
REQUIRED_FIELDS = frozenset({"precondition", "predicate", "input_profile", "source_ref"})
TEXT_KEYS = frozenset({"kind", "name", "comparison"})
MAX_WALK = 4096


@dataclass
class ProposerSource:
    """Who proposed a batch of candidates. It changes provenance and wording only, never checks.

    kind: ``local_model`` (a model on a loopback destination), ``remote_model`` (a model whose
    context packet left the machine), or ``agent_proposed`` (the calling coding agent).
    """

    kind: str
    label: str  # human description used in provenance references and assumptions
    reference_prefix: str
    id_tag: str  # contract-id segment: "model" | "agent"
    client: dict[str, str] | None = None
    egress: dict[str, Any] | None = None
    extra_assumptions: list[str] = field(default_factory=list)

    @classmethod
    def from_identity(cls, identity: ModelIdentity, egress: dict[str, Any] | None = None) -> "ProposerSource":
        remote = bool(egress and egress.get("context_left_machine"))
        kind = "remote_model" if remote else "local_model"
        label = f"{'remote' if remote else 'local'} model {identity.artifact_id} ({identity.revision[:19]}...)"
        notes = []
        if remote:
            notes.append(f"the context packet was sent to {egress['host']} ({egress['provider']}); it left this machine")
        return cls(kind, label, f"{identity.artifact_id}@{identity.revision}", "model", None, egress, notes)

    @classmethod
    def agent(cls, client: dict[str, str]) -> "ProposerSource":
        who = f"{client['name']} {client['version']}".strip()
        return cls("agent_proposed", f"agent {who} (self-reported client identity)", f"agent {who}", "agent", dict(client), None, [])

    def provenance(self, reference: str) -> dict[str, Any]:
        item: dict[str, Any] = {"kind": self.kind, "reference": reference[:1000]}
        if self.client is not None:
            item["client"] = dict(self.client)
        if self.egress is not None:
            item["egress"] = dict(self.egress)
        return item


class OutputRejection(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _no_constants(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name}")


def parse_response(text: Any) -> list[Any]:
    """The raw candidate list, or OutputRejection for the whole response."""
    if type(text) is not str:
        raise OutputRejection("MALFORMED_JSON", "the model returned no text")
    if len(text) > MAX_RESPONSE_CHARS:
        raise OutputRejection("MALFORMED_JSON", f"response exceeds {MAX_RESPONSE_CHARS} characters")
    try:
        doc = json.loads(text, parse_constant=_no_constants)
    except RecursionError:
        raise OutputRejection("MALFORMED_JSON", "response JSON is nested too deeply") from None
    except ValueError as exc:
        raise OutputRejection("MALFORMED_JSON", f"response is not valid JSON: {str(exc)[:200]}") from None
    if type(doc) is not dict or set(doc) != {"candidates"} or type(doc["candidates"]) is not list:
        raise OutputRejection("MALFORMED_RESPONSE", 'the response must be exactly one object {"candidates": [...]}')
    return doc["candidates"]


def _untyped_text(node: Any, path: str) -> str | None:
    """Path of the first free-text value in a DSL tree (iterative; bounded)."""
    if type(node) is str:
        return path
    stack = [(node, path)]
    seen = 0
    while stack:
        n, p = stack.pop()
        seen += 1
        if seen > MAX_WALK:
            return None  # the DSL validator enforces node budgets
        if isinstance(n, dict):
            for k, v in n.items():
                if type(v) is str and k not in TEXT_KEYS:
                    return f"{p}/{k}"
                if isinstance(v, (dict, list)):
                    stack.append((v, f"{p}/{k}"))
        elif isinstance(n, list):
            for i, v in enumerate(n):
                if type(v) is str:
                    return f"{p}/{i}"
                if isinstance(v, (dict, list)):
                    stack.append((v, f"{p}/{i}"))
    return None


def _source_span(bp: BuiltPacket, ref: Any) -> dict[str, Any]:
    if type(ref) is not dict or set(ref) != {"snippet", "start_line", "end_line"} or not all(type(ref[k]) is int for k in ref):
        raise OutputRejection("INVALID_SOURCE_REF", 'source_ref must be {"snippet": int, "start_line": int, "end_line": int}')
    i, a, b = ref["snippet"], ref["start_line"], ref["end_line"]
    if not 0 <= i < len(bp.snippets):
        raise OutputRejection("INVALID_SOURCE_REF", f"snippet {i} was not sent (0..{len(bp.snippets) - 1})")
    sp = bp.snippets[i].span
    if not sp.start_line <= a <= b <= sp.end_line:
        raise OutputRejection("INVALID_SOURCE_REF", f"lines {a}-{b} are outside snippet {i} (lines {sp.start_line}-{sp.end_line} of {sp.path})")
    li = bp.line_index[sp.path]
    start_col = sp.start_column if a == sp.start_line else 0
    if b == sp.end_line:
        end_col = sp.end_column
    else:
        nxt = li.line_starts[b] if b < len(li.line_starts) else len(li.raw)
        end_col = len(li.raw[li.offset(b, 0):nxt].rstrip(b"\r\n"))
    return li.span(sp.path, sp.snapshot_id, a, start_col, b, end_col).to_json()


def _boundary(bp: BuiltPacket, raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if raw is None:
        return [], []
    if type(raw) is not list:
        return [], ["boundary_inputs is not a list (ignored)"]
    prof = PROFILES[bp.input_profile]
    kept, dropped = [], []
    for i, ex in enumerate(raw[:16]):
        if type(ex) is not dict or set(ex) != set(bp.params):
            dropped.append(f"boundary_inputs/{i}: must give exactly the parameters {list(bp.params)}")
            continue
        try:
            vals = {k: _example_value(v, f"boundary_inputs/{i}/{k}") for k, v in ex.items()}
        except Rejection as exc:
            dropped.append(f"boundary_inputs/{i}: {exc.message}")
            continue
        why = next((f"{k}: {w}" for k, v in vals.items() if (w := check_value(prof, v)) is not None), None)
        if why:
            dropped.append(f"boundary_inputs/{i}: outside input profile {prof.name}: {why}")
            continue
        if len(kept) < MAX_BOUNDARY_INPUTS and vals not in kept:
            kept.append(vals)
    if len(raw) > 16:
        dropped.append(f"{len(raw) - 16} boundary input(s) beyond 16 ignored")
    return kept, dropped


def _text(v: Any, limit: int) -> str:
    return v[:limit] if type(v) is str else ""


def proposals_from_response(text: str, bp: BuiltPacket, identity: ModelIdentity | None, created_at: str, max_candidates: int,
                            source: ProposerSource | None = None) -> tuple[list[Proposal], dict[str, Any]]:
    """Map one proposer response (model text, or an agent's candidate list serialized as
    JSON) to proposals. Rejected candidates are returned as proposals with status
    ``rejected`` (so they are counted and reported); nothing is trusted. ``source`` only
    selects provenance and wording; every source gets exactly the same checks."""
    if source is None:
        assert identity is not None
        source = ProposerSource.from_identity(identity)
    kind = source.kind
    stats: dict[str, Any] = {"response_rejection": None, "raw_candidates": 0}
    try:
        raw = parse_response(text)
    except OutputRejection as exc:
        stats["response_rejection"] = {"code": exc.code, "message": exc.message}
        return [], stats
    stats["raw_candidates"] = len(raw)
    target = bp.target
    sym = target.symbol_id
    out: list[Proposal] = []
    seen_ids: set[str] = set()
    for i, c in enumerate(raw):
        def rejected(code: str, msg: str, doc: dict[str, Any] | None = None) -> None:
            pr = Proposal(doc or {"contract_id": f"{source.id_tag}-candidate-{i}", "scope": sym, "precondition": None, "predicate": None,
                                  "input_profile": bp.input_profile, "provenance": [], "examples": []},
                          kind, "", [], target.def_span.to_json(), status="rejected", reason=f"{code}: {msg}")
            pr.extra.update(rejection_code=code, index=i, source=kind)
            out.append(pr)

        if i >= max_candidates:
            rejected("TOO_MANY_CANDIDATES", f"only {max_candidates} candidates were requested")
            continue
        if type(c) is not dict:
            rejected("MALFORMED_CANDIDATE", "a candidate must be a JSON object")
            continue
        extra = set(c) - CANDIDATE_FIELDS
        if extra:
            rejected("FORBIDDEN_FIELD", f"field(s) {sorted(str(x)[:40] for x in extra)[:8]} are not part of a candidate (a proposer cannot set "
                                        "authority, lifecycle, provenance or evidence)")
            continue
        missing = REQUIRED_FIELDS - set(c)
        if missing:
            rejected("MALFORMED_CANDIDATE", f"missing field(s) {sorted(missing)}")
            continue
        bad = next((p for p in (_untyped_text(c["precondition"], "precondition"), _untyped_text(c["predicate"], "predicate")) if p), None)
        if bad:
            rejected("UNTYPED_TEXT", f"free text at {bad}: only typed DSL nodes are accepted (Python, SMT or prose is never evaluated)")
            continue
        if c["input_profile"] != bp.input_profile:
            rejected("PROFILE_MISMATCH", f"input_profile {str(c['input_profile'])[:80]!r} differs from the packet's {bp.input_profile}")
            continue
        try:
            span = _source_span(bp, c["source_ref"])
        except OutputRejection as exc:
            rejected(exc.code, exc.message)
            continue
        for key, typ in (("rationale", str), ("uncertainty", str), ("unsupported_dependencies", list), ("boundary_inputs", list)):
            if key in c and type(c[key]) is not typ:
                rejected("MALFORMED_CANDIDATE", f"{key} must be a {typ.__name__}")
                break
        else:
            deps = [d[:120] for d in c.get("unsupported_dependencies", []) if type(d) is str][:8]
            boundary, dropped = _boundary(bp, c.get("boundary_inputs"))
            key = short_hex([c["precondition"], c["predicate"], c["input_profile"]], 10)
            cid = _cid(target.module, target.function, source.id_tag, key)
            rationale = _text(c.get("rationale"), 300).strip()
            what = {"local_model": "local model", "remote_model": "remote model", "agent_proposed": "agent"}[kind]
            doc = {"schema_version": "1.0", "contract_id": cid, "revision": 1, "scope": sym, "authority": "INFERRED", "lifecycle_state": "VALIDATED",
                   "precondition": c["precondition"], "predicate": c["predicate"], "input_profile": bp.input_profile,
                   "provenance": [source.provenance(f"{source.reference_prefix} {PROMPT_VERSION} packet {bp.digest} candidate {i}")],
                   "supersedes": None, "reason": f"{what} proposal (its rationale is not evidence): " + (rationale or "no rationale given"),
                   "created_at": created_at}
            if boundary:
                doc["examples"] = boundary
            if cid in seen_ids:
                rejected("DUPLICATE", "same precondition, predicate and profile as an earlier candidate in this response", doc)
                continue
            seen_ids.add(cid)
            assumptions = [f"proposed by {source.label}; a proposal is not evidence", "INFERRED candidate: not approved intent",
                           *source.extra_assumptions]
            unc = _text(c.get("uncertainty"), 200).strip()
            if unc:
                assumptions.append(f"model-stated uncertainty: {unc}")
            if deps:
                assumptions.append("model-named unsupported dependencies: " + "; ".join(deps))
            if bp.packet["unsupported_constructs"]:
                assumptions.append("verifier-detected unsupported constructs: " + "; ".join(bp.packet["unsupported_constructs"][:4]))
            pr = Proposal(doc, kind, rationale or "no rationale given", assumptions, span)
            pr.extra.update(source=kind, index=i, uncertainty=unc or "not stated", unsupported_dependencies=deps, boundary_inputs=boundary,
                            boundary_dropped=dropped, source_ref=c["source_ref"])
            out.append(pr)
    return out, stats


def candidate_key(doc: dict[str, Any]) -> str:
    """Scope + canonical semantics, for comparing model and template candidates."""
    return canonical_json([doc.get("scope"), doc.get("precondition"), doc.get("predicate"), doc.get("input_profile")]).decode()


__all__ = ["parse_response", "proposals_from_response", "OutputRejection", "candidate_key", "CANDIDATE_FIELDS", "ProposerSource"]
