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


@dataclass
class ParsedFile:
    path: str
    raw: bytes
    text: str
    tree: ast.Module
    symtable: symtable.SymbolTable
    lines: LineIndex


class ParseFailure(Exception):
    """The file cannot be analyzed (syntax error, unsupported encoding). Not an engine error."""

    def __init__(self, path: str, kind: str, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.kind = kind
        self.message = message
        self.line = line


def parse_source(path: str, raw: bytes) -> ParsedFile:
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
        table = symtable.symtable(text, path, "exec")
    except SyntaxError as exc:
        raise ParseFailure(path, "syntax_error", f"SyntaxError: {exc.msg}", exc.lineno) from None
    except (ValueError, RecursionError, MemoryError) as exc:
        raise ParseFailure(path, "parse_error", f"{type(exc).__name__}: {exc}") from None
    return ParsedFile(path=path, raw=raw, text=text, tree=tree, symtable=table, lines=LineIndex(raw))
