"""Static resolution of a contract scope to a callable target (AST only, no imports).

A scope ``path/to/mod.py::name`` names a module-level, undecorated ``def`` (not async)
whose parameters are plain positional or keyword-only parameters. The runner later
imports ``path.to.mod`` from the copied snapshot (``python_path`` roots of the run
profile) and calls ``name`` with typed inputs.

A scope ``path/to/mod.py::Class.name`` names a ``def`` in the body of a module-level,
undecorated class without class keywords (no ``metaclass=``), decorated with exactly
``@staticmethod`` or ``@classmethod`` and bound once in the class body. The runner
resolves ``Class`` in the module, then the raw descriptor in ``Class.__dict__``, checks
its type and the wrapped function's qualified name, and calls the wrapped function
(with ``Class`` as the first argument for a classmethod; ``cls`` is not a contract
parameter). Instance methods (they need a receiver object the profiles cannot build),
nested classes and functions, ``*args``/``**kwargs``, other decorators and module paths
that are not importable dotted names are unsupported targets and give a clear rejection
instead of a guess.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from ..frontends.python import ParseFailure, parse_source
from ..models.spans import SourceSpan


class TargetError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Target:
    symbol_id: str
    path: str
    function: str
    module: str  # dotted import name relative to the python_path root
    root: str  # python_path root that contains the module ("." = analysis root)
    positional: tuple[str, ...]
    keyword_only: tuple[str, ...]
    def_span: SourceSpan
    annotations: dict[str, str]
    docstring: str | None
    owner: str | None = None  # class name for a static/class method target
    binding: str = "function"  # function | staticmethod | classmethod

    @property
    def params(self) -> tuple[str, ...]:
        return self.positional + self.keyword_only

    def to_json(self) -> dict[str, Any]:
        return {"symbol_id": self.symbol_id, "module": self.module, "function": self.function, "python_path_root": self.root,
                "positional": list(self.positional), "keyword_only": list(self.keyword_only),
                **({"class": self.owner, "binding": self.binding} if self.owner else {})}

    def call_spec(self) -> dict[str, Any]:
        """How the runner reaches and calls the target (probe request fields)."""
        return {"module": self.module, "function": self.function, "positional": list(self.positional), "keyword_only": list(self.keyword_only),
                "python_path_root": self.root, **({"owner": self.owner, "binding": self.binding} if self.owner else {})}


def module_name(path: str, python_path: list[str]) -> tuple[str, str]:
    for root in python_path:
        r = root.strip("/")
        prefix = "" if r in ("", ".") else r + "/"
        if prefix and not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if not rel.endswith(".py"):
            continue
        parts = rel[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and all(p.isidentifier() for p in parts):
            return ".".join(parts), (r or ".")
    raise TargetError("UNSUPPORTED_TARGET", f"{path} is not importable as a dotted module name under the run profile's python_path {python_path}")


def resolve_target(symbol_id: str, files: dict[str, bytes], snapshot_id: str, python_path: list[str] | None = None) -> Target:
    if "::" not in symbol_id:
        raise TargetError("UNKNOWN_SYMBOL", f"scope {symbol_id!r} must look like 'path/to/mod.py::function' or 'path/to/mod.py::Class.method'")
    path, name = symbol_id.split("::", 1)
    if path not in files:
        raise TargetError("UNKNOWN_SYMBOL", f"{path} is not in the captured snapshot")
    parts = name.split(".")
    if not all(p.isidentifier() for p in parts):
        raise TargetError("UNSUPPORTED_TARGET", f"{name!r} is not 'function' or 'Class.method' (identifiers only)")
    if len(parts) > 2:
        raise TargetError("UNSUPPORTED_TARGET", f"{name!r}: nested classes and functions are unsupported targets (only 'Class.method' of a module-level class)")
    try:
        parsed = parse_source(path, files[path])
    except ParseFailure as exc:
        raise TargetError("UNKNOWN_SYMBOL", f"{path} cannot be parsed: {exc.message}") from None
    owner, binding = None, "function"
    node = _only_binding(parsed.tree.body, parts[0], symbol_id, "module level")
    if len(parts) == 2:
        owner, mname = parts
        if not isinstance(node, ast.ClassDef):
            raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: {owner!r} is not a module-level class")
        if node.decorator_list:
            raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: class {owner} is decorated (the runtime class is not the analyzed class body)")
        if node.keywords:
            raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: class {owner} has class keywords ({', '.join(k.arg or '**' for k in node.keywords)}); "
                              "a metaclass can replace the class body")
        for b in ("staticmethod", "classmethod"):
            if any(_binds(n, b) for n in parsed.tree.body) or any(_binds(n, b) for n in node.body):
                raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: the name {b!r} is rebound in the module or class; the decorator is not the built-in")
        node = _only_binding(node.body, mname, symbol_id, f"class {owner} body")
        if not isinstance(node, ast.FunctionDef):
            raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: {mname!r} is not a plain (non-async) def in class {owner}")
        decs = [d.id for d in node.decorator_list if isinstance(d, ast.Name)]
        if not node.decorator_list:
            raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: instance methods are unsupported targets (calling one needs a receiver object, which no "
                              "input profile builds); only @staticmethod and @classmethod methods are supported")
        if len(node.decorator_list) != 1 or decs not in (["staticmethod"], ["classmethod"]):
            raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: a method target must be decorated with exactly @staticmethod or @classmethod "
                              "(other decorators replace the called object)")
        binding = decs[0]
        name = mname
    elif not isinstance(node, ast.FunctionDef):
        raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: {name!r} is not a plain (non-async) def")
    elif node.decorator_list:
        raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: decorated functions are unsupported targets (the called object is not the analyzed def)")
    a = node.args
    if a.vararg or a.kwarg:
        raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: *args/**kwargs parameters are unsupported")
    pos_nodes = a.posonlyargs + a.args
    if binding == "classmethod":
        if not pos_nodes:
            raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: a classmethod without a positional 'cls' parameter cannot be called")
        pos_nodes = pos_nodes[1:]  # cls is bound by the runner, not a contract parameter
    positional = tuple(p.arg for p in pos_nodes)
    kwonly = tuple(p.arg for p in a.kwonlyargs)
    module, root = module_name(path, python_path or ["."])
    ann = {p.arg: ast.unparse(p.annotation) for p in pos_nodes + a.kwonlyargs if p.annotation is not None}
    header_end = node.body[0] if node.body else node
    span = parsed.lines.span(path, snapshot_id, node.lineno, node.col_offset, header_end.lineno, header_end.col_offset) \
        if node.body and header_end.lineno > node.lineno else parsed.lines.node_span(path, snapshot_id, node)
    return Target(symbol_id, path, name, module, root, positional, kwonly, span, ann, ast.get_docstring(node), owner, binding)


def _only_binding(body: list[ast.stmt], name: str, symbol_id: str, where: str) -> ast.AST:
    defs = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom))
            and _binds(n, name)]
    if not defs:
        raise TargetError("UNKNOWN_SYMBOL", f"{symbol_id}: no definition named {name!r} at {where}")
    if len(defs) > 1:
        raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: {name!r} is bound more than once at {where}")
    return defs[0]


def _binds(n: ast.AST, name: str) -> bool:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return n.name == name
    if isinstance(n, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
    if isinstance(n, ast.AnnAssign):
        return isinstance(n.target, ast.Name) and n.target.id == name
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        return any((al.asname or al.name.split(".")[0]) == name for al in n.names)
    return False


def function_node(files: dict[str, bytes], symbol_id: str) -> ast.FunctionDef | None:
    path, name = symbol_id.split("::", 1)
    try:
        parsed = parse_source(path, files[path])
    except (ParseFailure, KeyError):
        return None
    body: list[ast.stmt] = parsed.tree.body
    *owners, last = name.split(".")
    for o in owners:
        body = next((n.body for n in body if isinstance(n, ast.ClassDef) and n.name == o), [])
    for n in body:
        if isinstance(n, ast.FunctionDef) and n.name == last:
            return n
    return None


__all__ = ["Target", "TargetError", "resolve_target", "module_name", "function_node"]
