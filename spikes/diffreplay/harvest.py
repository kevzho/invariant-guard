"""Input harvesting by AST (host side, no imports of repository code).

Sources, each recorded as the input's origin:

* ``test``      a call to the target with harvestable arguments in the repository's tests;
* ``doctest``   a call in a doctest example of the target's module;
* ``defaults``  a harvested call with one defaulted parameter perturbed (int/bool/str/float);
* ``shape``     Hypothesis draws shaped like a harvested literal argument (generated in-container);
* ``annotation``Hypothesis ``from_type`` on the target's inline annotations (in-container);
* ``stub``      Hypothesis ``from_type`` on the ``.pyi`` stub annotations (in-container).

A suffix ``_new`` marks inputs whose source text exists only in the patch snapshot (for
example a test added by the commit itself).

A harvested argument is either a literal (shipped as an encoded built-in value) or a
*harvestable expression*: lambdas, ``range``/``iter``/stdlib calls and names imported from
the target package or allowlisted stdlib modules, after inlining simple local
assignments. Expressions are repository-authored text and are evaluated only inside the
container, in a namespace built from the same import statements.
"""

from __future__ import annotations

import ast
import doctest
import json
from dataclasses import dataclass, field
from typing import Any

import dr_harness

STDLIB_OK = {"operator", "itertools", "math", "functools", "collections", "fractions", "decimal", "string", "datetime", "re", "random", "cmath",
             "statistics", "calendar", "time", "io", "collections.abc", "typing", "array", "heapq", "bisect", "copy", "uuid"}
SAFE_NAMES = set(dr_harness.SAFE_BUILTINS)
MAX_EXPR = 400


@dataclass
class Input:
    origin: str
    args: list[dict[str, Any]]
    kwargs: dict[str, dict[str, Any]]
    receiver: dict[str, Any] | None = None
    text: str = ""

    def key(self) -> str:
        return json.dumps([self.args, self.kwargs, self.receiver], sort_keys=True)


@dataclass
class Harvest:
    inputs: list[Input] = field(default_factory=list)
    namespaces: dict[str, dict[str, Any]] = field(default_factory=dict)
    receivers: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def ns_id(self, spec: dict[str, Any]) -> str:
        k = json.dumps(spec, sort_keys=True)
        for nid, s in self.namespaces.items():
            if json.dumps(s, sort_keys=True) == k:
                return nid
        nid = f"ns{len(self.namespaces)}"
        self.namespaces[nid] = spec
        return nid

    def bump(self, k: str) -> None:
        self.stats[k] = self.stats.get(k, 0) + 1


# -- import maps -----------------------------------------------------------------------------
def import_map(tree: ast.AST, this_module: str | None = None) -> tuple[dict[str, tuple[str, str | None]], list[str]]:
    """local name -> (module, attr or None); plus star-imported modules."""
    m: dict[str, tuple[str, str | None]] = {}
    stars: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    m[a.asname] = (a.name, None)
                else:
                    m[a.name.split(".")[0]] = (a.name.split(".")[0], None)
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                if a.name == "*":
                    stars.append(n.module)
                else:
                    m[a.asname or a.name] = (n.module, a.name)
    return m, stars


def _is_module_ref(binding: tuple[str, str | None], pkg: str) -> str | None:
    """If the binding names a module (``import x.y as z`` or ``from pkg import submodule``), its dotted name."""
    mod, attr = binding
    if attr is None:
        return mod
    if mod == pkg or mod.startswith(pkg + "."):
        return f"{mod}.{attr}"  # may be a submodule; checked by the caller against known modules
    if f"{mod}.{attr}" in STDLIB_OK:
        return f"{mod}.{attr}"
    return None


class Ctx:
    """Resolution context for one source of calls (a test file or the target module's doctests)."""

    def __init__(self, imports: dict[str, tuple[str, str | None]], stars: list[str], pkg: str, modules: set[str], target_module: str,
                 module_globals: set[str] | None = None) -> None:
        self.imports = imports
        self.stars = stars
        self.pkg = pkg
        self.modules = modules  # dotted names of the snapshot's package modules
        self.target_module = target_module
        self.module_globals = module_globals  # doctest: names visible in the target module

    def module_alias(self, name: str) -> str | None:
        b = self.imports.get(name)
        if b is None:
            return None
        d = _is_module_ref(b, self.pkg)
        if d and (d in self.modules or d in STDLIB_OK or d == self.pkg):
            return d
        return None

    def names_target_module(self, dotted: str) -> bool:
        # Packages re-export names from their submodules (more_itertools/__init__, dateutil.parser -> dateutil.parser._parser).
        return dotted == self.target_module or dotted == self.pkg or self.target_module.startswith(dotted + ".")

    def ref_is(self, node: ast.AST, name: str) -> bool:
        """Does ``node`` denote the target-module attribute ``name``?"""
        if isinstance(node, ast.Name):
            if node.id != name and node.id not in self.imports:
                return False
            b = self.imports.get(node.id)
            if b is not None:
                return b[1] == name and self.names_target_module(b[0])
            if self.module_globals is not None and node.id == name:
                return True
            return node.id == name and any(self.names_target_module(s) for s in self.stars)
        if isinstance(node, ast.Attribute) and node.attr == name:
            v = node.value
            if isinstance(v, ast.Name):
                d = self.module_alias(v.id)
                return d is not None and self.names_target_module(d)
            if isinstance(v, ast.Attribute):  # pkg.mod.fn
                return ast.unparse(v) in (self.target_module, self.pkg)
        return False

    def ref_is_any(self, node: ast.AST, names: list[str]) -> bool:
        return any(self.ref_is(node, n) for n in names)

    def name_ok(self, name: str) -> bool:
        if name in SAFE_NAMES:
            return True
        if self.module_globals is not None and name in self.module_globals:
            return True
        b = self.imports.get(name)
        if b is None:
            return any(self.names_target_module(s) for s in self.stars) and self.module_globals is None and False
        mod = b[0]
        return mod == self.pkg or mod.startswith(self.pkg + ".") or mod in STDLIB_OK or mod.split(".")[0] in STDLIB_OK


# -- expression safety + inlining --------------------------------------------------------------
class _Inline(ast.NodeTransformer):
    def __init__(self, env: dict[str, ast.AST], bound: set[str]) -> None:
        self.env = env
        self.bound = bound

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        inner = _Inline(self.env, self.bound | {a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs})
        node.body = inner.visit(node.body)
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in self.env and node.id not in self.bound and isinstance(node.ctx, ast.Load):
            import copy

            return copy.deepcopy(self.env[node.id])
        return node


def inline(expr: ast.AST, env: dict[str, ast.AST]) -> ast.AST:
    import copy

    return _Inline(env, set()).visit(copy.deepcopy(expr))


def used_names(expr: ast.AST) -> tuple[set[str], bool]:
    """Free names of an expression; and whether it contains disallowed syntax."""
    bad = False
    free: set[str] = set()

    def walk(n: ast.AST, bound: set[str]) -> None:
        nonlocal bad
        if isinstance(n, ast.Lambda):
            b = bound | {a.arg for a in n.args.args + n.args.kwonlyargs + n.args.posonlyargs}
            walk(n.body, b)
            for d in n.args.defaults:
                walk(d, bound)
            return
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            b = set(bound)
            for g in n.generators:
                walk(g.iter, b)
                for t in ast.walk(g.target):
                    if isinstance(t, ast.Name):
                        b.add(t.id)
                for c in g.ifs:
                    walk(c, b)
            for part in ([n.key, n.value] if isinstance(n, ast.DictComp) else [n.elt]):
                walk(part, b)
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

    walk(expr, set())
    return free, bad


def literal_value(expr: ast.AST) -> tuple[bool, Any]:
    try:
        return True, ast.literal_eval(expr)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False, None


def arg_spec(expr: ast.AST, ctx: Ctx, h: Harvest) -> dict[str, Any] | None:
    ok, val = literal_value(expr)
    if ok:
        node, _ = dr_harness.encode(val)
        try:
            dr_harness.decode(node)
            return {"v": node}
        except (ValueError, TypeError, KeyError):
            pass
    free, bad = used_names(expr)
    if bad or not all(ctx.name_ok(n) for n in free):
        return None
    src = ast.unparse(expr)
    if len(src) > MAX_EXPR:
        return None
    imports = sorted([n, *ctx.imports[n]] for n in free if n in ctx.imports and n not in SAFE_NAMES)
    spec = {"module_globals": ctx.target_module if ctx.module_globals is not None else None, "imports": imports}
    return {"x": src, "ns": h.ns_id(spec)}


# -- call discovery ----------------------------------------------------------------------------
def _parametrize_envs(fn: ast.FunctionDef | ast.AsyncFunctionDef, limit: int = 40) -> list[dict[str, ast.AST]]:
    """Bindings from literal ``@pytest.mark.parametrize("a,b", [...])`` decorators (cross product, bounded)."""
    envs: list[dict[str, ast.AST]] = [{}]
    for d in fn.decorator_list:
        if not (isinstance(d, ast.Call) and ast.unparse(d.func).endswith("parametrize") and len(d.args) >= 2):
            continue
        ok, names = literal_value(d.args[0])
        if not ok:
            continue
        names = [x.strip() for x in names.split(",")] if isinstance(names, str) else list(names)
        rows = d.args[1]
        if not isinstance(rows, (ast.List, ast.Tuple)):
            continue
        new = []
        for row in rows.elts:
            if isinstance(row, ast.Call) and ast.unparse(row.func).endswith("param") and row.args:
                vals = row.args if len(names) > 1 else [row.args[0]]
                if len(names) > 1 and len(row.args) == len(names):
                    vals = row.args
            elif len(names) == 1:
                vals = [row]
            elif isinstance(row, (ast.Tuple, ast.List)) and len(row.elts) == len(names):
                vals = row.elts
            else:
                continue
            for e in envs:
                ne = dict(e)
                ne.update(dict(zip(names, vals)))
                new.append(ne)
        envs = new[:limit]
    return envs


def _scopes(tree: ast.Module):
    """Yield (statements, initial env) blocks: module body and every function body, in order."""
    yield tree.body, {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for env in _parametrize_envs(n):
                yield n.body, env


def _flat_statements(body: list[ast.stmt]):
    for st in body:
        yield st
        for blk in ("body", "orelse", "finalbody"):
            sub = getattr(st, blk, None)
            if isinstance(sub, list) and not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield from _flat_statements(sub)
        for hnd in getattr(st, "handlers", []) or []:
            yield from _flat_statements(hnd.body)


def _calls_in(st: ast.stmt):
    for n in ast.walk(st):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Call):
            yield n
        if isinstance(n, ast.Attribute):
            yield n


def collect_from_block(stmts: list[ast.stmt], ctx: Ctx, qualname: str, kind: str, h: Harvest, origin: str, sink: list[Input],
                       env0: dict[str, ast.AST] | None = None, aliases: dict[str, dict[str, Any]] | None = None,
                       class_aliases: list[str] | None = None) -> None:
    env: dict[str, ast.AST] = {}
    for k, v in (env0 or {}).items():
        free, bad = used_names(v)
        if not bad and all(ctx.name_ok(n) for n in free):
            env[k] = v
    recv_env: dict[str, ast.Call] = {}
    parts = qualname.split(".")
    is_method = len(parts) > 1
    cls_name = parts[0] if is_method else None
    cls_names = [cls_name] + list(class_aliases or []) if is_method else []
    meth = parts[-1]
    for st in _flat_statements(stmts):
        # Track simple local assignments for inlining (in program order, single target).
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            name = st.targets[0].id
            val = inline(st.value, env)
            free, bad = used_names(val)
            if is_method and isinstance(val, ast.Call) and ctx.ref_is_any(val.func, cls_names):
                recv_env[name] = val
            if not bad and all(ctx.name_ok(n) for n in free) and len(ast.unparse(val)) <= MAX_EXPR:
                env[name] = val
            else:
                env.pop(name, None)
                recv_env.pop(name, None) if not (is_method and isinstance(val, ast.Call) and ctx.ref_is_any(val.func, cls_names)) else None
        for n in _calls_in(st):
            if isinstance(n, ast.Call):
                f = n.func
                if not is_method:
                    if not ctx.ref_is_any(f, [meth] + list(class_aliases or [])):
                        continue
                    receiver = None
                else:
                    alias = next((a for a in (aliases or {}) if ctx.ref_is(f, a)), None)
                    if alias is not None:
                        receiver = aliases[alias]
                        h.bump("alias_calls")
                        recv_node = None
                    elif not (isinstance(f, ast.Attribute) and f.attr == meth):
                        continue
                    else:
                        recv_node = f.value
                if is_method and recv_node is None:
                    pass
                elif is_method:
                    if isinstance(recv_node, ast.Name) and recv_node.id in recv_env:
                        recv_node = recv_env[recv_node.id]
                    if ctx.ref_is_any(recv_node, cls_names):
                        if kind not in ("staticmethod", "classmethod"):
                            continue
                        receiver = None
                    elif isinstance(recv_node, ast.Call) and ctx.ref_is_any(recv_node.func, cls_names):
                        receiver = _receiver(recv_node, ctx, h, env)
                        if receiver is None:
                            h.bump("receiver_unharvestable")
                            continue
                    else:
                        continue
                h.bump("calls_seen")
                if any(isinstance(a, ast.Starred) for a in n.args) or any(k.arg is None for k in n.keywords):
                    h.bump("calls_starred")
                    continue
                args = [arg_spec(inline(a, env), ctx, h) for a in n.args]
                kwargs = {k.arg: arg_spec(inline(k.value, env), ctx, h) for k in n.keywords}
                if any(a is None for a in args) or any(v is None for v in kwargs.values()):
                    h.bump("calls_unharvestable_args")
                    continue
                sink.append(Input(origin, args, kwargs, receiver, ast.unparse(n)[:200]))
            elif is_method and kind == "property" and isinstance(n, ast.Attribute) and n.attr == meth and isinstance(n.ctx, ast.Load):
                recv_node = n.value
                if isinstance(recv_node, ast.Name) and recv_node.id in recv_env:
                    recv_node = recv_env[recv_node.id]
                if isinstance(recv_node, ast.Call) and ctx.ref_is_any(recv_node.func, cls_names):
                    r = _receiver(recv_node, ctx, h, env)
                    if r is not None:
                        h.bump("calls_seen")
                        sink.append(Input(origin, [], {}, r, ast.unparse(n)[:200]))
        if is_method and meth.startswith("__") and meth.endswith("__"):
            for args_nodes, recv_node, text in _dunder_uses(st, meth):
                if isinstance(recv_node, ast.Name) and recv_node.id in recv_env:
                    recv_node = recv_env[recv_node.id]
                if not (isinstance(recv_node, ast.Call) and ctx.ref_is_any(recv_node.func, cls_names)):
                    continue
                r = _receiver(recv_node, ctx, h, env)
                if r is None:
                    h.bump("receiver_unharvestable")
                    continue
                h.bump("calls_seen")
                args = [arg_spec(inline(a, env), ctx, h) for a in args_nodes]
                if any(a is None for a in args):
                    h.bump("calls_unharvestable_args")
                    continue
                sink.append(Input(origin, args, {}, r, text[:200]))
        # Also collect receivers alone (for annotation-driven generation of methods).
        if is_method:
            for n in ast.walk(st):
                if isinstance(n, ast.Call) and ctx.ref_is_any(n.func, cls_names):
                    r = _receiver(n, ctx, h, env)
                    if r is not None and r not in h.receivers:
                        h.receivers.append(r)


_UNARY_BUILTIN = {"__len__": "len", "__iter__": "iter", "__bool__": "bool", "__repr__": "repr", "__str__": "str", "__reversed__": "reversed",
                  "__hash__": "hash", "__abs__": "abs", "__int__": "int", "__float__": "float", "__index__": "operator.index"}
_ITER_CONSUMERS = {"list", "tuple", "set", "sorted", "iter", "frozenset", "dict", "sum", "min", "max", "any", "all"}
_CMP = {ast.Eq: "__eq__", ast.NotEq: "__ne__", ast.Lt: "__lt__", ast.LtE: "__le__", ast.Gt: "__gt__", ast.GtE: "__ge__"}
_BIN = {ast.Add: "__add__", ast.Sub: "__sub__", ast.Mult: "__mul__", ast.BitOr: "__or__", ast.BitAnd: "__and__", ast.BitXor: "__xor__",
        ast.Mod: "__mod__", ast.FloorDiv: "__floordiv__", ast.Div: "__truediv__", ast.LShift: "__lshift__", ast.RShift: "__rshift__"}


def _slice_expr(node: ast.AST) -> ast.AST:
    """``a[1:2]`` passes ``slice(1, 2, None)`` to ``__getitem__``; tuples of slices likewise."""
    if isinstance(node, ast.Slice):
        none = ast.Constant(None)
        return ast.Call(ast.Name("slice", ast.Load()), [node.lower or none, node.upper or none, node.step or none], [])
    if isinstance(node, ast.Tuple):
        return ast.Tuple([_slice_expr(e) for e in node.elts], ast.Load())
    return node


def _dunder_uses(st: ast.stmt, meth: str):
    """(argument nodes, receiver node, text) for operator-syntax uses of a dunder in one statement."""
    for n in ast.walk(st):
        if meth == "__getitem__" and isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load):
            yield [_slice_expr(n.slice)], n.value, ast.unparse(n)
        elif meth == "__setitem__" and isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Subscript):
            yield [_slice_expr(n.targets[0].slice), n.value], n.targets[0].value, ast.unparse(n)
        elif meth == "__delitem__" and isinstance(n, ast.Delete) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Subscript):
            yield [_slice_expr(n.targets[0].slice)], n.targets[0].value, ast.unparse(n)
        elif meth == "__contains__" and isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], (ast.In, ast.NotIn)):
            yield [n.left], n.comparators[0], ast.unparse(n)
        elif isinstance(n, ast.Compare) and len(n.ops) == 1 and _CMP.get(type(n.ops[0])) == meth:
            yield [n.comparators[0]], n.left, ast.unparse(n)
        elif isinstance(n, ast.BinOp) and _BIN.get(type(n.op)) == meth:
            yield [n.right], n.left, ast.unparse(n)
        elif isinstance(n, ast.Call) and len(n.args) == 1 and not n.keywords:
            fname = ast.unparse(n.func)
            if _UNARY_BUILTIN.get(meth) == fname or (meth == "__iter__" and fname in _ITER_CONSUMERS):
                yield [], n.args[0], ast.unparse(n)
        elif meth == "__call__" and isinstance(n, ast.Call) and isinstance(n.func, (ast.Call, ast.Name)):
            yield n.args, n.func, ast.unparse(n)
        elif meth == "__iter__" and isinstance(n, (ast.For, ast.comprehension)):
            yield [], n.iter, "for ... in " + ast.unparse(n.iter)


def _receiver(call: ast.Call, ctx: Ctx, h: Harvest, env: dict[str, ast.AST]) -> dict[str, Any] | None:
    if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
        return None
    args = [arg_spec(inline(a, env), ctx, h) for a in call.args]
    kwargs = {k.arg: arg_spec(inline(k.value, env), ctx, h) for k in call.keywords}
    if any(a is None for a in args) or any(v is None for v in kwargs.values()):
        return None
    return {"args": args, "kwargs": kwargs}


def module_global_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for st in tree.body:
        for n in ast.walk(st) if isinstance(st, (ast.If, ast.Try)) else [st]:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    for x in ast.walk(t):
                        if isinstance(x, ast.Name):
                            out.add(x.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    if a.name != "*":
                        out.add(a.asname or a.name.split(".")[0])
    return out


def doctest_blocks(tree: ast.Module) -> list[tuple[str, list[ast.stmt]]]:
    """Each docstring's examples as a statement list (examples that do not parse are skipped)."""
    out = []
    parser = doctest.DocTestParser()
    for n in [tree] + [x for x in ast.walk(tree) if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]:
        doc = ast.get_docstring(n, clean=True)
        if not doc or ">>>" not in doc:
            continue
        stmts: list[ast.stmt] = []
        try:
            examples = parser.get_examples(doc)
        except ValueError:
            continue
        for ex in examples:
            try:
                stmts.extend(ast.parse(ex.source).body)
            except SyntaxError:
                continue
        out.append((getattr(n, "name", "<module>"), stmts))
    return out


def module_instances(tree: ast.Module) -> dict[str, tuple[str, dict[str, Any]]]:
    """Module-level ``VAR = Cls(literal args)`` -> (Cls, receiver spec)."""
    out = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) and isinstance(st.value, ast.Call) \
                and isinstance(st.value.func, ast.Name):
            c = st.value
            vals = [literal_value(a) for a in c.args] + [literal_value(k.value) for k in c.keywords if k.arg]
            if all(ok for ok, _ in vals) and not any(k.arg is None for k in c.keywords):
                out[st.targets[0].id] = (c.func.id, {"args": [{"v": dr_harness.encode(literal_value(a)[1])[0]} for a in c.args],
                                                     "kwargs": {k.arg: {"v": dr_harness.encode(literal_value(k.value)[1])[0]} for k in c.keywords}})
    return out


def module_aliases(tree: ast.Module, qualname: str) -> dict[str, dict[str, Any]]:
    """Module-level ``alias = VAR.meth`` where VAR is a module-level instance of the target class."""
    parts = qualname.split(".")
    if len(parts) != 2:
        return {}
    inst = module_instances(tree)
    out = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) and isinstance(st.value, ast.Attribute) \
                and st.value.attr == parts[1] and isinstance(st.value.value, ast.Name) and inst.get(st.value.value.id, ("",))[0] == parts[0]:
            out[st.targets[0].id] = inst[st.value.value.id][1]
    return out


def name_aliases(tree: ast.Module, name: str) -> list[str]:
    """Module-level ``Alias = name`` bindings (e.g. ``OMD = OrderedMultiDict``)."""
    return [st.targets[0].id for st in tree.body if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)
            and isinstance(st.value, ast.Name) and st.value.id == name]


_TREES: dict[int, ast.Module | None] = {}


def _parse_cached(raw: bytes) -> ast.Module | None:
    k = hash(raw)
    if k not in _TREES:
        if len(_TREES) > 64:
            _TREES.clear()
        try:
            _TREES[k] = ast.parse(raw)
        except SyntaxError:
            _TREES[k] = None
    return _TREES[k]


def harvest(target_module: str, target_path: str, qualname: str, kind: str, pkg: str, modules: set[str],
            patch_files: dict[str, bytes], base_files: dict[str, bytes], test_paths_patch: list[str], test_paths_base: list[str]) -> Harvest:
    h = Harvest()
    found: dict[str, list[Input]] = {"patch": [], "base": []}
    for side, files, tests in (("patch", patch_files, test_paths_patch), ("base", base_files, test_paths_base)):
        sink = found[side]
        mtree = _parse_cached(files[target_path]) if target_path in files else None
        aliases = module_aliases(mtree, qualname) if mtree is not None else {}
        cal = name_aliases(mtree, qualname.split(".")[0]) if mtree is not None else []
        for tp in tests:
            tree = _parse_cached(files[tp])
            if tree is None:
                continue
            imps, stars = import_map(tree)
            ctx = Ctx(imps, stars, pkg, modules, target_module)
            for block, env0 in _scopes(tree):
                collect_from_block(block, ctx, qualname, kind, h, "test", sink, env0, aliases, cal)
        if target_path in files:
            try:
                tree = ast.parse(files[target_path])
            except SyntaxError:
                tree = None
            if tree is not None:
                globs = module_global_names(tree)
                for _, stmts in doctest_blocks(tree):
                    dimps, dstars = import_map(ast.Module(body=stmts, type_ignores=[]))
                    ctx = Ctx(dimps, dstars, pkg, modules, target_module, module_globals=globs | set(dimps))
                    collect_from_block(stmts, ctx, qualname, kind, h, "doctest", sink, None, aliases, cal)
    base_keys = {(i.origin, i.text) for i in found["base"]}
    seen = set()
    for i in found["patch"] + found["base"]:
        k = i.key()
        if k in seen:
            continue
        seen.add(k)
        if i in found["patch"] and (i.origin, i.text) not in base_keys:
            i.origin += "_new"
        h.inputs.append(i)
    return h


# -- defaults perturbation ------------------------------------------------------------------------
def default_variants(fn: ast.FunctionDef | ast.AsyncFunctionDef, inputs: list[Input], limit: int = 30) -> list[Input]:
    a = fn.args
    pos = a.posonlyargs + a.args
    defaults: dict[str, Any] = {}
    for p, d in zip(pos[len(pos) - len(a.defaults):], a.defaults):
        ok, v = literal_value(d)
        if ok and p not in a.posonlyargs:
            defaults[p.arg] = v
    for p, d in zip(a.kwonlyargs, a.kw_defaults):
        if d is not None:
            ok, v = literal_value(d)
            if ok:
                defaults[p.arg] = v
    if not defaults:
        return []
    names = [p.arg for p in pos]
    out: list[Input] = []
    for inp in inputs:
        if not inp.origin.startswith(("test", "doctest")):
            continue
        given = set(names[: len(inp.args)]) | set(inp.kwargs)
        for p, d in defaults.items():
            if p in given or p in ("self", "cls"):
                continue
            if type(d) is bool:
                vs: list[Any] = [not d]
            elif type(d) is int:
                vs = [d - 1, d + 1, 0, -1, 2]
            elif type(d) is float:
                vs = [0.0, -1.0, d * 2 + 1]
            elif type(d) is str:
                vs = ["", d + "x", " "]
            else:
                continue
            for v in dict.fromkeys(vs):
                if v == d and type(v) is type(d):
                    continue
                kw = dict(inp.kwargs)
                kw[p] = {"v": dr_harness.encode(v)[0]}
                out.append(Input("defaults", list(inp.args), kw, inp.receiver, f"{inp.text} [{p}={v!r}]"))
                if len(out) >= limit:
                    return out
    return out
