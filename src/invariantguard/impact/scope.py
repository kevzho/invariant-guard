"""Which files `guard verify` must lower: the changed files and the files that can reach them (I-143).

Cross-module resolution (``frontends/python/resolve.py``) follows only ``from M import N``
bindings, through at most ``MAX_IMPORT_HOPS`` modules. ``import M``, star imports and
dynamic module features leave names unresolved, so they create no dependency on M's content.
Therefore a symbol's lowering, validity key and dependency edges can change only if it
reaches a changed file through a chain of names:

* a top-level binding of ``X`` (a def, class, assignment or import statement) refers to a
  name of ``X`` (any ``Name`` node anywhere in its statements, nested scopes included) whose
  binding is affected and propagates; or
* it is ``from M import N`` (anywhere in its statements) where ``M`` maps to a changed file
  or ``N`` is an affected, propagating binding of ``M``.

A binding propagates when the resolver can look through it: an undecorated top-level
function bound once (inlined, so its body's names matter) or a top-level ``from`` import
bound once. A class, an assignment, a decorated or conditional def, or a name with several
bindings resolves to a descriptor of that binding's own AST (``Binding.descriptor``), which
is the same on both sides of an unchanged file, so an affected binding of that kind is
lowered itself but changes nothing for its referrers.

The closure over these edges over-approximates every dependency the resolver can create (it
ignores the hop limit and binding multiplicity, which only make resolution fail). A file is
lowered when it changed or when one of its non-import bindings, or its module-level code, is
affected; in an unchanged file only the symbols defined in the statements of affected bindings
are lowered (and the module initializer). Every other symbol has, on both sides, the same
bytes and only bindings whose resolution cannot differ, so it is outside the slice and is not
lowered. Modules are still parsed on demand where resolution needs them.
"""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field

from ..frontends.python.parse import ParseFailure, parse_tree
from ..frontends.python.resolve import from_import_module, stored_names_outer
from ..frontends.python.symbols import stored_names

MODULE_CODE = "<module-code>"  # top-level statements that bind no name


@dataclass
class Statement:
    """Facts of one top-level statement (shared by every name it binds)."""

    names: tuple[str, ...]
    refs: frozenset[str]  # names bound at the top level of the same file that the statement mentions
    imports: frozenset[tuple[str, str]]  # (dotted module, imported name or "*")


@dataclass
class Binding:
    import_only: bool = True
    stmts: set[int] = field(default_factory=set)  # indexes of the top-level statements that bind it
    kinds: list[str] = field(default_factory=list)  # one entry per binding statement

    @property
    def propagates(self) -> bool:
        """Whether a referrer's lowering can depend on more than this binding's own syntax.

        The resolver inlines an undecorated top-level function (its body resolves further
        names) and follows a top-level ``from M import N``. Every other binding (a class, an
        assignment, a decorated or conditional def, several bindings of one name) resolves
        to a descriptor of its own AST only, which is identical in an unchanged file.
        """
        return len(self.kinds) == 1 and self.kinds[0] in ("def", "import_from")


@dataclass
class FileFacts:
    stmts: list[Statement]
    bindings: dict[str, Binding]


def file_facts(path: str, raw: bytes, max_nodes: int | None) -> FileFacts | None:
    """Import and reference facts of one file; None when it cannot be parsed (it then resolves nothing)."""
    try:
        tree = parse_tree(path, raw, max_nodes)[1]
    except ParseFailure:
        return None
    bindings: dict[str, Binding] = {}
    pending: list[tuple[tuple[str, ...], set[str], frozenset[tuple[str, str]]]] = []
    for i, stmt in enumerate(tree.body):
        kind: dict[str, str] = {}
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = {stmt.name} | stored_names_outer(stmt)
            if isinstance(stmt, ast.FunctionDef) and not stmt.decorator_list:
                kind[stmt.name] = "def"
        else:
            names = set(stored_names(stmt)) or {MODULE_CODE}
            if isinstance(stmt, ast.ImportFrom):
                kind = dict.fromkeys(names, "import_from")
        refs: set[str] = set()
        imports: set[tuple[str, str]] = set()
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name):
                refs.add(node.id)
            elif isinstance(node, ast.ImportFrom):
                mod = from_import_module(path, node.level, node.module)
                if mod is not None:
                    imports.update((mod, a.name) for a in node.names)
        import_only = isinstance(stmt, (ast.Import, ast.ImportFrom))
        for n in names:
            b = bindings.setdefault(n, Binding())
            b.import_only = b.import_only and import_only
            b.stmts.add(i)
            b.kinds.append(kind.get(n, "other"))
        pending.append((tuple(sorted(names)), refs, frozenset(imports)))
    # Only names bound at the top level of this file can link two of its statements.
    stmts = [Statement(names, frozenset(refs & bindings.keys()), imports) for names, refs, imports in pending]
    return FileFacts(stmts, bindings)


def module_paths(dotted: str) -> tuple[str, str]:
    base = dotted.replace(".", "/")
    return f"{base}.py", f"{base}/__init__.py"


@dataclass
class Scope:
    files: dict[str, set[int] | None]  # path to lower -> top-level statements to lower (None: whole file)
    reasons: dict[str, str]  # path -> why it is lowered


def lowering_scope(facts: dict[str, FileFacts | None], changed: set[str]) -> Scope:
    """Files to lower, given the facts of every path on either side and the changed paths."""
    importers: dict[tuple[str, str], list[tuple[str, int]]] = {}  # (path, name or "*") -> dependent statements
    referrers: dict[tuple[str, str], list[int]] = {}  # (path, name) -> statements of the same file that refer to it
    for path, ff in facts.items():
        if ff is None:
            continue
        for i, st in enumerate(ff.stmts):
            for mod, imported in st.imports:
                for target in module_paths(mod):
                    importers.setdefault((target, "*"), []).append((path, i))
                    if imported != "*":
                        importers.setdefault((target, imported), []).append((path, i))
            for r in st.refs:
                referrers.setdefault((path, r), []).append(i)
    affected: dict[tuple[str, str], str] = {}
    marked: set[tuple[str, int]] = set()
    queue: deque[tuple[str, str]] = deque()

    def mark(path: str, i: int, why: str) -> None:
        """Every name a statement binds depends on everything the statement mentions."""
        if (path, i) in marked:
            return
        marked.add((path, i))
        ff = facts[path]
        assert ff is not None
        for n in ff.stmts[i].names:
            if (path, n) not in affected:
                affected[(path, n)] = why
                queue.append((path, n))

    for p in sorted(changed):
        for dep, i in importers.get((p, "*"), ()):
            mark(dep, i, f"imports from changed {p}")
    while queue:
        path, name = queue.popleft()
        ff = facts.get(path)
        b = ff.bindings.get(name) if ff is not None else None
        if b is not None and not b.propagates:
            continue  # affected itself (lowered below), but its referrers resolve it to the same descriptor
        why = f"reaches {path}::{name}"
        for dep, i in importers.get((path, name), ()):
            mark(dep, i, why)
        for i in referrers.get((path, name), ()):
            mark(path, i, why)
    files: dict[str, set[int] | None] = {p: None for p in changed}
    reasons = {p: "changed" for p in changed}
    for (path, name), why in sorted(affected.items()):
        ff = facts.get(path)
        b = ff.bindings.get(name) if ff is not None else None
        if path in changed or b is None or b.import_only:
            continue
        sel = files.setdefault(path, set())
        assert sel is not None
        sel |= b.stmts
        reasons.setdefault(path, f"{name}: {why}")
    return Scope(files, reasons)
