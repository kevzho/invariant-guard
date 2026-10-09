"""Typed value encoding for probe inputs and observations (M3, docs/06, docs/10).

Probe inputs and the results observed inside the runner cross a process (and container)
boundary as JSON. Plain JSON loses distinctions that contracts depend on (``1`` vs
``1.0``, ``-0.0``, NaN/infinity, tuples), so every value is a tagged node:

* ``{"t": "none"}``, ``{"t": "bool", "v": true}``
* ``{"t": "int", "v": "123"}`` (decimal string, so no precision is lost)
* ``{"t": "float", "v": "0x1.0000000000000p+0"}`` (``float.hex``: exact, incl. nan/inf)
* ``{"t": "str", "v": "..."}``
* ``{"t": "list", "v": [...]}``, ``{"t": "tuple", "v": [...]}``
* ``{"t": "dict", "v": [["key", node], ...]}`` (string keys only)
* ``{"t": "opaque", "type": "module.Qualname", "why": "..."}`` - a value the encoder does
  not support (custom objects, subclasses, cycles, too deep/large). Decoding yields an
  :class:`Opaque` marker; the predicate evaluator turns it into UNKNOWN.

Only *exact* built-in types are encoded (``type(x) is int``), so no user-defined method
(``__eq__``, ``__iter__``, ``__float__``...) is ever called while encoding or decoding.
The probe harness (``execution/probe_harness.py``) carries its own copy of the encoder
because it runs standalone inside the runner; ``tests/unit/test_values.py`` checks that
the two agree.

Decoding is defensive: the observation stream comes from untrusted code, so sizes,
depth, integer digits and tags are bounded and validated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MAX_DEPTH = 32
MAX_NODES = 100_000
MAX_INT_DIGITS = 4000
MAX_STR = 65_536


class ValueError_(Exception):
    """A malformed or oversized encoded value."""


@dataclass(frozen=True)
class Opaque:
    """A value that could not be represented exactly (custom object, cycle, too large)."""

    type_name: str
    why: str

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<opaque {self.type_name}: {self.why}>"


def encode(value: Any, *, max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES) -> dict[str, Any]:
    """Encode an exact built-in value (host side: generated probe inputs)."""
    count = [0]

    def enc(x: Any, depth: int, active: set[int]) -> dict[str, Any]:
        count[0] += 1
        if count[0] > max_nodes:
            return {"t": "opaque", "type": type(x).__name__, "why": "too many nodes"}
        if depth > max_depth:
            return {"t": "opaque", "type": type(x).__name__, "why": "nesting too deep"}
        t = type(x)
        if x is None:
            return {"t": "none"}
        if t is bool:
            return {"t": "bool", "v": x}
        if t is int:
            s = str(x) if x.bit_length() < 13_000 else None
            if s is None or len(s.lstrip("-")) > MAX_INT_DIGITS:
                return {"t": "opaque", "type": "int", "why": "integer too large"}
            return {"t": "int", "v": s}
        if t is float:
            return {"t": "float", "v": x.hex()}
        if t is str:
            if len(x) > MAX_STR:
                return {"t": "opaque", "type": "str", "why": "string too long"}
            return {"t": "str", "v": x}
        if t in (list, tuple, dict):
            if id(x) in active:
                return {"t": "opaque", "type": t.__name__, "why": "cyclic value"}
            active.add(id(x))
            try:
                if t is dict:
                    items = []
                    for k, v in x.items():
                        if type(k) is not str:
                            return {"t": "opaque", "type": "dict", "why": "non-string key"}
                        items.append([k, enc(v, depth + 1, active)])
                    return {"t": "dict", "v": items}
                return {"t": t.__name__, "v": [enc(v, depth + 1, active) for v in x]}
            finally:
                active.discard(id(x))
        return {"t": "opaque", "type": f"{t.__module__}.{t.__qualname__}", "why": "unsupported type"}

    return enc(value, 0, set())


def decode(node: Any, *, max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES) -> Any:
    """Decode a tagged node into exact built-in values (or :class:`Opaque` markers)."""
    count = [0]

    def dec(n: Any, depth: int) -> Any:
        count[0] += 1
        if count[0] > max_nodes:
            raise ValueError_("encoded value has too many nodes")
        if depth > max_depth:
            raise ValueError_("encoded value is nested too deeply")
        if type(n) is not dict or type(n.get("t")) is not str:
            raise ValueError_("encoded value node must be an object with a string tag 't'")
        tag = n["t"]
        allowed = {"none": {"t"}, "bool": {"t", "v"}, "int": {"t", "v"}, "float": {"t", "v"}, "str": {"t", "v"}, "list": {"t", "v"},
                   "tuple": {"t", "v"}, "dict": {"t", "v"}, "opaque": {"t", "type", "why"}}
        if tag not in allowed:
            raise ValueError_(f"unknown value tag {tag!r}")
        if set(n) != allowed[tag]:
            raise ValueError_(f"value node {tag!r} has unexpected fields {sorted(set(n) ^ allowed[tag])}")
        v = n.get("v")
        if tag == "none":
            return None
        if tag == "bool":
            if type(v) is not bool:
                raise ValueError_("bool node needs a boolean")
            return v
        if tag == "int":
            if type(v) is not str or not v or len(v.lstrip("-")) > MAX_INT_DIGITS or not v.lstrip("-").isdigit() or v.startswith("--"):
                raise ValueError_("int node needs a bounded decimal string")
            return int(v)
        if tag == "float":
            if type(v) is not str or len(v) > 40:
                raise ValueError_("float node needs a hex string")
            try:
                return float.fromhex(v)
            except (ValueError, OverflowError):
                raise ValueError_("float node has an invalid hex string") from None
        if tag == "str":
            if type(v) is not str or len(v) > MAX_STR:
                raise ValueError_("str node needs a bounded string")
            return v
        if tag in ("list", "tuple"):
            if type(v) is not list:
                raise ValueError_(f"{tag} node needs an array")
            items = [dec(x, depth + 1) for x in v]
            return items if tag == "list" else tuple(items)
        if tag == "dict":
            if type(v) is not list:
                raise ValueError_("dict node needs an array of pairs")
            out: dict[str, Any] = {}
            for pair in v:
                if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str:
                    raise ValueError_("dict entries must be [string, node] pairs")
                out[pair[0]] = dec(pair[1], depth + 1)
            return out
        typ, why = n.get("type"), n.get("why")
        if type(typ) is not str or type(why) is not str:
            raise ValueError_("opaque node needs string 'type' and 'why'")
        return Opaque(typ[:200], why[:200])

    return dec(node, 0)


def to_plain(value: Any) -> Any:
    """A JSON-friendly rendering for reports (lossy for NaN/inf, which become strings)."""
    if isinstance(value, Opaque):
        return f"<opaque {value.type_name}: {value.why}>"
    if type(value) is float and not math.isfinite(value):
        return repr(value)
    if type(value) in (list, tuple):
        return [to_plain(v) for v in value]
    if type(value) is dict:
        return {k: to_plain(v) for k, v in value.items()}
    return value


def contains_opaque(value: Any) -> bool:
    if isinstance(value, Opaque):
        return True
    if type(value) in (list, tuple):
        return any(contains_opaque(v) for v in value)
    if type(value) is dict:
        return any(contains_opaque(v) for v in value.values())
    return False


__all__ = ["encode", "decode", "Opaque", "ValueError_", "to_plain", "contains_opaque"]
