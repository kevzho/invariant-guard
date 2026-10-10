"""Harvest replay inputs from a repository's tests and doctests (host side, AST only).

Nothing here imports or runs repository code. For one target (function, class, method,
static/class method or property) the harvester finds its call sites in test files and in
the doctests of its own module and turns each call into a typed probe input:

* a literal argument becomes an encoded built-in ``Value``;
* any other argument becomes an ``Expr``: the argument's exact source text plus the
  ``Binding`` statements it depends on (simple local assignments and
  ``pytest.mark.parametrize`` values), each also exact source text at its own line.
  Text is never rewritten (no inlining), so ``run.check_inputs`` can verify every piece
  against the snapshot before anything executes;
* for a method, the receiver is the expression that built the object
  (``Box([1, 2]).push(3)`` or ``b = Box([1, 2])``), and the earlier method calls on the
  same local object in the same block become ``steps`` replayed before the target call
  (stateful call sequences);
* operator syntax counts as a call of the matching dunder (``b[i]`` for ``__getitem__``,
  ``x in b``, ``len(b)``, ``for _ in b`` ...).

Free names an expression uses must be bound locally, safe builtins, or imported from the
target's package or an allowlisted standard-library module; anything else (fixtures,
mocks, test helpers) makes the call unharvestable and is counted, not guessed.

``default_variants`` adds inputs that perturb one defaulted parameter of a harvested call.
"""

from __future__ import annotations

import ast
import doctest
import json
from dataclasses import dataclass, field
from typing import Any

from .run import Binding, Construct, Expr, Input, InputRejected, Namespace, Probe, Step, Target, Value, check_inputs
from .values import EncodeError, encode_input

STDLIB_OK = frozenset({"operator", "itertools", "math", "functools", "collections", "fractions", "decimal", "string", "datetime", "re", "random",
                       "cmath", "statistics", "calendar", "time", "io", "collections.abc", "typing", "array", "heapq", "bisect", "copy", "uuid",
                       "enum", "textwrap", "json", "zoneinfo"})
SAFE_NAMES = frozenset((
    "abs all any bool bytearray bytes callable chr complex dict divmod enumerate filter float frozenset hash int isinstance "
    "issubclass iter len list map max min next object ord pow range repr reversed round set slice sorted str sum tuple type zip "
    "None True False Ellipsis NotImplemented ArithmeticError AssertionError AttributeError Exception IndexError KeyError LookupError "
    "OverflowError RuntimeError StopIteration TypeError ValueError ZeroDivisionError").split())
MAX_EXPR = 400
MAX_STEPS = 20
MAX_INPUTS = 200


@dataclass
class Harvested:
    origin: str  # test | doctest | defaults
    args: tuple[Input, ...]
    kwargs: tuple[tuple[str, Input], ...]
    receiver: Input | Construct | None
    steps: tuple[Step, ...]
    path: str
    line: int
    text: str
    target: Target | None = None  # set for generated inputs (the job's target)

    def key(self) -> str:
        return json.dumps(_plain([self.args, self.kwargs, self.receiver, self.steps]), sort_keys=True)

    def probe(self, pid: str, target: Target, seed: int = 0) -> Probe:
        return Probe(pid, target, self.args, self.kwargs, self.receiver, seed, self.steps)


@dataclass
class HarvestResult:
    inputs: list[Harvested] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def bump(self, k: str, n: int = 1) -> None:
        self.stats[k] = self.stats.get(k, 0) + n


def _plain(x: Any) -> Any:
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if hasattr(x, "__dataclass_fields__"):
        return {k: _plain(getattr(x, k)) for k in x.__dataclass_fields__}
    return x


# -- name resolution --------------------------------------------------------------------------
def import_map(stmts: list[ast.stmt]) -> tuple[dict[str, tuple[str, str | None]], list[str]]:
    """local name -> (module, attribute or None); plus star-imported modules (absolute imports only)."""
    m: dict[str, tuple[str, str | None]] = {}
    stars: list[str] = []
    for st in stmts:
        for n in ast.walk(st):
            if isinstance(n, ast.Import):
                for a in n.names:
                    m[a.asname or a.name.split(".")[0]] = (a.name if a.asname else a.name.split(".")[0], None)
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                for a in n.names:
                    if a.name == "*":
                        stars.append(n.module)
                    else:
                        m[a.asname or a.name] = (n.module, a.name)
    return m, stars


class Ctx:
    """How names in one source (a test file, or the doctests of the target module) resolve."""

    def __init__(self, imports: dict[str, tuple[str, str | None]], stars: list[str], pkg: str, modules: set[str], target_module: str,
                 module_globals: set[str] | None = None) -> None:
        self.imports, self.stars, self.pkg, self.modules = imports, stars, pkg, modules
        self.target_module = target_module
        self.module_globals = module_globals  # doctests: the target module's own names

    def _module_of(self, name: str) -> str | None:
        b = self.imports.get(name)
        if b is None:
            return None
        mod, attr = b
        dotted = mod if attr is None else f"{mod}.{attr}"
        return dotted if (dotted in self.modules or dotted in STDLIB_OK or dotted == self.pkg) else None

    def names_target_module(self, dotted: str) -> bool:
        # Packages re-export names of their submodules (pkg/__init__ -> pkg.sub).
        return dotted == self.target_module or dotted == self.pkg or self.target_module.startswith(dotted + ".")

    def ref_is(self, node: ast.AST, name: str) -> bool:
        """Does ``node`` denote the target module's attribute ``name``?"""
        if isinstance(node, ast.Name):
            b = self.imports.get(node.id)
            if b is not None:
                return b[1] == name and self.names_target_module(b[0])
            if node.id != name:
                return False
            return self.module_globals is not None or any(self.names_target_module(s) for s in self.stars)
        if isinstance(node, ast.Attribute) and node.attr == name:
            v = node.value
            if isinstance(v, ast.Name):
                d = self._module_of(v.id)
                return d is not None and self.names_target_module(d)
            if isinstance(v, ast.Attribute):
                return ast.unparse(v) in (self.target_module, self.pkg)
        return False

    def ref_is_any(self, node: ast.AST, names: list[str]) -> bool:
        return any(self.ref_is(node, n) for n in names)

    def name_ok(self, name: str) -> bool:
        if name in SAFE_NAMES or (self.module_globals is not None and name in self.module_globals):
            return True
        b = self.imports.get(name)
        if b is None:
            return False
        mod = b[0]
        return mod == self.pkg or mod.startswith(self.pkg + ".") or mod in STDLIB_OK or mod.split(".")[0] in STDLIB_OK

    def namespace(self, free: set[str]) -> Namespace:
        imps = tuple(sorted((n, *self.imports[n]) for n in free if n in self.imports and n not in SAFE_NAMES))
        return Namespace(self.target_module if self.module_globals is not None else None, imps)


def free_names(expr: ast.AST) -> tuple[set[str], bool]:
    """Free names of an expression, and whether it uses syntax we never harvest."""
    bad = False
    free: set[str] = set()

    def walk(n: ast.AST, bound: frozenset[str]) -> None:
        nonlocal bad
        if isinstance(n, ast.Lambda):
            a = n.args
            for d in a.defaults + [d for d in a.kw_defaults if d is not None]:
                walk(d, bound)
            walk(n.body, bound | {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs + [v for v in (a.vararg, a.kwarg) if v]})
            return
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            b = set(bound)
            for g in n.generators:
                walk(g.iter, frozenset(b))
                b |= {t.id for t in ast.walk(g.target) if isinstance(t, ast.Name)}
                for c in g.ifs:
                    walk(c, frozenset(b))
            for part in ([n.key, n.value] if isinstance(n, ast.DictComp) else [n.elt]):
                walk(part, frozenset(b))
            return
        if isinstance(n, (ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr, ast.Starred)):
            bad = True
        if isinstance(n, ast.Name):
            if n.id not in bound:
                free.add(n.id)
            return
        if isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            bad = True
        for c in ast.iter_child_nodes(n):
            walk(c, bound)

    walk(expr, frozenset())
    return free, bad


def literal(expr: ast.AST) -> tuple[bool, Any]:
    try:
        return True, ast.literal_eval(expr)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


# -- source blocks ------------------------------------------------------------------------------
@dataclass
class Source:
    path: str
    text: str  # decoded file text
    line_offset: int = 0  # doctest examples: line of the example in the file, minus one
    col_text: str | None = None  # text the node offsets refer to (doctest example source)

    def segment(self, node: ast.AST) -> str | None:
        return ast.get_source_segment(self.col_text if self.col_text is not None else self.text, node)

    def line(self, node: ast.AST) -> int:
        return self.line_offset + getattr(node, "lineno", 1)


class Block:
    """Statements of one scope, scanned in order with their local bindings."""

    def __init__(self, ctx: Ctx, src: Source, res: HarvestResult) -> None:
        self.ctx, self.src, self.res = ctx, src, res
        self.env: dict[str, tuple[Binding, set[str], set[str]]] = {}  # name -> (binding, local deps, non-local free names)
        self.calls: dict[str, list[Step]] = {}  # local object -> method calls on it so far

    def bind(self, name: str, value: ast.AST, src: Source | None = None) -> None:
        src = src or self.src
        text = src.segment(value)
        free, bad = free_names(value)
        local = {n for n in free if n in self.env}
        if text is None or bad or len(text) > MAX_EXPR or not all(n in local or self.ctx.name_ok(n) for n in free):
            self.env.pop(name, None)
        else:
            self.env[name] = (Binding(name, text, src.line(value)), local, free - local)
        self.calls.pop(name, None)

    def _bindings(self, names: set[str]) -> tuple[Binding, ...]:
        order: list[str] = []

        def visit(n: str, seen: frozenset[str]) -> None:
            if n in order or n in seen or n not in self.env:
                return
            for dep in sorted(self.env[n][1]):
                visit(dep, seen | {n})
            order.append(n)

        for n in sorted(names):
            visit(n, frozenset())
        return tuple(self.env[n][0] for n in order)

    def arg(self, node: ast.AST) -> Input | None:
        ok, val = literal(node)
        if ok:
            try:
                return Value.of(val)
            except EncodeError:
                pass
        text = self.src.segment(node)
        free, bad = free_names(node)
        if text is None or bad or len(text) > MAX_EXPR:
            return None
        local = {n for n in free if n in self.env}
        if not all(n in local or self.ctx.name_ok(n) for n in free):
            return None
        bindings = self._bindings(local)
        names = (free - local) | {n for b in bindings for n in self.env[b.name][2]}
        return Expr(text, self.src.path, self.src.line(node), self.ctx.namespace(names), bindings)

    def call_args(self, call: ast.Call) -> tuple[tuple[Input, ...], tuple[tuple[str, Input], ...]] | None:
        if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
            self.res.bump("unharvestable_starred")
            return None
        args = [self.arg(a) for a in call.args]
        kwargs = [(k.arg, self.arg(k.value)) for k in call.keywords]
        if any(a is None for a in args) or any(v is None for _, v in kwargs):
            self.res.bump("unharvestable_args")
            return None
        return tuple(args), tuple(kwargs)  # type: ignore[arg-type]


# -- target matching ----------------------------------------------------------------------------
_UNARY = {"__len__": "len", "__iter__": "iter", "__bool__": "bool", "__repr__": "repr", "__str__": "str", "__reversed__": "reversed",
          "__hash__": "hash", "__abs__": "abs", "__int__": "int", "__float__": "float"}
_ITER_CONSUMERS = {"list", "tuple", "set", "sorted", "iter", "frozenset", "dict", "sum", "min", "max", "any", "all"}
_CMP = {ast.Eq: "__eq__", ast.NotEq: "__ne__", ast.Lt: "__lt__", ast.LtE: "__le__", ast.Gt: "__gt__", ast.GtE: "__ge__"}
_BIN = {ast.Add: "__add__", ast.Sub: "__sub__", ast.Mult: "__mul__", ast.BitOr: "__or__", ast.BitAnd: "__and__", ast.BitXor: "__xor__",
        ast.Mod: "__mod__", ast.FloorDiv: "__floordiv__", ast.Div: "__truediv__"}


def _dunder_uses(st: ast.stmt, meth: str):
    """(argument nodes, receiver node, call node) for operator-syntax uses of a dunder (no slices)."""
    for n in ast.walk(st):
        if meth == "__getitem__" and isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and not isinstance(n.slice, (ast.Slice, ast.Tuple)):
            yield [n.slice], n.value, n
        elif meth == "__contains__" and isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], (ast.In, ast.NotIn)):
            yield [n.left], n.comparators[0], n
        elif isinstance(n, ast.Compare) and len(n.ops) == 1 and _CMP.get(type(n.ops[0])) == meth:
            yield [n.comparators[0]], n.left, n
        elif isinstance(n, ast.BinOp) and _BIN.get(type(n.op)) == meth:
            yield [n.right], n.left, n
        elif isinstance(n, ast.Call) and len(n.args) == 1 and not n.keywords and isinstance(n.func, ast.Name):
            if _UNARY.get(meth) == n.func.id or (meth == "__iter__" and n.func.id in _ITER_CONSUMERS):
                yield [], n.args[0], n


def _flat(body: list[ast.stmt]):
    for st in body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield st
        for blk in ("body", "orelse", "finalbody"):
            sub = getattr(st, blk, None)
            if isinstance(sub, list):
                yield from _flat(sub)
        for h in getattr(st, "handlers", []) or []:
            yield from _flat(h.body)


def _calls(st: ast.stmt):
    stack = [st]
    while stack:
        n = stack.pop()
        if n is not st and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(n, (ast.Call, ast.Attribute)):
            yield n
        stack.extend(reversed(list(ast.iter_child_nodes(n))))


class Matcher:
    def __init__(self, qualname: str, kind: str, class_aliases: list[str]) -> None:
        parts = qualname.split(".")
        self.kind = kind  # function | class | method | staticmethod | classmethod | property
        self.member = parts[-1]
        self.owner = parts[0] if len(parts) > 1 else None
        self.owner_names = ([self.owner] + class_aliases) if self.owner else []
        self.callee_names = [parts[0]] + (class_aliases if kind == "class" else [])


def scan_block(stmts: list[ast.stmt], ctx: Ctx, src: Source, m: Matcher, origin: str, res: HarvestResult,
               params: list[tuple[str, ast.AST, Source]] | None = None) -> None:
    b = Block(ctx, src, res)
    for name, node, psrc in params or []:
        b.bind(name, node, psrc)

    def receiver_of(node: ast.AST) -> tuple[Input | None, str | None]:
        """(receiver input, local object name) for a node that evaluates to an owner instance."""
        if isinstance(node, ast.Name) and node.id in b.env and node.id in b.calls:
            return b.arg(node), node.id
        if makes_owner(node):
            return b.arg(node), None
        return None, None

    def makes_owner(node: ast.AST) -> bool:
        """``Cls(...)`` or an alternate constructor ``Cls.factory(...)``."""
        return isinstance(node, ast.Call) and (ctx.ref_is_any(node.func, m.owner_names) or (
            isinstance(node.func, ast.Attribute) and ctx.ref_is_any(node.func.value, m.owner_names)))

    def emit(call: ast.AST, args, kwargs, receiver, steps) -> None:
        res.bump("calls_seen")
        h = Harvested(origin, args, kwargs, receiver, tuple(steps[-MAX_STEPS:]), src.path, src.line(call), (src.segment(call) or "")[:200])
        try:
            check_inputs(h.probe("check", Target("x.py", "x")), {src.path: src.text.encode()})
        except InputRejected:
            res.bump("provenance_mismatch")
            return
        res.inputs.append(h)

    for st in _flat(stmts):
        for call in _calls(st):
            if isinstance(call, ast.Call):
                f = call.func
                if m.owner is None:
                    if not ctx.ref_is_any(f, m.callee_names):
                        continue
                    ca = b.call_args(call)
                    if ca:
                        emit(call, *ca, None, [])
                    continue
                if not (isinstance(f, ast.Attribute) and f.attr == m.member):
                    # A method call on a tracked local object: remember it as a step.
                    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in b.calls:
                        ca = b.call_args(call)
                        if ca is not None:
                            b.calls[f.value.id].append(Step(f.attr, *ca))
                    continue
                if m.kind in ("staticmethod", "classmethod") and ctx.ref_is_any(f.value, m.owner_names):
                    ca = b.call_args(call)
                    if ca:
                        emit(call, *ca, None, [])
                    continue
                recv, var = receiver_of(f.value)
                if recv is None:
                    continue
                ca = b.call_args(call)
                if ca:
                    emit(call, *ca, recv, list(b.calls.get(var, [])) if var else [])
                    if var:
                        b.calls[var].append(Step(m.member, *ca))
            elif m.kind == "property" and call.attr == m.member and isinstance(call.ctx, ast.Load):
                recv, var = receiver_of(call.value)
                if recv is not None:
                    emit(call, (), (), recv, list(b.calls.get(var, [])) if var else [])
        if m.owner is not None and m.member.startswith("__") and m.member.endswith("__"):
            for arg_nodes, recv_node, node in _dunder_uses(st, m.member):
                recv, var = receiver_of(recv_node)
                if recv is None:
                    continue
                args = [b.arg(a) for a in arg_nodes]
                if any(a is None for a in args):
                    res.bump("unharvestable_args")
                    continue
                emit(node, tuple(args), (), recv, list(b.calls.get(var, [])) if var else [])
        # Bindings take effect after the statement (``x = f(x)`` reads the old x).
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            name = st.targets[0].id
            b.bind(name, st.value)
            if m.owner is not None and makes_owner(st.value) and name in b.env:
                b.calls[name] = []
        elif isinstance(st, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.With, ast.Delete)):
            for t in ast.walk(st):
                if isinstance(t, ast.Name) and isinstance(t.ctx, (ast.Store, ast.Del)):
                    b.env.pop(t.id, None)
                    b.calls.pop(t.id, None)


def _parametrize(fn: ast.FunctionDef | ast.AsyncFunctionDef, src: Source, limit: int = 40) -> list[list[tuple[str, ast.AST, Source]]]:
    """Rows of literal-named ``@pytest.mark.parametrize`` decorators (cross product, bounded)."""
    rows: list[list[tuple[str, ast.AST, Source]]] = [[]]
    for d in fn.decorator_list:
        if not (isinstance(d, ast.Call) and ast.unparse(d.func).endswith("parametrize") and len(d.args) >= 2):
            continue
        ok, names = literal(d.args[0])
        if not ok or not isinstance(names, (str, list, tuple)):
            continue
        names = [x.strip() for x in names.split(",")] if isinstance(names, str) else list(names)
        if not isinstance(d.args[1], (ast.List, ast.Tuple)):
            continue
        new = []
        for row in d.args[1].elts:
            if isinstance(row, ast.Call) and ast.unparse(row.func).endswith("param"):
                vals = list(row.args)
            elif len(names) == 1:
                vals = [row]
            elif isinstance(row, (ast.Tuple, ast.List)):
                vals = list(row.elts)
            else:
                continue
            if len(vals) != len(names):
                continue
            for r in rows:
                new.append(r + [(n, v, src) for n, v in zip(names, vals)])
        rows = new[:limit]
    return rows


def _scopes(tree: ast.Module, src: Source):
    yield tree.body, None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for row in _parametrize(n, src):
                yield n.body, row


def _doctest_blocks(tree: ast.Module, path: str, file_text: str) -> list[list[tuple[ast.stmt, Source]]]:
    """Per docstring: its examples' statements, each with a Source locating it in the file."""
    out = []
    parser = doctest.DocTestParser()
    for owner in [tree] + [x for x in ast.walk(tree) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]:
        body = getattr(owner, "body", [])
        if not body or not (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
            continue
        raw = body[0].value.value
        if ">>>" not in raw:
            continue
        try:
            examples = parser.get_examples(raw)
        except ValueError:
            continue
        located: list[tuple[ast.stmt, Source]] = []
        for ex in examples:
            try:
                parsed = ast.parse(ex.source)
            except SyntaxError:
                continue
            # ex.lineno is 0-based within the raw docstring, whose first line is the line of
            # the opening quotes; node line numbers inside ex.source are 1-based.
            s = Source(path, file_text, body[0].value.lineno + ex.lineno - 1, ex.source)
            located.extend((st, s) for st in parsed.body)
        if located:
            out.append(located)
    return out


def _scan_located(located: list[tuple[ast.stmt, Source]], ctx: Ctx, m: Matcher, res: HarvestResult) -> None:
    """scan_block over statements that each carry their own Source (doctest examples)."""
    # Group consecutive statements of one example; bindings persist across examples by
    # replaying earlier assignment statements as params of later groups.
    carried: list[tuple[str, ast.AST, Source]] = []
    for st, s in located:
        scan_block([st], ctx, s, m, "doctest", res, params=list(carried))
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            carried = [p for p in carried if p[0] != st.targets[0].id] + [(st.targets[0].id, st.value, s)]


# -- entry points --------------------------------------------------------------------------------
def module_global_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for st in tree.body:
        for n in (ast.walk(st) if isinstance(st, (ast.If, ast.Try)) else [st]):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.Assign):
                out.update(x.id for t in n.targets for x in ast.walk(t) if isinstance(x, ast.Name))
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                out.update(a.asname or a.name.split(".")[0] for a in n.names if a.name != "*")
    return out


def class_aliases(tree: ast.Module, cls: str) -> list[str]:
    """Module-level ``Alias = Cls`` bindings (e.g. ``OMD = OrderedMultiDict``)."""
    return [st.targets[0].id for st in tree.body if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)
            and isinstance(st.value, ast.Name) and st.value.id == cls]


def inheriting_subclasses(tree: ast.Module, cls: str, member: str) -> list[str]:
    """Module-level classes that (transitively) subclass ``cls`` by name and do not define ``member``.

    Calling ``member`` on their instances runs the target's own code (``LRU`` inherits
    ``LRI.update``). Classes from other modules are not followed.
    """
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    out: list[str] = []
    changed = True
    while changed:
        changed = False
        for name, node in classes.items():
            if name == cls or name in out:
                continue
            bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
            if bases & ({cls} | set(out)):
                defines = any(isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == member for s in node.body) or any(
                    isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id == member for t in s.targets) for s in node.body)
                if not defines:
                    out.append(name)
                    changed = True
    return out


def target_kind(tree: ast.Module, qualname: str) -> str | None:
    """function | class | method | staticmethod | classmethod | property, from the definition."""
    scope: list[ast.stmt] = tree.body
    node: ast.AST | None = None
    parts = qualname.split(".")
    for i, p in enumerate(parts):
        node = next((s for s in scope if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and s.name == p), None)
        if node is None:
            return None
        if i < len(parts) - 1:
            if not isinstance(node, ast.ClassDef):
                return None
            scope = node.body
    if isinstance(node, ast.ClassDef):
        return "class"
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    if len(parts) == 1:
        return "function"
    decos = {ast.unparse(d) for d in node.decorator_list}
    if "staticmethod" in decos:
        return "staticmethod"
    if "classmethod" in decos:
        return "classmethod"
    if "property" in decos or any(d.endswith(".setter") or d.endswith(".getter") for d in decos):
        return "property"
    return "method"


def harvest(target: Target, files: dict[str, bytes], test_paths: list[str], *, package: str | None = None) -> HarvestResult:
    """Inputs for ``target`` from ``test_paths`` and the doctests of its own module.

    ``files`` maps snapshot paths to bytes and must contain the target module and the
    tests. ``package`` is the top-level import package (default: the first component of
    the target's module name).
    """
    res = HarvestResult()
    module = target.module
    pkg = package or module.split(".")[0]
    root = target.python_path_root.strip("/")
    modules = set()
    for p in files:
        if p.endswith(".py"):
            rel = p[len(root) + 1:] if root not in ("", ".") and p.startswith(root + "/") else p
            t = Target(rel, "_")
            try:
                modules.add(t.module)
            except ValueError:
                pass
    try:
        mtree = ast.parse(files[target.path])
    except (KeyError, SyntaxError):
        res.bump("target_module_unparsable")
        return res
    kind = target_kind(mtree, target.qualname)
    if kind is None:
        res.bump("target_not_found")
        return res
    owner = target.qualname.split(".")[0]
    aliases = class_aliases(mtree, owner)
    if "." in target.qualname:
        for sub in inheriting_subclasses(mtree, owner, target.qualname.split(".")[-1]):
            aliases += [sub] + class_aliases(mtree, sub)
    m = Matcher(target.qualname, kind, aliases)
    for tp in test_paths:
        try:
            text = files[tp].decode("utf-8")
            tree = ast.parse(text)
        except (KeyError, UnicodeDecodeError, SyntaxError):
            res.bump("test_file_unparsable")
            continue
        imps, stars = import_map(tree.body)
        ctx = Ctx(imps, stars, pkg, modules, module)
        src = Source(tp, text)
        for body, params in _scopes(tree, src):
            scan_block(body, ctx, src, m, "test", res, params)
    mtext = files[target.path].decode("utf-8", "replace")
    globs = module_global_names(mtree)
    for located in _doctest_blocks(mtree, target.path, mtext):
        imps, stars = import_map([st for st, _ in located])
        _scan_located(located, Ctx(imps, stars, pkg, modules, module, module_globals=globs | set(imps)), m, res)
    seen: set[str] = set()
    uniq = []
    for h in res.inputs:
        k = h.key()
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    res.bump("duplicates", len(res.inputs) - len(uniq))
    res.inputs = uniq[:MAX_INPUTS]
    res.bump("harvested", len(res.inputs))
    return res


def default_variants(files: dict[str, bytes], target: Target, inputs: list[Harvested], limit: int = 30) -> list[Harvested]:
    """Harvested calls with one omitted defaulted parameter set to a nearby literal value."""
    try:
        tree = ast.parse(files[target.path])
    except (KeyError, SyntaxError):
        return []
    scope: list[ast.stmt] = tree.body
    fn = None
    for p in target.qualname.split("."):
        fn = next((s for s in scope if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and s.name == p), None)
        if fn is None:
            return []
        scope = fn.body
    if isinstance(fn, ast.ClassDef):
        fn = next((s for s in fn.body if isinstance(s, ast.FunctionDef) and s.name == "__init__"), None)
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    a = fn.args
    pos = a.posonlyargs + a.args
    if pos and pos[0].arg in ("self", "cls"):
        pos = pos[1:]
    defaults: dict[str, Any] = {}
    for p, d in zip(pos[len(pos) - len(a.defaults):], a.defaults):
        ok, v = literal(d)
        if ok and p not in a.posonlyargs:
            defaults[p.arg] = v
    for p, d in zip(a.kwonlyargs, a.kw_defaults):
        ok, v = literal(d) if d is not None else (False, None)
        if ok:
            defaults[p.arg] = v
    names = [p.arg for p in pos]
    out: list[Harvested] = []
    for h in inputs:
        given = set(names[: len(h.args)]) | {k for k, _ in h.kwargs}
        for p, d in defaults.items():
            if p in given:
                continue
            if type(d) is bool:
                vs: list[Any] = [not d]
            elif type(d) is int:
                vs = [d - 1, d + 1, 0, -1, 2]
            elif type(d) is float:
                vs = [0.0, -1.0, d * 2 + 1]
            elif type(d) is str:
                vs = ["", d + "x", " "]
            elif d is None:
                vs = [0, ""]
            else:
                continue
            for v in dict.fromkeys(vs):
                if v == d and type(v) is type(d):
                    continue
                try:
                    val = Value(encode_input(v))
                except EncodeError:
                    continue
                out.append(Harvested("defaults", h.args, h.kwargs + ((p, val),), h.receiver, h.steps, h.path, h.line, f"{h.text} [{p}={v!r}]"))
                if len(out) >= limit:
                    return out
    return out
