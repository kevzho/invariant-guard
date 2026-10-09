"""Static resolution of a contract scope to a callable target (AST only, no imports).

A scope ``path/to/mod.py::name`` names a module-level, undecorated ``def`` (not async)
whose parameters are plain positional or keyword-only parameters. The runner later
imports ``path.to.mod`` from the copied snapshot (``python_path`` roots of the run
profile) and calls ``name`` with typed inputs. Methods, nested functions, ``*args``/
``**kwargs``, decorated functions and module paths that are not importable dotted names
are unsupported targets in DSL v1 and give a clear rejection instead of a guess.
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

    @property
    def params(self) -> tuple[str, ...]:
        return self.positional + self.keyword_only

    def to_json(self) -> dict[str, Any]:
        return {"symbol_id": self.symbol_id, "module": self.module, "function": self.function, "python_path_root": self.root,
                "positional": list(self.positional), "keyword_only": list(self.keyword_only)}


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
        raise TargetError("UNKNOWN_SYMBOL", f"scope {symbol_id!r} must look like 'path/to/mod.py::function'")
    path, name = symbol_id.split("::", 1)
    if path not in files:
        raise TargetError("UNKNOWN_SYMBOL", f"{path} is not in the captured snapshot")
    if not name.isidentifier():
        raise TargetError("UNSUPPORTED_TARGET", f"{name!r} is not a module-level function name (methods and nested functions are unsupported targets)")
    try:
        parsed = parse_source(path, files[path])
    except ParseFailure as exc:
        raise TargetError("UNKNOWN_SYMBOL", f"{path} cannot be parsed: {exc.message}") from None
    defs = [n for n in parsed.tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom))
            and _binds(n, name)]
    if not defs:
        raise TargetError("UNKNOWN_SYMBOL", f"{symbol_id}: no module-level definition named {name!r}")
    if len(defs) > 1:
        raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: {name!r} is bound more than once at module level")
    node = defs[0]
    if not isinstance(node, ast.FunctionDef):
        raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: {name!r} is not a plain (non-async) def")
    if node.decorator_list:
        raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: decorated functions are unsupported targets (the called object is not the analyzed def)")
    a = node.args
    if a.vararg or a.kwarg:
        raise TargetError("UNSUPPORTED_TARGET", f"{symbol_id}: *args/**kwargs parameters are unsupported")
    positional = tuple(p.arg for p in a.posonlyargs + a.args)
    kwonly = tuple(p.arg for p in a.kwonlyargs)
    module, root = module_name(path, python_path or ["."])
    ann = {p.arg: ast.unparse(p.annotation) for p in a.posonlyargs + a.args + a.kwonlyargs if p.annotation is not None}
    header_end = node.body[0] if node.body else node
    span = parsed.lines.span(path, snapshot_id, node.lineno, node.col_offset, header_end.lineno, header_end.col_offset) \
        if node.body and header_end.lineno > node.lineno else parsed.lines.node_span(path, snapshot_id, node)
    return Target(symbol_id, path, name, module, root, positional, kwonly, span, ann, ast.get_docstring(node))


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
    for n in parsed.tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


__all__ = ["Target", "TargetError", "resolve_target", "module_name", "function_node"]
