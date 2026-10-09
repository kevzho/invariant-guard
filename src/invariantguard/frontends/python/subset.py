"""Supported-subset checks for the M1 Python frontend.

Function-level checks decide whether a symbol can be lowered at all. Statement and
expression-level unsupported constructs inside a lowerable function are handled by the
lowering pass (they become UNKNOWN_EFFECT and taint dependent obligations).
"""

from __future__ import annotations

import ast

from .symbols import _UNSET, FUNCTION_NODES, SymbolInfo, own_scope_nodes

SYMBOL_KIND_REASONS = {
    "method": "methods are outside the M1 subset (receiver types and dispatch not modeled)",
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
    if sym.kind != "function":
        return SYMBOL_KIND_REASONS.get(sym.kind, f"{sym.kind} is outside the M1 subset")
    node = sym.node
    assert isinstance(node, ast.FunctionDef)
    if node.decorator_list:
        return "decorated function: a decorator may replace or wrap it, so callers may not reach this body with the admitted domain"
    if getattr(node, "type_params", None):
        return "functions with type parameters are outside the M1 subset"
    for n in own_scope_nodes(node):
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return "generator functions are outside the M1 subset"
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
