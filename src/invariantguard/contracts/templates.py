"""Deterministic contract templates (M3 T32, docs/06 "Discovery and falsification").

Templates read source text only (stdlib ``ast``; nothing is imported or executed) and
propose INFERRED candidates. Every candidate keeps its source (provenance kind and a
source span) and the assumptions it rests on; a template never approves anything and a
docstring never becomes DECLARED (docs/06: tests/docs are evidence about cases, not
authority).

Input profile: from parameter annotations (``list[float]``, ``list[int]``, ``int``,
``float``) or, for unannotated parameters, from the literal argument types of doctest
calls (``>>> f([2, 1])``; parsed with ``ast.literal_eval``, never run). Functions whose
domain cannot be established are skipped and reported.

Templates:

* ``purity``: ``unchanged_argument(p)`` for a list parameter that the body never mutates
  syntactically (no mutating method call, item/attribute assignment, ``del``, augmented
  assignment, aliasing, or passing it to a call other than a fixed set of
  non-mutating builtins). This is a syntactic indication only; rebinding of those
  builtins and unknown callees are assumptions stated with the candidate.
* ``length``: ``len(result) == len(p)`` when every ``return`` is a single-generator
  list comprehension over ``p`` without filters.
* ``assertion``: ``assert`` statements at the top of the body over parameters become the
  precondition; ``assert`` on the returned name immediately before ``return name``
  becomes a predicate (comparisons, ``and``, ``len``/``sum``, numeric literals).
* ``repeatability``: three calls on fresh copies of the same input give identical
  results, for functions with no ``global``/``nonlocal`` and no use of ``random``,
  ``time``, ``datetime``, ``uuid``, ``os`` or ``secrets``.

Doctest calls with literal arguments are attached as ``examples`` (probed first).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

from ..frontends.python import ParseFailure, parse_source
from ..models.spans import short_hex
from .dsl import Rejection
from .model import validate_revision
from .targets import TargetError, resolve_target
from .vacuity import check_nonvacuous

NON_MUTATING_BUILTINS = frozenset({"len", "sum", "sorted", "min", "max", "list", "tuple", "abs", "enumerate", "zip", "range", "iter", "any", "all",
                                   "isinstance", "round", "reversed", "set", "frozenset", "str", "repr", "map", "filter"})
MUTATING_METHODS = frozenset({"append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse", "__setitem__", "__delitem__", "__iadd__", "__imul__"})
NONDETERMINISM = frozenset({"random", "time", "datetime", "uuid", "os", "secrets"})
_ID_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass
class Proposal:
    doc: dict[str, Any]
    template: str
    rationale: str
    assumptions: list[str]
    source_span: dict[str, Any]
    status: str = "candidate"
    reason: str = ""
    semantic_digest: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _ann_profile(ann: str | None) -> str | None:
    if ann is None:
        return None
    a = ann.replace(" ", "")
    if a in ("list[float]", "list[int|float]", "list[float|int]"):
        return "bounded_builtin_numeric_list_v1"
    if a == "list[int]":
        return "bounded_builtin_int_list_v1"
    if a == "int":
        return "bounded_builtin_ints_v1"
    if a == "float":
        return "bounded_builtin_finite_floats_v1"
    return None


def _value_profile(v: Any) -> str | None:
    if type(v) is list:
        if v and all(type(x) is int for x in v):
            return "bounded_builtin_int_list_v1"
        if v and all(type(x) in (int, float) for x in v) and any(type(x) is float for x in v):
            return "bounded_builtin_numeric_list_v1"
        return None
    if type(v) is int:
        return "bounded_builtin_ints_v1"
    if type(v) is float:
        return "bounded_builtin_finite_floats_v1"
    return None


def doctest_examples(doc: str | None, name: str, params: tuple[str, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in (doc or "").splitlines():
        line = line.strip()
        if not line.startswith(">>> "):
            continue
        try:
            expr = ast.parse(line[4:], mode="eval").body
        except SyntaxError:
            continue
        if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == name and not expr.keywords and len(expr.args) == len(params)):
            continue
        try:
            vals = [ast.literal_eval(a) for a in expr.args]
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
        out.append(dict(zip(params, vals)))
    return out[:16]


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _mutation_free(fn: ast.FunctionDef, p: str) -> tuple[bool, str]:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == p:
                if f.attr in MUTATING_METHODS or not f.attr.startswith(("count", "index", "copy")):
                    return False, f"calls {p}.{f.attr}(...)"
            for a in list(n.args) + [k.value for k in n.keywords]:
                if isinstance(a, ast.Name) and a.id == p and not (isinstance(f, ast.Name) and f.id in NON_MUTATING_BUILTINS):
                    callee = f.id if isinstance(f, ast.Name) else ast.unparse(f)
                    return False, f"passes {p} to {callee}(), which may mutate it"
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]
            for t in targets:
                if isinstance(t, (ast.Subscript, ast.Attribute)) and p in _names_in(t.value):
                    return False, f"assigns into {p}"
                if isinstance(n, ast.AugAssign) and isinstance(t, ast.Name) and t.id == p:
                    return False, f"augmented assignment to {p} mutates a list in place"
            value = getattr(n, "value", None)
            if isinstance(value, ast.Name) and value.id == p:
                return False, f"aliases {p}"
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            return False, "uses global/nonlocal"
        if isinstance(n, (ast.For, ast.comprehension)) and isinstance(n.target, ast.Name) and n.target.id == p:
            return False, f"rebinds {p}"
    return True, "no syntactic mutation"


def _returns(fn: ast.FunctionDef) -> list[ast.Return]:
    out = []
    stack: list[ast.AST] = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Return):
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _dsl(node: ast.AST, params: set[str], result_name: str | None) -> dict[str, Any] | None:
    """Translate a small assertion subset into DSL (None if outside the subset)."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        args = [_dsl(v, params, result_name) for v in node.values]
        return None if any(a is None for a in args) else {"kind": "and", "args": args}
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = _dsl_term(node.left, params, result_name), _dsl_term(node.comparators[0], params, result_name)
        if left is None or right is None:
            return None
        op = node.ops[0]
        if isinstance(op, ast.Lt):
            return {"kind": "lt", "left": left, "right": right}
        if isinstance(op, ast.LtE):
            return {"kind": "le", "left": left, "right": right}
        if isinstance(op, ast.Gt):
            return {"kind": "gt", "left": left, "right": right}
        if isinstance(op, ast.GtE):
            return {"kind": "le", "left": right, "right": left}
        if isinstance(op, ast.Eq):
            return {"kind": "eq", "left": left, "right": right}
    if isinstance(node, ast.Compare) and len(node.ops) == 2 and all(isinstance(o, (ast.Lt, ast.LtE)) for o in node.ops):
        a = _dsl(ast.Compare(left=node.left, ops=[node.ops[0]], comparators=[node.comparators[0]]), params, result_name)
        b = _dsl(ast.Compare(left=node.comparators[0], ops=[node.ops[1]], comparators=[node.comparators[1]]), params, result_name)
        return None if a is None or b is None else {"kind": "and", "args": [a, b]}
    return None


def _dsl_term(node: ast.AST, params: set[str], result_name: str | None) -> dict[str, Any] | None:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float) and type(node.value) is not bool:
        return {"kind": "literal", "value": node.value}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) and type(node.operand.value) in (int, float):
        return {"kind": "literal", "value": -node.operand.value}
    if isinstance(node, ast.Name):
        if node.id == result_name:
            return {"kind": "result"}
        if node.id in params:
            return {"kind": "arg", "name": node.id}
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("len", "sum") and len(node.args) == 1 and not node.keywords:
        inner = _dsl_term(node.args[0], params, result_name)
        return None if inner is None else {"kind": node.func.id, "value": inner}
    return None


def _cid(*parts: str) -> str:
    return _ID_SAFE.sub("_", ".".join(parts))[:96]


@dataclass
class TargetInfo:
    symbol_id: str
    node: ast.FunctionDef
    target: Any  # targets.Target
    profile: str
    profile_source: str
    examples: list[dict[str, Any]]


def establish_profile(target: Any, examples: list[dict[str, Any]]) -> tuple[str | None, str]:
    """One input profile for every parameter, from annotations or doctest literal types (never guessed)."""
    params = target.params
    profs = {_ann_profile(target.annotations.get(p)) for p in params}
    source = "annotations"
    if None in profs:
        ex_profs = {_value_profile(v) for ex in examples for v in ex.values()}
        profs = ex_profs if examples else {None}
        source = "doctest examples"
    if len(profs) != 1 or None in profs:
        return None, source
    return next(iter(profs)), source


def target_functions(files: dict[str, bytes], snapshot_id: str, python_path: list[str] | None = None) -> tuple[list[TargetInfo], list[dict[str, Any]]]:
    """Module-level functions that can be contract targets, with their established profile
    (shared by template and model discovery, so both see the same targets)."""
    out: list[TargetInfo] = []
    skipped: list[dict[str, Any]] = []
    for path in sorted(files):
        try:
            parsed = parse_source(path, files[path])
        except ParseFailure as exc:
            skipped.append({"path": path, "reason": f"not parsed: {exc.message}"})
            continue
        for node in parsed.tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            sym = f"{path}::{node.name}"
            try:
                target = resolve_target(sym, files, snapshot_id, python_path)
            except TargetError as exc:
                skipped.append({"symbol_id": sym, "reason": exc.message})
                continue
            if not target.params:
                skipped.append({"symbol_id": sym, "reason": "no parameters: no bounded input domain to probe"})
                continue
            examples = doctest_examples(target.docstring, node.name, target.params)
            profile, source = establish_profile(target, examples)
            if profile is None:
                skipped.append({"symbol_id": sym, "reason": "no single input profile could be established from annotations or doctest examples"})
                continue
            out.append(TargetInfo(sym, node, target, profile, source, examples))
    return out, skipped


def discover(files: dict[str, bytes], snapshot_id: str, created_at: str, python_path: list[str] | None = None) -> tuple[list[Proposal], list[dict[str, Any]]]:
    proposals: list[Proposal] = []
    infos, skipped = target_functions(files, snapshot_id, python_path)
    for info in infos:
        node, target, sym, examples, profile, source = info.node, info.target, info.symbol_id, info.examples, info.profile, info.profile_source
        params = target.params
        span = target.def_span.to_json()
        pset = set(params)
        pre_conj, pre_refs = [], []
        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue
            if isinstance(stmt, ast.Assert):
                d = _dsl(stmt.test, pset, None)
                if d is not None:
                    pre_conj.append(d)
                    pre_refs.append(f"assert at line {stmt.lineno}")
                continue
            break
        pre = {"kind": "and", "args": pre_conj} if pre_conj else {"kind": "literal", "value": True}
        base_assumptions = [f"input profile {profile} inferred from {source}", "INFERRED candidate: not approved intent"]
        if pre_conj:
            base_assumptions.append("precondition taken from entry assertions (" + ", ".join(pre_refs) + "); assertions are disabled under python -O")

        def add(template: str, predicate: dict[str, Any], rationale: str, assumptions: list[str], prov: list[dict[str, str]], suffix: str = "") -> None:
            cid = _cid(target.module, node.name, template, *( [suffix] if suffix else []))
            doc = {"schema_version": "1.0", "contract_id": cid, "revision": 1, "scope": sym, "authority": "INFERRED", "lifecycle_state": "VALIDATED",
                   "precondition": pre, "predicate": predicate, "input_profile": profile,
                   "provenance": prov + ([{"kind": "assertion", "reference": f"{sym}: {r}"} for r in pre_refs])
                   + ([{"kind": "docstring", "reference": f"{sym}: {len(examples)} doctest input(s) used as examples"}] if examples else []),
                   "supersedes": None, "reason": rationale, "created_at": created_at}
            if examples:
                doc["examples"] = examples
            proposals.append(Proposal(doc, template, rationale, base_assumptions + assumptions, span))

        for p in params:
            if profile in ("bounded_builtin_numeric_list_v1", "bounded_builtin_int_list_v1"):
                ok, why = _mutation_free(node, p)
                if ok:
                    add("purity", {"kind": "unchanged_argument", "name": p},
                        f"{node.name} does not syntactically mutate list parameter {p}",
                        ["purity is inferred from syntax: the builtins it calls are assumed not rebound; aliasing through other objects is not analyzed"],
                        [{"kind": "template", "reference": f"purity:no-syntactic-mutation:{p}"}], p)
                else:
                    skipped.append({"symbol_id": sym, "template": "purity", "param": p, "reason": why})
        rets = _returns(node)
        for p in params:
            if rets and all(isinstance(r.value, ast.ListComp) and len(r.value.generators) == 1 and not r.value.generators[0].ifs
                            and not r.value.generators[0].is_async and isinstance(r.value.generators[0].iter, ast.Name)
                            and r.value.generators[0].iter.id == p for r in rets):
                add("length", {"kind": "eq", "left": {"kind": "len", "value": {"kind": "result"}}, "right": {"kind": "len", "value": {"kind": "arg", "name": p}}},
                    f"every return of {node.name} is a filter-free list comprehension over {p}", [],
                    [{"kind": "template", "reference": f"length:list-comprehension-over:{p}"}], p)
        body = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        for i, stmt in enumerate(body[:-1]):
            nxt = body[i + 1]
            if isinstance(stmt, ast.Assert) and isinstance(nxt, ast.Return) and isinstance(nxt.value, ast.Name):
                d = _dsl(stmt.test, pset, nxt.value.id)
                if d is not None and nxt.value.id not in pset:
                    add("assertion", d, f"assertion on the returned value before `return {nxt.value.id}`",
                        ["the assertion documents intended behavior; it is not evaluated under python -O"],
                        [{"kind": "assertion", "reference": f"{sym}: assert at line {stmt.lineno} before return"}], f"l{stmt.lineno}")
        names = _names_in(node)
        if not (names & NONDETERMINISM) and not any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(node)):
            add("repeatability", {"kind": "repeatability", "repeats": 3, "seed": 0, "comparison": "exact"},
                f"{node.name} uses no random/time/datetime/uuid/os/secrets names and no global state statements",
                ["module-level mutable state reached through calls or attributes is not analyzed"],
                [{"kind": "template", "reference": "repeatability:no-syntactic-nondeterminism"}])
    return proposals, skipped


def validate_proposals(proposals: list[Proposal], files: dict[str, bytes], snapshot_id: str, python_path: list[str] | None = None) -> None:
    """The mechanical candidate pipeline shared by templates and the local model (M4 T43):
    full revision validation (schema, symbols, typing, budgets, behavior reference,
    admissible precondition) and behavioral non-vacuity. A proposal's source never
    changes what is checked."""
    for pr in proposals:
        if pr.status == "rejected":  # rejected earlier (e.g. by the model-output parser); keep its reason
            continue
        try:
            tc = validate_revision(pr.doc, files, snapshot_id, python_path=python_path)
            pr.extra["nonvacuity"] = check_nonvacuous(tc)
        except Rejection as exc:
            pr.status, pr.reason = "rejected", f"{exc.code}: {exc.message}"
            pr.extra["rejection_code"] = exc.code
            continue
        pr.status, pr.semantic_digest = "validated", tc.semantic_digest
        pr.extra["typed"] = tc
        pr.extra["admissible_witness"] = {k: v for k, v in (tc.admissible_witness or {}).items()}


def candidate_batch(proposals: list[Proposal], snapshot_id: str, *, source: str = "template", model_identity: dict[str, Any] | None = None,
                    context_packet_digest: str | None = None, data_egress: dict[str, Any] | None = None,
                    proposer_client: dict[str, str] | None = None) -> dict[str, Any]:
    """A schema-valid CandidateBatch for the proposals that were not rejected."""
    cands = []
    for pr in proposals:
        if pr.status == "rejected" or pr.semantic_digest is None:
            continue
        cands.append({"candidate_id": "cand-" + short_hex([pr.doc["contract_id"], pr.semantic_digest], 16), "scope": pr.doc["scope"],
                      "precondition": pr.doc["precondition"], "predicate": pr.doc["predicate"], "input_profile": pr.doc["input_profile"],
                      "source_refs": [pr.source_span], "rationale": pr.rationale[:2000],
                      "uncertainty": str(pr.extra.get("uncertainty") or "; ".join(pr.assumptions))[:1000],
                      "unsupported_dependencies": list(pr.extra.get("unsupported_dependencies") or []),
                      "boundary_inputs": list(pr.extra.get("boundary_inputs") or [])})
    out = {"schema_version": "1.0", "batch_id": "batch-" + short_hex([snapshot_id, source, context_packet_digest, [c["candidate_id"] for c in cands]]),
           "snapshot_id": snapshot_id, "source": source, "model_identity": model_identity, "context_packet_digest": context_packet_digest,
           "candidates": cands[:64]}
    if data_egress is not None:
        out["data_egress"] = data_egress
    if proposer_client is not None:
        out["proposer_client"] = proposer_client
    return out


__all__ = ["discover", "validate_proposals", "candidate_batch", "Proposal", "doctest_examples", "establish_profile", "target_functions"]
