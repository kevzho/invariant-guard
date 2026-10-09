"""Source spans and content digests.

Conventions (docs/09): one-based lines, zero-based UTF-8 byte columns, exclusive end.
Absolute byte offsets into the captured file bytes are also recorded.

CPython's ``ast`` reports ``col_offset`` as a UTF-8 byte offset into the decoded line.
The tokenizer strips a leading UTF-8 BOM, so line 1 is treated as starting after the
BOM. Line terminators recognised: ``\\r\\n``, ``\\n`` and a lone ``\\r`` (as CPython does).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

UTF8_BOM = b"\xef\xbb\xbf"
_LINE_END = re.compile(rb"\r\n|\r|\n")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON encoding used for content addressing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest_json(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj))


def short_hex(obj: Any, length: int = 32) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()[:length]


@dataclass(frozen=True, slots=True)
class SourceSpan:
    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    start_byte: int
    end_byte: int
    snapshot_id: str

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "snapshot_id": self.snapshot_id,
        }


class LineIndex:
    """Maps (line, byte column) pairs to absolute byte offsets in raw file bytes."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.bom = raw.startswith(UTF8_BOM)
        first = len(UTF8_BOM) if self.bom else 0
        # "\r\n" is one terminator; a lone "\r" or "\n" is one (as CPython's tokenizer).
        self.line_starts = [first] + [m.end() for m in _LINE_END.finditer(raw, first)]

    def offset(self, line: int, column: int) -> int:
        if line < 1 or line > len(self.line_starts):
            raise ValueError(f"line {line} out of range")
        return self.line_starts[line - 1] + column

    def span(self, path: str, snapshot_id: str, line: int, col: int, end_line: int, end_col: int) -> SourceSpan:
        start = self.offset(line, col)
        end = self.offset(end_line, end_col)
        if end < start:
            raise ValueError("span end precedes start")
        return SourceSpan(path, line, col, end_line, end_col, start, end, snapshot_id)

    def node_span(self, path: str, snapshot_id: str, node: Any) -> SourceSpan:
        end_line = getattr(node, "end_lineno", None) or node.lineno
        end_col = getattr(node, "end_col_offset", None)
        if end_col is None:
            end_col = node.col_offset
        return self.span(path, snapshot_id, node.lineno, node.col_offset, end_line, end_col)

    def text(self, span: SourceSpan) -> str:
        return self.raw[span.start_byte : span.end_byte].decode("utf-8", errors="replace")
