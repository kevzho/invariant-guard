"""Symbol discovery and division-site ownership.

Every function-like scope (module-level function, method, nested function, lambda)
becomes a symbol. Code executed at import (module body, class bodies, default values,
decorators) is attributed to an initializer symbol. Every syntactic ``/``, ``//`` and
``%`` (BinOp or AugAssign) is assigned to exactly one symbol, so obligations are never
dropped from the denominator even when the owning symbol is unsupported.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

DIV_AST_OPS = (ast.Div, ast.FloorDiv, ast.Mod)
# Names whose use can rebind module globals dynamically (resolution is then refused, I-032).
DYNAMIC_GLOBAL_NAMES = frozenset({"globals", "vars", "setattr", "delattr", "exec", "eval", "__import__", "__builtins__"})

_UNSET = object()
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def is_division_node(node: ast.AST) -> bool:
    return isinstance(node, (ast.BinOp, ast.AugAssign)) and isinstance(node.op, DIV_AST_OPS)


@dataclass
class SymbolInfo:
    qualname: str
    kind: str  # function | method | nested_function | lambda | async_function | module_initializer | class_body | conditional_function
    node: ast.AST
    top_level_function: bool
    division_nodes: list[ast.AST] = field(default_factory=list)
    unsupported_memo: object = _UNSET  # cached function_unsupported_reason (subset.py)


class SymbolCollector:
    def __init__(self) -> None:
        self.symbols: list[SymbolInfo] = []
        self._names: dict[str, int] = {}
        self._lambda_counter: dict[str, int] = {}
        # Collected in the same traversal for global resolution (resolve.py).
        self.global_names: set[str] = set()
        self.dynamic_reason: str | None = None

    def _unique(self, qualname: str) -> str:
        n = self._names.get(qualname, 0) + 1
        self._names[qualname] = n
        return qualname if n == 1 else f"{qualname}#{n}"

    def collect(self, tree: ast.Module) -> list[SymbolInfo]:
        module_sym = SymbolInfo("<module>", "module_initializer", tree, False)
        self.symbols.append(module_sym)
        for stmt in tree.body:
            self._visit(stmt, module_sym, prefix="", in_class=None, top=True)
        return self.symbols

    # -- traversal -----------------------------------------------------------------
    def _visit(self, node: ast.AST, owner: SymbolInfo, prefix: str, in_class: str | None, top: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._visit_function(node, owner, prefix, in_class, top)
            return
        if isinstance(node, ast.Lambda):
            self._visit_lambda(node, owner, prefix)
            return
        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                self._visit(dec, owner, prefix, in_class, False)
            for base in node.bases:
                self._visit(base, owner, prefix, in_class, False)
            for kw in node.keywords:
                self._visit(kw, owner, prefix, in_class, False)
            cname = prefix + node.name
            cls_sym = SymbolInfo(self._unique(cname), "class_body", node, False)
            self.symbols.append(cls_sym)
            for stmt in node.body:
                self._visit(stmt, cls_sym, prefix=cname + ".", in_class=cname, top=False)
            return
        if is_division_node(node):
            owner.division_nodes.append(node)
        elif isinstance(node, ast.Global):
            self.global_names.update(node.names)
        elif isinstance(node, ast.Name):
            if node.id in DYNAMIC_GLOBAL_NAMES and self.dynamic_reason is None:
                self.dynamic_reason = f"module uses '{node.id}', which can rebind module globals dynamically"
        elif isinstance(node, ast.Attribute) and self.dynamic_reason is None:
            if node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys":
                self.dynamic_reason = "module uses 'sys.modules', which can rebind module globals dynamically"
            elif node.attr == "__dict__":
                self.dynamic_reason = "module uses '__dict__', which can rebind attributes dynamically"
        for child in ast.iter_child_nodes(node):
            # Statements nested in if/try/etc. at module level are not "top level" defs.
            self._visit(child, owner, prefix, in_class, False)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, owner: SymbolInfo, prefix: str, in_class: str | None, top: bool) -> None:
        # Decorators, defaults and annotations are evaluated in the enclosing scope.
        for dec in node.decorator_list:
            self._visit(dec, owner, prefix, in_class, False)
        self._visit_arguments_outer(node.args, owner, prefix, in_class)
        if node.returns is not None:
            self._visit(node.returns, owner, prefix, in_class, False)
        if in_class is not None:
            kind = "method"
        elif owner.kind == "module_initializer" and top:
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
        elif owner.kind == "module_initializer":
            kind = "conditional_function"
        else:
            kind = "nested_function"
        qual = prefix + node.name
        sym = SymbolInfo(self._unique(qual), kind, node, kind == "function")
        self.symbols.append(sym)
        inner_prefix = qual + ".<locals>."
        for stmt in node.body:
            self._visit(stmt, sym, inner_prefix, None, False)

    def _visit_lambda(self, node: ast.Lambda, owner: SymbolInfo, prefix: str) -> None:
        self._visit_arguments_outer(node.args, owner, prefix, None)
        k = self._lambda_counter.get(prefix, 0) + 1
        self._lambda_counter[prefix] = k
        sym = SymbolInfo(self._unique(f"{prefix}<lambda>#{k}"), "lambda", node, False)
        self.symbols.append(sym)
        self._visit(node.body, sym, prefix + "<lambda>.<locals>.", None, False)

    def _visit_arguments_outer(self, args: ast.arguments, owner: SymbolInfo, prefix: str, in_class: str | None) -> None:
        for d in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
            self._visit(d, owner, prefix, in_class, False)
        for a in args.posonlyargs + args.args + args.kwonlyargs + [x for x in (args.vararg, args.kwarg) if x]:
            if a.annotation is not None:
                self._visit(a.annotation, owner, prefix, in_class, False)


def outer_scope_parts(node: ast.AST) -> list[ast.AST]:
    """The parts of a def/lambda/class that CPython evaluates in the enclosing scope.

    Decorators, parameter defaults, parameter and return annotations (without
    ``from __future__ import annotations``), class bases and keywords. A ``yield`` there
    makes the enclosing function a generator (CPython 3.13: ``def f(): lambda a=(yield): a``
    is a generator function). Type parameters are their own annotation scope (a ``yield``
    there is a SyntaxError), so they are not included.
    """
    parts: list[ast.AST | None] = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = node.args
        parts += a.defaults + a.kw_defaults
        if not isinstance(node, ast.Lambda):
            parts += node.decorator_list
            parts += [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs + [v for v in (a.vararg, a.kwarg) if v]]
            parts.append(node.returns)
    elif isinstance(node, ast.ClassDef):
        parts += node.decorator_list + node.bases + [k.value for k in node.keywords]
    return [p for p in parts if p is not None]


def own_scope_nodes(fn: ast.AST):
    """Yield nodes in a function's own scope (not inside nested def/lambda/class bodies).

    A nested def/lambda/class node is yielded, and so are its outer-scope parts
    (``outer_scope_parts``), but not its body, which is a scope of its own.
    """
    stack = list(ast.iter_child_nodes(fn))
    # Exclude the function's own decorators/defaults/annotations (outer scope).
    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        stack = list(fn.body)
    elif isinstance(fn, ast.Lambda):
        stack = [fn.body]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(outer_scope_parts(node))
            continue
        stack.extend(ast.iter_child_nodes(node))


def is_generator_function(fn: ast.AST) -> bool:
    """True when ``yield``/``yield from`` occurs in the function's own scope (I-138)."""
    return any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in own_scope_nodes(fn))


def stored_names(node: ast.AST) -> set[str]:
    """Names a statement may bind or unbind in the enclosing function scope."""
    names: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
            continue  # body is a separate scope
        elif isinstance(n, ast.Lambda):
            continue
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for alias in n.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchAs) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchStar) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            names.add(n.rest)
        stack.extend(ast.iter_child_nodes(n))
    return names
