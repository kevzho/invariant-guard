"""Concrete evaluator for the typed IR.

Used to re-validate solver witnesses (docs/05: "For exact modeled SAT, check the
witness against the typed IR evaluator"). It evaluates supported built-in operations
with the host interpreter's own int/float/bool semantics, which are the semantics the
IR claims to model. It never imports or executes repository code; unknown effects
(calls, attributes, indexes, unsupported constructs) stop evaluation as STUCK.

Loops (I-132) run for real: a latch's ``back_edge`` jump returns to the header, whose
``LOOP_PHI`` values are chosen by predecessor like any PHI, and ``ITER``/``NEXT``/``ELEMENT``
iterate the concrete built-in iterable (``range``, list, tuple, str). Iterating anything
else is STUCK. The step limit bounds long loops (STUCK, never a verdict).

A generator function's body (I-138) runs as consumed by ``next()`` until exhaustion: each
yield point evaluates to None, and evaluation ends at the body's return or first exception.
"""

from __future__ import annotations

import copy
import operator
import re
from dataclasses import dataclass, field
from typing import Any

from .model import DIVISION_OPS, Effect, Function, Op

_CMP = {
    "EQ": operator.eq,
    "NE": operator.ne,
    "LT": operator.lt,
    "LE": operator.le,
    "GT": operator.gt,
    "GE": operator.ge,
}

_ARITH = {
    Op.ADD: operator.add,
    Op.SUB: operator.sub,
    Op.MUL: operator.mul,
    Op.DIV: operator.truediv,
    Op.FLOORDIV: operator.floordiv,
    Op.MOD: operator.mod,
}

# Augmented assignment (``x op= y``) uses the in-place protocol: for a list, ``+=`` and
# ``*=`` mutate the object that every alias shares (list.__iadd__/__imul__), while ints,
# floats, strs and tuples rebind. The ``operator.i*`` functions are exactly ``x op= y``.
# Evaluating ``xs *= 0`` as ``xs * 0`` would leave aliases holding the old list and
# validate witnesses CPython never fails on (audit A2-01).
_IARITH = {
    Op.ADD: operator.iadd,
    Op.SUB: operator.isub,
    Op.MUL: operator.imul,
    Op.DIV: operator.itruediv,
    Op.FLOORDIV: operator.ifloordiv,
    Op.MOD: operator.imod,
}


class _Unmodeled:
    def __repr__(self) -> str:
        return "<unmodeled>"

    def __copy__(self) -> "_Unmodeled":
        return self  # a singleton sentinel: identity checks must survive copying

    def __deepcopy__(self, memo: dict) -> "_Unmodeled":
        return self


UNMODELED = _Unmodeled()


class _Unbound:
    def __repr__(self) -> str:
        return "<unbound>"


# Value of a local that is unbound on the path taken. A PHI may carry it (a join is not a
# read); reading it raises UnboundLocalError, as CPython does at the load (audit A-03).
UNBOUND = _Unbound()

_NONE_ATTRS = frozenset(dir(None))

# Largest product (in bits) the evaluator computes; larger multiplications are STUCK.
MAX_INT_BITS = 1 << 16
# Largest sequence a repetition may build in the evaluator; larger ones are STUCK.
MAX_SEQ_LEN = 1 << 16


def _plain(x: Any) -> bool:
    """True for a built-in value with no unmodeled or unbound part (formatting it runs no user code)."""
    if x is None or isinstance(x, (bool, int, float, str)):
        return True
    return isinstance(x, (list, tuple)) and all(_plain(e) for e in x)


# Width and precision fields of a printf-style conversion (``%-*.3d``, ``%(k)10s``).
_FMT_FIELDS = re.compile(r"%(?:\([^)]*\))?[#0\- +]*(\*|\d+)?(?:\.(\*|\d+))?")


def _format_too_wide(fmt: str, args: Any) -> bool:
    """True when ``fmt % args`` may build a string longer than MAX_SEQ_LEN through a width or
    precision (a literal field, or a ``*`` taken from an argument); conservative."""
    star = False
    for m in _FMT_FIELDS.finditer(fmt):
        for g in m.groups():
            if g == "*":
                star = True
            elif g is not None and (len(g) > 6 or int(g) > MAX_SEQ_LEN):
                return True
    if star:
        vals = args if isinstance(args, tuple) else (args,)
        return any(isinstance(v, int) and abs(v) > MAX_SEQ_LEN for v in vals)
    return False


def _bits(x: Any) -> int:
    return x.bit_length() if isinstance(x, int) else 0


def _unbound(out: "EvalOutcome", env: dict[str, Any], ins: Any) -> "EvalOutcome":
    out.kind, out.detail = "RAISED_OTHER", f"UnboundLocalError at {ins.result or ins.span.start_line} (local read on a path where it is unbound)"
    out.values = env
    return out


@dataclass
class EvalOutcome:
    # REACHED_FAILURE: an obligation operation failed at ``instr_result`` (``failure``:
    #   ZeroDivisionError, IndexError, or AttributeError on a None receiver).
    # RETURNED / RAISED: the function finished without such a failure.
    # RAISED_OTHER: another built-in exception (e.g. OverflowError) occurred.
    # STUCK: an unknown effect or unmodeled value was needed.
    kind: str
    instr_result: str | None = None
    detail: str = ""
    trace: list[tuple[str, str, bool]] = field(default_factory=list)  # (block, cond value id, taken)
    blocks: list[str] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)
    return_value: Any = None
    failure: str | None = None
    stuck_attrs: dict[str, Any] = field(default_factory=dict)  # attrs of the instruction evaluation stopped at


def evaluate(fn: Function, args: dict[str, Any], *, assertions_enabled: bool = True, max_steps: int = 100_000) -> EvalOutcome:
    env: dict[str, Any] = {}
    out = EvalOutcome(kind="STUCK")
    bid = fn.entry_id
    prev: str | None = None
    steps = 0
    while True:
        block = fn.blocks[bid]
        out.blocks.append(bid)
        for ins in block.instructions:
            steps += 1
            if steps > max_steps:
                out.kind, out.detail = "STUCK", "step limit"
                return out
            op = ins.op
            if op is Op.PARAM:
                if ins.attrs.get("runtime_mode"):
                    env[ins.result] = assertions_enabled
                else:
                    # Copy: evaluation may mutate a list argument in place (``xs += ...``);
                    # the caller's witness must keep the input value it reports.
                    env[ins.result] = copy.deepcopy(args.get(ins.attrs["name"], UNMODELED))
            elif op is Op.CONST:
                env[ins.result] = ins.attrs["value"]
            elif op is Op.ASSIGN:
                val = env[ins.operands[0]]
                if val is UNBOUND and ins.attrs.get("checked_read"):
                    return _unbound(out, env, ins)
                env[ins.result] = val
            elif op is Op.LOOP_HEAD:
                pass  # marker for static analyses; effects happen at their own instructions
            elif op is Op.ITER:
                vals = [env[o] for o in ins.operands]
                if any(x is UNBOUND for x in vals):
                    return _unbound(out, env, ins)
                kind = ins.attrs.get("iter_kind")
                if kind == "range" and all(isinstance(x, int) for x in vals):
                    try:
                        env[ins.result] = iter(range(*vals))
                    except ValueError as exc:  # range() arg 3 must not be zero
                        out.kind, out.detail = "RAISED_OTHER", f"ValueError at {ins.result}: {exc}"
                        out.values = env
                        return out
                elif kind in ("list", "tuple", "str") and isinstance(vals[0], {"list": list, "tuple": tuple, "str": str}[kind]):
                    env[ins.result] = iter(vals[0])  # a live iterator, as in CPython (a list may grow while iterated)
                else:
                    out.kind, out.detail = "STUCK", f"iterating an unmodeled value at {ins.result}"
                    out.values = env
                    return out
            elif op is Op.NEXT:
                it = env[ins.operands[0]]
                try:
                    env[ins.result + "#element"] = next(it)
                    env[ins.result] = True
                except StopIteration:
                    env[ins.result] = False
            elif op is Op.ELEMENT:
                env[ins.result] = env[ins.operands[0] + "#element"]
            elif op is Op.UNKNOWN_EFFECT and ins.attrs.get("exception_edge"):
                # Execution enters the try body; an exception happens only where an instruction
                # raises, and evaluation stops there (exceptional paths are never validated).
                env[ins.result] = False
            elif op is Op.UNKNOWN_EFFECT and ins.attrs.get("yield_point"):
                # The modeled consumer calls next() until exhaustion (I-138): it runs no code
                # between resumptions and sends None, so ``yield v`` evaluates to None. ``yield
                # from`` a concrete list/tuple/str hands out its items (no code runs) and
                # evaluates to None; delegating to anything else may run user code.
                val = env[ins.operands[0]]
                if val is UNBOUND:
                    return _unbound(out, env, ins)
                if ins.attrs["yield_point"] == "yield_from" and not (
                        ins.attrs.get("iter_kind") in ("list", "tuple", "str") and type(val) in (list, tuple, str)):
                    out.kind, out.detail = "STUCK", f"`yield from` an unmodeled iterable at {ins.result}"
                    out.values = env
                    return out
                env[ins.result] = None
            elif op is Op.CALL and ins.attrs.get("generator_call") and ins.effect is Effect.NONE:
                env[ins.result] = UNMODELED  # a generator object: creating it runs none of its body
            elif op is Op.LOOP_PHI and ins.attrs.get("handler"):
                out.kind, out.detail = "STUCK", "exceptional control flow is not evaluated"
                out.values = env
                return out
            elif op in (Op.PHI, Op.LOOP_PHI):
                chosen = None
                matched = False
                for pred, val in ins.attrs["incoming"]:
                    if pred == prev:
                        chosen, matched = val, True
                        break
                if not matched:
                    out.kind, out.detail = "STUCK", f"PHI {ins.result} has no incoming value for predecessor {prev}"
                    out.values = env
                    return out
                # A join does not read the variable: an unbound incoming stays UNBOUND.
                env[ins.result] = UNBOUND if chosen is None else env[chosen]
            elif op in _ARITH:
                a, b = env[ins.operands[0]], env[ins.operands[1]]
                if a is UNBOUND or b is UNBOUND:
                    return _unbound(out, env, ins)
                if a is UNMODELED or b is UNMODELED:
                    out.kind, out.detail = "STUCK", f"unmodeled operand at {ins.result}"
                    out.values = env
                    return out
                if ins.flavor == "python.str_format":
                    # Evaluator values are built-in objects only (user objects are UNMODELED),
                    # so formatting them runs no user code and matches CPython exactly.
                    if not isinstance(a, str) or not _plain(b):
                        out.kind, out.detail = "STUCK", f"unmodeled formatting argument at {ins.result}"
                        out.values = env
                        return out
                    if _format_too_wide(a, b):
                        # Like sequence repetition: never allocate an unbounded string.
                        out.kind, out.detail = "STUCK", f"formatting width or precision too large to evaluate at {ins.result}"
                        out.values = env
                        return out
                    try:
                        env[ins.result] = a % b
                    except (TypeError, ValueError, OverflowError) as exc:
                        out.kind, out.detail = "RAISED_OTHER", f"{type(exc).__name__} at {ins.result}"
                        out.values = env
                        return out
                    continue
                if op is Op.MUL and ins.flavor == "python.sequence_repeat":
                    seq, cnt = (a, b) if isinstance(a, (list, tuple)) else (b, a)
                    if not isinstance(seq, (list, tuple)) or not isinstance(cnt, int) or len(seq) * max(cnt, 0) > MAX_SEQ_LEN:
                        out.kind, out.detail = "STUCK", f"sequence repetition too large or unmodeled at {ins.result}"
                        out.values = env
                        return out
                elif op is Op.MUL and _bits(a) + _bits(b) > MAX_INT_BITS:
                    # Avoid unbounded time/memory on e.g. repeated squaring (audit A-02).
                    out.kind, out.detail = "STUCK", f"integer operands too large to evaluate at {ins.result}"
                    out.values = env
                    return out
                try:
                    env[ins.result] = (_IARITH if ins.attrs.get("augmented") else _ARITH)[op](a, b)
                except ZeroDivisionError:
                    if op in DIVISION_OPS:
                        out.kind, out.instr_result, out.failure = "REACHED_FAILURE", ins.result, "ZeroDivisionError"
                        out.values = env
                        return out
                    raise
                except (OverflowError, TypeError) as exc:
                    out.kind, out.detail = "RAISED_OTHER", f"{type(exc).__name__} at {ins.result}"
                    out.values = env
                    return out
            elif op is Op.COMPARE:
                a, b = env[ins.operands[0]], env[ins.operands[1]]
                if a is UNBOUND or b is UNBOUND:
                    return _unbound(out, env, ins)
                if a is UNMODELED or b is UNMODELED:
                    out.kind, out.detail = "STUCK", f"unmodeled operand at {ins.result}"
                    out.values = env
                    return out
                try:
                    env[ins.result] = bool(_CMP[ins.attrs["cmp"]](a, b))
                except TypeError as exc:
                    out.kind, out.detail = "RAISED_OTHER", f"TypeError at {ins.result}: {exc}"
                    out.values = env
                    return out
            elif op is Op.IS_NONE:
                a = env[ins.operands[0]]
                if a is UNBOUND:
                    return _unbound(out, env, ins)
                if a is UNMODELED:
                    out.kind, out.detail = "STUCK", f"unmodeled operand at {ins.result}"
                    out.values = env
                    return out
                env[ins.result] = a is None
            elif op is Op.BUILD_SEQ:
                elems = [env[o] for o in ins.operands]
                if any(e is UNBOUND for e in elems):
                    return _unbound(out, env, ins)
                env[ins.result] = list(elems) if ins.type is not None and ins.type.kind.value == "list" else tuple(elems)
            elif op is Op.LEN and isinstance(env.get(ins.operands[0]), (list, tuple)):
                env[ins.result] = len(env[ins.operands[0]])
            elif op is Op.INDEX and ins.flavor == "python.sequence_index":
                seq, idx = env[ins.operands[0]], env[ins.operands[1]]
                if seq is UNBOUND or idx is UNBOUND:
                    return _unbound(out, env, ins)
                if not isinstance(seq, (list, tuple)) or not isinstance(idx, int):
                    out.kind, out.detail = "STUCK", f"unmodeled sequence or index at {ins.result}"
                    out.values = env
                    return out
                try:
                    env[ins.result] = seq[idx]
                except IndexError:
                    out.kind, out.instr_result, out.failure = "REACHED_FAILURE", ins.result, "IndexError"
                    out.values = env
                    return out
            elif op is Op.ATTRIBUTE and ins.attrs.get("obligation_property"):
                recv = env[ins.operands[0]]
                if recv is UNBOUND:
                    return _unbound(out, env, ins)
                if recv is None:
                    # NoneType has a fixed attribute set; any other attribute raises.
                    out.kind, out.instr_result, out.failure = "REACHED_FAILURE", ins.result, "AttributeError"
                    out.values = env
                    return out
                out.kind, out.detail = "STUCK", f"attribute .{ins.attrs.get('attribute')} of a non-None value is not modeled"
                out.values = env
                return out
            elif op is Op.ATTRIBUTE and env.get(ins.operands[0]) is None and ins.attrs.get("attribute") not in _NONE_ATTRS:
                out.kind, out.detail = "RAISED_OTHER", f"AttributeError at {ins.result} (attribute of None)"
                out.values = env
                return out
            else:  # CALL, ATTRIBUTE, INDEX, LEN, UNKNOWN_EFFECT
                out.stuck_attrs = dict(ins.attrs, op=op.value, receiver_is_none=env.get(ins.operands[0]) is None if ins.operands else None)
                out.kind, out.detail = "STUCK", f"{op.value} at {ins.result or ins.span.start_line}: {ins.attrs.get('reason', 'unknown effect')}"
                out.values = env
                return out
        term = block.terminator
        if term is None:
            out.kind, out.detail = "STUCK", f"block {bid} has no terminator"
            return out
        if term.op is Op.RETURN:
            out.kind = "RETURNED"
            out.return_value = env.get(term.operands[0], UNMODELED) if term.operands else None
            out.values = env
            return out
        if term.op is Op.RAISE:
            out.kind, out.detail = "RAISED", term.attrs.get("exception", "exception")
            out.values = env
            return out
        prev = bid
        if term.op is Op.JUMP:
            bid = term.attrs["target"]
            continue
        cond = env[term.operands[0]]
        if cond is UNMODELED or not isinstance(cond, bool):
            out.kind, out.detail = "STUCK", "branch on unmodeled value"
            out.values = env
            return out
        out.trace.append((bid, term.operands[0], cond))
        bid = term.attrs["true_target"] if cond else term.attrs["false_target"]
