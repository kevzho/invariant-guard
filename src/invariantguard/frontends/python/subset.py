"""Supported-subset checks for the M1 Python frontend.

Function-level checks decide whether a symbol can be lowered at all. Statement and
expression-level unsupported constructs inside a lowerable function are handled by the
lowering pass (they become UNKNOWN_EFFECT and taint dependent obligations).

Generator functions (``yield``/``yield from`` in the body) are lowered like any function
(I-135); async functions, and so async generators, stay unsupported.
"""

from __future__ import annotations

import ast

from .symbols import _UNSET, FUNCTION_NODES, SymbolInfo, own_scope_nodes

SYMBOL_KIND_REASONS = {
    "nested_function": "nested functions are outside the M1 subset (closures not modeled)",
    "lambda": "lambdas are outside the M1 subset",
    "async_function": "async functions are outside the M1 subset",
    "conditional_function": "conditionally defined functions are outside the M1 subset",
    "module_initializer": "module-level code executes at import and is not analyzed in M1",
    "class_body": "class bodies execute at import and are not analyzed in M1",
}


def function_unsupported_reason(sym: SymbolInfo) -> str | None:
    if sym.unsupported_memo is not _UNSET:
        return sym.unsupported_memo  # type: ignore[return-value]
    reason = _unsupported_reason(sym)
    sym.unsupported_memo = reason
    return reason


def _unsupported_reason(sym: SymbolInfo) -> str | None:
    if sym.kind == "method":
        why = _method_reason(sym)
        if why is not None:
            return why
    elif sym.kind != "function":
        return SYMBOL_KIND_REASONS.get(sym.kind, f"{sym.kind} is outside the M1 subset")
    node = sym.node
    assert isinstance(node, ast.FunctionDef)
    if node.decorator_list:
        return "decorated function: a decorator may replace or wrap it, so callers may not reach this body with the admitted domain"
    if getattr(node, "type_params", None):
        return "functions with type parameters are outside the M1 subset"
    for n in own_scope_nodes(node):
        if isinstance(n, ast.Await):
            return "await is outside the M1 subset"
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            return "global/nonlocal declarations are outside the M1 subset"
        if isinstance(n, ast.NamedExpr):
            return "assignment expressions (:=) are outside the M1 subset"
        if isinstance(n, FUNCTION_NODES):
            for inner in ast.walk(n):
                if isinstance(inner, ast.Nonlocal):
                    return "a nested function may rebind this function's locals via nonlocal"
    return None


def is_mangled_private(name: str) -> bool:
    """True for names CPython rewrites inside a class body (``__x`` -> ``_Cls__x``)."""
    return name.startswith("__") and not name.endswith("__")


def _method_reason(sym: SymbolInfo) -> str | None:
    """Method-specific limits (I-130). A supported method is lowered as a plain function.

    The receiver is an ordinary parameter with no established type, so nothing about
    ``self``/``cls`` (attributes, dispatch, subclasses) is assumed. Name resolution is
    unchanged: CPython skips class scopes when a method body resolves a free name, so
    free names resolve to module globals and builtins exactly as in a module-level function.
    """
    node = sym.node
    if isinstance(node, ast.AsyncFunctionDef):
        return "async methods are outside the subset"
    assert isinstance(node, ast.FunctionDef)
    if ".<locals>." in sym.qualname:
        return "methods of classes defined inside functions are outside the subset (closures not modeled)"
    if node.decorator_list:
        return "decorated method: a decorator (including staticmethod/classmethod/property) may replace or wrap it, so callers may not reach this body with the admitted domain"
    args = node.args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs + [x for x in (args.vararg, args.kwarg) if x]]
    for n in own_scope_nodes(node):
        if isinstance(n, ast.Name):
            names.append(n.id)
        elif isinstance(n, ast.arg):
            names.append(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.extend((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.append(n.name)
    if any(is_mangled_private(x) for x in names):
        return "private-name mangling inside a class body (__name -> _Class__name) is not modeled"
    return None
