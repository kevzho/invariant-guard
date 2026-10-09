"""Python frontend: stdlib ast/symtable parsing, symbol resolution and IR lowering.

The frontend never imports the target module. Parsing success is not analysis coverage:
every function-like symbol is reported, and unsupported ones carry their division sites
as UNKNOWN obligations.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from ...ir.model import DIVISION_PROPERTY, Function, ModuleIR, ObligationSite, Op, UnsupportedSymbol
from ...models.spans import digest_json, sha256_bytes, short_hex
from .lower import PROFILE_TYPES, FunctionLowerer, find_table
from .parse import PYTHON_GRAMMAR, ParsedFile, ParseFailure, parse_source
from .resolve import ModuleContext, ProgramIndex, ast_fingerprint
from .subset import function_unsupported_reason
from .symbols import SymbolInfo

__all__ = [
    "lower_module", "parse_source", "ParseFailure", "PYTHON_GRAMMAR", "FrontendResult", "FRONTEND_CAPABILITIES",
    "ProgramIndex", "ModuleContext", "domain_decls",
]

_AST_TO_OP = {ast.Div: Op.DIV, ast.FloorDiv: Op.FLOORDIV, ast.Mod: Op.MOD}

FRONTEND_CAPABILITIES = {
    "language": "python",
    "grammar": PYTHON_GRAMMAR,
    "parser": "stdlib ast + symtable (no imports, no execution)",
    "supported_type_domains": [
        "exact built-in int (declared or annotation-conditional)", "bool", "literal float/str/None constants",
        "exact built-in list/tuple lengths (literal, declared or annotation-conditional)",
        "optional exact values (X | None, Optional[X]) for None-flow",
    ],
    "effect_precision": "unknown calls/attributes/indexes are arbitrary effects; direct calls to resolved module functions are instantiated (context-sensitive summaries)",
    "properties": ["division.zero_denominator", "index.out_of_range", "none.attribute_access"],
    "library_summaries": [],
}


@dataclass
class FrontendResult:
    module: ModuleIR
    function_errors: list[dict[str, Any]]  # engine errors during lowering (not unsupported constructs)
    diagnostics: list[dict[str, Any]]


def domain_decls(value: Any) -> list[Any]:
    """Normalize a domain map value (one declaration, or several for disjoint params)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _make_sites(parsed: ParsedFile, snapshot_id: str, sym: SymbolInfo) -> dict[int, ObligationSite]:
    sites: dict[int, ObligationSite] = {}
    counts: dict[str, int] = {}
    for ordinal, node in enumerate(sym.division_nodes):
        span = parsed.lines.node_span(parsed.path, snapshot_id, node)
        divisor = node.right if isinstance(node, ast.BinOp) else node.value
        dspan = parsed.lines.node_span(parsed.path, snapshot_id, divisor)
        dump = ast.dump(node, include_attributes=False)
        n = counts.get(dump, 0)
        counts[dump] = n + 1
        sites[id(node)] = ObligationSite(
            ordinal=ordinal,
            op=_AST_TO_OP[type(node.op)],
            span=span,
            text=parsed.lines.text(span),
            divisor_text=parsed.lines.text(dspan),
            property_id=DIVISION_PROPERTY,
            anchor=f"{DIVISION_PROPERTY}:{short_hex([DIVISION_PROPERTY, dump], 16)}#{n}",
        )
    return sites


def _symbol_fingerprint(sym: SymbolInfo, reason: str | None) -> str:
    """Fingerprint of an unsupported symbol: everything its results depend on.

    Its obligations are UNKNOWN with ``reason``; their identities are the division
    nodes (anchors). Dumping the whole node (a class body includes every method) would be
    quadratic in nesting and is not needed for soundness of the validity key.
    """
    return digest_json({"kind": sym.kind, "qualname": sym.qualname, "reason": reason,
                        "divisions": [ast_fingerprint(n) for n in sym.division_nodes]})


def lower_module(parsed: ParsedFile, snapshot_id: str, declared_domains: dict[str, Any], program: ProgramIndex | None = None) -> FrontendResult:
    """Lower all supported functions of one parsed file.

    ``declared_domains`` maps symbol_id -> a declaration (or a list of declarations for
    disjoint parameter sets) with ``params`` (tuple or None), ``profile``, ``authority``
    and ``reason`` attributes (see invariantguard.config.DomainDecl). ``program`` indexes
    the other parsed modules of the snapshot for cross-module resolution; without it,
    only names bound in this module resolve.
    """
    ctx = program.context(parsed.path) if program is not None else None
    if ctx is None:
        program = ProgramIndex.build([parsed], {parsed.path})
        ctx = program.context(parsed.path)
    assert ctx is not None
    parsed = ctx.parsed  # the context's own parse (identical bytes) owns the AST nodes used below
    symbols = ctx.symbols
    functions: list[Function] = []
    unsupported: list[UnsupportedSymbol] = []
    errors: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    requested: list[str] = []
    for sym in symbols:
        symbol_id = f"{parsed.path}::{sym.qualname}"
        if sym.kind in ("module_initializer", "class_body") and not sym.division_nodes:
            continue  # import-time code without division sites contributes no obligations
        requested.append(symbol_id)
        sites = _make_sites(parsed, snapshot_id, sym)
        reason = function_unsupported_reason(sym)
        node_span = parsed.lines.node_span(parsed.path, snapshot_id, sym.node) if sym.kind != "module_initializer" else parsed.lines.span(parsed.path, snapshot_id, 1, 0, 1, 0)
        if reason is not None:
            for s in sites.values():
                s.unsupported_reason = reason
            unsupported.append(UnsupportedSymbol(symbol_id, sym.kind, reason, node_span, sorted(sites.values(), key=lambda s: s.ordinal),
                                                 fingerprint=_symbol_fingerprint(sym, reason)))
            if symbol_id in declared_domains:
                diagnostics.append({"kind": "domain_on_unsupported_symbol", "symbol_id": symbol_id, "message": f"declared domain not used: {reason}"})
            continue
        node = sym.node
        assert isinstance(node, ast.FunctionDef)
        table = find_table(parsed.symtable, node)
        if table is None:
            errors.append({"symbol_id": symbol_id, "stage": "symbol_resolution", "error_type": "SymbolTableMismatch", "message": "could not match function to its symbol table", "_sites": sorted(sites.values(), key=lambda s: s.ordinal)})
            continue
        local_names = {s.get_name() for s in table.get_symbols() if s.is_local() or s.is_parameter()}
        decls = domain_decls(declared_domains.get(symbol_id))
        declared_params: dict[str, str] | None = None
        if decls:
            param_names = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
            declared_params = {}
            for decl in decls:
                wanted = list(decl.params) if decl.params is not None else param_names
                missing = [p for p in wanted if p not in param_names]
                if missing:
                    diagnostics.append({"kind": "domain_unknown_params", "symbol_id": symbol_id, "message": f"declared domain names parameters not in signature: {missing}"})
                if decl.profile not in PROFILE_TYPES:
                    diagnostics.append({"kind": "domain_unknown_profile", "symbol_id": symbol_id, "message": f"unknown domain profile {decl.profile!r}"})
                    continue
                for p in wanted:
                    if p in param_names:
                        declared_params[p] = decl.profile
        try:
            fn = FunctionLowerer(
                node=node,
                symbol_id=symbol_id,
                path=parsed.path,
                snapshot_id=snapshot_id,
                lines=parsed.lines,
                local_names=local_names,
                declared_params=declared_params,
                sites=sites,
                ctx=ctx,
            ).lower()
        except Exception as exc:  # noqa: BLE001 - classified as an engine error, never as unsupported
            errors.append({"symbol_id": symbol_id, "stage": "lowering", "error_type": type(exc).__name__, "message": str(exc), "_sites": sorted(sites.values(), key=lambda s: s.ordinal)})
            continue
        if decls:
            fn.domain_note = "; ".join(f"{d.profile} ({d.authority}): {d.reason}" for d in decls)
        functions.append(fn)
    imports = []
    for stmt in parsed.tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            imports.append({
                "module": getattr(stmt, "module", None) or ",".join(a.name for a in stmt.names),
                "names": [a.asname or a.name for a in stmt.names],
                "line": stmt.lineno,
                "resolved": False,
                "executed": False,
            })
    module = ModuleIR(
        path=parsed.path,
        source_digest=sha256_bytes(parsed.raw),
        functions=functions,
        unsupported_symbols=unsupported,
        imports=imports,
        requested_symbols=requested,
    )
    return FrontendResult(module=module, function_errors=errors, diagnostics=diagnostics)
