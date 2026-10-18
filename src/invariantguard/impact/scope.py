"""Which files `guard verify` must lower: the changed files and the files that can reach them (I-143).

Cross-module resolution (``frontends/python/resolve.py``) follows only ``from M import N``
bindings, through at most ``MAX_IMPORT_HOPS`` modules. ``import M``, star imports and
dynamic module features leave names unresolved, so they create no dependency on M's content.
Therefore a symbol's lowering, validity key and dependency edges can change only if it
reaches a changed file through a chain of names:

* a top-level binding of ``X`` (a def, class, assignment or import statement) refers to a
  name of ``X`` (any ``Name`` node anywhere in its statements, nested scopes included) whose
  binding is affected; or
* it is ``from M import N`` (anywhere in its statements) where ``M`` maps to a changed file
  or ``N`` is an affected binding of ``M``.

The closure over these edges over-approximates every dependency the resolver can create (it
ignores the hop limit and binding multiplicity, which only make resolution fail). A file is
lowered when it changed or when one of its non-import bindings, or its module-level code, is
affected. Every other file has, on both sides, the same bytes and only bindings whose
resolution cannot differ, so its symbols are outside the slice and are not lowered. Their
modules are still parsed on demand where resolution needs them.
"""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field

from ..frontends.python.parse import ParseFailure, parse_source
from ..frontends.python.resolve import from_import_module, stored_names_outer
from ..frontends.python.symbols import stored_names

MODULE_CODE = "<module-code>"  # top-level statements that bind no name


@dataclass
class Binding:
    refs: set[str] = field(default_factory=set)
    imports: set[tuple[str, str]] = field(default_factory=set)  # (dotted module, imported name or "*")
    import_only: bool = True


@dataclass
class FileFacts:
    bindings: dict[str, Binding]


def file_facts(path: str, raw: bytes, max_nodes: int | None) -> FileFacts | None:
    """Import and reference facts of one file; None when it cannot be parsed (it then resolves nothing)."""
    try:
        tree = parse_source(path, raw, max_nodes).tree
    except ParseFailure:
        return None
    out: dict[str, Binding] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = {stmt.name} | stored_names_outer(stmt)
        else:
            names = set(stored_names(stmt)) or {MODULE_CODE}
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
            b = out.setdefault(n, Binding())
            b.refs |= refs
            b.imports |= imports
            b.import_only = b.import_only and import_only
    return FileFacts(out)


def module_paths(dotted: str) -> tuple[str, str]:
    base = dotted.replace(".", "/")
    return f"{base}.py", f"{base}/__init__.py"


@dataclass
class Scope:
    files: set[str]  # paths to lower (on whichever side they exist)
    reasons: dict[str, str]  # path -> why it is lowered


def lowering_scope(facts: dict[str, FileFacts | None], changed: set[str]) -> Scope:
    """Files to lower, given the facts of every path on either side and the changed paths."""
    importers: dict[tuple[str, str], list[tuple[str, str]]] = {}  # (path, name or "*") -> dependent bindings
    referrers: dict[tuple[str, str], list[str]] = {}  # (path, name) -> bindings of the same file that refer to it
    for path, ff in facts.items():
        if ff is None:
            continue
        for name, b in ff.bindings.items():
            for mod, imported in b.imports:
                for target in module_paths(mod):
                    importers.setdefault((target, "*"), []).append((path, name))
                    if imported != "*":
                        importers.setdefault((target, imported), []).append((path, name))
            for r in b.refs:
                referrers.setdefault((path, r), []).append(name)
    affected: dict[tuple[str, str], str] = {}
    queue: deque[tuple[str, str]] = deque()

    def mark(key: tuple[str, str], why: str) -> None:
        if key not in affected:
            affected[key] = why
            queue.append(key)

    for p in sorted(changed):
        for dep in importers.get((p, "*"), ()):
            mark(dep, f"imports from changed {p}")
    while queue:
        path, name = queue.popleft()
        why = f"reaches {path}::{name}"
        for dep in importers.get((path, name), ()):
            mark(dep, why)
        for other in referrers.get((path, name), ()):
            mark((path, other), why)
    files = {p for p in changed}
    reasons = {p: "changed" for p in changed}
    for (path, name), why in sorted(affected.items()):
        ff = facts.get(path)
        b = ff.bindings.get(name) if ff is not None else None
        if path not in files and b is not None and not b.import_only:
            files.add(path)
            reasons[path] = f"{name}: {why}"
    return Scope(files, reasons)
