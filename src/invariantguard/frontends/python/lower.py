"""Lower a supported module-level Python function to the typed minimal IR.

Supported (precisely): see docs/IMPLEMENTATION_STATUS.md "Supported Python subset".
Anything else inside a lowerable function becomes UNKNOWN_EFFECT: names it may bind
are rebound to unknown values and division sites inside it are UNKNOWN obligations.

M2 additions:

* **Direct call summaries.** A call ``f(...)`` whose name resolves (``resolve.py``) to an
  undecorated, supported module-level ``def`` is instantiated at the call site: the
  callee body is lowered into the caller CFG with its parameters bound to the caller's
  argument values (context-sensitive summary). Callee spans keep the callee's path.
  Recursion, depth/size bounds and unsupported callees stay unknown calls. A marker
  ``ASSIGN`` of the returned value records the resolution assumption.
* **Module constants** (``LIMIT = 10`` bound once) lower to constants with an attached
  resolution assumption.
* **Sequences.** List/tuple displays (``BUILD_SEQ``), builtin ``len`` on exact
  sequences (``LEN``) and subscripts of exact sequences (``INDEX``, obligation
  ``index.out_of_range``).
* **Narrow None-flow.** ``X | None`` / ``Optional[X]`` annotations and joins of ``None``
  with one exact kind give ``OPTIONAL`` values; ``x is not None`` / ``x is None`` tests
  narrow ``x`` in the guarded branch; attribute loads on possibly-``None`` receivers are
  obligations (``none.attribute_access``).

Every global/builtin lookup the lowering performs is recorded as a dependency record so
that summaries are invalidated when anything they read changes (docs/07).
"""

from __future__ import annotations

import ast
import symtable
from dataclasses import dataclass, field
from typing import Any

from ...ir.model import (
    DIVISION_OPS,
    DIVISION_PROPERTY,
    EXACT_NON_NONE,
    INDEX_PROPERTY,
    NONE_PROPERTY,
    NUMERIC_EXACT,
    SEQUENCE_KINDS,
    UNKNOWN_TYPE,
    VIA_CALL_SUFFIX,
    Block,
    Effect,
    Function,
    Instruction,
    Kind,
    ObligationSite,
    Op,
    Origin,
    Param,
    SemType,
)
from ...models.spans import LineIndex, SourceSpan, short_hex
from .resolve import ModuleContext, Resolution, ast_fingerprint, body_fingerprint, constant_value
from .subset import function_unsupported_reason
from .symbols import stored_names

_BINOPS = {ast.Add: Op.ADD, ast.Sub: Op.SUB, ast.Mult: Op.MUL, ast.Div: Op.DIV, ast.FloorDiv: Op.FLOORDIV, ast.Mod: Op.MOD}
_CMPOPS = {ast.Eq: "EQ", ast.NotEq: "NE", ast.Lt: "LT", ast.LtE: "LE", ast.Gt: "GT", ast.GtE: "GE"}
_FLAVORS = {
    Op.ADD: "python.bigint",
    Op.SUB: "python.bigint",
    Op.MUL: "python.bigint",
    Op.DIV: "python.true_division",
    Op.FLOORDIV: "python.floor_division",
    Op.MOD: "python.modulo",
}

def _sequence_result(op: Op, lk: Kind, rk: Kind) -> Kind | None:
    """Kind of a built-in sequence concatenation/repetition result, else None."""
    if op is Op.ADD and lk is rk and lk in SEQUENCE_KINDS:
        return lk
    if op is Op.MUL and lk in SEQUENCE_KINDS and rk in NUMERIC_EXACT:
        return lk
    if op is Op.MUL and rk in SEQUENCE_KINDS and lk in NUMERIC_EXACT:
        return rk
    return None


_STMT_REASONS = {
    ast.For: "for loop (loops are outside the supported subset)",
    ast.AsyncFor: "async for loop (outside the supported subset)",
    ast.While: "while loop (loops are outside the supported subset)",
    ast.Try: "exception handling (try/except) is outside the supported subset",
    ast.With: "with statement (context managers may run user code and suppress exceptions)",
    ast.AsyncWith: "async with statement (outside the supported subset)",
    ast.Match: "match statement (outside the supported subset)",
    ast.Delete: "del statement (outside the supported subset)",
    ast.Import: "import inside a function (never executed by guard; bound names unknown)",
    ast.ImportFrom: "import inside a function (never executed by guard; bound names unknown)",
    ast.FunctionDef: "nested function definition (separate unsupported symbol)",
    ast.AsyncFunctionDef: "nested async function definition (separate unsupported symbol)",
    ast.ClassDef: "nested class definition (outside the supported subset)",
}
if hasattr(ast, "TryStar"):
    _STMT_REASONS[ast.TryStar] = "exception groups (try/except*) are outside the supported subset"
if hasattr(ast, "TypeAlias"):
    _STMT_REASONS[ast.TypeAlias] = "type alias statement (outside the supported subset)"

# Attributes that exist on None (CPython 3.13): loading them never raises AttributeError.
NONE_ATTRS = frozenset(dir(None))

MAX_INLINE_DEPTH = 6  # nested call-site instantiations per analyzed function
MAX_FUNCTION_VALUES = 6000  # stop instantiating callees once a function's IR is this large

# Declared domain profiles (config [[domains]] profile) -> parameter type.
PROFILE_TYPES = {
    "python_exact_builtin_ints": SemType(Kind.INT, Origin.DECLARED),
    "python_exact_builtin_list": SemType(Kind.LIST, Origin.DECLARED),
    "python_exact_builtin_tuple": SemType(Kind.TUPLE, Origin.DECLARED),
    "python_optional_exact_builtin_int": SemType(Kind.OPTIONAL, Origin.DECLARED, Kind.INT),
}

_ANN_SCALARS = {"int": Kind.INT, "bool": Kind.BOOL, "float": Kind.FLOAT}
_ANN_SEQS = {"list": Kind.LIST, "tuple": Kind.TUPLE}


class LoweringError(Exception):
    """Internal lowering inconsistency: an engine error, never an unsupported construct."""


def find_table(module_table: symtable.SymbolTable, node: ast.FunctionDef) -> symtable.SymbolTable | None:
    candidates = [c for c in module_table.get_children() if c.get_name() == node.name and c.get_type() == symtable.SymbolTableType.FUNCTION]
    for c in candidates:
        if c.get_lineno() == node.lineno:
            return c
    return candidates[0] if len(candidates) == 1 else None


def _body_fingerprint(node: ast.FunctionDef) -> str:
    return body_fingerprint(node)


def join_types(types: list[SemType]) -> SemType:
    """Type of a PHI: one exact kind, or None joined with one exact kind (OPTIONAL)."""
    kinds = {t.kind for t in types}
    if len(kinds) == 1 and Kind.OPTIONAL not in kinds:
        return SemType(next(iter(kinds)), Origin.DERIVED)
    has_none = False
    inner: set[Kind] = set()
    for t in types:
        if t.kind is Kind.NONE:
            has_none = True
        elif t.kind is Kind.OPTIONAL and t.inner is not None:
            has_none = True
            inner.add(t.inner)
        else:
            inner.add(t.kind)
    if has_none and len(inner) == 1:
        k = next(iter(inner))
        if k in EXACT_NON_NONE:
            return SemType(Kind.OPTIONAL, Origin.DERIVED, k)
    return UNKNOWN_TYPE


def annotation_type(ann: ast.AST | None, lookup=None) -> tuple[SemType, str | None]:
    """Type from a parameter annotation (unenforced: callers get CONDITIONAL results).

    ``lookup(name)`` returns the module resolution of a name; a builtin annotation name
    that the module rebinds (e.g. ``int = MyInt``) is not trusted.
    """
    if ann is None:
        return UNKNOWN_TYPE, None
    shown = ast.unparse(ann)

    def builtin(name: str) -> bool:
        if lookup is None:
            return True
        return lookup(name).kind == "builtin"

    def scalar(node: ast.AST) -> Kind | None:
        text = None
        if isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
        if text in _ANN_SCALARS and builtin(text):
            return _ANN_SCALARS[text]
        if text in _ANN_SEQS and builtin(text):
            return _ANN_SEQS[text]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in _ANN_SEQS and builtin(node.value.id):
            return _ANN_SEQS[node.value.id]
        return None

    def is_none(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value is None

    k = scalar(ann)
    if k is not None:
        return SemType(k, Origin.ANNOTATION), shown
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        for a, b in ((ann.left, ann.right), (ann.right, ann.left)):
            if is_none(b):
                inner = scalar(a)
                if inner is not None:
                    return SemType(Kind.OPTIONAL, Origin.ANNOTATION, inner), shown
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) and ann.value.id == "Optional" and lookup is not None:
        res = lookup("Optional")
        if res.record.get("typing_optional"):
            inner = scalar(ann.slice)
            if inner is not None:
                return SemType(Kind.OPTIONAL, Origin.ANNOTATION, inner), shown
    return SemType(Kind.OBJECT, Origin.ANNOTATION), shown


@dataclass
class Frame:
    """One function body being lowered: the analyzed function or an inlined callee."""

    node: ast.FunctionDef
    symbol_id: str
    path: str
    lines: LineIndex
    local_names: set[str]
    ctx: ModuleContext
    depth: int = 0
    chain: tuple[dict[str, str], ...] = ()
    var_prefix: str = ""
    returns: list[tuple[Block, str]] = field(default_factory=list)
    cont: Block | None = None
    anchor_counts: dict[str, int] = field(default_factory=dict)


class FunctionLowerer:
    def __init__(
        self,
        *,
        node: ast.FunctionDef,
        symbol_id: str,
        path: str,
        snapshot_id: str,
        lines: LineIndex,
        local_names: set[str],
        module_functions: set[str] | None = None,  # unused since M2 (resolution via ctx); kept for callers
        declared_params: dict[str, str] | None,
        sites: dict[int, ObligationSite],
        ctx: ModuleContext | None = None,
    ) -> None:
        self.node = node
        self.symbol_id = symbol_id
        self.snapshot_id = snapshot_id
        self.declared_params = declared_params  # name -> profile, or None
        self.sites = sites
        if ctx is None:
            raise LoweringError("FunctionLowerer requires a ModuleContext since M2")
        self.root_frame = Frame(node=node, symbol_id=symbol_id, path=path, lines=lines, local_names=set(local_names), ctx=ctx)
        self.frame = self.root_frame
        self.blocks: dict[str, Block] = {}
        self.cur: Block | None = None
        self.entry: Block | None = None
        self._v = 0
        self._b = 0
        self.regions: list[tuple[SourceSpan, str]] = []
        self.var_names: dict[str, str] = {}
        self.types: dict[str, SemType] = {}
        self.consts: dict[str, Any] = {}
        self.debug_value: str | None = None
        # PHI values for variables that are unbound on at least one incoming path
        # (directly or through an earlier maybe-unbound PHI).
        self.maybe_unbound_values: set[str] = set()
        self.narrowed: set[str] = set()  # values narrowed to non-None by an `is (not) None` test
        self.has_asserts = any(isinstance(n, ast.Assert) for n in ast.walk(node))
        self.extra_sites: list[ObligationSite] = []
        self.ordinals: dict[str, int] = {}
        self.deps: dict[str, dict[str, Any]] = {}
        self.inlined: list[str] = []
        self.unresolved_calls: list[str] = []

    # -- helpers -------------------------------------------------------------------
    @property
    def path(self) -> str:
        return self.frame.path

    def span(self, node: ast.AST) -> SourceSpan:
        return self.frame.lines.node_span(self.frame.path, self.snapshot_id, node)

    def new_block(self, label: str) -> Block:
        bid = f"b{self._b}"
        self._b += 1
        blk = Block(bid, label)
        self.blocks[bid] = blk
        return blk

    def _new_value(self) -> str:
        rid = f"v{self._v}"
        self._v += 1
        return rid

    def emit(
        self,
        op: Op,
        operands: tuple[str, ...],
        typ: SemType | None,
        span: SourceSpan,
        effect: Effect = Effect.NONE,
        flavor: str | None = None,
        block: Block | None = None,
        **attrs: Any,
    ) -> str:
        target = block if block is not None else self.cur
        if target is None:
            raise LoweringError("emit into a terminated block")
        rid = self._new_value()
        if self.frame.depth > 0:
            attrs.setdefault("inlined_from", self.frame.symbol_id)
        target.instructions.append(Instruction(op, operands, rid, typ, span, effect, flavor, dict(attrs)))
        if typ is not None:
            self.types[rid] = typ
        return rid

    def terminate(self, block: Block, op: Op, operands: tuple[str, ...], span: SourceSpan, **attrs: Any) -> None:
        if block.terminator is not None:
            raise LoweringError(f"block {block.block_id} already terminated")
        block.terminator = Instruction(op, operands, None, None, span, Effect.NONE, None, dict(attrs))
        if op is Op.JUMP:
            self.blocks[attrs["target"]].preds.append(block.block_id)
        elif op is Op.BRANCH:
            self.blocks[attrs["true_target"]].preds.append(block.block_id)
            self.blocks[attrs["false_target"]].preds.append(block.block_id)

    def const(self, value: Any, span: SourceSpan, block: Block | None = None, **attrs: Any) -> str:
        if isinstance(value, bool):
            kind = Kind.BOOL
        elif isinstance(value, int):
            kind = Kind.INT
        elif isinstance(value, float):
            kind = Kind.FLOAT
        elif value is None:
            kind = Kind.NONE
        elif isinstance(value, str):
            kind = Kind.STR
        else:
            kind = Kind.OBJECT
        rid = self.emit(Op.CONST, (), SemType(kind, Origin.LITERAL), span, block=block, value=value, **attrs)
        self.consts[rid] = value
        return rid

    def unknown(self, span: SourceSpan, reason: str, operands: tuple[str, ...] = ()) -> str:
        return self.emit(Op.UNKNOWN_EFFECT, operands, UNKNOWN_TYPE, span, Effect.UNKNOWN, None, reason=reason)

    def region(self, node: ast.AST, reason: str) -> None:
        if self.frame.depth == 0:
            self.regions.append((self.span(node), reason))

    def kind(self, v: str) -> Kind:
        return self.types.get(v, UNKNOWN_TYPE).kind

    def typ(self, v: str) -> SemType:
        return self.types.get(v, UNKNOWN_TYPE)

    def name_var(self, v: str, name: str) -> None:
        self.var_names.setdefault(v, self.frame.var_prefix + name)

    def add_dep(self, record: dict[str, Any]) -> None:
        key = short_hex(record)
        self.deps.setdefault(key, dict(record))

    def resolve_global(self, name: str) -> Resolution:
        res = self.frame.ctx.lookup(name)
        self.add_dep(res.record)
        return res

    def ann_lookup(self, name: str) -> Resolution:
        """Resolution of an annotation name; ``Optional`` must come from ``typing``."""
        ctx = self.frame.ctx
        if name == "Optional":
            bs = ctx.bindings.get("Optional", [])
            ok = (
                len(bs) == 1 and bs[0].top_level and isinstance(bs[0].node, ast.ImportFrom)
                and bs[0].node.module == "typing" and bs[0].node.level == 0
                and any(a.name == "Optional" and (a.asname or a.name) == "Optional" for a in bs[0].node.names)
                and not ctx.star_import and "Optional" not in ctx.global_decls
            )
            rec = {"kind": "global", "module": ctx.path, "name": "Optional", "resolution": "typing.Optional" if ok else "unresolved",
                   "descriptor": short_hex(ctx.name_descriptor("Optional")), "typing_optional": ok}
            self.add_dep(rec)
            return Resolution("const" if ok else "unresolved", "Optional", rec)
        return self.resolve_global(name)

    def ensure_debug(self) -> str:
        """``__debug__`` runtime-mode input (python -O disables asserts), created on demand."""
        if self.debug_value is None:
            assert self.entry is not None
            rid = self._new_value()
            typ = SemType(Kind.BOOL, Origin.RUNTIME_MODE)
            ins = Instruction(Op.PARAM, (), rid, typ, self.root_frame.lines.node_span(self.root_frame.path, self.snapshot_id, self.node),
                              Effect.NONE, None, {"name": "__debug__", "runtime_mode": True})
            self.entry.instructions.insert(0, ins)
            self.types[rid] = typ
            self.var_names[rid] = "__debug__"
            self.debug_value = rid
            self.has_asserts = True
        return self.debug_value

    # -- obligation sites ---------------------------------------------------------------
    def next_anchor(self, prop: str, node: ast.AST) -> str:
        """Structural anchor of an operation (numbered per frame, shared by obligation and
        non-obligation occurrences so that both versions of a function number alike)."""
        f = self.frame
        dump = ast.dump(node, include_attributes=False)
        key = prop + "|" + dump
        n = f.anchor_counts.get(key, 0)
        f.anchor_counts[key] = n + 1
        local_anchor = f"{prop}:{short_hex([prop, dump], 16)}#{n}"
        return local_anchor if f.depth == 0 else "".join(f"{c['call']}>" for c in f.chain) + local_anchor

    def new_site(self, prop: str, op: Op, node: ast.AST, operand: ast.AST, subject: ast.AST | None = None) -> ObligationSite:
        f = self.frame
        anchor = self.next_anchor(prop, node)
        if f.depth == 0:
            prop_id, required, via = prop, True, None
        else:
            prop_id, required = prop + VIA_CALL_SUFFIX, False
            via = [dict(c) for c in f.chain]
        ordinal = self.ordinals.get(prop_id, 0)
        self.ordinals[prop_id] = ordinal + 1
        span = self.span(node)
        site = ObligationSite(
            ordinal=ordinal, op=op, span=span, text=f.lines.text(span),
            divisor_text=f.lines.text(self.span(operand)), property_id=prop_id, anchor=anchor,
            required=required, via=via, subject_text=f.lines.text(self.span(subject)) if subject is not None else None,
        )
        self.extra_sites.append(site)
        return site

    # -- function --------------------------------------------------------------------
    def lower(self) -> Function:
        node = self.node
        entry = self.new_block("entry")
        self.entry = entry
        self.cur = entry
        env: dict[str, str] = {}
        params: list[Param] = []
        args = node.args
        declared = self.declared_params or {}
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            ann_type, shown = annotation_type(a.annotation, self.ann_lookup)
            if a.arg in declared:
                typ = PROFILE_TYPES.get(declared[a.arg])
                if typ is None:
                    raise LoweringError(f"unknown domain profile {declared[a.arg]!r}")
            else:
                typ = ann_type
            v = self.emit(Op.PARAM, (), typ, self.span(a), name=a.arg)
            env[a.arg] = v
            self.var_names[v] = a.arg
            params.append(Param(a.arg, v, typ, shown))
        for a in (args.vararg, args.kwarg):
            if a is None:
                continue
            v = self.emit(Op.PARAM, (), SemType(Kind.OBJECT, Origin.UNKNOWN), self.span(a), name=a.arg, variadic=True)
            env[a.arg] = v
            self.var_names[v] = a.arg
            params.append(Param(a.arg, v, SemType(Kind.OBJECT, Origin.UNKNOWN), None))
        if self.has_asserts:
            self.ensure_debug()
        end, env = self.lower_stmts(node.body, entry, env)
        if end is not None:
            none = self.const(None, self.span(node), block=end)
            self.terminate(end, Op.RETURN, (none,), self.span(node), implicit=True)
        self._assign_unlowered_sites()
        all_sites = sorted(self.sites.values(), key=lambda s: s.ordinal) + self.extra_sites
        fn = Function(
            symbol_id=self.symbol_id,
            name=node.name,
            params=params,
            blocks=self.blocks,
            entry_id=entry.block_id,
            span=self.root_frame.lines.node_span(self.root_frame.path, self.snapshot_id, node),
            sites=all_sites,
            has_asserts=self.has_asserts,
            var_names=self.var_names,
            fingerprint=ast_fingerprint(node),
            body_fingerprint=_body_fingerprint(node),
            deps=[self.deps[k] for k in sorted(self.deps)],
            inlined=sorted(set(self.inlined)),
            unresolved_calls=sorted(set(self.unresolved_calls)),
        )
        fn.topo_order()  # asserts acyclicity
        return fn

    def _assign_unlowered_sites(self) -> None:
        for site in self.sites.values():
            if site.block_id is not None:
                continue
            best: tuple[int, str] | None = None
            for rspan, reason in self.regions:
                if rspan.path == site.span.path and rspan.start_byte <= site.span.start_byte and site.span.end_byte <= rspan.end_byte:
                    size = rspan.end_byte - rspan.start_byte
                    if best is None or size < best[0]:
                        best = (size, reason)
            if best is None:
                raise LoweringError(f"division site {site.ordinal} of {self.symbol_id} was neither lowered nor inside an unsupported region")
            site.unsupported_reason = best[1]

    # -- statements -------------------------------------------------------------------
    def lower_stmts(self, stmts: list[ast.stmt], block: Block | None, env: dict[str, str]) -> tuple[Block | None, dict[str, str]]:
        for i, stmt in enumerate(stmts):
            if block is None:
                # Statically dead code after return/raise: lower into a block without
                # predecessors so its obligations are accounted for (as unreachable).
                block = self.new_block("unreachable")
                env = dict(env)
            if i == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str) and stmts is self.frame.node.body:
                continue  # docstring
            self.cur = block
            block, env = self.lower_stmt(stmt, env)
        return block, env

    def lower_stmt(self, stmt: ast.stmt, env: dict[str, str]) -> tuple[Block | None, dict[str, str]]:
        if isinstance(stmt, ast.Pass):
            return self.cur, env
        if isinstance(stmt, ast.Expr):
            self.expr(stmt.value, env)
            return self.cur, env
        if isinstance(stmt, ast.Assign):
            if all(isinstance(t, ast.Name) for t in stmt.targets):
                v = self.expr(stmt.value, env)
                for t in stmt.targets:
                    env[t.id] = v
                    self.name_var(v, t.id)
                return self.cur, env
            return self.unsupported_stmt(stmt, env, "assignment to a non-name target (tuple/attribute/subscript) is outside the supported subset")
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.simple:
                if stmt.value is None:
                    return self.cur, env  # annotation only; binds nothing
                v = self.expr(stmt.value, env)
                env[stmt.target.id] = v
                self.name_var(v, stmt.target.id)
                return self.cur, env
            return self.unsupported_stmt(stmt, env, "annotated assignment to a non-name target is outside the supported subset")
        if isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name) and type(stmt.op) in _BINOPS:
                cur = self.load(stmt.target.id, stmt.target, env)
                rhs = self.expr(stmt.value, env)
                v = self.binop(_BINOPS[type(stmt.op)], cur, rhs, stmt, augmented=True)
                env[stmt.target.id] = v
                self.name_var(v, stmt.target.id)
                return self.cur, env
            return self.unsupported_stmt(stmt, env, "augmented assignment with this target/operator is outside the supported subset")
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                v = self.const(None, self.span(stmt))
            else:
                v = self.expr(stmt.value, env)
            block = self.cur
            assert block is not None
            if self.frame.depth > 0:
                cont = self.frame.cont
                assert cont is not None
                self.frame.returns.append((block, v))
                self.terminate(block, Op.JUMP, (), self.span(stmt), target=cont.block_id, inlined_return=True)
            else:
                self.terminate(block, Op.RETURN, (v,), self.span(stmt))
            return None, env
        if isinstance(stmt, ast.Raise):
            ops: tuple[str, ...] = ()
            if stmt.exc is not None:
                ops = (self.expr(stmt.exc, env),)
            if stmt.cause is not None:
                ops = ops + (self.expr(stmt.cause, env),)
            block = self.cur
            assert block is not None
            self.terminate(block, Op.RAISE, ops, self.span(stmt), exception=ast.unparse(stmt.exc) if stmt.exc else "re-raise")
            return None, env
        if isinstance(stmt, ast.If):
            return self.lower_if(stmt, env)
        if isinstance(stmt, ast.Assert):
            return self.lower_assert(stmt, env)
        reason = _STMT_REASONS.get(type(stmt), f"{type(stmt).__name__} statement is outside the supported subset")
        return self.unsupported_stmt(stmt, env, reason)

    def unsupported_stmt(self, stmt: ast.stmt, env: dict[str, str], reason: str) -> tuple[Block | None, dict[str, str]]:
        self.region(stmt, reason)
        span = self.span(stmt)
        self.emit(Op.UNKNOWN_EFFECT, (), None, span, Effect.UNKNOWN, None, reason=reason)
        for name in sorted(stored_names(stmt) & self.frame.local_names):
            v = self.unknown(span, f"'{name}' may be rebound inside unsupported construct: {reason}")
            env[name] = v
            self.var_names[v] = self.frame.var_prefix + name
        return self.cur, env

    @staticmethod
    def _none_test(test: ast.expr) -> tuple[str, bool] | None:
        """(name, narrowed_when_true) for ``name is not None`` / ``name is None`` tests."""
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            inner = FunctionLowerer._none_test(test.operand)
            return (inner[0], not inner[1]) if inner else None
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot)):
            left, right = test.left, test.comparators[0]
            if isinstance(right, ast.Constant) and right.value is None and isinstance(left, ast.Name):
                name = left.id
            elif isinstance(left, ast.Constant) and left.value is None and isinstance(right, ast.Name):
                name = right.id
            else:
                return None
            return name, isinstance(test.ops[0], ast.IsNot)
        return None

    def _narrow(self, block: Block, env: dict[str, str], name: str, node: ast.AST) -> None:
        v = env.get(name)
        if v is None or v in self.maybe_unbound_values:
            return
        t = self.typ(v)
        if t.kind is not Kind.OPTIONAL or t.inner is None:
            return
        nv = self.emit(Op.ASSIGN, (v,), SemType(t.inner, t.origin), self.span(node), block=block, narrowed=True, variable=name)
        self.narrowed.add(nv)
        env[name] = nv
        self.var_names[nv] = self.frame.var_prefix + name

    def lower_if(self, stmt: ast.If, env: dict[str, str]) -> tuple[Block | None, dict[str, str]]:
        then_b = self.new_block("then")
        else_b = self.new_block("else")
        start = self.cur
        assert start is not None
        self.lower_test(stmt.test, start, env, then_b, else_b, kind="if")
        then_env, else_env = dict(env), dict(env)
        nt = self._none_test(stmt.test)
        if nt is not None:
            name, when_true = nt
            self._narrow(then_b if when_true else else_b, then_env if when_true else else_env, name, stmt.test)
        then_end, then_env = self.lower_stmts(stmt.body, then_b, then_env)
        else_end, else_env = self.lower_stmts(stmt.orelse, else_b, else_env)
        return self.join([(then_end, then_env), (else_end, else_env)], self.span(stmt))

    def lower_assert(self, stmt: ast.Assert, env: dict[str, str]) -> tuple[Block, dict[str, str]]:
        debug = self.ensure_debug()
        check_b = self.new_block("assert_check")
        fail_b = self.new_block("assert_fail")
        cont_b = self.new_block("assert_cont")
        span = self.span(stmt)
        block = self.cur
        assert block is not None
        self.terminate(block, Op.BRANCH, (debug,), span, true_target=check_b.block_id, false_target=cont_b.block_id, kind="assertions_enabled")
        self.lower_test(stmt.test, check_b, env, cont_b, fail_b, kind="assert")
        ops: tuple[str, ...] = ()
        if stmt.msg is not None:
            self.cur = fail_b
            ops = (self.expr(stmt.msg, dict(env)),)
        fail_end = self.cur if stmt.msg is not None else fail_b
        assert fail_end is not None
        self.terminate(fail_end, Op.RAISE, ops, span, exception="AssertionError")
        self.cur = cont_b
        return cont_b, env

    def join(self, ends: list[tuple[Block | None, dict[str, str]]], span: SourceSpan) -> tuple[Block | None, dict[str, str]]:
        live = [(b, e) for b, e in ends if b is not None]
        if not live:
            return None, {}
        join_b = self.new_block("join")
        for b, _ in live:
            self.terminate(b, Op.JUMP, (), span, target=join_b.block_id)
        names = sorted(set().union(*(e.keys() for _, e in live)))
        env: dict[str, str] = {}
        for name in names:
            vals = [e.get(name) for _, e in live]
            if all(v == vals[0] for v in vals) and vals[0] is not None:
                env[name] = vals[0]
                continue
            incoming = [(b.block_id, e.get(name)) for b, e in live]
            maybe_unbound = any(v is None or v in self.maybe_unbound_values for v in vals)
            if not maybe_unbound:
                typ = join_types([self.typ(v) for v in vals if v is not None])
            else:
                typ = UNKNOWN_TYPE
            ops = tuple(v for v in vals if v is not None)
            v = self.emit(Op.PHI, ops, typ, span, block=join_b, incoming=incoming, variable=self.frame.var_prefix + name, maybe_unbound=maybe_unbound)
            if maybe_unbound:
                self.maybe_unbound_values.add(v)
            env[name] = v
            self.var_names[v] = self.frame.var_prefix + name
        self.cur = join_b
        return join_b, env

    # -- branch conditions ---------------------------------------------------------------
    def lower_test(self, test: ast.expr, block: Block, env: dict[str, str], t: Block, f: Block, kind: str) -> None:
        if isinstance(test, ast.BoolOp):
            cur = block
            for i, value in enumerate(test.values):
                last = i == len(test.values) - 1
                if last:
                    self.lower_test(value, cur, env, t, f, kind)
                else:
                    mid = self.new_block("and" if isinstance(test.op, ast.And) else "or")
                    if isinstance(test.op, ast.And):
                        self.lower_test(value, cur, env, mid, f, kind)
                    else:
                        self.lower_test(value, cur, env, t, mid, kind)
                    cur = mid
            return
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            self.lower_test(test.operand, block, env, f, t, kind)
            return
        if isinstance(test, ast.Compare) and len(test.ops) > 1:
            self.cur = block
            left = self.expr(test.left, env)
            for i, (op, comp) in enumerate(zip(test.ops, test.comparators)):
                right = self.expr(comp, env)
                c = self.compare(op, left, right, test)
                last = i == len(test.ops) - 1
                nxt = t if last else self.new_block("chain")
                cur = self.cur
                assert cur is not None
                self.terminate(cur, Op.BRANCH, (c,), self.span(test), true_target=nxt.block_id, false_target=f.block_id, kind=kind)
                if not last:
                    self.cur = nxt
                left = right
            return
        self.cur = block
        v = self.expr(test, env)
        c = self.truth(v, test)
        cur = self.cur
        assert cur is not None
        self.terminate(cur, Op.BRANCH, (c,), self.span(test), true_target=t.block_id, false_target=f.block_id, kind=kind)

    def truth(self, v: str, node: ast.AST) -> str:
        k = self.kind(v)
        span = self.span(node)
        boolean = SemType(Kind.BOOL, Origin.DERIVED)
        if k is Kind.BOOL:
            return v
        if k is Kind.INT:
            zero = self.const(0, span)
            return self.emit(Op.COMPARE, (v, zero), boolean, span, cmp="NE", synthesized="truthiness")
        if k is Kind.FLOAT:
            zero = self.const(0.0, span)
            return self.emit(Op.COMPARE, (v, zero), boolean, span, cmp="NE", synthesized="truthiness")
        if k is Kind.NONE:
            return self.const(False, span)
        if k is Kind.STR:
            empty = self.const("", span)
            return self.emit(Op.COMPARE, (v, empty), boolean, span, cmp="NE", synthesized="truthiness")
        if k in SEQUENCE_KINDS:
            n = self.emit(Op.LEN, (v,), SemType(Kind.INT, Origin.DERIVED), span, synthesized="truthiness")
            zero = self.const(0, span)
            return self.emit(Op.COMPARE, (n, zero), boolean, span, cmp="NE", synthesized="truthiness")
        if k is Kind.OPTIONAL:
            # bool(None) and bool() of an exact built-in value never run user code.
            return self.emit(Op.UNKNOWN_EFFECT, (v,), SemType(Kind.BOOL, Origin.UNKNOWN), span, Effect.NONE, None,
                             reason="truthiness of an optional value is not modeled")
        return self.emit(
            Op.UNKNOWN_EFFECT, (v,), SemType(Kind.BOOL, Origin.UNKNOWN), span, Effect.UNKNOWN, None,
            reason="truthiness of a value of unknown type may call __bool__/__len__",
        )

    # -- expressions ----------------------------------------------------------------------
    def load(self, name: str, node: ast.AST, env: dict[str, str]) -> str:
        span = self.span(node)
        if name in env:
            v = env[name]
            if v in self.maybe_unbound_values:
                # Reading a local that is unbound on some path raises UnboundLocalError at
                # the read itself (even for a plain alias ``x = d``), so the read is an
                # explicit, checked instruction rather than a silent alias (audit A-03).
                r = self.emit(
                    Op.ASSIGN, (v,), UNKNOWN_TYPE, span, Effect.MAY_RAISE, None,
                    checked_read=True, variable=name,
                    reason=f"variable '{name}' may be unbound on some path (UnboundLocalError)",
                )
                self.var_names[r] = self.frame.var_prefix + name
                return r
            return v
        if name in self.frame.local_names:
            return self.unknown(span, f"local variable '{name}' is unbound here (UnboundLocalError)")
        res = self.resolve_global(name)
        if res.kind == "const":
            attrs = {"global": name, "resolution_assumption": res.assumption, "resolution_conditional": res.conditional}
            if isinstance(res.value, tuple):
                elems = tuple(self.const(x, span) for x in res.value)
                return self.emit(Op.BUILD_SEQ, elems, SemType(Kind.TUPLE, Origin.LITERAL), span, length=len(elems), **attrs)
            return self.const(res.value, span, **attrs)
        if res.kind == "function":
            return self.unknown(span, f"function object '{name}' used as a value is not modeled")
        why = res.reason or "not modeled"
        return self.unknown(span, f"global or builtin name '{name}' is not modeled ({why})")

    def expr(self, node: ast.expr, env: dict[str, str]) -> str:
        span = self.span(node)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (bool, int, float, str)) or node.value is None:
                return self.const(node.value, span)
            self.region(node, f"{type(node.value).__name__} constants are outside the supported subset")
            return self.unknown(span, f"{type(node.value).__name__} constant not modeled")
        if isinstance(node, ast.Name):
            return self.load(node.id, node, env)
        if isinstance(node, ast.BinOp):
            left = self.expr(node.left, env)
            right = self.expr(node.right, env)
            op = _BINOPS.get(type(node.op))
            if op is None:
                return self.emit(
                    Op.UNKNOWN_EFFECT, (left, right), UNKNOWN_TYPE, span, Effect.UNKNOWN, None,
                    reason=f"operator {type(node.op).__name__} is not modeled",
                )
            return self.binop(op, left, right, node)
        if isinstance(node, ast.UnaryOp):
            operand = self.expr(node.operand, env)
            k = self.kind(operand)
            if isinstance(node.op, (ast.USub, ast.UAdd)) and k in NUMERIC_EXACT:
                zero = self.const(0, span)
                op = Op.SUB if isinstance(node.op, ast.USub) else Op.ADD
                return self.emit(op, (zero, operand), SemType(Kind.INT, Origin.DERIVED), span, Effect.NONE, "python.bigint", synthesized="unary")
            return self.emit(
                Op.UNKNOWN_EFFECT, (operand,), UNKNOWN_TYPE, span, Effect.UNKNOWN, None,
                reason=f"unary {type(node.op).__name__} on {k.value} is not modeled",
            )
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left = self.expr(node.left, env)
            right = self.expr(node.comparators[0], env)
            return self.compare(node.ops[0], left, right, node)
        if isinstance(node, ast.Call):
            return self.call(node, env)
        if isinstance(node, ast.Attribute):
            base = self.expr(node.value, env)
            k = self.kind(base)
            if (k in (Kind.NONE, Kind.OPTIONAL) or base in self.narrowed) and node.attr not in NONE_ATTRS:
                site = self.new_site(NONE_PROPERTY, Op.ATTRIBUTE, node, node.value, node.value)
                rid = self.emit(
                    Op.ATTRIBUTE, (base,), UNKNOWN_TYPE, span, Effect.MAY_RAISE, "python.attribute",
                    attribute=node.attr, obligation_property=site.property_id, obligation_ordinal=site.ordinal,
                    reason=f"attribute .{node.attr} of a possibly-None receiver: the value is not modeled",
                )
                site.block_id, site.result_id = self.cur.block_id, rid  # type: ignore[union-attr]
                return rid
            if k in EXACT_NON_NONE:
                # Anchor non-obligation loads on exact receivers too: the same load may be an
                # obligation in the other snapshot (e.g. before a None guard was added), and
                # RESOLVED attribution needs to recognize it. Unknown receivers need none.
                return self.emit(Op.ATTRIBUTE, (base,), UNKNOWN_TYPE, span, Effect.MAY_RAISE, None, attribute=node.attr,
                                 anchor=self.next_anchor(NONE_PROPERTY, node), reason=f"attribute access .{node.attr} not modeled")
            return self.emit(Op.ATTRIBUTE, (base,), UNKNOWN_TYPE, span, Effect.UNKNOWN, None, attribute=node.attr,
                             reason=f"attribute access .{node.attr} not modeled")
        if isinstance(node, ast.Subscript):
            base = self.expr(node.value, env)
            if isinstance(node.slice, ast.Slice):
                self.region(node, "slicing is outside the supported subset")
                return self.unknown(span, "slicing is not modeled", (base,))
            idx = self.expr(node.slice, env)
            if self.kind(base) in SEQUENCE_KINDS:
                site = self.new_site(INDEX_PROPERTY, Op.INDEX, node, node.slice, node.value)
                rid = self.emit(
                    Op.INDEX, (base, idx), UNKNOWN_TYPE, span, Effect.MAY_RAISE, "python.sequence_index",
                    obligation_property=site.property_id, obligation_ordinal=site.ordinal,
                    reason="element value of a built-in sequence is not modeled",
                )
                site.block_id, site.result_id = self.cur.block_id, rid  # type: ignore[union-attr]
                return rid
            return self.emit(Op.INDEX, (base, idx), UNKNOWN_TYPE, span, Effect.UNKNOWN, None,
                             reason=f"indexing a value of type {self.kind(base).value} is not modeled")
        if isinstance(node, (ast.List, ast.Tuple)) and isinstance(node.ctx, ast.Load) and not any(isinstance(e, ast.Starred) for e in node.elts):
            elems = tuple(self.expr(e, env) for e in node.elts)
            kind = Kind.LIST if isinstance(node, ast.List) else Kind.TUPLE
            return self.emit(Op.BUILD_SEQ, elems, SemType(kind, Origin.LITERAL), span, length=len(elems))
        reason = f"{type(node).__name__} expression is outside the supported subset"
        self.region(node, reason)
        return self.unknown(span, reason)

    # -- calls ---------------------------------------------------------------------------
    def _bind_args(self, call: ast.Call, callee: ast.FunctionDef) -> tuple[list[tuple[str, ast.expr | None, Any]], str | None]:
        """Static argument binding: [(param, arg_node or None, default)] or a reason it is not modeled."""
        a = callee.args
        if a.vararg is not None or a.kwarg is not None:
            return [], "callee has *args/**kwargs parameters"
        if any(isinstance(x, ast.Starred) for x in call.args) or any(k.arg is None for k in call.keywords):
            return [], "call uses * or ** argument unpacking"
        positional = a.posonlyargs + a.args
        if len(call.args) > len(positional):
            return [], "too many positional arguments (TypeError is not modeled)"
        bound: dict[str, ast.expr] = {}
        for p, arg in zip(positional, call.args):
            bound[p.arg] = arg
        posonly = {p.arg for p in a.posonlyargs}
        allowed = {p.arg for p in a.args + a.kwonlyargs}
        for kw in call.keywords:
            if kw.arg in posonly or kw.arg not in allowed or kw.arg in bound:
                return [], f"keyword argument '{kw.arg}' does not bind cleanly (TypeError is not modeled)"
            bound[kw.arg] = kw.value
        defaults = dict(zip([p.arg for p in positional][len(positional) - len(a.defaults):], a.defaults))
        defaults.update({p.arg: d for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None})
        out: list[tuple[str, ast.expr | None, Any]] = []
        for p in positional + a.kwonlyargs:
            if p.arg in bound:
                out.append((p.arg, bound[p.arg], None))
                continue
            if p.arg not in defaults:
                return [], f"missing argument '{p.arg}' (TypeError is not modeled)"
            ok, value = constant_value(defaults[p.arg])
            if not ok:
                return [], f"default value of '{p.arg}' is not a literal constant"
            out.append((p.arg, None, value))
        return out, None

    def _inline_blocker(self, res: Resolution, call: ast.Call) -> tuple[str | None, list[tuple[str, ast.expr | None, Any]], Any]:
        callee = res.node
        assert isinstance(callee, ast.FunctionDef) and res.ctx is not None
        self.add_dep({"kind": "function_body", "symbol": res.symbol_id, "fingerprint": ast_fingerprint(callee)})
        sym = res.ctx.symbol_for(callee)
        if sym is None:
            return "callee symbol not found", [], None
        why = function_unsupported_reason(sym)
        if why is not None:
            return f"callee is unsupported: {why}", [], None
        active = {self.symbol_id} | {c["callee"] for c in self.frame.chain}
        if res.symbol_id in active:
            return "recursive call (strongly connected component) is not summarized", [], None
        if self.frame.depth + 1 > MAX_INLINE_DEPTH:
            return f"call depth bound {MAX_INLINE_DEPTH} reached", [], None
        if self._v > MAX_FUNCTION_VALUES:
            return f"summary instantiation size bound ({MAX_FUNCTION_VALUES} IR values) reached", [], None
        table = find_table(res.ctx.parsed.symtable, callee)
        if table is None:
            return "callee symbol table not found", [], None
        binding, why = self._bind_args(call, callee)
        if why is not None:
            return why, [], None
        return None, binding, table

    def call(self, node: ast.Call, env: dict[str, str]) -> str:
        span = self.span(node)
        func = node.func
        callee_text = ast.unparse(func)
        res: Resolution | None = None
        resolution = "unresolved"
        blocker: str | None = None
        if isinstance(func, ast.Name):
            if func.id in env or func.id in self.frame.local_names:
                resolution = "local_variable"
            else:
                res = self.resolve_global(func.id)
                resolution = {"function": "module_function", "builtin": "builtin", "const": "constant"}.get(res.kind, "unresolved")
                if res.kind == "unresolved":
                    blocker = res.reason
        if res is not None and res.kind == "builtin" and func.id == "len" and len(node.args) == 1 and not node.keywords and not isinstance(node.args[0], ast.Starred):  # type: ignore[union-attr]
            arg = self.expr(node.args[0], env)
            if self.kind(arg) in SEQUENCE_KINDS:
                return self.emit(Op.LEN, (arg,), SemType(Kind.INT, Origin.DERIVED), span, callee="len", builtin=True)
            return self._unknown_call(span, callee_text, (arg,), "builtin", f"len() of a value of type {self.kind(arg).value} is not modeled")
        if res is not None and res.kind == "function":
            blocker, binding, table = self._inline_blocker(res, node)
            if blocker is None:
                return self._inline(node, res, binding, table, env)
        # Generic unknown call: evaluate the callee expression and arguments once.
        ops: list[str] = []
        if not isinstance(func, ast.Name):
            ops.append(self.expr(func, env))
        for a in node.args:
            inner = a.value if isinstance(a, ast.Starred) else a
            ops.append(self.expr(inner, env))
        for kw in node.keywords:
            ops.append(self.expr(kw.value, env))
        if res is not None and res.kind == "builtin":
            why = f"builtin '{callee_text}' is not modeled"
        elif blocker:
            why = blocker
        elif resolution == "local_variable":
            why = "callee is a local variable"
        else:
            why = "callee is not a resolvable module-level function"
        return self._unknown_call(span, callee_text, tuple(ops), resolution, why)

    def _unknown_call(self, span: SourceSpan, callee: str, ops: tuple[str, ...], resolution: str, why: str) -> str:
        self.unresolved_calls.append(f"{callee} ({why})")
        return self.emit(
            Op.CALL, ops, UNKNOWN_TYPE, span, Effect.UNKNOWN, None,
            callee=callee, resolution=resolution,
            reason=f"call to '{callee}' ({resolution}): result and effects unknown: {why}",
        )

    def _inline(self, node: ast.Call, res: Resolution, binding, table, env: dict[str, str]) -> str:
        callee = res.node
        assert isinstance(callee, ast.FunctionDef) and res.ctx is not None and res.symbol_id is not None
        span = self.span(node)
        # Arguments are evaluated in the caller frame exactly once, in call-site order
        # (positional arguments, then keywords as written) -- CPython's order, which can
        # differ from the parameter order when keywords are used.
        by_node: dict[int, str] = {}
        for a in list(node.args) + [kw.value for kw in node.keywords]:
            by_node[id(a)] = self.expr(a, env)
        arg_values: dict[str, str] = {pname: by_node[id(arg_node)] for pname, arg_node, _ in binding if arg_node is not None}
        f = self.frame
        dump = ast.dump(node, include_attributes=False)
        key = "call|" + dump
        n = f.anchor_counts.get(key, 0)
        f.anchor_counts[key] = n + 1
        call_anchor = f"call:{short_hex([res.symbol_id, dump], 16)}#{n}"
        cont = self.new_block(f"return:{callee.name}")
        local_names = {s.get_name() for s in table.get_symbols() if s.is_local() or s.is_parameter()}
        frame = Frame(
            node=callee, symbol_id=res.symbol_id, path=res.ctx.path, lines=res.ctx.parsed.lines,
            local_names=local_names, ctx=res.ctx, depth=f.depth + 1,
            chain=f.chain + ({"callee": res.symbol_id, "call": call_anchor, "assumption": res.assumption,
                              "conditional": "yes" if res.conditional else "no", "call_span": span.to_json()},),
            var_prefix=f"{callee.name}.", cont=cont,
        )
        callee_env: dict[str, str] = {}
        self.frame = frame
        try:
            for pname, arg_node, default in binding:
                if arg_node is None:
                    a = next(x for x in callee.args.posonlyargs + callee.args.args + callee.args.kwonlyargs if x.arg == pname)
                    callee_env[pname] = self.const(default, self.span(a), default_of=pname)
                else:
                    callee_env[pname] = arg_values[pname]
            if any(isinstance(x, ast.Assert) for x in ast.walk(callee)):
                self.ensure_debug()
            end, _ = self.lower_stmts(callee.body, self.cur, callee_env)
            if end is not None:
                none = self.const(None, self.span(callee), block=end)
                frame.returns.append((end, none))
                self.terminate(end, Op.JUMP, (), self.span(callee), target=cont.block_id, inlined_return=True)
        finally:
            self.frame = f
        self.cur = cont
        rets = frame.returns
        if not rets:
            value = self.const(None, span)  # unreachable continuation: the callee always raises
        elif len(rets) == 1:
            value = rets[0][1]
        else:
            typ = join_types([self.typ(v) for _, v in rets])
            value = self.emit(Op.PHI, tuple(v for _, v in rets), typ, span, incoming=[(b.block_id, v) for b, v in rets],
                              variable=f"{callee.name}()", maybe_unbound=False)
        marker = self.emit(
            Op.ASSIGN, (value,), self.typ(value), span, Effect.NONE, None,
            inlined_call=res.symbol_id, callee=ast.unparse(node.func),
            resolution_assumption=res.assumption, resolution_conditional=res.conditional,
        )
        self.var_names[marker] = f"{callee.name}(...)"
        if value in self.narrowed:
            self.narrowed.add(marker)
        self.inlined.append(res.symbol_id)
        return marker

    # -- operators ------------------------------------------------------------------------
    def binop(self, op: Op, left: str, right: str, node: ast.AST, augmented: bool = False) -> str:
        lk, rk = self.kind(left), self.kind(right)
        span = self.span(node)
        if lk in NUMERIC_EXACT and rk in NUMERIC_EXACT:
            if op is Op.DIV:
                typ, effect = SemType(Kind.FLOAT, Origin.DERIVED), Effect.MAY_RAISE
            else:
                typ = SemType(Kind.INT, Origin.DERIVED)
                effect = Effect.MAY_RAISE if op in DIVISION_OPS else Effect.NONE
            flavor = _FLAVORS[op]
        elif {lk, rk} <= {Kind.INT, Kind.BOOL, Kind.FLOAT}:
            typ, effect, flavor = SemType(Kind.FLOAT, Origin.DERIVED), Effect.MAY_RAISE, "python.float64"
        elif (seq_kind := _sequence_result(op, lk, rk)) is not None:
            # Built-in list/tuple concatenation (same kind) and repetition by an exact int:
            # CPython dispatches these to the sequence itself (int.__mul__/__add__ return
            # NotImplemented for a sequence operand), so the result is an exact sequence of
            # the same kind. Without this the result became an untyped object and every
            # later index on it silently stopped being an obligation (audit A2-02).
            typ = SemType(seq_kind, Origin.DERIVED)
            flavor = "python.sequence_concat" if op is Op.ADD else "python.sequence_repeat"
            effect = Effect.NONE if op is Op.ADD else Effect.MAY_RAISE  # huge repeat counts raise
            if augmented and seq_kind is Kind.LIST:
                # ``xs += ys`` / ``xs *= n`` mutate the list in place, i.e. every alias of it.
                effect = Effect.UNKNOWN
        else:
            typ, effect, flavor = UNKNOWN_TYPE, Effect.UNKNOWN, "python.dynamic_dispatch"
        attrs: dict[str, Any] = {}
        if augmented:
            attrs["augmented"] = True
        if flavor in ("python.sequence_concat", "python.sequence_repeat") and effect is Effect.UNKNOWN:
            attrs["reason"] = f"in-place list {'+=' if op is Op.ADD else '*='} mutates the list shared by every alias; alias/effect tracking for lists is not modeled"
        if flavor == "python.dynamic_dispatch":
            attrs["reason"] = f"{op.value} on {lk.value}/{rk.value} operands may dispatch to user-defined operator methods"
        site: ObligationSite | None = None
        if op in DIVISION_OPS:
            if self.frame.depth == 0:
                site = self.sites.get(id(node))
                if site is None:
                    raise LoweringError(f"division at {span.start_line}:{span.start_column} has no registered site")
            else:
                divisor = node.right if isinstance(node, ast.BinOp) else node.value  # type: ignore[attr-defined]
                site = self.new_site(DIVISION_PROPERTY, op, node, divisor)
            attrs["obligation_property"] = site.property_id
            attrs["obligation_ordinal"] = site.ordinal
        rid = self.emit(op, (left, right), typ, span, effect, flavor, **attrs)
        if site is not None:
            site.block_id = self.cur.block_id  # type: ignore[union-attr]
            site.result_id = rid
        return rid

    def compare(self, op: ast.cmpop, left: str, right: str, node: ast.AST) -> str:
        span = self.span(node)
        lk, rk = self.kind(left), self.kind(right)
        boolean = SemType(Kind.BOOL, Origin.DERIVED)
        if isinstance(op, (ast.Is, ast.IsNot)) and (rk is Kind.NONE or lk is Kind.NONE):
            subject = left if rk is Kind.NONE else right
            isnone = self.emit(Op.IS_NONE, (subject,), boolean, span)
            if isinstance(op, ast.Is):
                return isnone
            false = self.const(False, span)
            return self.emit(Op.COMPARE, (isnone, false), boolean, span, cmp="EQ", synthesized="is_not")
        cmp = _CMPOPS.get(type(op))
        builtin = {Kind.INT, Kind.BOOL, Kind.FLOAT, Kind.NONE, Kind.STR}
        if cmp is None or lk not in builtin or rk not in builtin:
            return self.emit(
                Op.UNKNOWN_EFFECT, (left, right), SemType(Kind.BOOL, Origin.UNKNOWN), span, Effect.UNKNOWN, None,
                reason=f"comparison {type(op).__name__} on {lk.value}/{rk.value} may dispatch to user code",
            )
        numeric = {Kind.INT, Kind.BOOL, Kind.FLOAT}
        if lk in numeric and rk in numeric:
            effect = Effect.NONE
        elif cmp in ("EQ", "NE"):
            effect = Effect.NONE
        else:
            effect = Effect.MAY_RAISE  # e.g. None < 1 raises TypeError
        return self.emit(Op.COMPARE, (left, right), boolean, span, effect, None, cmp=cmp)
