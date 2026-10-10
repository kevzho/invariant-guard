"""Host side of the ig-replay-1 value encoding.

Inputs are encoded on the host from exact built-in values only (no target types), with
the tags of ``invariantguard.contracts.values`` plus replay extensions for ``xdict`` (any
built-in keys), ``set``, ``frozenset``, ``bytes``, ``bytearray``, ``complex`` and
``range``. ``execution/replay_harness.py`` carries its own copy (it runs standalone);
``tests/integration/test_replay_harness.py`` checks that the two agree.

Observations come from untrusted target code. ``check_node`` validates their shape and
size before anything else looks at them; it never reconstructs target objects.
"""

from __future__ import annotations

import json
from typing import Any

MAX_DEPTH = 24
MAX_NODES = 20000
MAX_STR = 8192
MAX_INT_BITS = 13000

INPUT_TAGS = frozenset({"none", "bool", "int", "float", "str", "list", "tuple", "dict", "xdict", "set", "frozenset", "bytes", "bytearray",
                        "complex", "range"})
OBSERVATION_TAGS = INPUT_TAGS | {"slice", "dt", "num", "exception", "class", "callable", "iter", "object", "opaque", "digest"}


class EncodeError(ValueError):
    """A host value that cannot be sent as a replay input."""


def _sort_key(node: dict[str, Any]) -> str:
    return json.dumps(node, sort_keys=True, separators=(",", ":"))


def encode_input(value: Any) -> dict[str, Any]:
    """Encode an exact built-in value; anything else is refused (never sent opaquely)."""
    count = [0]

    def enc(x: Any, depth: int, active: set[int]) -> dict[str, Any]:
        count[0] += 1
        if count[0] > MAX_NODES or depth > MAX_DEPTH:
            raise EncodeError("input too large or too deep")
        t = type(x)
        if x is None:
            return {"t": "none"}
        if t is bool:
            return {"t": "bool", "v": x}
        if t is int:
            if x.bit_length() > MAX_INT_BITS:
                raise EncodeError("integer too large")
            return {"t": "int", "v": str(x)}
        if t is float:
            return {"t": "float", "v": x.hex()}
        if t is str:
            if len(x) > MAX_STR:
                raise EncodeError("string too long")
            return {"t": "str", "v": x}
        if t in (bytes, bytearray):
            return {"t": t.__name__, "v": bytes(x).hex()}
        if t is complex:
            return {"t": "complex", "v": [x.real.hex(), x.imag.hex()]}
        if t is range:
            return {"t": "range", "v": [x.start, x.stop, x.step]}
        if t in (list, tuple, dict, set, frozenset):
            if id(x) in active:
                raise EncodeError("cyclic input")
            active.add(id(x))
            try:
                if t in (list, tuple):
                    return {"t": t.__name__, "v": [enc(v, depth + 1, active) for v in x]}
                if t is dict:
                    if all(type(k) is str for k in x):
                        return {"t": "dict", "v": [[k, enc(v, depth + 1, active)] for k, v in x.items()]}
                    return {"t": "xdict", "v": [[enc(k, depth + 1, active), enc(v, depth + 1, active)] for k, v in x.items()]}
                return {"t": t.__name__, "v": sorted((enc(v, depth + 1, active) for v in x), key=_sort_key)}
            finally:
                active.discard(id(x))
        raise EncodeError(f"type {t.__module__}.{t.__qualname__} is not a replay input type")

    return enc(value, 0, set())


def decode_input(n: Any, depth: int = 0) -> Any:
    """Inverse of ``encode_input`` (same rules as the harness's ``decode``)."""
    if depth > MAX_DEPTH or type(n) is not dict:
        raise EncodeError("bad value node")
    t, v = n.get("t"), n.get("v")
    if t == "none":
        return None
    if t == "bool":
        return bool(v)
    if t == "int":
        return int(v)
    if t == "float":
        return float.fromhex(v)
    if t == "str":
        return v
    if t == "list":
        return [decode_input(x, depth + 1) for x in v]
    if t == "tuple":
        return tuple(decode_input(x, depth + 1) for x in v)
    if t == "dict":
        return {k: decode_input(x, depth + 1) for k, x in v}
    if t == "xdict":
        return {decode_input(k, depth + 1): decode_input(x, depth + 1) for k, x in v}
    if t == "set":
        return {decode_input(x, depth + 1) for x in v}
    if t == "frozenset":
        return frozenset(decode_input(x, depth + 1) for x in v)
    if t == "bytes":
        return bytes.fromhex(v)
    if t == "bytearray":
        return bytearray.fromhex(v)
    if t == "complex":
        return complex(float.fromhex(v[0]), float.fromhex(v[1]))
    if t == "range":
        return range(*v)
    raise EncodeError(f"not a decodable input node {t!r}")


def check_node(node: Any, *, max_depth: int = MAX_DEPTH + 8, max_nodes: int = MAX_NODES * 4) -> str | None:
    """Validate an observed (untrusted) value node; returns a problem description or None."""
    count = [0]
    stack: list[tuple[Any, int]] = [(node, 0)]
    while stack:
        n, depth = stack.pop()
        count[0] += 1
        if count[0] > max_nodes:
            return "observation has too many nodes"
        if depth > max_depth:
            return "observation is nested too deeply"
        if type(n) is not dict or n.get("t") not in OBSERVATION_TAGS:
            return f"unknown value node {str(n)[:80]!r}"
        for key in ("v", "items", "iter", "args"):
            sub = n.get(key)
            if isinstance(sub, list):
                for x in sub:
                    if isinstance(x, list):  # [key, node] pairs
                        stack.extend((y, depth + 1) for y in x if isinstance(y, dict))
                    elif isinstance(x, dict):
                        stack.append((x, depth + 1))
            elif isinstance(sub, dict):
                stack.append((sub, depth + 1))
        state = n.get("state")
        if isinstance(state, list):
            stack.extend((x[1], depth + 1) for x in state if isinstance(x, list) and len(x) == 2)
    return None
