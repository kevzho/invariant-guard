"""Parse captured Python source bytes with the stdlib ``ast`` and ``symtable`` modules.

Nothing here imports, compiles-to-run or executes the target. ``ast.parse`` and
``symtable.symtable`` only tokenize and parse. Encoding is detected with
``tokenize.detect_encoding`` (also pure parsing). Only UTF-8 sources are supported
so that AST byte columns match file bytes.
"""

from __future__ import annotations

import ast
import io
import symtable
import sys
import tokenize
from dataclasses import dataclass

from ...models.spans import LineIndex

PYTHON_GRAMMAR = f"{sys.version_info.major}.{sys.version_info.minor}"

# Resource limits, checked on the AST before anything walks it recursively. The analyses walk
# ASTs recursively, so a deeper tree can exhaust the interpreter's recursion limit (the
# shallowest crash measured was about 450 levels of unary minus; I-142). A file over a limit is
# skipped as `resource_limit` (missing coverage), never an engine crash.
MAX_AST_DEPTH = 200
DEFAULT_MAX_FILE_NODES = 100_000
MAX_SOURCE_BYTES = 16 << 20


@dataclass
class ParsedFile:
    path: str
    raw: bytes
    text: str
    tree: ast.Module
    symtable: symtable.SymbolTable
    lines: LineIndex


class ParseFailure(Exception):
    """The file cannot be analyzed (syntax error, unsupported encoding, resource limit). Not an engine error."""

    def __init__(self, path: str, kind: str, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.kind = kind
        self.message = message
        self.line = line


def ast_size(tree: ast.AST, max_nodes: int | None) -> tuple[int, int, bool]:
    """(nodes, depth, truncated): counted level by level without recursion, stopping once over a limit."""
    nodes, depth, level = 0, 0, [tree]
    while level:
        depth += 1
        nodes += len(level)
        if depth > MAX_AST_DEPTH or (max_nodes is not None and nodes > max_nodes):
            return nodes, depth, True
        nxt: list[ast.AST] = []
        for node in level:
            nxt.extend(ast.iter_child_nodes(node))
        level = nxt
    return nodes, depth, False


def parse_tree(path: str, raw: bytes, max_nodes: int | None = DEFAULT_MAX_FILE_NODES) -> tuple[str, ast.Module]:
    """(text, tree) under the same checks and limits as ``parse_source``, without the symbol table."""
    if len(raw) > MAX_SOURCE_BYTES:
        raise ParseFailure(path, "resource_limit", f"file is {len(raw)} bytes (limit {MAX_SOURCE_BYTES})")
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except SyntaxError as exc:
        raise ParseFailure(path, "encoding", str(exc)) from None
    if encoding.lower() not in ("utf-8", "utf-8-sig"):
        raise ParseFailure(path, "encoding", f"source encoding {encoding!r} is not supported (UTF-8 only in M1)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseFailure(path, "encoding", f"invalid UTF-8: {exc}") from None
    try:
        tree = ast.parse(raw, filename=path, mode="exec", type_comments=False)
        nodes, depth, over = ast_size(tree, max_nodes)
    except SyntaxError as exc:
        raise ParseFailure(path, "syntax_error", f"SyntaxError: {exc.msg}", exc.lineno) from None
    except (ValueError, RecursionError, MemoryError) as exc:
        raise ParseFailure(path, "parse_error", f"{type(exc).__name__}: {exc}") from None
    if over:
        if depth > MAX_AST_DEPTH:
            raise ParseFailure(path, "resource_limit", f"syntax tree is deeper than {MAX_AST_DEPTH} levels (engine limit)")
        raise ParseFailure(path, "resource_limit", f"syntax tree has more than {max_nodes} nodes (analysis.max_file_nodes)")
    return text, tree


def parse_source(path: str, raw: bytes, max_nodes: int | None = DEFAULT_MAX_FILE_NODES) -> ParsedFile:
    text, tree = parse_tree(path, raw, max_nodes)
    try:
        table = symtable.symtable(text, path, "exec")
    except SyntaxError as exc:
        raise ParseFailure(path, "syntax_error", f"SyntaxError: {exc.msg}", exc.lineno) from None
    except (ValueError, RecursionError, MemoryError) as exc:
        raise ParseFailure(path, "parse_error", f"{type(exc).__name__}: {exc}") from None
    return ParsedFile(path=path, raw=raw, text=text, tree=tree, symtable=table, lines=LineIndex(raw))
