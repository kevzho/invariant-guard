"""Module-level name bindings, conservative global resolution and AST fingerprints (M2).

A module-level function can read a global name ``N`` that the static analysis may
resolve only when the binding is unambiguous *within the analyzed source*:

* exactly one module-level binding statement for ``N``, placed directly in the module
  body (not under ``if``/``try``/loops), and
* no ``global N`` declaration anywhere in the module, no ``from X import *``, and no use
  of ``globals``/``vars``/``setattr``/``delattr``/``exec``/``eval``/``__import__``/
  ``sys.modules`` that could rebind module globals dynamically.

The binding then determines the resolution: an undecorated ``def N`` (a call summary
target), ``N = <literal>`` (a module constant), or ``from M import X as N`` where ``M``
maps to exactly one captured file (cross-module; conditional on import resolution). A
name with no module binding resolves to the builtin of that name. Everything else is
unresolved and stays an unknown value/call.

Rebinding a module global from *other* code (monkeypatching ``mod.N = ...``) is outside
the modeled semantics. Results that rely on a resolution state that assumption.

Every lookup returns a dependency record whose ``descriptor`` digests everything the
resolution depended on (all binding statements of the name, the module flags and, for
imports, the module-to-file mapping and the target binding). The records feed the
dependency graph and the summary validity keys (docs/07).
"""

from __future__ import annotations

import ast
import sys
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from ...models.spans import digest_json
from .parse import ParsedFile, ParseFailure
from .symbols import DYNAMIC_GLOBAL_NAMES, SymbolCollector, SymbolInfo, stored_names

_FP_CACHE: "weakref.WeakKeyDictionary[ast.AST, str]" = weakref.WeakKeyDictionary()
CONSTANT_TYPES = (bool, int, float, str, type(None))
MAX_IMPORT_HOPS = 4


_BODY_FP_CACHE: "weakref.WeakKeyDictionary[ast.AST, str]" = weakref.WeakKeyDictionary()


def ast_fingerprint(node: ast.AST) -> str:
    """Digest of an AST without positions: comments and layout do not change it (cached per node)."""
    fp = _FP_CACHE.get(node)
    if fp is None:
        text = ast.dump(node, annotate_fields=True, include_attributes=False)
        fp = digest_json(text)
        _FP_CACHE[node] = fp
        if isinstance(node, ast.FunctionDef):
            # The dump starts with "FunctionDef(name='<name>'": blanking it gives the
            # rename-insensitive body fingerprint from the same (single) dump.
            prefix = f"FunctionDef(name={node.name!r}"
            assert text.startswith(prefix)
            _BODY_FP_CACHE[node] = digest_json("FunctionDef(name='_'" + text[len(prefix):])
    return fp


def body_fingerprint(node: ast.FunctionDef) -> str:
    """Fingerprint of a def with its own name blanked (rename detection)."""
    ast_fingerprint(node)
    return _BODY_FP_CACHE[node]


_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _docstring_nodes(node: ast.AST) -> list[ast.Constant]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, _DOC_OWNERS) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.append(first.value)
    return out


def code_fingerprint(node: ast.AST) -> str:
    """Fingerprint with every docstring (of the node and the scopes nested in it) blanked.

    Equal code fingerprints with different ``ast_fingerprint`` values mean a doc-only
    edit. Docstrings stay part of ``ast_fingerprint``: doctests are executable behavior.
    """
    docs = _docstring_nodes(node)
    if not docs:
        return digest_json(["code", ast_fingerprint(node)])
    saved = [d.value for d in docs]
    try:
        for d in docs:
            d.value = ""
        return digest_json(["code", ast.dump(node, annotate_fields=True, include_attributes=False)])
    finally:
        for d, v in zip(docs, saved):
            d.value = v


def _outer_parts(stmt: ast.AST) -> list[ast.AST]:
    """Parts of a def/class statement evaluated in the enclosing scope (not its body)."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = stmt.args
        return [*stmt.decorator_list, *a.defaults, *(d for d in a.kw_defaults if d is not None),
                *(x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs + [v for v in (a.vararg, a.kwarg) if v] if x.annotation is not None),
                *([stmt.returns] if stmt.returns is not None else [])]
    if isinstance(stmt, ast.ClassDef):
        return [*stmt.decorator_list, *stmt.bases, *stmt.keywords]
    return []


def initializer_fingerprint(node: ast.Module | ast.ClassDef, code_only: bool = False) -> str:
    """Fingerprint of the import-time code of a module or class body.

    Function and class bodies are separate symbols; only the parts of their definitions
    evaluated at import time (name, decorators, defaults, annotations, bases) count here.
    Defs nested inside compound statements are kept whole (conservative).
    """
    fp = code_fingerprint if code_only else ast_fingerprint
    parts: list[Any] = [type(node).__name__, getattr(node, "name", None)]
    if isinstance(node, ast.ClassDef):
        parts.append([fp(p) for p in _outer_parts(node)])
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append([type(stmt).__name__, stmt.name, [fp(p) for p in _outer_parts(stmt)]])
        elif code_only and stmt is node.body[0] and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            parts.append("docstring")
        else:
            parts.append(fp(stmt))
    return digest_json(parts)


def constant_value(node: ast.AST | None) -> tuple[bool, Any]:
    """(True, value) for an immutable literal module constant we model; (False, None) otherwise."""
    if isinstance(node, ast.Constant) and isinstance(node.value, CONSTANT_TYPES):
        return True, node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) in (int, float)
    ):
        return True, -node.operand.value
    if isinstance(node, ast.Tuple) and isinstance(node.ctx, ast.Load) and len(node.elts) <= 64:
        vals = []
        for e in node.elts:
            ok, v = constant_value(e)
            if not ok or isinstance(v, tuple):
                return False, None
            vals.append(v)
        return True, tuple(vals)
    return False, None


@dataclass
class Binding:
    kind: str  # def | async_def | class | const | import_from | import | assign | other
    node: ast.stmt
    top_level: bool

    def descriptor(self, name: str | None = None) -> dict[str, Any]:
        """What the binding of ``name`` depends on. A top-level import binds each name
        independently: adding ``b`` to ``from m import a`` does not change how ``a`` resolves,
        so the descriptor names only the import of ``name`` (module, level, imported name)."""
        if name is not None and self.kind in ("import_from", "import") and isinstance(self.node, (ast.Import, ast.ImportFrom)):
            for a in self.node.names:
                bound = a.asname or (a.name if isinstance(self.node, ast.ImportFrom) else a.name.split(".")[0])
                if bound == name:
                    mod = self.node.module if isinstance(self.node, ast.ImportFrom) else None
                    level = self.node.level if isinstance(self.node, ast.ImportFrom) else 0
                    return {"kind": self.kind, "top_level": self.top_level, "import": [mod, level, a.name, a.asname]}
        return {"kind": self.kind, "top_level": self.top_level, "ast": ast_fingerprint(self.node)}


@dataclass
class Resolution:
    kind: str  # function | const | builtin | unresolved
    name: str
    record: dict[str, Any]
    reason: str = ""
    ctx: "ModuleContext | None" = None  # module that defines the function / constant
    node: ast.AST | None = None  # FunctionDef for functions
    value: Any = None  # constant value
    symbol_id: str | None = None
    conditional: bool = False  # depends on cross-module import resolution
    assumption: str = ""


def class_body_name(class_qualname: str) -> str:
    """Pseudo-global naming the import-time code of a class body (methods depend on it)."""
    return f"{class_qualname}.<class-body>"


def module_name_for(path: str) -> str | None:
    parts = path[:-3].split("/") if path.endswith(".py") else None
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


class ModuleContext:
    """Binding table of one parsed module plus its symbol list."""

    def __init__(self, parsed: ParsedFile, program: "ProgramIndex | None" = None) -> None:
        self.parsed = parsed
        self.path = parsed.path
        self.program = program
        self.module_name = module_name_for(parsed.path)
        collector = SymbolCollector()
        self.symbols: list[SymbolInfo] = collector.collect(parsed.tree)
        self._by_node = {id(s.node): s for s in self.symbols}
        self.bindings: dict[str, list[Binding]] = {}
        self.global_decls: set[str] = set(collector.global_names)
        self.star_import = False
        self.dynamic_reason: str | None = collector.dynamic_reason
        self._scan()
        self._cache: dict[str, Resolution] = {}

    # -- scanning -----------------------------------------------------------------------
    def _scan(self) -> None:
        # Global declarations and dynamic-global uses were collected by SymbolCollector in
        # its traversal of the whole module (one pass instead of two).
        for stmt in self.parsed.tree.body:
            self._scan_stmt(stmt, top_level=True)

    def _scan_stmt(self, stmt: ast.stmt, top_level: bool) -> None:
        if isinstance(stmt, ast.ImportFrom) and any(a.name == "*" for a in stmt.names):
            self.star_import = True
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "def" if isinstance(stmt, ast.FunctionDef) else ("async_def" if isinstance(stmt, ast.AsyncFunctionDef) else "class")
            self._add(stmt.name, Binding(kind, stmt, top_level))
            # Decorators/defaults may contain walrus bindings in module scope.
            for extra in stored_names_outer(stmt):
                self._add(extra, Binding("other", stmt, top_level))
            return
        names = stored_names(stmt)
        if not names:
            return
        kind = "other"
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            kind = "const" if constant_value(stmt.value)[0] else "assign"
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            kind = "const" if constant_value(stmt.value)[0] else "assign"
        elif isinstance(stmt, ast.ImportFrom):
            kind = "import_from"
        elif isinstance(stmt, ast.Import):
            kind = "import"
        compound = isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)) or (
            hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar)
        )
        if compound:
            # Every name bound anywhere inside a compound statement is a conditional binding.
            for name in names:
                self._add(name, Binding("other", stmt, False))
            return
        for name in names:
            self._add(name, Binding(kind, stmt, top_level))

    def _add(self, name: str, b: Binding) -> None:
        self.bindings.setdefault(name, []).append(b)

    # -- lookups -------------------------------------------------------------------------
    def symbol_for(self, node: ast.AST) -> SymbolInfo | None:
        return self._by_node.get(id(node))

    def summary(self) -> dict[str, str]:
        """Compact binding table: name -> digest of its bindings and the module flags.

        Kept after the module's AST is evicted (the slice compares these across snapshots).
        The module path is excluded so that a renamed file compares equal.
        """
        out = {name: digest_json({**self.name_descriptor(name), "module": None}) for name in self.bindings}
        for sym in self.symbols:
            if sym.kind == "class_body":
                # Docstring-blind: a class docstring edit does not change what its methods read.
                out[class_body_name(sym.qualname)] = initializer_fingerprint(sym.node, code_only=True)
        return out

    def name_descriptor(self, name: str) -> dict[str, Any]:
        return {
            "module": self.path,
            "bindings": [b.descriptor(name) for b in self.bindings.get(name, [])],
            "declared_global": name in self.global_decls,
            "star_import": self.star_import,
            "dynamic": self.dynamic_reason,
        }

    def lookup(self, name: str, hops: int = 0) -> Resolution:
        key = f"{name}@{hops}"
        if key in self._cache:
            return self._cache[key]
        res = self._lookup(name, hops)
        self._cache[key] = res
        return res

    def _lookup(self, name: str, hops: int) -> Resolution:
        desc = self.name_descriptor(name)
        record: dict[str, Any] = {"kind": "global", "module": self.path, "name": name}

        def done(kind: str, reason: str = "", extra: dict[str, Any] | None = None, **kw: Any) -> Resolution:
            record["resolution"] = kind
            record["descriptor"] = digest_json({**desc, "extra": extra})
            return Resolution(kind, name, record, reason=reason, **kw)

        if self.dynamic_reason is not None:
            return done("unresolved", f"global '{name}' not resolved: {self.dynamic_reason}")
        if self.star_import:
            return done("unresolved", f"global '{name}' not resolved: the module has a star import")
        if name in self.global_decls:
            return done("unresolved", f"global '{name}' not resolved: a 'global {name}' declaration may rebind it")
        bindings = self.bindings.get(name, [])
        if not bindings:
            return done("builtin", f"'{name}' has no module-level binding (builtin)")
        if len(bindings) > 1:
            return done("unresolved", f"global '{name}' not resolved: it has {len(bindings)} module-level bindings")
        b = bindings[0]
        if not b.top_level:
            return done("unresolved", f"global '{name}' not resolved: it is bound conditionally (inside a compound statement)")
        # No line numbers: this text is part of cached results reused across layout-only edits.
        where = self.path
        if b.kind == "def":
            node = b.node
            assert isinstance(node, ast.FunctionDef)
            if node.decorator_list:
                return done("unresolved", f"global '{name}' is a decorated function (the decorator may replace it)")
            return done(
                "function", ctx=self, node=node, symbol_id=f"{self.path}::{name}",
                assumption=f"module global '{name}' is the function defined in {where} (bound once in its module; not rebound by other modules)",
            )
        if b.kind == "const":
            stmt = b.node
            value_node = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
            ok, value = constant_value(value_node)
            if not ok:
                return done("unresolved", f"global '{name}' is not a literal constant")
            return done(
                "const", value=value, ctx=self,
                assumption=f"module constant '{name}' = {value!r} in {where} (bound once in its module; not rebound by other modules)",
            )
        if b.kind == "import_from":
            stmt = b.node
            assert isinstance(stmt, ast.ImportFrom)
            alias = next(a for a in stmt.names if (a.asname or a.name) == name)
            target_mod, why = self._resolve_from(stmt)
            if target_mod is None or self.program is None:
                return done("unresolved", f"'{name}' is imported from an unresolved module ({why})", extra={"import": why})
            if hops >= MAX_IMPORT_HOPS:
                return done("unresolved", f"'{name}' import chain is longer than {MAX_IMPORT_HOPS} hops")
            target_ctx = self.program.context(target_mod)
            mapping = self.program.module_candidates(self._module_for_from(stmt) or "")
            if target_ctx is None:
                return done("unresolved", f"'{name}' is imported from {target_mod}, which was not parsed", extra={"import": mapping})
            inner = target_ctx.lookup(alias.name, hops + 1)
            extra = {"import": mapping, "target": inner.record.get("descriptor"), "target_kind": inner.kind}
            if inner.kind not in ("function", "const"):
                return done("unresolved", f"'{name}' imported from {target_mod}: {inner.reason or inner.kind}", extra=extra)
            assumption = (
                f"import '{name}' in {self.path} resolves to {target_mod} (analysis root on sys.path; module not shadowed by an installed or standard-library module); "
                + inner.assumption
            )
            return done(inner.kind, ctx=inner.ctx, node=inner.node, symbol_id=inner.symbol_id, value=inner.value,
                        conditional=True, assumption=assumption, extra=extra)
        return done("unresolved", f"global '{name}' is bound by a {b.kind} statement that is not modeled")

    def _module_for_from(self, stmt: ast.ImportFrom) -> str | None:
        return from_import_module(self.path, stmt.level, stmt.module)

    def _resolve_from(self, stmt: ast.ImportFrom) -> tuple[str | None, str]:
        mod = self._module_for_from(stmt)
        if mod is None:
            return None, "relative import above the analysis root"
        top = mod.split(".")[0]
        if stmt.level == 0 and (top in sys.stdlib_module_names or top in sys.builtin_module_names):
            return None, f"module '{mod}' may resolve to the standard library"
        if self.program is None:
            return None, "no program index"
        cands = self.program.module_candidates(mod)
        present = [c for c, ok in cands.items() if ok]
        if len(present) != 1:
            return None, f"module '{mod}' maps to {len(present)} captured files ({', '.join(sorted(cands)) or 'none'})"
        return present[0], "ok"


def from_import_module(path: str, level: int, module: str | None) -> str | None:
    """Dotted module that ``from <level dots><module> import ...`` in file ``path`` names (None above the root)."""
    if level == 0:
        return module
    pkg = path.split("/")[:-1]
    if level - 1 > len(pkg):
        return None
    base = pkg[: len(pkg) - (level - 1)]
    parts = base + (module.split(".") if module else [])
    return ".".join(parts) if parts else None


def stored_names_outer(stmt: ast.stmt) -> set[str]:
    """Names bound in the enclosing scope by decorators/defaults/bases (e.g. walrus)."""
    out: set[str] = set()
    outer: list[ast.AST] = []
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        outer = list(stmt.decorator_list) + list(stmt.args.defaults) + [d for d in stmt.args.kw_defaults if d is not None]
    elif isinstance(stmt, ast.ClassDef):
        outer = list(stmt.decorator_list) + list(stmt.bases) + [k.value for k in stmt.keywords]
    for n in outer:
        for sub in ast.walk(n):
            if isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                out.add(sub.target.id)
    return out


class ProgramIndex:
    """Modules of one snapshot for cross-module resolution, parsed on demand.

    Module contexts (AST + symtable + bindings) live in a bounded LRU cache, so peak
    memory does not grow with the number of files; an evicted module is re-parsed from
    the same captured bytes if it is needed again (deterministic). A compact binding
    summary of every module that was ever built is kept for impact analysis.
    """

    def __init__(self, all_paths: set[str], loader: Callable[[str], ParsedFile], cache_size: int = 64) -> None:
        self.all_paths = set(all_paths)
        self._loader = loader
        self._lru: "OrderedDict[str, ModuleContext]" = OrderedDict()
        self.cache_size = cache_size
        self.failures: dict[str, ParseFailure] = {}
        self.summaries: dict[str, dict[str, str]] = {}
        self.parses = 0

    @classmethod
    def build(cls, parsed_files: list[ParsedFile], all_paths: set[str]) -> "ProgramIndex":
        by_path = {pf.path: pf for pf in parsed_files}

        def loader(path: str) -> ParsedFile:
            if path not in by_path:
                raise ParseFailure(path, "not_captured", "module is not part of this program index")
            return by_path[path]

        prog = cls(set(all_paths) | set(by_path), loader, cache_size=max(64, len(by_path)))
        for pf in parsed_files:
            prog.context(pf.path)
        return prog

    def context(self, path: str) -> ModuleContext | None:
        ctx = self._lru.get(path)
        if ctx is not None:
            self._lru.move_to_end(path)
            return ctx
        if path in self.failures or path not in self.all_paths:
            return None
        try:
            parsed = self._loader(path)
        except ParseFailure as exc:
            self.failures[path] = exc
            return None
        self.parses += 1
        ctx = ModuleContext(parsed, self)
        self._lru[path] = ctx
        if path not in self.summaries:
            self.summaries[path] = ctx.summary()
        while len(self._lru) > self.cache_size:
            self._lru.popitem(last=False)
        return ctx

    def module_candidates(self, dotted: str) -> dict[str, bool]:
        if not dotted:
            return {}
        base = dotted.replace(".", "/")
        return {c: c in self.all_paths for c in (f"{base}.py", f"{base}/__init__.py")}
