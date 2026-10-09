"""ContextPacket construction (M4 T40, docs/08 "Context packet").

A packet holds only what one target function needs, read from the captured snapshot with
the AST (nothing is imported or executed):

1. the target function's source (required; a target that does not fit the budget is
   skipped, never truncated mid-function);
2. trusted static facts computed by the verifier (signature, unenforced annotations,
   the input profile the candidates must use, syntactic facts);
3. direct callees defined in the snapshot (same module, or ``from M import f`` of a
   snapshot module);
4. direct callers in the snapshot (by name in the same module, or through such an import);
5. test functions that mention the target (files named ``test_*.py``/``*_test.py`` or
   under a ``tests/`` directory).

Items 3-5 are added in that priority order while they fit ``max_bytes`` (UTF-8 bytes of
snippet text plus facts) and the token estimate (``ceil(bytes / 3)``, conservative for
code); whatever does not fit is omitted and the omission is stated in the facts. Every
snippet is marked ``trusted: false``: source, comments and docstrings are data, including
any text that looks like instructions (the prompt frames them accordingly).
"""

from __future__ import annotations

import ast
import builtins
import math
from dataclasses import dataclass, field
from typing import Any

from ..frontends.python import ParseFailure, parse_source
from ..models.spans import SourceSpan, digest_json
from ..contracts.profiles import PROFILES
from ..contracts.targets import Target, TargetError, resolve_target

PACKET_SCHEMA_VERSION = "1.0"
DEFAULT_MAX_BYTES = 12_000
DEFAULT_MAX_TOKENS = 4_000
MAX_SNIPPET = 20_000  # schema limit per snippet
MAX_CALLERS = 4
MAX_CALLEES = 4
MAX_TESTS = 3
TEST_DIR_PARTS = ("tests", "test")


@dataclass(frozen=True)
class ContextBudget:
    max_bytes: int = DEFAULT_MAX_BYTES
    max_tokens: int = DEFAULT_MAX_TOKENS

    def to_json(self) -> dict[str, int]:
        return {"max_bytes": self.max_bytes, "max_tokens": self.max_tokens}


def estimate_tokens(n_bytes: int) -> int:
    return math.ceil(n_bytes / 3)


@dataclass
class Snippet:
    role: str  # target | callee | caller | test
    symbol: str
    span: SourceSpan
    text: str

    def to_json(self) -> dict[str, Any]:
        return {"span": self.span.to_json(), "text": self.text, "trusted": False}


@dataclass
class BuiltPacket:
    packet: dict[str, Any]  # schema-valid ContextPacket
    digest: str
    target: Target
    snippets: list[Snippet]
    roles: list[str]  # role of snippets[i]
    omitted: list[dict[str, Any]] = field(default_factory=list)
    bytes_used: int = 0
    params: tuple[str, ...] = ()
    input_profile: str = ""
    line_index: dict[str, Any] = field(default_factory=dict)  # path -> LineIndex (for source-reference validation)


class ContextError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    parts = path.split("/")[:-1]
    return name.startswith("test_") or name.endswith("_test.py") or any(p in TEST_DIR_PARTS for p in parts)


def _module_of(path: str) -> str:
    p = path[:-3] if path.endswith(".py") else path
    parts = p.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _called_names(fn: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out.add(n.func.id)
    return out


def _mentions(fn: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(fn)) or \
        any(isinstance(n, ast.Attribute) and n.attr == name for n in ast.walk(fn))


class ContextPacketBuilder:
    """Indexes a snapshot once (AST only) and builds packets for many targets."""

    def __init__(self, files: dict[str, bytes], snapshot_id: str, python_path: list[str] | None = None) -> None:
        self.files = files
        self.snapshot_id = snapshot_id
        self.python_path = python_path or ["."]
        self.parsed: dict[str, Any] = {}
        self.funcs: dict[str, dict[str, ast.FunctionDef]] = {}
        self.imports: dict[str, dict[str, str]] = {}  # path -> local name -> "path::name" of a snapshot function
        by_module = {_module_of(p): p for p in files if p.endswith(".py")}
        for path in sorted(files):
            if not path.endswith(".py"):
                continue
            try:
                pf = parse_source(path, files[path])
            except ParseFailure:
                continue
            self.parsed[path] = pf
            self.funcs[path] = {n.name: n for n in pf.tree.body if isinstance(n, ast.FunctionDef)}
        for path, pf in self.parsed.items():
            imp: dict[str, str] = {}
            for n in pf.tree.body:
                if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                    src = by_module.get(n.module)
                    for al in n.names:
                        if src is not None and al.name in self.funcs.get(src, {}):
                            imp[al.asname or al.name] = f"{src}::{al.name}"
            self.imports[path] = imp

    def _resolve_call(self, path: str, name: str) -> str | None:
        if name in self.funcs.get(path, {}):
            return f"{path}::{name}"
        return self.imports.get(path, {}).get(name)

    def _snippet(self, role: str, symbol: str) -> Snippet:
        path, name = symbol.split("::", 1)
        pf = self.parsed[path]
        node = self.funcs[path][name]
        start = min([d.lineno for d in node.decorator_list] + [node.lineno])
        span = pf.lines.span(path, self.snapshot_id, start, 0, node.end_lineno or node.lineno, node.end_col_offset or 0)
        text = pf.lines.text(span)
        return Snippet(role, symbol, span, text[:MAX_SNIPPET])

    def callees(self, symbol: str) -> list[str]:
        path, name = symbol.split("::", 1)
        node = self.funcs[path][name]
        out = []
        for called in sorted(_called_names(node)):
            sym = self._resolve_call(path, called)
            if sym is not None and sym != symbol:
                out.append(sym)
        return out

    def callers(self, symbol: str) -> list[str]:
        tpath, tname = symbol.split("::", 1)
        out = []
        for path in sorted(self.funcs):
            if _is_test_path(path):
                continue
            for fname, node in sorted(self.funcs[path].items()):
                if f"{path}::{fname}" == symbol:
                    continue
                for called in _called_names(node):
                    if self._resolve_call(path, called) == symbol:
                        out.append(f"{path}::{fname}")
                        break
        return out

    def tests(self, symbol: str) -> list[str]:
        _, tname = symbol.split("::", 1)
        out = []
        for path in sorted(self.funcs):
            if not _is_test_path(path):
                continue
            for fname, node in sorted(self.funcs[path].items()):
                if fname.startswith("test") and _mentions(node, tname):
                    out.append(f"{path}::{fname}")
        return out

    def unsupported(self, symbol: str) -> list[str]:
        path, name = symbol.split("::", 1)
        node = self.funcs[path][name]
        out = []
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                if isinstance(n.func, ast.Name) and self._resolve_call(path, n.func.id) is None and not hasattr(builtins, n.func.id):
                    out.append(f"call to {n.func.id}() is not defined in the snapshot (behavior unknown to the verifier)")
                elif isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id not in self._params(node):
                    out.append(f"call to {ast.unparse(n.func)}() on a module or object outside the target (behavior unknown)")
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                out.append("uses global/nonlocal state")
            elif isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await)):
                out.append("generator/async behavior")
            elif isinstance(n, ast.Try):
                out.append("exception handling (try/except) alters observable behavior")
        return sorted(set(out))[:12]

    @staticmethod
    def _params(node: ast.FunctionDef) -> set[str]:
        a = node.args
        return {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}

    def build(self, symbol: str, input_profile: str, *, budget: ContextBudget = ContextBudget(),
              existing_contract_ids: list[str] | None = None) -> BuiltPacket:
        if input_profile not in PROFILES:
            raise ContextError("UNKNOWN_PROFILE", f"input profile {input_profile!r} is unknown")
        try:
            target = resolve_target(symbol, self.files, self.snapshot_id, self.python_path)
        except TargetError as exc:
            raise ContextError(exc.code, exc.message) from None
        path, name = symbol.split("::", 1)
        if path not in self.funcs or name not in self.funcs[path]:
            raise ContextError("UNKNOWN_SYMBOL", f"{symbol} is not a parsed module-level function")
        prof = PROFILES[input_profile]
        node = self.funcs[path][name]
        ret = ast.unparse(node.returns) if node.returns is not None else None
        facts = [
            f"target {symbol}: module-level function with parameters ({', '.join(target.params)})",
            "annotations (declared, NOT enforced at runtime): " + (", ".join(f"{p}: {a}" for p, a in sorted(target.annotations.items())) or "none")
            + (f"; return: {ret}" if ret else ""),
            f"candidate input profile (every candidate must use it): {prof.name}: {prof.description}",
            f"docstring present: {'yes' if target.docstring else 'no'}",
        ]
        snippets: list[Snippet] = []
        omitted: list[dict[str, Any]] = []
        used = sum(len(f.encode()) for f in facts)
        target_snip = self._snippet("target", symbol)
        tbytes = len(target_snip.text.encode())
        if tbytes + used > budget.max_bytes or estimate_tokens(tbytes + used) > budget.max_tokens:
            raise ContextError("CONTEXT_TOO_LARGE", f"{symbol} needs {tbytes + used} bytes of context; the budget is {budget.max_bytes} bytes / "
                                                    f"{budget.max_tokens} tokens (the target is never truncated)")
        snippets.append(target_snip)
        used += tbytes
        groups = [("callee", self.callees(symbol)[:MAX_CALLEES]), ("caller", self.callers(symbol)[:MAX_CALLERS]), ("test", self.tests(symbol)[:MAX_TESTS])]
        for role, syms in groups:
            for s in syms:
                sn = self._snippet(role, s)
                b = len(sn.text.encode())
                if used + b > budget.max_bytes or estimate_tokens(used + b) > budget.max_tokens:
                    omitted.append({"role": role, "symbol": s, "bytes": b, "reason": "context budget"})
                    continue
                snippets.append(sn)
                used += b
        unsupported = self.unsupported(symbol)
        if omitted:
            facts.append("context budget omitted: " + ", ".join(f"{o['role']} {o['symbol']} ({o['bytes']} bytes)" for o in omitted))
        packet = {
            "schema_version": PACKET_SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "symbol_ids": [symbol],
            "source_spans": [s.span.to_json() for s in snippets],
            "source_digest": digest_json([[s.span.to_json(), s.text] for s in snippets]),
            "snippets": [s.to_json() for s in snippets],
            "trusted_static_facts": facts,
            "candidate_domain": prof.name,
            "existing_contract_ids": sorted(existing_contract_ids or []),
            "unsupported_constructs": unsupported,
            "resource_budget": budget.to_json(),
        }
        return BuiltPacket(packet=packet, digest=digest_json(packet), target=target, snippets=snippets, roles=[s.role for s in snippets],
                           omitted=omitted, bytes_used=used, params=target.params, input_profile=prof.name,
                           line_index={p: self.parsed[p].lines for p in {s.span.path for s in snippets}})


def build_context_packet(files: dict[str, bytes], snapshot_id: str, symbol: str, input_profile: str, *, budget: ContextBudget = ContextBudget(),
                         existing_contract_ids: list[str] | None = None, python_path: list[str] | None = None) -> BuiltPacket:
    return ContextPacketBuilder(files, snapshot_id, python_path).build(symbol, input_profile, budget=budget, existing_contract_ids=existing_contract_ids)


__all__ = ["ContextBudget", "ContextPacketBuilder", "BuiltPacket", "ContextError", "build_context_packet", "estimate_tokens", "Snippet"]
