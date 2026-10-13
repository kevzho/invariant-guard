"""Typed minimal Verification IR (docs/04_VERIFICATION_IR.md).

Values are SSA-like: every instruction result has an immutable value ID within a
function. Local variables are renamed to versioned values during lowering, and joins
introduce PHI instructions. For every static analysis the CFG is acyclic.

Loops (I-132) keep it acyclic. A loop header's ``LOOP_PHI`` values stand for the state at
the start of an arbitrary iteration: statically they are fresh values of their type,
never a function of their incoming values. The latch block ends in a ``JUMP`` marked
``back_edge``. ``Block.successors`` (and therefore every static analysis) ignores that
edge, while the concrete evaluator follows it and selects the ``LOOP_PHI`` incoming value
by predecessor, so it executes the real iterations when it re-validates a witness.

Exceptions (I-133) reuse the same idea. A ``try`` statement branches, before its body, on an
``UNKNOWN_EFFECT`` marked ``exception_edge`` into its handlers (and into the exceptional
entry of its ``finally``), where every local the protected code may bind is a fresh
``LOOP_PHI`` (attr ``handler``). That over-approximates "an exception at any point". The
evaluator takes the normal branch (exceptions happen only where an instruction raises) and
stops at the first exception, so a failure that needs an exceptional path to be reached is
never validated: it stays POSSIBLY_UNSAFE.

M2 additions (each needed by a shipped analysis, docs/04 "extend only when needed"):
``BUILD_SEQ`` (built-in list/tuple displays, for bounds checks), the ``LIST``/``TUPLE``/
``OPTIONAL`` kinds (sequence bounds and narrow None-flow), and obligation sites for the
properties ``index.out_of_range`` and ``none.attribute_access`` next to
``division.zero_denominator``. Inlined direct calls (call summaries) keep their callee
spans and mark the resolution they rely on in instruction attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..models.spans import SourceSpan

IR_SCHEMA_VERSION = "ir-0.3"
SUPPORTED_IR_VERSIONS = frozenset({IR_SCHEMA_VERSION})


class Op(str, Enum):
    CONST = "CONST"
    PARAM = "PARAM"
    ASSIGN = "ASSIGN"
    ADD = "ADD"
    SUB = "SUB"
    MUL = "MUL"
    DIV = "DIV"
    FLOORDIV = "FLOORDIV"
    MOD = "MOD"
    COMPARE = "COMPARE"
    IS_NONE = "IS_NONE"
    LEN = "LEN"
    INDEX = "INDEX"
    CALL = "CALL"
    ATTRIBUTE = "ATTRIBUTE"
    RETURN = "RETURN"
    RAISE = "RAISE"
    BRANCH = "BRANCH"
    JUMP = "JUMP"
    PHI = "PHI"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"
    BUILD_SEQ = "BUILD_SEQ"  # M2: built-in list/tuple display with a known length
    # Loops (I-132)
    LOOP_PHI = "LOOP_PHI"  # loop-carried variable at the header: any value of its type (statically)
    LOOP_HEAD = "LOOP_HEAD"  # header marker; effect UNKNOWN when any iteration may run unknown code
    ITER = "ITER"  # iter() of the iterable (range arguments, or the iterable value)
    NEXT = "NEXT"  # True if the iterator produced another element (statically unknown)
    ELEMENT = "ELEMENT"  # the element produced by the preceding NEXT


DIVISION_OPS = frozenset({Op.DIV, Op.FLOORDIV, Op.MOD})
TERMINATORS = frozenset({Op.RETURN, Op.RAISE, Op.BRANCH, Op.JUMP})


class Kind(str, Enum):
    """Language-level type kinds known to the M1 Python frontend."""

    INT = "int"  # exact built-in int (python.bigint semantics)
    BOOL = "bool"  # exact built-in bool
    FLOAT = "float"  # built-in float; values are not modeled by the solver
    NONE = "NoneType"
    STR = "str"
    LIST = "list"  # exact built-in list (mutable: length facts die at unknown effects)
    TUPLE = "tuple"  # exact built-in tuple (immutable length)
    OPTIONAL = "optional"  # None or a value of the exact kind ``SemType.inner``
    OBJECT = "object"  # arbitrary/unknown object; operators may dispatch to user code


class Origin(str, Enum):
    DECLARED = "declared"  # config [[domains]]
    ANNOTATION = "annotation"
    LITERAL = "literal"
    DERIVED = "derived"
    RUNTIME_MODE = "runtime_mode"
    UNKNOWN = "unknown"


class Effect(str, Enum):
    NONE = "none"
    # Can raise a built-in exception the IR evaluator reproduces faithfully
    # (ZeroDivisionError, OverflowError) but never runs user code.
    MAY_RAISE = "may_raise"
    # Arbitrary: may run user code, mutate state, raise or not return.
    UNKNOWN = "unknown"


NUMERIC_EXACT = frozenset({Kind.INT, Kind.BOOL})
SEQUENCE_KINDS = frozenset({Kind.LIST, Kind.TUPLE})
# Kinds whose values never run user code for built-in operations and are never None.
EXACT_NON_NONE = frozenset({Kind.INT, Kind.BOOL, Kind.FLOAT, Kind.STR, Kind.LIST, Kind.TUPLE})


@dataclass(frozen=True)
class SemType:
    kind: Kind
    origin: Origin
    inner: Kind | None = None  # the non-None kind of an OPTIONAL value

    @property
    def numeric_semantics(self) -> str | None:
        k = self.inner if self.kind is Kind.OPTIONAL else self.kind
        if k in NUMERIC_EXACT:
            return "python.bigint"
        if k is Kind.FLOAT:
            return "python.float64"
        return None

    def to_json(self) -> dict[str, Any]:
        if self.kind is Kind.NONE:
            nullability = "null"
        elif self.kind is Kind.OPTIONAL:
            nullability = "nullable"
        elif self.kind is Kind.OBJECT:
            nullability = "unknown"
        else:
            nullability = "non_null"
        lang = f"optional[{self.inner.value}]" if self.kind is Kind.OPTIONAL and self.inner is not None else self.kind.value
        return {
            "language_type": lang,
            "numeric_semantics": self.numeric_semantics,
            "nullability": nullability,
            "certainty_origin": self.origin.value,
        }


UNKNOWN_TYPE = SemType(Kind.OBJECT, Origin.UNKNOWN)


@dataclass(slots=True)
class Instruction:
    op: Op
    operands: tuple[str, ...]
    result: str | None
    type: SemType | None
    span: SourceSpan
    effect: Effect = Effect.NONE
    flavor: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        attrs = {}
        for k, v in sorted(self.attrs.items()):
            if k == "value":
                attrs[k] = v if isinstance(v, (int, float, str, bool)) or v is None else repr(v)
            else:
                attrs[k] = v
        return {
            "op": self.op.value,
            "operands": list(self.operands),
            "result_id": self.result,
            "semantic_type": self.type.to_json() if self.type else None,
            "source_span": self.span.to_json(),
            "effect": self.effect.value,
            "flavor": self.flavor,
            "attrs": attrs,
        }


@dataclass(slots=True)
class Block:
    block_id: str
    label: str
    instructions: list[Instruction] = field(default_factory=list)
    terminator: Instruction | None = None
    preds: list[str] = field(default_factory=list)

    def successors(self) -> list[str]:
        t = self.terminator
        if t is None:
            return []
        if t.op is Op.JUMP:
            return [] if t.attrs.get("back_edge") else [t.attrs["target"]]
        if t.op is Op.BRANCH:
            return [t.attrs["true_target"], t.attrs["false_target"]]
        return []

    def to_json(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "label": self.label,
            "instructions": [i.to_json() for i in self.instructions],
            "terminator": self.terminator.to_json() if self.terminator else None,
            "predecessor_ids": list(self.preds),
        }


@dataclass
class Param:
    name: str
    value_id: str
    type: SemType
    annotation: str | None


DIVISION_PROPERTY = "division.zero_denominator"
INDEX_PROPERTY = "index.out_of_range"
NONE_PROPERTY = "none.attribute_access"
VIA_CALL_SUFFIX = ".via_call"


@dataclass
class ObligationSite:
    """A risky operation inside a symbol that carries one proof obligation.

    ``division.zero_denominator``: every syntactic ``/``, ``//`` or ``%`` (BinOp or
    AugAssign), collected before lowering. ``index.out_of_range``: subscripts whose
    receiver the frontend established as an exact built-in list/tuple.
    ``none.attribute_access``: attribute loads whose receiver may be ``None`` (exact
    optional types). A ``*.via_call`` property marks the same check inside a callee body
    that was inlined at a direct call site of this symbol (reachable-call evidence).
    """

    ordinal: int
    op: Op
    span: SourceSpan
    text: str
    divisor_text: str  # operand text: divisor, index expression or attribute receiver
    property_id: str = DIVISION_PROPERTY
    # Structural anchor for matching across snapshots (no line numbers; docs/07).
    anchor: str = ""
    required: bool = True
    # Call chain for inlined (via_call) sites:
    # [{"callee": symbol_id, "call": anchor, "call_span": span json, ...}, ...]
    via: list[dict[str, Any]] | None = None
    subject_text: str | None = None  # receiver text of an index/attribute site
    # Set when the site was lowered to an analyzable instruction.
    block_id: str | None = None
    result_id: str | None = None
    # Set when the site lies in an unsupported region / symbol.
    unsupported_reason: str | None = None
    # Set when an enclosing handler of this function definitely catches the site's exception
    # and does not re-raise (I-133): the failure is handled, so the site is advisory.
    handled_by: str | None = None

    @property
    def base_property(self) -> str:
        return self.property_id[: -len(VIA_CALL_SUFFIX)] if self.property_id.endswith(VIA_CALL_SUFFIX) else self.property_id


# Backwards-compatible name used by M1 code and tests.
DivisionSite = ObligationSite


@dataclass
class Function:
    symbol_id: str
    name: str
    params: list[Param]
    blocks: dict[str, Block]
    entry_id: str
    span: SourceSpan
    sites: list[ObligationSite]
    domain_note: str | None = None
    has_asserts: bool = False
    var_names: dict[str, str] = field(default_factory=dict)  # value id -> source variable name
    # M2 impact/summary data recorded during lowering (docs/07 graph model).
    fingerprint: str = ""  # digest of the position-free AST of this def
    body_fingerprint: str = ""  # same, with the def's own name blanked (rename detection)
    code_fingerprint: str = ""  # same as fingerprint with docstrings blanked (doc-only edits)
    deps: list[dict[str, Any]] = field(default_factory=list)  # resolution records (calls, globals, types)
    inlined: list[str] = field(default_factory=list)  # callee symbol ids instantiated at call sites
    unresolved_calls: list[str] = field(default_factory=list)  # dynamic/unknown call edges (disclosed)
    reads: list[dict[str, Any]] = field(default_factory=list)  # conservative global reads (impact graph only, not the key)

    @property
    def division_sites(self) -> list[ObligationSite]:
        return [s for s in self.sites if s.property_id == DIVISION_PROPERTY]

    def instr_by_result(self) -> dict[str, Instruction]:
        out: dict[str, Instruction] = {}
        for b in self.blocks.values():
            for ins in b.instructions:
                if ins.result is not None:
                    out[ins.result] = ins
        return out

    def def_block(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for b in self.blocks.values():
            for ins in b.instructions:
                if ins.result is not None:
                    out[ins.result] = b.block_id
        return out

    def exits(self) -> list[str]:
        return [b.block_id for b in self.blocks.values() if b.terminator is not None and b.terminator.op in (Op.RETURN, Op.RAISE)]

    def effects(self) -> list[str]:
        eff = set()
        for b in self.blocks.values():
            for ins in b.instructions:
                if ins.effect is not Effect.NONE:
                    eff.add(ins.effect.value)
        return sorted(eff)

    def topo_order(self) -> list[str]:
        """Topological order of blocks (acyclic by construction: loop back edges are not successors)."""
        indeg = {bid: 0 for bid in self.blocks}
        for b in self.blocks.values():
            for s in b.successors():
                indeg[s] += 1
        ready = [bid for bid in self.blocks if indeg[bid] == 0]
        order: list[str] = []
        # Stable: process in creation order.
        rank = {bid: i for i, bid in enumerate(self.blocks)}
        ready.sort(key=rank.__getitem__)
        while ready:
            bid = ready.pop(0)
            order.append(bid)
            for s in self.blocks[bid].successors():
                indeg[s] -= 1
                if indeg[s] == 0:
                    ready.append(s)
                    ready.sort(key=rank.__getitem__)
        if len(order) != len(self.blocks):
            raise AssertionError("CFG contains a cycle; M1 lowering must produce acyclic CFGs")
        return order

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "parameters": [
                {"name": p.name, "value_id": p.value_id, "semantic_type": p.type.to_json(), "annotation": p.annotation}
                for p in self.params
            ],
            "declared_domain": self.domain_note,
            "blocks": [b.to_json() for b in self.blocks.values()],
            "entry_id": self.entry_id,
            "exits": self.exits(),
            "effects": self.effects(),
            "source_span": self.span.to_json(),
        }


@dataclass
class UnsupportedSymbol:
    symbol_id: str
    kind: str
    reason: str
    span: SourceSpan
    division_sites: list[ObligationSite]
    fingerprint: str = ""  # whole definition (functions) or import-time code (module/class bodies)
    code_fingerprint: str = ""  # same with docstrings blanked (doc-only edits)
    reads: list[dict[str, Any]] = field(default_factory=list)  # conservative global reads (impact graph only)

    @property
    def sites(self) -> list[ObligationSite]:
        return self.division_sites


@dataclass
class ModuleIR:
    path: str
    source_digest: str
    functions: list[Function]
    unsupported_symbols: list[UnsupportedSymbol]
    imports: list[dict[str, Any]]
    requested_symbols: list[str]
    schema_version: str = IR_SCHEMA_VERSION
    language: str = "python"
    semantics_profile: str = "python3-cpython-builtin-v1"

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "language": self.language,
            "semantics_profile": self.semantics_profile,
            "path": self.path,
            "source_digest": self.source_digest,
            "symbols": [f.to_json() for f in self.functions],
            "imports": self.imports,
            "coverage": {
                "requested_symbols": list(self.requested_symbols),
                "lowered_symbols": [f.symbol_id for f in self.functions],
                "unsupported_symbols": [
                    {"symbol_id": u.symbol_id, "kind": u.kind, "reason": u.reason, "source_span": u.span.to_json()}
                    for u in self.unsupported_symbols
                ],
            },
        }


def check_ir_version(module: ModuleIR) -> None:
    if module.schema_version not in SUPPORTED_IR_VERSIONS:
        raise IncompatibleIRVersion(f"IR version {module.schema_version!r} not supported (supported: {sorted(SUPPORTED_IR_VERSIONS)})")


class IncompatibleIRVersion(Exception):
    pass
