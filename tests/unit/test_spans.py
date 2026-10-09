import ast

from invariantguard.frontends.python import parse_source
from invariantguard.models.spans import LineIndex, sha256_bytes

SNAP = "sha256:" + "0" * 64


def _division_spans(raw: bytes):
    parsed = parse_source("m.py", raw)
    out = []
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            out.append((node, parsed.lines.node_span("m.py", SNAP, node)))
    return parsed, out


def test_unicode_columns_are_utf8_bytes_with_exclusive_end():
    raw = 'def f(größe, b):\n    s = "Δ≠0"; return größe / b\n'.encode()
    parsed, spans = _division_spans(raw)
    (node, span), = spans
    line = raw.split(b"\n")[1]
    start = line.index("größe / b".encode())
    assert span.start_line == span.end_line == 2
    assert span.start_column == start  # byte column, not character column
    assert span.end_column == start + len("größe / b".encode())  # exclusive end
    assert span.start_column != line.decode().index("größe / b")  # differs from char column here
    assert raw[span.start_byte:span.end_byte] == "größe / b".encode()
    assert parsed.lines.text(span) == ast.get_source_segment(parsed.text, node)


def test_crlf_lone_cr_and_bom():
    raw = b"\xef\xbb\xbfx = 1\r\ny = 2\rdef f(a, b):\n    return a / b\n"
    parsed, spans = _division_spans(raw)
    (node, span), = spans
    assert span.start_line == 4 and span.start_column == 11
    assert raw[span.start_byte:span.end_byte] == b"a / b"
    li = LineIndex(raw)
    assert li.line_starts[0] == 3  # BOM is not part of line 1 columns
    assert li.offset(2, 0) == raw.index(b"y = 2")
    assert li.offset(3, 0) == raw.index(b"def f")


def test_multiline_span_and_empty_span():
    raw = b"def f(a, b):\n    return (a /\n            b)\n"
    _, spans = _division_spans(raw)
    (_, span), = spans
    assert (span.start_line, span.end_line) == (2, 3)
    assert raw[span.start_byte:span.end_byte] == b"a /\n            b"
    li = LineIndex(raw)
    s = li.span("m.py", SNAP, 1, 4, 1, 4)
    assert s.start_byte == s.end_byte  # empty span is representable


def test_content_digest():
    assert sha256_bytes(b"") == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_bytes("é".encode()) != sha256_bytes("é".encode("latin-1"))
