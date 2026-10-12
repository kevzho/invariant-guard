"""Contract revisions and their validation (docs/06 "Candidate validation").

A *revision* is an immutable JSON document (``contract_revision.schema.json``). Its
*semantic digest* covers exactly what evidence depends on: scope, canonical precondition
and predicate, input profile, examples and the DSL/profile versions. Changing any of them
is a new revision whose evidence starts empty (docs/06 "Contract changes and stale
evidence"); authority, reasons and timestamps are not part of it.

``validate_revision`` performs every docs/06 check without executing anything:
schema version and fields, known target symbol (AST only), parameter coverage by the
input profile, DSL typing and budgets, a nonvacuous predicate (it must observe behavior),
and a nonempty admitted domain: a bounded deterministic search must find at least one
input that satisfies profile and precondition, otherwise the contract is
INVALID_CONTRACT (an unsatisfiable precondition is never a proof of success).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..models.spans import canonical_json, digest_json
from .dsl import DSL_VERSION, EvalEnv, Rejection, canonicalize, check_expression, evaluate
from .profiles import PROFILE_VERSION, PROFILES, InputProfile, admit_inputs, check_value, domain_hints
from .targets import Target, TargetError, resolve_target

CONTRACT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
SYMBOL_RE = re.compile(r"^[^\s:][^:]*::[^\s].*$")
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$")
MAX_CONTRACT_BYTES = 32 * 1024
MAX_EXAMPLES = 16
WITNESS_SEARCH_LIMIT = 256
AUTHORITIES = ("DECLARED", "APPROVED", "INFERRED", "REJECTED", "RETIRED")
LIFECYCLE = ("CANDIDATE", "VALIDATED", "CHECKED", "REVIEWED", "REJECTED", "INVALID_CONTRACT")
PROVENANCE_KINDS = ("maintainer_approval", "declaration", "template", "local_model", "test", "docstring", "assertion", "remote_model", "agent_proposed")
# Optional structured provenance fields (additive within schema 1.0): ``client`` for an
# agent proposal (self-reported name/version), ``egress`` for a proposal whose context
# packet was sent to a proposer endpoint (remote_model; see semantic/remote.py).
PROVENANCE_OPTIONAL = {"client": ("agent_proposed",), "egress": ("remote_model", "local_model")}
EGRESS_FIELDS = {"provider", "host", "model_id", "context_left_machine", "context_packet_digest", "context_packet_bytes", "request_bytes", "request_sha256"}
_FIELDS = {"schema_version", "contract_id", "revision", "scope", "authority", "lifecycle_state", "precondition", "predicate", "input_profile",
           "provenance", "supersedes", "reason", "created_at", "examples"}
_REQUIRED = _FIELDS - {"examples"}


@dataclass
class TypedContract:
    """A validated revision bound to a concrete target in one snapshot."""

    doc: dict[str, Any]
    contract_id: str
    revision: int
    scope: str
    authority: str
    input_profile: InputProfile
    precondition: dict[str, Any]  # canonical
    predicate: dict[str, Any]  # canonical
    examples: list[dict[str, Any]]
    semantic_digest: str
    target: Target
    repeat: dict[str, Any] | None
    references: set[str]
    admissible_witness: dict[str, Any] | None = None
    search: dict[str, int] = field(default_factory=dict)

    def precondition_holds(self, args: dict[str, Any]) -> tuple[bool | None, str]:
        r = evaluate(self.precondition, EvalEnv(args=args))
        return ({"TRUE": True, "FALSE": False}.get(r.status), r.reason)

    @property
    def domain_text(self) -> str:
        return f"inputs of profile {self.input_profile.name} that satisfy the contract precondition"


def semantic_digest(scope: str, precondition: dict[str, Any], predicate: dict[str, Any], input_profile: str, examples: list[Any]) -> str:
    return digest_json({"dsl": DSL_VERSION, "profiles": PROFILE_VERSION, "scope": scope, "precondition": canonicalize(precondition),
                        "predicate": canonicalize(predicate),
                        "input_profile": input_profile, "examples": examples})


def _reject(code: str, path: str, msg: str) -> Rejection:
    return Rejection(code, path, msg)


def check_document_shape(doc: Any) -> None:
    """Field-level checks equivalent to the JSON Schema (the core has no jsonschema dependency)."""
    if type(doc) is not dict:
        raise _reject("MALFORMED_CONTRACT", "", "a contract revision must be a JSON object")
    try:
        size = len(canonical_json(doc))
    except RecursionError:  # CPython 3.11's json encoder recurses on the Python stack; hostile nesting must not crash
        raise _reject("OVERSIZED", "", "contract revision is nested too deeply to serialize") from None
    if size > MAX_CONTRACT_BYTES:
        raise _reject("OVERSIZED", "", f"contract revision exceeds {MAX_CONTRACT_BYTES} bytes")
    extra, missing = set(doc) - _FIELDS, _REQUIRED - set(doc)
    if extra:
        raise _reject("MALFORMED_CONTRACT", "", f"unknown field(s) {sorted(extra)}")
    if missing:
        raise _reject("MALFORMED_CONTRACT", "", f"missing field(s) {sorted(missing)}")
    if doc["schema_version"] != "1.0":
        raise _reject("SCHEMA_VERSION", "schema_version", f"unsupported schema_version {doc['schema_version']!r} (expected '1.0')")
    if type(doc["contract_id"]) is not str or not CONTRACT_ID_RE.match(doc["contract_id"]):
        raise _reject("MALFORMED_CONTRACT", "contract_id", "contract_id must match [A-Za-z0-9_.-]{1,96}")
    if type(doc["revision"]) is not int or doc["revision"] < 1:
        raise _reject("MALFORMED_CONTRACT", "revision", "revision must be an integer >= 1")
    if type(doc["scope"]) is not str or not SYMBOL_RE.match(doc["scope"]):
        raise _reject("MALFORMED_CONTRACT", "scope", "scope must be a symbol id 'path/to/mod.py::function'")
    if doc["authority"] not in AUTHORITIES:
        raise _reject("MALFORMED_CONTRACT", "authority", f"authority must be one of {AUTHORITIES}")
    if doc["lifecycle_state"] not in LIFECYCLE:
        raise _reject("MALFORMED_CONTRACT", "lifecycle_state", f"lifecycle_state must be one of {LIFECYCLE}")
    if type(doc["input_profile"]) is not str or not doc["input_profile"]:
        raise _reject("MISSING_DOMAIN", "input_profile", "an input profile is required (no contract without a domain)")
    prov = doc["provenance"]
    if type(prov) is not list or not prov:
        raise _reject("MISSING_PROVENANCE", "provenance", "at least one provenance entry is required")
    for i, p in enumerate(prov):
        if (type(p) is not dict or not {"kind", "reference"} <= set(p) or p["kind"] not in PROVENANCE_KINDS or type(p["reference"]) is not str
                or not p["reference"]):
            raise _reject("MISSING_PROVENANCE", f"provenance/{i}", f"provenance entries are {{kind, reference}} with kind in {PROVENANCE_KINDS}")
        for k in set(p) - {"kind", "reference"}:
            if k not in PROVENANCE_OPTIONAL or p["kind"] not in PROVENANCE_OPTIONAL[k]:
                raise _reject("MISSING_PROVENANCE", f"provenance/{i}/{k}", f"field {k!r} is not allowed in a {p['kind']} provenance entry")
        cl = p.get("client")
        if cl is not None and (type(cl) is not dict or set(cl) != {"name", "version"} or not all(type(v) is str for v in cl.values())
                               or not 1 <= len(cl["name"]) <= 100 or len(cl["version"]) > 100):
            raise _reject("MISSING_PROVENANCE", f"provenance/{i}/client", "client must be {name, version} (strings, at most 100 characters)")
        eg = p.get("egress")
        if eg is not None and (type(eg) is not dict or not set(eg) <= EGRESS_FIELDS or type(eg.get("context_left_machine")) is not bool
                               or not all(type(eg.get(k)) is str and eg.get(k) for k in ("provider", "host", "model_id", "context_packet_digest"))):
            raise _reject("MISSING_PROVENANCE", f"provenance/{i}/egress", "egress must be a data-egress disclosure object")
    if doc["supersedes"] is not None and (type(doc["supersedes"]) is not int or doc["supersedes"] < 1):
        raise _reject("MALFORMED_CONTRACT", "supersedes", "supersedes must be null or a revision number")
    if type(doc["reason"]) is not str or not doc["reason"].strip():
        raise _reject("MALFORMED_CONTRACT", "reason", "a non-empty reason is required")
    if type(doc["created_at"]) is not str or not TIMESTAMP_RE.match(doc["created_at"]):
        raise _reject("MALFORMED_CONTRACT", "created_at", "created_at must be an RFC 3339 UTC timestamp")
    ex = doc.get("examples", [])
    if type(ex) is not list or len(ex) > MAX_EXAMPLES:
        raise _reject("MALFORMED_CONTRACT", "examples", f"examples must be a list of at most {MAX_EXAMPLES} input objects")


def check_schema(doc: dict[str, Any]) -> None:
    """Second, independent check against contract_revision.schema.json when jsonschema is installed."""
    try:
        from ..models.schema import SchemaValidationUnavailable, validation_errors

        errs = validation_errors("contract_revision", doc)
        if errs:
            raise _reject("SCHEMA", "", "; ".join(errs[:3]))
    except SchemaValidationUnavailable:
        pass


def _example_value(v: Any, path: str) -> Any:
    """Examples are plain JSON: numbers, booleans, null and lists of them."""
    if v is None or type(v) in (bool, int, float):
        if type(v) is float and v != v:
            raise _reject("MALFORMED_CONTRACT", path, "example values must be finite")
        return v
    if type(v) is list and len(v) <= 64:
        return [_example_value(x, f"{path}/{i}") for i, x in enumerate(v)]
    raise _reject("MALFORMED_CONTRACT", path, "example values are numbers, booleans, null or lists of them (at most 64 items)")


def validate_revision(doc: Any, files: dict[str, bytes], snapshot_id: str, *, python_path: list[str] | None = None,
                      search_limit: int = WITNESS_SEARCH_LIMIT) -> TypedContract:
    """Validate a revision against a snapshot. Raises :class:`Rejection`."""
    check_document_shape(doc)
    profile = PROFILES.get(doc["input_profile"])
    if profile is None:
        raise _reject("UNKNOWN_PROFILE", "input_profile", f"unknown input profile {doc['input_profile']!r}; known: {sorted(PROFILES)}")
    try:
        target = resolve_target(doc["scope"], files, snapshot_id, python_path)
    except TargetError as exc:
        raise _reject(exc.code, "scope", exc.message) from None
    params = {p: profile.dsl_type for p in target.params}
    if not params:
        raise _reject("UNSUPPORTED_TARGET", "scope", "the target has no parameters; a bounded input profile cannot describe its domain")
    pre = check_expression(doc["precondition"], params, "precondition", "precondition")
    post = check_expression(doc["predicate"], params, "predicate", "predicate")
    if not post.references:
        raise _reject("VACUOUS", "predicate", "the predicate never observes behavior (no result, after_arg, unchanged_argument or repeatability); "
                      "it is decided by the inputs alone")
    examples = []
    for i, ex in enumerate(doc.get("examples", [])):
        if type(ex) is not dict or set(ex) != set(target.params):
            raise _reject("MALFORMED_CONTRACT", f"examples/{i}", f"an example must give exactly the parameters {list(target.params)}")
        ex = {k: _example_value(v, f"examples/{i}/{k}") for k, v in ex.items()}
        for k, v in ex.items():
            why = check_value(profile, v)
            if why is not None:
                raise _reject("MALFORMED_CONTRACT", f"examples/{i}/{k}", f"example is outside input profile {profile.name}: {why}")
        examples.append(ex)
    check_schema(doc)
    digest = semantic_digest(doc["scope"], pre.canonical, post.canonical, profile.name, examples)
    tc = TypedContract(doc=doc, contract_id=doc["contract_id"], revision=doc["revision"], scope=doc["scope"], authority=doc["authority"],
                       input_profile=profile, precondition=pre.canonical, predicate=post.canonical, examples=examples, semantic_digest=digest,
                       target=target, repeat=post.repeat, references=post.references)
    seed = int(digest.split(":", 1)[1][:15], 16)
    adm = admit_inputs(profile, list(target.params), tc.precondition_holds, domain_hints(pre.canonical, list(target.params)), seed=seed,
                       limit=1, examples=examples, max_attempts=search_limit)
    tc.search = {"attempts": adm.attempts, "admitted": len(adm.inputs), "excluded": len(adm.excluded)}
    if not adm.inputs:
        raise _reject("INVALID_CONTRACT", "precondition",
                      f"no admissible input found: a bounded search of {adm.attempts} inputs of profile {profile.name} found none satisfying the "
                      "precondition (unsatisfiable or contradictory domain; this is never evidence that the contract holds)")
    tc.admissible_witness = adm.inputs[0][0]
    return tc


def load_document(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_CONTRACT_BYTES:
        raise _reject("OVERSIZED", "", f"contract file exceeds {MAX_CONTRACT_BYTES} bytes")
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=_no_constants)
    except RecursionError:
        raise _reject("BUDGET_EXCEEDED", "", "contract JSON is nested too deeply") from None
    except (UnicodeDecodeError, ValueError) as exc:
        raise _reject("MALFORMED_CONTRACT", "", f"not valid JSON: {exc}") from None
    return doc


def _no_constants(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name} is not allowed")


__all__ = ["TypedContract", "validate_revision", "check_document_shape", "semantic_digest", "load_document", "AUTHORITIES", "LIFECYCLE", "CONTRACT_ID_RE"]
