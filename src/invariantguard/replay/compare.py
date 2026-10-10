"""Compare base and patch observations of the same probe (observable equivalence).

A probe runs twice on the base and once on the patch. Its class is decided on the
*observable view* of each observation:

* private instance state (``_name`` attributes) is dropped;
* the concrete iterator type is dropped (``map`` vs ``generator`` vs ``list_iterator``),
  only the produced items and a lazily raised exception count;
* an object that exposes a protocol (container items, or iteration) is compared through
  that protocol only, not its attributes (an internal representation such as tombstones
  in a list does not count while ``list(x)`` agrees);
* timing fields are dropped.

Classes. Differences: ``different_return``, ``different_mutation``, ``new_exception``,
``removed_exception``, ``different_exception``, ``exception_message`` (same exception
type, different message), ``timeout`` (exactly one side timed out). Not differences:
``same``, ``internal_state_only`` (raw observations differ only outside the observable
view), ``nondeterministic`` (the two base runs disagree), ``unconfirmed`` (the repeat base
run timed out), ``timeout_both``, and the not-executed classes ``not_executable`` (the
input could not be built on either side), ``no_baseline`` (the target does not import or
resolve on the base), ``unknown`` (any other non-execution).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .harvest import Harvested
from .run import Binding, Construct, Expr, Value

DIFF_CLASSES = ("different_return", "different_mutation", "new_exception", "removed_exception", "different_exception", "exception_message", "timeout")
NOT_EXECUTED = ("input_error", "import_error", "resolve_error", "harness_error", "crashed", "invalid_observation", "batch_error",
                "not_allowed", "runner_unavailable", None)


def observable(n: Any) -> Any:
    """The observable view of an encoded value or observation fragment."""
    if isinstance(n, list):
        return [observable(x) for x in n]
    if not isinstance(n, dict):
        return n
    t = n.get("t")
    if t == "iter":
        return {k: observable(v) for k, v in n.items() if k != "type"}
    if t == "object" and ("items" in n or "iter" in n):
        keep = ("t", "type", "items", "iter", "iter_truncated", "iter_raised", "iter_error")
        return {k: observable(n[k]) for k in keep if k in n}
    out = {}
    for k, v in n.items():
        if k == "state" and isinstance(v, list):
            out[k] = [[a, observable(b)] for a, b in v if not str(a).startswith("_")]
        elif k == "elapsed_ms":
            continue
        else:
            out[k] = observable(v)
    return out


def canon(o: dict[str, Any] | None, view: bool = True) -> str:
    if o is None:
        return "null"
    keep = {k: o.get(k) for k in ("status", "result", "mutated", "receiver_after", "steps", "detail")}
    return json.dumps(observable(keep) if view else keep, sort_keys=True)


def classify(b1: dict[str, Any] | None, b2: dict[str, Any] | None, p: dict[str, Any] | None) -> str:
    sb, sp = (b1 or {}).get("status"), (p or {}).get("status")
    if sb in NOT_EXECUTED or sp in NOT_EXECUTED:
        if sb == "input_error" and sp == "input_error":
            return "not_executable"
        if sb in ("resolve_error", "import_error") and sp == "ok":
            return "no_baseline"
        return "unknown"
    if sb == "timeout" and sp == "timeout":
        return "timeout_both"
    if b2 is not None and b2.get("status") == "timeout" and sb != "timeout":
        return "unconfirmed"
    if b2 is not None and canon(b1) != canon(b2):
        return "nondeterministic"
    if sb == "timeout" or sp == "timeout":
        return "timeout"
    assert b1 is not None and p is not None
    if canon(b1) == canon(p):
        return "same" if canon(b1, False) == canon(p, False) else "internal_state_only"
    rb, rp = observable(b1["result"]), observable(p["result"])
    if rb["kind"] == "returned" and rp["kind"] == "returned":
        return "different_return" if json.dumps(rb["value"], sort_keys=True) != json.dumps(rp["value"], sort_keys=True) else "different_mutation"
    if rb["kind"] == "returned":
        return "new_exception"
    if rp["kind"] == "returned":
        return "removed_exception"
    if rb["exception"]["type"] != rp["exception"]["type"]:
        return "different_exception"
    if rb["exception"]["message"] != rp["exception"]["message"]:
        return "exception_message"
    return "different_mutation"


def confirmed(b1: dict[str, Any], p: dict[str, Any], b_again: dict[str, Any] | None, p_again: list[dict[str, Any] | None]) -> bool:
    """A difference is reported only if a fresh base run reproduces the base and fresh patch runs reproduce the patch."""
    return b_again is not None and canon(b_again) == canon(b1) and len(p_again) > 0 and all(x is not None and canon(x) == canon(p) for x in p_again)


# -- rendering ------------------------------------------------------------------------------------
def render(n: Any, limit: int = 12) -> str:
    if not isinstance(n, dict):
        return repr(n)
    t, v = n.get("t"), n.get("v")
    if t == "none":
        return "None"
    if t in ("bool", "int"):
        return str(v)
    if t == "float":
        return repr(float.fromhex(v))
    if t == "str":
        return repr(v)
    if t in ("list", "tuple", "set", "frozenset"):
        inner = ", ".join(render(x) for x in v[:limit]) + (", ..." if len(v) > limit else "")
        return {"list": f"[{inner}]", "tuple": f"({inner}{',' if len(v) == 1 else ''})", "set": f"{{{inner}}}" if v else "set()",
                "frozenset": f"frozenset({{{inner}}})"}[t]
    if t in ("dict", "xdict"):
        return "{" + ", ".join(f"{repr(k) if t == 'dict' else render(k)}: {render(x)}" for k, x in v[:limit]) + "}"
    if t == "iter":
        s = "<iterator [" + ", ".join(render(x) for x in v[:limit]) + (", ..." if n.get("truncated") or len(v) > limit else "") + "]"
        if n.get("raised"):
            s += f", then raised {n['raised']['type']}: {n['raised']['message'][:80]}"
        return s + ">"
    if t == "object":
        bits = []
        if "items" in n:
            bits.append("items=" + render({"t": "list", "v": [x[1] if isinstance(x, list) else x for x in n["items"]]}))
        if "iter" in n:
            bits.append("iter=[" + ", ".join(render(x) for x in n["iter"][:8]) + "]")
        if "state" in n:
            bits.append(", ".join(f"{k}={render(x)}" for k, x in n["state"][:6]))
        return f"<{n['type']} {'; '.join(bits)}>"
    if t == "dt":
        return f"{n['type']}({n.get('v')})"
    if t in ("bytes", "bytearray"):
        return f"{t}.fromhex({v!r})"
    if t == "exception":
        return f"{n['type']}{render(n['args'])}"
    if t in ("callable", "class"):
        return f"<{t} {n.get('name')}>"
    if t == "range":
        return f"range{tuple(v)}"
    if t == "digest":
        return f"<{n.get('type')} of {n.get('bytes')} bytes, sha256 {str(n.get('sha256'))[:12]}>"
    if t == "opaque":
        return f"<opaque {n.get('type')}: {n.get('why')}>"
    return json.dumps(n)[:120]


def render_outcome(o: dict[str, Any] | None) -> str:
    if o is None:
        return "no observation"
    if o.get("status") != "ok":
        d = o.get("detail") or {}
        return f"{o.get('status')}" + (f" ({d.get('type', '')}: {str(d.get('message', ''))[:100]})" if isinstance(d, dict) and d else "")
    r = o["result"]
    s = f"returned {render(r['value'])}" if r["kind"] == "returned" else f"raised {r['exception']['type']}: {r['exception']['message'][:160]}"
    if o.get("mutated"):
        s += "; mutated " + ", ".join(f"{k} -> {render(v)}" for k, v in o["mutated"].items())
    return s


def _arg(a: Any) -> str:
    if isinstance(a, Value):
        return render(a.node)
    if isinstance(a, Expr):
        return a.text
    return "?"


def render_call(h: Harvested, qualname: str) -> str:
    """A replayable rendering: bindings, earlier steps, then the call."""
    parts = [_arg(a) for a in h.args] + [f"{k}={_arg(v)}" for k, v in h.kwargs]
    name = qualname.split(".")[-1]
    pre: list[str] = []
    for inp in [*h.args, *(v for _, v in h.kwargs), h.receiver]:
        if isinstance(inp, Expr):
            for b in inp.bindings:
                line = f"{b.name} = {b.text}"
                if line not in pre:
                    pre.append(line)
    if isinstance(h.receiver, Construct):
        recv = qualname.split(".")[0] + "(" + ", ".join([_arg(a) for a in h.receiver.args] + [f"{k}={_arg(v)}" for k, v in h.receiver.kwargs]) + ")"
    elif h.receiver is not None:
        recv = _arg(h.receiver)
    else:
        recv = None
    for s in h.steps:
        pre.append(f"{recv}.{s.method}(" + ", ".join([_arg(a) for a in s.args] + [f"{k}={_arg(v)}" for k, v in s.kwargs]) + ")")
    call = f"{recv}.{name}(" + ", ".join(parts) + ")" if recv is not None else f"{qualname}(" + ", ".join(parts) + ")"
    return "; ".join(pre + [call])


# -- clustering -------------------------------------------------------------------------------------
@dataclass
class Example:
    symbol: str  # changed symbol id
    target: str  # replayed target symbol id (the symbol itself, its class, or a caller)
    relation: str  # direct | constructor | caller
    cls: str
    origin: str
    call: str
    base: str
    patch: str
    acknowledged: bool  # the input comes from a test or doctest that only the patch contains


@dataclass
class Cluster:
    symbol: str
    target: str
    relation: str
    cls: str
    examples: list[Example] = field(default_factory=list)

    @property
    def witness(self) -> Example:
        return min(self.examples, key=lambda e: (len(e.call), e.call))

    @property
    def acknowledged(self) -> bool:
        return any(e.acknowledged for e in self.examples)


def cluster(examples: list[Example]) -> list[Cluster]:
    out: dict[tuple[str, str, str], Cluster] = {}
    for e in examples:
        key = (e.symbol, e.target, e.cls)
        out.setdefault(key, Cluster(e.symbol, e.target, e.relation, e.cls)).examples.append(e)
    return sorted(out.values(), key=lambda c: (c.symbol, c.target, c.cls))


__all__ = ["DIFF_CLASSES", "observable", "canon", "classify", "confirmed", "render", "render_outcome", "render_call", "Example", "Cluster", "cluster", "Binding"]
