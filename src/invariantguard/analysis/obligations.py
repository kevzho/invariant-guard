"""Proof obligations: ``division.zero_denominator`` (M1), ``index.out_of_range`` and
``none.attribute_access`` (M2), plus their ``*.via_call`` reachable-call variants.

For every obligation site the checker decides, in order:

1. unsupported region/symbol             -> UNKNOWN
2. unreachable under interval/path facts   -> PROVED_SAFE
3. operand types not exact built-ins      -> UNKNOWN
4. cheap facts exclude the bad state      -> PROVED_SAFE
   (divisor excludes 0; index within [-len, len); receiver narrowed to non-None)
5. SMT (optional): UNSAT -> PROVED_SAFE; SAT -> witness re-validated by the IR evaluator
6. bounded concrete witness search (validated by the IR evaluator) -> PROVED_UNSAFE
7. otherwise                              -> UNKNOWN with the concrete reason

Scope and authority come from where each relevant parameter's domain was established.
Results that rely on a resolved module global (a call summary or a module constant)
state that resolution as an assumption; cross-module import resolution makes them
CONDITIONAL.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from ..ir.evaluator import UNMODELED, evaluate
from ..ir.model import (
    DIVISION_PROPERTY,
    EXACT_NON_NONE,
    INDEX_PROPERTY,
    NONE_PROPERTY,
    NUMERIC_EXACT,
    SEQUENCE_KINDS,
    DivisionSite,
    Effect,
    Function,
    Instruction,
    Kind,
    ObligationSite,
    Op,
    Origin,
)
from ..models.enums import AnalysisMethod, Authority, DomainSource, PropertyResult, ScopeValidity, SolverStatus
from .absint import IntervalAnalysis
from .intervals import AbsInt

PROPERTY_ID = DIVISION_PROPERTY
QUANTIFIER_SAFE = "for all admitted function inputs"
QUANTIFIER_UNSAFE = "exists admitted function input"
# Generator functions (I-135): calling one runs nothing, so a claim quantifies over consumers too.
QUANTIFIER_SAFE_GENERATOR = "for all admitted function inputs and every consumer of the generator (next/send/throw/close, any sent values)"
QUANTIFIER_UNSAFE_GENERATOR = "exists admitted function input such that consuming the generator with next() until exhaustion reaches the failure"

_CMP_TEXT = {"EQ": "==", "NE": "!=", "LT": "<", "LE": "<=", "GT": ">", "GE": ">="}
_NEG_TEXT = {"EQ": "!=", "NE": "==", "LT": ">=", "LE": ">", "GT": "<=", "GE": "<"}
_ARITH_TEXT = {Op.ADD: "+", Op.SUB: "-", Op.MUL: "*", Op.DIV: "/", Op.FLOORDIV: "//", Op.MOD: "%"}

WITNESS_SEARCH_VALUES = (0, 1, -1, 2, -2, 3, -3)
WITNESS_SEARCH_MAX = 4096
MODELED_PARAM_KINDS = NUMERIC_EXACT | SEQUENCE_KINDS | {Kind.OPTIONAL}
SEARCH_TRUNCATED = object()  # the bounded search stopped at the budget deadline (not a completed search)
_FAILURE = {DIVISION_PROPERTY: "ZeroDivisionError", INDEX_PROPERTY: "IndexError", NONE_PROPERTY: "AttributeError"}


class EngineInconsistency(Exception):
    """Internal disagreement between analysis components (an engine error)."""


@dataclass
class Budget:
    deadline: float  # time.monotonic() deadline for the static phase
    per_query_timeout_ms: int
    witness_search: bool = True

    def exhausted(self) -> bool:
        return time.monotonic() >= self.deadline

    def remaining_ms(self) -> int:
        return max(0, int((self.deadline - time.monotonic()) * 1000))


@dataclass
class ObligationOutcome:
    symbol_id: str
    site: ObligationSite
    result: PropertyResult
    method: AnalysisMethod
    scope: ScopeValidity | None
    authority: Authority
    domain_source: DomainSource
    domain: str
    assumptions: list[str]
    reasons: list[str]
    witness: dict[str, Any] | None = None
    witness_validated: bool = False
    path_constraints: list[str] = field(default_factory=list)
    solver_runs: list[dict[str, Any]] = field(default_factory=list)
    quantifier: str = ""
    runtime_mode: str | None = None
    divisor_fact: str | None = None
    # Immutable evidence record of a valid cache entry this result was reused from
    # (keeps its original run, snapshot and spans; docs/07 "remap current spans").
    reused_evidence: dict[str, Any] | None = None

    @property
    def property_id(self) -> str:
        return self.site.property_id


def obligation_id(symbol_id: str, ordinal: int, property_id: str = PROPERTY_ID) -> str:
    return f"{symbol_id}::{property_id}#{ordinal}"


def site_obligation_id(symbol_id: str, site: ObligationSite) -> str:
    return obligation_id(symbol_id, site.ordinal, site.property_id)


def failure_name(site: ObligationSite) -> str:
    return _FAILURE.get(site.base_property, "failure")


# ---------------------------------------------------------------------------------------
def unsupported_outcome(symbol_id: str, site: ObligationSite, reason: str) -> ObligationOutcome:
    return ObligationOutcome(
        symbol_id=symbol_id,
        site=site,
        result=PropertyResult.UNKNOWN,
        method=AnalysisMethod.UNSUPPORTED,
        scope=None,
        authority=Authority.INFERRED,
        domain_source=DomainSource.NONE,
        domain="not established (unsupported construct)",
        assumptions=[],
        reasons=[f"unsupported: {reason}"],
        quantifier="none (no claim)",
    )


def _describe_kind(t) -> str:
    k = t.kind
    if k is Kind.INT:
        return "exact Python built-in int"
    if k is Kind.BOOL:
        return "exact Python built-in bool"
    if k is Kind.FLOAT:
        return "Python built-in float"
    if k is Kind.LIST:
        return "exact Python built-in list (any length; elements not modeled)"
    if k is Kind.TUPLE:
        return "exact Python built-in tuple (any length; elements not modeled)"
    if k is Kind.OPTIONAL and t.inner is not None:
        return f"None or an exact Python built-in {t.inner.value}"
    return k.value


class FunctionChecker:
    def __init__(self, fn: Function, solver: Any | None, solver_status: SolverStatus, budget: Budget, declared_reason: str | None) -> None:
        self.fn = fn
        self.solver = solver
        self.solver_status = solver_status
        self.budget = budget
        self.declared_reason = declared_reason
        self.defs = fn.instr_by_result()
        self.def_block = fn.def_block()
        self.ai = IntervalAnalysis(fn).run()
        self._region_memo: dict[str, set[str]] = {}
        self._z3ctx: Any = None

    # -- helpers ---------------------------------------------------------------------------
    def kind(self, v: str) -> Kind:
        ins = self.defs.get(v)
        return ins.type.kind if ins is not None and ins.type is not None else Kind.OBJECT

    def backward_region(self, bid: str) -> set[str]:
        if bid in self._region_memo:
            return self._region_memo[bid]
        seen = {bid}
        stack = [bid]
        while stack:
            for p in self.fn.blocks[stack.pop()].preds:
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        self._region_memo[bid] = seen
        return seen

    def instr_index(self, bid: str, result: str) -> int:
        for i, ins in enumerate(self.fn.blocks[bid].instructions):
            if ins.result == result:
                return i
        raise EngineInconsistency(f"instruction {result} not in block {bid}")

    def prefix_instructions(self, site: ObligationSite) -> list[Instruction]:
        bid = site.block_id
        assert bid is not None and site.result_id is not None
        idx = self.instr_index(bid, site.result_id)
        out: list[Instruction] = []
        region = self.backward_region(bid)
        # Block creation order, not set order: reasons list these, and must not vary with the hash seed.
        for b in (b for b in self.fn.blocks if b in region):
            instrs = self.fn.blocks[b].instructions
            out.extend(instrs[:idx] if b == bid else instrs)
        return out

    def site_roots(self, site: ObligationSite) -> list[str]:
        ins = self.defs[site.result_id]  # type: ignore[index]
        if site.base_property == DIVISION_PROPERTY:
            return [ins.operands[1]]
        return list(ins.operands)

    def cone(self, site: ObligationSite) -> set[str]:
        """Values the obligation's reachability and bad state can depend on."""
        bid = site.block_id
        assert bid is not None and site.result_id is not None
        idx = self.instr_index(bid, site.result_id)
        region = self.backward_region(bid)
        roots: list[str] = self.site_roots(site)
        for b in region:
            blk = self.fn.blocks[b]
            if b != bid and blk.terminator is not None and blk.terminator.op is Op.BRANCH:
                roots.append(blk.terminator.operands[0])
            instrs = blk.instructions if b != bid else blk.instructions[:idx]
            for ins in instrs:
                if ins.op in (Op.DIV, Op.FLOORDIV, Op.MOD) or ins.attrs.get("obligation_property"):
                    # An earlier checked operation constrains the path only through its
                    # operands' types and values (audit A-07).
                    roots.extend(ins.operands)
                elif ins.op is Op.ASSIGN and ins.attrs.get("checked_read"):
                    roots.extend(ins.operands)
        return self.closure(roots)

    def domain_cone(self, site: ObligationSite, cone: set[str]) -> set[str]:
        """Values whose *types* the claim relies on: the cone plus, for a division, the
        dividend. Built-in zero-divisor semantics hold only if the dividend really is a
        built-in number; when that type comes from an annotation the result is conditional
        on it, whichever value the divisor has (audit A4-01)."""
        if site.base_property != DIVISION_PROPERTY or site.result_id is None:
            return cone
        return cone | self.closure([self.defs[site.result_id].operands[0]])

    def closure(self, roots: list[str]) -> set[str]:
        """Backward data/branch dependencies of ``roots`` (inclusive)."""
        seen: set[str] = set()
        stack = list(roots)
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            ins = self.defs.get(v)
            if ins is None:
                continue
            stack.extend(ins.operands)
            if ins.op is Op.PHI:
                stack.extend(val for _, val in ins.attrs["incoming"] if val)
                # A phi also depends on which edge was taken.
                for pred, _ in ins.attrs["incoming"]:
                    t = self.fn.blocks[pred].terminator
                    if t is not None and t.op is Op.BRANCH:
                        stack.append(t.operands[0])
        return seen

    def upstream_unknown(self, site: ObligationSite) -> list[Instruction]:
        return [i for i in self.prefix_instructions(site) if i.effect is Effect.UNKNOWN]

    def resolution_notes(self, site: ObligationSite, cone: set[str]) -> tuple[list[str], bool]:
        """Assumptions from resolved globals (summaries, constants) this result relies on."""
        notes: list[str] = []
        conditional = False
        instrs = list(self.prefix_instructions(site)) + [self.defs[v] for v in sorted(cone) if v in self.defs]
        for ins in instrs:
            a = ins.attrs.get("resolution_assumption")
            if a:
                notes.append(a)
                conditional = conditional or bool(ins.attrs.get("resolution_conditional"))
        for c in site.via or []:
            if c.get("assumption"):
                notes.append(c["assumption"])
            conditional = conditional or c.get("conditional") == "yes"
        return sorted(dict.fromkeys(notes)), conditional

    def domain_info(self, cone: set[str]) -> tuple[DomainSource, Authority, ScopeValidity, str, list[str]]:
        in_cone = [p for p in self.fn.params if p.value_id in cone]
        params = [p for p in in_cone if p.type.kind in MODELED_PARAM_KINDS]
        # Any annotation-derived type in the cone is an unenforced assumption, so the
        # result is conditional on it (audit A-08).
        annotated = [p for p in in_cone if p.type.origin is Origin.ANNOTATION and p.type.kind in (MODELED_PARAM_KINDS | {Kind.FLOAT})]
        declared = [p for p in params if p.type.origin is Origin.DECLARED]
        unmodeled = self.unmodeled_leaves(cone)
        if not params and not annotated:
            if unmodeled:
                return (
                    DomainSource.NONE,
                    Authority.INFERRED,
                    ScopeValidity.CONDITIONAL,
                    "not established: depends on " + "; ".join(unmodeled[:3]),
                    ["values of unmodeled inputs/effects (" + "; ".join(unmodeled[:3]) + ") are arbitrary; the result holds only as far as it does not depend on them"],
                )
            return (
                DomainSource.LITERAL_ONLY,
                Authority.INFERRED,
                ScopeValidity.EXACT_MODEL,
                "no function inputs influence this obligation (exact literals only)",
                [],
            )
        shown = params or annotated
        names = ", ".join(p.name for p in shown)
        if annotated:
            ann = ", ".join(f"{p.name}: {p.annotation}" for p in annotated)
            kinds = sorted({(p.type.inner.value + " or None") if p.type.kind is Kind.OPTIONAL and p.type.inner else p.type.kind.value for p in annotated})
            assumptions = [
                f"annotation-only domain ({ann}): assumes callers pass exact built-in {'/'.join(kinds)} values (type(x) is {kinds[0].split(' ')[0]}); annotations are not enforced at runtime",
                "bool and int subclasses (which may override arithmetic/comparison operators) and list/tuple subclasses are outside this assumption",
            ]
            if declared:
                assumptions.append("declared domain for: " + ", ".join(p.name for p in declared) + (f" ({self.declared_reason})" if self.declared_reason else ""))
            return (DomainSource.ANNOTATION, Authority.INFERRED, ScopeValidity.CONDITIONAL, f"{names}: exact Python built-in values (annotation-derived, conditional)", assumptions)
        reason = self.declared_reason or "declared in configuration"
        if all(p.type.kind is Kind.INT for p in params):
            domain = f"{names}: exact Python built-in integers"
        else:
            domain = "; ".join(f"{p.name}: {_describe_kind(p.type)}" for p in params)
        return (
            DomainSource.CONFIG_DECLARED,
            Authority.DECLARED,
            ScopeValidity.EXACT_MODEL,
            domain,
            [f"domain explicitly admitted in configuration: {reason}"],
        )

    # -- rendering ---------------------------------------------------------------------------
    def render(self, v: str, depth: int = 0) -> str:
        name = self.fn.var_names.get(v)
        ins = self.defs.get(v)
        if ins is None:
            return v
        if ins.op is Op.PARAM:
            return ins.attrs["name"]
        if name is not None and depth > 0:
            return name
        if ins.op is Op.CONST:
            return repr(ins.attrs.get("value"))
        if ins.op in _ARITH_TEXT:
            a, b = ins.operands
            if ins.attrs.get("synthesized") == "unary":
                return f"{'-' if ins.op is Op.SUB else '+'}{self.render(b, depth + 1)}"
            return f"{self.render(a, depth + 1)} {_ARITH_TEXT[ins.op]} {self.render(b, depth + 1)}"
        if ins.op is Op.COMPARE:
            a, b = ins.operands
            if ins.attrs.get("synthesized") == "is_not":
                return f"{self.render(self.defs[a].operands[0], depth + 1)} is not None"
            return f"{self.render(a, depth + 1)} {_CMP_TEXT[ins.attrs['cmp']]} {self.render(b, depth + 1)}"
        if ins.op is Op.IS_NONE:
            return f"{self.render(ins.operands[0], depth + 1)} is None"
        if ins.op is Op.LEN:
            return f"len({self.render(ins.operands[0], depth + 1)})"
        if ins.op is Op.BUILD_SEQ:
            inner = ", ".join(self.render(o, depth + 1) for o in ins.operands[:4]) + (", ..." if len(ins.operands) > 4 else "")
            return f"[{inner}]" if ins.type is not None and ins.type.kind is Kind.LIST else f"({inner}{',' if len(ins.operands) == 1 else ''})"
        if ins.op is Op.INDEX:
            return f"{self.render(ins.operands[0], depth + 1)}[{self.render(ins.operands[1], depth + 1)}]"
        if ins.op is Op.ASSIGN and not ins.attrs.get("checked_read"):
            return self.render(ins.operands[0], depth)
        if name is not None:
            return name
        return f"<{ins.op.value.lower()} {v}>"

    def render_branch(self, cond: str, taken: bool, kind: str | None) -> str:
        if kind == "assertions_enabled":
            return "assertions enabled (__debug__ is True)" if taken else "assertions disabled (python -O)"
        ins = self.defs.get(cond)
        if ins is not None and ins.op is Op.COMPARE and ins.attrs.get("synthesized") != "is_not" and not taken:
            a, b = ins.operands
            return f"{self.render(a, 1)} {_NEG_TEXT[ins.attrs['cmp']]} {self.render(b, 1)}"
        text = self.render(cond)
        return text if taken else f"not ({text})"

    def bad_text(self, site: ObligationSite) -> str:
        ins = self.defs[site.result_id]  # type: ignore[index]
        prop = site.base_property
        if prop == INDEX_PROPERTY:
            seq, idx = (self.render(o, 1) for o in ins.operands)
            return f"not (-len({seq}) <= {idx} < len({seq}))"
        if prop == NONE_PROPERTY:
            return f"{self.render(ins.operands[0], 1)} is None"
        return f"{self.render(ins.operands[1], 1)} == 0"

    def path_constraints(self, site: ObligationSite, trace: list[tuple[str, str, bool]], cone: set[str]) -> list[str]:
        out: list[str] = []
        # Definitions of named intermediate values the obligation depends on.
        for v in sorted(cone, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0):
            ins = self.defs.get(v)
            name = self.fn.var_names.get(v)
            if ins is None or name is None or ins.op in (Op.PARAM, Op.PHI, Op.CONST):
                continue
            if ins.op in _ARITH_TEXT or ins.op in (Op.COMPARE, Op.LEN):
                out.append(f"{name} == {self.render(v)}")
        for bid, cond, taken in trace:
            term = self.fn.blocks[bid].terminator
            out.append(self.render_branch(cond, taken, term.attrs.get("kind") if term else None))
        out.append(self.bad_text(site))
        return out

    # -- witness validation ------------------------------------------------------------------
    def full_args(self, model: dict[str, Any]) -> dict[str, Any]:
        args: dict[str, Any] = {}
        for p in self.fn.params:
            k = p.type.kind
            if k in NUMERIC_EXACT:
                v = model.get(p.name, 0 if k is Kind.INT else False)
                args[p.name] = bool(v) if k is Kind.BOOL else int(v)
            elif k is Kind.LIST:
                args[p.name] = list(model.get(p.name, []))
            elif k is Kind.TUPLE:
                args[p.name] = tuple(model.get(p.name, ()))
            elif k is Kind.OPTIONAL and p.type.inner in NUMERIC_EXACT:
                v = model.get(p.name, 0 if p.type.inner is Kind.INT else False)
                args[p.name] = None if v is None else (bool(v) if p.type.inner is Kind.BOOL else int(v))
            else:
                args[p.name] = UNMODELED
        return args

    def validate(self, site: ObligationSite, args: dict[str, Any], assertions: bool):
        return evaluate(self.fn, args, assertions_enabled=assertions)

    def _search_values(self, t) -> tuple | None:
        k = t.kind
        if k is Kind.INT:
            return WITNESS_SEARCH_VALUES
        if k is Kind.BOOL:
            return (False, True)
        if k is Kind.LIST:
            return ([], [0], [0, 0], [0, 0, 0])
        if k is Kind.TUPLE:
            return ((), (0,), (0, 0), (0, 0, 0))
        if k is Kind.OPTIONAL:
            if t.inner is Kind.INT:
                return (None, 0, 1, -1, 2, -2)
            if t.inner is Kind.BOOL:
                return (None, False, True)
        return None

    def search(self, site: ObligationSite) -> tuple[dict[str, Any], bool, Any] | None:
        exact = [(p, self._search_values(p.type)) for p in self.fn.params]
        exact = [(p, r) for p, r in exact if r is not None]
        ranges = [r for _, r in exact]
        modes = (True, False) if self.fn.has_asserts else (True,)
        total = len(modes)
        for r in ranges:
            total *= len(r)
        if total > WITNESS_SEARCH_MAX:
            return None
        for assertions in modes:
            for combo in itertools.product(*ranges):
                if self.budget.exhausted():
                    return SEARCH_TRUNCATED  # type: ignore[return-value]
                args = {p.name: UNMODELED for p in self.fn.params}
                args.update({p.name: (list(v) if isinstance(v, list) else v) for (p, _), v in zip(exact, combo)})
                out = self.validate(site, args, assertions)
                if out.kind == "REACHED_FAILURE" and out.instr_result == site.result_id:
                    return {k: v for k, v in args.items() if v is not UNMODELED}, assertions, out
        return None

    # -- main ----------------------------------------------------------------------------------
    def check_site(self, site: ObligationSite) -> ObligationOutcome:
        sid = self.fn.symbol_id
        if site.block_id is None or site.result_id is None:
            return unsupported_outcome(sid, site, site.unsupported_reason or "not lowered")
        prop = site.base_property
        fact: Any = self.ai.division_facts.get(site.result_id) if prop == DIVISION_PROPERTY else self.ai.site_facts.get(site.result_id)
        if fact is None:
            raise EngineInconsistency(f"no analysis fact for obligation {site.result_id}")
        cone = self.cone(site)
        dcone = self.domain_cone(site, cone)
        source, authority, scope, domain, assumptions = self.domain_info(dcone)
        # Creating a generator binds its parameters, but its body starts only at the first
        # next(); in between, the consumer can rebind them through gi_frame.f_locals.
        prestart = [p.name for p in self.fn.params if p.value_id in dcone] if self.fn.generator else []
        notes, notes_conditional = self.resolution_notes(site, cone)
        assumptions = assumptions + notes
        upstream = self.upstream_unknown(site)
        base = dict(symbol_id=sid, site=site, authority=authority, domain_source=source, domain=domain)

        def safe(method: AnalysisMethod, reason: str, divisor_fact: str | None = None) -> ObligationOutcome:
            s, a = scope, list(assumptions)
            if upstream:
                s = ScopeValidity.CONDITIONAL
                a.append(
                    "unknown effects on paths to this operation (e.g. "
                    + ", ".join(sorted({u.attrs.get("callee") or u.attrs.get("reason", "unknown") for u in upstream})[:3])
                    + ") do not rebind this function's locals via frame introspection"
                )
            if prestart:
                s = ScopeValidity.CONDITIONAL
                a.append("the generator's consumer does not rebind its parameters (" + ", ".join(prestart)
                         + ") via frame introspection (gi_frame.f_locals) before the first next()")
            if notes_conditional:
                s = ScopeValidity.CONDITIONAL
            if self.fn.has_asserts:
                a.append("holds both with assertions enabled and under python -O")
            return ObligationOutcome(**base, result=PropertyResult.PROVED_SAFE, method=method, scope=s, assumptions=a,
                                     reasons=[reason], quantifier=QUANTIFIER_SAFE_GENERATOR if self.fn.generator else QUANTIFIER_SAFE,
                                     divisor_fact=divisor_fact)

        def unknown(method: AnalysisMethod, reasons: list[str], **kw: Any) -> ObligationOutcome:
            return ObligationOutcome(**base, result=PropertyResult.UNKNOWN, method=method, scope=None, assumptions=list(assumptions),
                                     reasons=reasons, quantifier="none (no claim)", **kw)

        if not fact.reachable:
            return safe(AnalysisMethod.UNREACHABLE, "no feasible path reaches this operation under the modeled branch facts")
        reasons: list[str] = []
        divisor_fact: AbsInt | None = None
        if prop == DIVISION_PROPERTY:
            divisor_v = self.defs[site.result_id].operands[1]
            if self.defs[site.result_id].flavor == "python.str_format":
                return safe(AnalysisMethod.TYPE_FACT, "an exact str literal % an exact tuple selects str.__mod__ (printf-style formatting), which performs no numeric division; "
                            "exceptions raised by element __str__/__repr__/__format__ hooks are obligations of those hooks, not of this operation")
            if fact.dividend_kind not in (Kind.INT, Kind.BOOL, Kind.FLOAT):
                return unknown(AnalysisMethod.UNSUPPORTED, [
                    f"dividend has type {fact.dividend_kind.value}: the operator may dispatch to a user-defined method, so built-in zero-divisor semantics are not established"
                    + self._unknown_source(self.defs[site.result_id].operands[0])
                ])
            divisor_fact = fact.divisor
            if fact.divisor_kind is Kind.FLOAT:
                if isinstance(fact.divisor_const, float) and fact.divisor_const != 0.0:
                    return safe(AnalysisMethod.INTERVAL, f"divisor is the nonzero float literal {fact.divisor_const!r}")
                if not isinstance(fact.divisor_const, float):
                    return unknown(AnalysisMethod.UNSUPPORTED, ["divisor is a computed float: float semantics (NaN, infinities, rounding) are not modeled"])
            elif fact.divisor_kind not in NUMERIC_EXACT:
                return unknown(AnalysisMethod.UNSUPPORTED, [
                    f"divisor has type {fact.divisor_kind.value}: its value is not modeled" + self._unknown_source(divisor_v)
                ])
            elif divisor_fact is not None and divisor_fact.excludes_zero():
                return safe(AnalysisMethod.INTERVAL, f"divisor {self.render(divisor_v, 1)} has interval/nonzero fact {divisor_fact!r}, which excludes 0", repr(divisor_fact))
            if divisor_fact is not None:
                reasons.append(f"interval analysis: divisor fact {divisor_fact!r} includes 0")
        elif prop == INDEX_PROPERTY:
            seq_v, idx_v = self.defs[site.result_id].operands
            if fact.seq_kind not in SEQUENCE_KINDS:
                raise EngineInconsistency(f"index obligation {site.result_id} on a non-sequence receiver")
            if fact.index_kind not in NUMERIC_EXACT:
                return unknown(AnalysisMethod.UNSUPPORTED, [
                    f"index has type {fact.index_kind.value}: it may be a slice or an object whose __index__ runs user code" + self._unknown_source(idx_v)
                ])
            if fact.seq_kind is Kind.LIST and upstream:
                effects = ", ".join(sorted({u.attrs.get("callee") or u.attrs.get("reason", "unknown") for u in upstream})[:3])
                return unknown(AnalysisMethod.UNSUPPORTED, [
                    f"list {self.render(seq_v, 1)} may be mutated (through an alias or an escaped reference) by unknown effects before this index ({effects}); alias/effect tracking for lists is not modeled"
                ])
            L, idx = fact.seq_len, fact.index
            if L is not None and idx is not None and not L.bottom and not idx.bottom:
                lo_len = L.itv.lo
                if lo_len > idx.itv.hi and idx.itv.lo >= -lo_len:
                    return safe(AnalysisMethod.INTERVAL, f"index {self.render(idx_v, 1)} in {idx!r} lies within [-len, len) for every len({self.render(seq_v, 1)}) in {L!r}")
                reasons.append(f"interval analysis: index {idx!r} vs len {L!r} does not exclude IndexError")
        else:  # none.attribute_access
            recv_v = self.defs[site.result_id].operands[0]
            if fact.recv_kind in EXACT_NON_NONE:
                return safe(AnalysisMethod.PATH_FACT, f"receiver {self.render(recv_v, 1)} was narrowed to non-None by an `is None`/`is not None` test on every path here")
            if fact.recv_kind not in (Kind.NONE, Kind.OPTIONAL):
                return unknown(AnalysisMethod.UNSUPPORTED, [f"receiver has type {fact.recv_kind.value}: its None-state is not modeled"])

        # -- escalate --------------------------------------------------------------------------
        bad = self.bad_text(site)
        solver_runs: list[dict[str, Any]] = []
        if self.budget.exhausted():
            return unknown(AnalysisMethod.BUDGET, reasons + ["static analysis budget exhausted before this obligation was resolved"])

        candidate: dict[str, Any] | None = None
        if self.solver is not None:
            outcome, witness_mode, sat_run = self._solve(site, solver_runs)
            status = outcome.status
            if status is SolverStatus.UNSAT:
                r = safe(AnalysisMethod.SMT, f"SMT: reach(path) AND {bad} is UNSAT", repr(divisor_fact) if divisor_fact else None)
                r.solver_runs = solver_runs
                return r
            if status is SolverStatus.SAT:
                assert outcome.model is not None
                assertions = bool(outcome.model.get("__debug__", True))
                args = self.full_args(outcome.model)
                ev = self.validate(site, args, assertions)
                if ev.kind == "REACHED_FAILURE" and ev.instr_result == site.result_id:
                    return self._unsafe(site, base, assumptions, args, assertions, ev, cone, AnalysisMethod.SMT, solver_runs, witness_mode, None, notes_conditional)
                candidate = {k: v for k, v in args.items() if v is not UNMODELED}
                if ev.kind in ("RETURNED", "RAISED", "REACHED_FAILURE") and outcome.exact:
                    raise EngineInconsistency(
                        f"exact SMT witness {candidate} for {site_obligation_id(sid, site)} did not reach the failure in the IR evaluator ({ev.kind} {ev.detail})"
                    )
                reasons.append(f"SMT: SAT, but the witness could not be validated by the IR evaluator ({ev.kind}: {ev.detail or 'diverged'}); query overapproximates: havoc of {', '.join(outcome.havoc_values) or 'none'}")
            else:
                # Elapsed time lives only in solver_runs (a timing field), keeping reasons deterministic.
                reasons.append(f"SMT: {status.value}" + (f" ({outcome.reason})" if outcome.reason else "") + f" (per-query limit {outcome.timeout_ms} ms"
                               + (f", rlimit {outcome.rlimit}" if outcome.rlimit else "") + ")")
        else:
            reasons.append(f"solver {self.solver_status.value.lower()}: interval analysis alone cannot decide this obligation")

        if self.budget.witness_search:
            found = self.search(site)
            if found is SEARCH_TRUNCATED:
                return unknown(AnalysisMethod.BUDGET, reasons + ["static analysis budget exhausted during the bounded witness search"], solver_runs=solver_runs)
            if found is not None:
                args, assertions, ev = found
                return self._unsafe(site, base, assumptions, args, assertions, ev, cone, AnalysisMethod.WITNESS_SEARCH, solver_runs, None, reasons, notes_conditional)
            reasons.append(
                f"bounded concrete witness search over {list(WITNESS_SEARCH_VALUES)} per integer parameter (lists of length 0-3, None) found no validated witness (bounded; not a proof)"
            )
        if candidate is not None:
            a = list(assumptions) + [f"assumes unmodeled values/effects ({', '.join(u.attrs.get('callee') or u.attrs.get('reason', '?') for u in upstream[:3]) or 'havoc'}) admit the witness"]
            return ObligationOutcome(**base, result=PropertyResult.POSSIBLY_UNSAFE, method=AnalysisMethod.SMT, scope=ScopeValidity.OVERAPPROXIMATE,
                                     assumptions=a, reasons=reasons, witness=_jsonable(candidate), witness_validated=False, solver_runs=solver_runs,
                                     quantifier="exists input in an overapproximate model (may be spurious)")
        return unknown(AnalysisMethod.SMT if self.solver is not None else AnalysisMethod.INTERVAL, reasons, solver_runs=solver_runs,
                       divisor_fact=repr(divisor_fact) if divisor_fact else None)

    def unmodeled_leaves(self, values: set[str]) -> list[str]:
        """Human-readable sources of unmodeled values among ``values`` (and their operands)."""
        out: list[str] = []
        seen: set[str] = set()
        stack = sorted(values, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0)
        while stack:
            v = stack.pop(0)
            if v in seen:
                continue
            seen.add(v)
            ins = self.defs.get(v)
            if ins is None:
                continue
            if ins.op is Op.PARAM and ins.type is not None and ins.type.kind not in MODELED_PARAM_KINDS and not ins.attrs.get("runtime_mode"):
                p = next((p for p in self.fn.params if p.value_id == v), None)
                ann = f"annotated {p.annotation}" if p is not None and p.annotation else "unannotated"
                out.append(f"parameter '{ins.attrs['name']}' ({ann}, no declared exact domain)")
            elif ins.attrs.get("control"):
                pass  # a nondeterministic control choice (exception edges): every outcome is explored
            elif ins.op in (Op.CALL, Op.UNKNOWN_EFFECT, Op.ATTRIBUTE, Op.INDEX) and ins.result is not None and ins.type is not None:
                out.append(str(ins.attrs.get("reason", ins.op.value)))
            elif ins.op in (Op.ELEMENT, Op.LOOP_PHI, Op.ITER) and ins.attrs.get("reason") and (ins.type is None or ins.type.kind is Kind.OBJECT):
                out.append(str(ins.attrs["reason"]))
            elif ins.op is Op.PHI and ins.attrs.get("maybe_unbound"):
                out.append(f"variable '{ins.attrs.get('variable')}' that may be unbound on some path")
            stack.extend(ins.operands)
        return list(dict.fromkeys(out))

    def _unknown_source(self, v: str) -> str:
        ins = self.defs.get(v)
        if ins is None:
            return ""
        if ins.op in (Op.ADD, Op.SUB, Op.MUL, Op.DIV, Op.FLOORDIV, Op.MOD) and ins.type is not None and ins.type.kind is Kind.OBJECT:
            leaves = self.unmodeled_leaves({v})
            return (f"; it is computed by `{self.render(v)}` on operands of unmodeled type, so the operator may dispatch to "
                    "user-defined methods (e.g. bool or int subclasses)" + (": " + "; ".join(leaves[:3]) if leaves else ""))
        if ins.op is Op.CALL:
            return f"; it comes from {ins.attrs.get('reason')}"
        if ins.op in (Op.UNKNOWN_EFFECT, Op.ATTRIBUTE, Op.INDEX) or (ins.op in (Op.ELEMENT, Op.LOOP_PHI) and ins.attrs.get("reason")):
            return f"; it comes from: {ins.attrs.get('reason')}"
        if ins.op is Op.PARAM:
            p = next((p for p in self.fn.params if p.name == ins.attrs.get("name")), None)
            ann = f" annotated {p.annotation}" if p is not None and p.annotation else " with no annotation"
            return f"; parameter '{ins.attrs.get('name')}'{ann} and no declared domain"
        if ins.op is Op.PHI and ins.attrs.get("maybe_unbound"):
            return f"; variable '{ins.attrs.get('variable')}' may be unbound on some path"
        if ins.op is Op.ASSIGN and ins.attrs.get("checked_read"):
            return f"; variable '{ins.attrs.get('variable')}' may be unbound on some path"
        if ins.op is Op.ASSIGN and ins.attrs.get("inlined_call"):
            return self._unknown_source(ins.operands[0]) or f"; it is the result of the call summary for {ins.attrs.get('inlined_call')}"
        return ""

    def _bad_constraint(self, enc: Any, site: ObligationSite, bid: str, idx: int) -> list[Any]:
        ins = self.defs[site.result_id]  # type: ignore[index]
        prop = site.base_property
        if prop == DIVISION_PROPERTY:
            divisor = ins.operands[1]
            return [enc.int_term(divisor) == 0] if self.kind(divisor) in NUMERIC_EXACT else []
        if prop == INDEX_PROPERTY:
            return [enc.z3.Not(enc.in_bounds(ins, bid, idx))]
        return [enc.none_term(ins.operands[0])]

    def _solve(self, site: ObligationSite, runs: list[dict[str, Any]]):
        """Query reach AND bad-state, preferring a witness with assertions enabled."""
        bid, rid = site.block_id, site.result_id
        assert bid is not None and rid is not None
        idx = self.instr_index(bid, rid)

        if self._z3ctx is None and hasattr(self.solver, "new_context"):
            self._z3ctx = self.solver.new_context()  # one context per function: deterministic models

        def run(debug_true: bool):
            enc = self.solver.encoder(self.fn, self._z3ctx) if self._z3ctx is not None else self.solver.encoder(self.fn)
            z3 = enc.z3
            constraints = [enc.prefix(bid, idx)] + self._bad_constraint(enc, site, bid, idx)
            # Make every modeled parameter part of the model for complete witnesses.
            for p in self.fn.params:
                k = p.type.kind
                if k in NUMERIC_EXACT:
                    enc.term(p.value_id)
                elif k in SEQUENCE_KINDS:
                    enc.seq_len_base(p.value_id)
                elif k is Kind.OPTIONAL and p.type.inner in NUMERIC_EXACT:
                    enc.term(p.value_id)
                    enc.none_term(p.value_id)
            for ins in self.fn.blocks[self.fn.entry_id].instructions:
                if ins.op is Op.PARAM and ins.attrs.get("runtime_mode"):
                    var = enc.term(ins.result)
                    if debug_true:
                        constraints.append(var == z3.BoolVal(True))
            timeout = min(self.budget.per_query_timeout_ms, max(1, self.budget.remaining_ms()))
            out = self.solver.check(enc, constraints, timeout_ms=timeout)
            runs.append({
                "solver": "z3",
                "solver_version": self.solver.version,
                "query_digest": out.query_digest,
                "response": out.status.value,
                "reason_unknown": out.reason,
                "elapsed_ms": out.elapsed_ms,
                "timeout_ms": out.timeout_ms,
                "rlimit": out.rlimit,
                "exact_encoding": out.exact,
                "assertions_constraint": "enabled" if debug_true else "any",
            })
            return out

        first = run(False)
        if first.status is SolverStatus.SAT and self.fn.has_asserts and first.model is not None and first.model.get("__debug__") is False:
            second = run(True)
            if second.status is SolverStatus.SAT:
                return second, "assertions enabled", second
            if second.status is SolverStatus.UNSAT:
                return first, "only under python -O", first
            return first, "only under python -O (enabled-mode query inconclusive)", first
        return first, None, first

    def _unsafe(self, site, base, assumptions, args, assertions, ev, cone, method, solver_runs, witness_mode, prior_reasons=None, notes_conditional=False):
        witness = _jsonable({k: v for k, v in args.items() if v is not UNMODELED})
        a = list(assumptions)
        scope = ScopeValidity.EXACT_MODEL if base["domain_source"] in (DomainSource.CONFIG_DECLARED, DomainSource.LITERAL_ONLY) else ScopeValidity.CONDITIONAL
        if notes_conditional:
            scope = ScopeValidity.CONDITIONAL
        runtime_mode = None
        if self.fn.has_asserts:
            if assertions:
                runtime_mode = "assertions enabled"
            else:
                runtime_mode = "python -O (assertions disabled)"
                scope = ScopeValidity.CONDITIONAL
                a.append("failure is reachable only when Python runs with assertions disabled (python -O); "
                         + ("with assertions enabled no admitted input reaches it (SMT UNSAT)" if witness_mode == "only under python -O" else "assertions-enabled reachability was not established"))
        reasons = list(prior_reasons or [])
        reasons.append(
            ("SMT: SAT; " if method is AnalysisMethod.SMT else "bounded concrete search; ")
            + f"witness {witness} re-validated by the IR evaluator: {ev.failure or failure_name(site)} at this operation"
            + (" when the generator is consumed with next() (None resumes every yield)" if self.fn.generator else "")
        )
        return ObligationOutcome(
            **base,
            result=PropertyResult.PROVED_UNSAFE,
            method=method,
            scope=scope,
            assumptions=a,
            reasons=reasons,
            witness=witness,
            witness_validated=True,
            path_constraints=self.path_constraints(site, ev.trace, cone),
            solver_runs=solver_runs,
            quantifier=QUANTIFIER_UNSAFE_GENERATOR if self.fn.generator else QUANTIFIER_UNSAFE,
            runtime_mode=runtime_mode,
        )


def _jsonable(w: dict[str, Any]) -> dict[str, Any]:
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in w.items()}


def check_function(fn: Function, solver: Any | None, solver_status: SolverStatus, budget: Budget, declared_reason: str | None) -> list[ObligationOutcome]:
    checker = FunctionChecker(fn, solver, solver_status, budget, declared_reason)
    out = []
    for site in fn.sites:
        o = checker.check_site(site)
        if site.handled_by and o.result is not PropertyResult.PROVED_SAFE:
            o.reasons.append(f"handled: the {failure_name(site)} is caught by {site.handled_by}, which does not re-raise; "
                             "this failure is advisory (INFO) and the obligation is not required")
        out.append(o)
    return out


__all__ = ["DivisionSite", "ObligationSite", "check_function", "obligation_id", "site_obligation_id", "unsupported_outcome", "Budget", "ObligationOutcome"]
