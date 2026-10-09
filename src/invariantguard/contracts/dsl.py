"""Typed property DSL v1: validation, canonicalization and a pure evaluator (docs/06).

Expressions are JSON objects with a ``kind``. Only these kinds exist (anything else,
including any attempt to name code, is rejected):

==================  ==========================================  ==================
kind                fields                                      type
==================  ==========================================  ==================
arg                 name (a parameter of the target)            profile type
result              -                                           dyn (runtime-typed)
before_arg          name                                        profile type
after_arg           name                                        dyn
literal             value (int/float, finite, |v| <= 2^53; bool; null)
eq                  left, right                                 bool
lt, le              left, right (numbers)                       bool
gt                  left, right; canonicalized to lt(right, left)
and, or             args (1..32 booleans)                       bool (Kleene logic)
not                 arg                                         bool
len                 value (list)                                num (int)
sum                 value (list of numbers)                     num
all_finite          value (number or list of numbers)           bool
approx_eq           left, right, atol, rtol, require_finite     bool
all_between         value (list of numbers), lower, upper       bool
unchanged_argument  name                                        bool (relation)
repeatability       repeats (2..50), seed, comparison="exact"   bool (relation)
==================  ==========================================  ==================

Budgets: at most 64 nodes and depth 16 per expression, 32 operands per ``and``/``or``,
names of at most 64 characters, and an evaluation budget of 200,000 operations.

Semantics (evaluated on decoded *built-in* values only; no repository code is ever called):

* numbers are exact built-in ``int``/``float``; ``bool`` is not a number;
* ``eq`` is structural equality with exact categories (``True`` is not ``1``; a tuple
  is not a list); numbers compare by Python value (``1 == 1.0``);
* ``lt``/``le`` follow Python on numbers (any comparison with NaN is false);
* ``sum`` is CPython 3.13's built-in ``sum`` (start 0; compensated float summation);
* ``approx_eq``: with ``require_finite`` a NaN or infinite operand makes it false; NaN
  is never approximately equal; infinities are equal only to the same infinity;
  otherwise ``|a - b| <= max(rtol * max(|a|, |b|), atol)``;
* ``all_between`` is inclusive; NaN elements make it false;
* ``unchanged_argument(x)``: the exact typed encoding of ``x`` after the call equals the
  one before it (snapshots taken by the harness);
* ``repeatability``: every repeated call (fresh copies of the same input, same process)
  produced an identical exactly-encoded result.

A runtime type mismatch on a ``dyn`` value (for example ``sum`` of a result that is
``None``) makes the predicate FALSE: the observed behavior does not have the shape the
contract requires. A value the harness could not encode (opaque object, cycle) makes the
predicate UNKNOWN. A call that raised violates every contract (the precondition admitted
the input, so a normal return is required).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .profiles import is_num
from .values import Opaque, contains_opaque

DSL_VERSION = "dsl-1"
MAX_NODES = 64
MAX_DEPTH = 16
MAX_ARGS = 32
MAX_NAME = 64
MAX_LITERAL = 2 ** 53
MAX_REPEATS = 50
MAX_EVAL_OPS = 200_000

_FIELDS: dict[str, frozenset[str]] = {
    "arg": frozenset({"kind", "name"}),
    "result": frozenset({"kind"}),
    "before_arg": frozenset({"kind", "name"}),
    "after_arg": frozenset({"kind", "name"}),
    "literal": frozenset({"kind", "value"}),
    "eq": frozenset({"kind", "left", "right"}),
    "lt": frozenset({"kind", "left", "right"}),
    "le": frozenset({"kind", "left", "right"}),
    "gt": frozenset({"kind", "left", "right"}),
    "and": frozenset({"kind", "args"}),
    "or": frozenset({"kind", "args"}),
    "not": frozenset({"kind", "arg"}),
    "len": frozenset({"kind", "value"}),
    "sum": frozenset({"kind", "value"}),
    "all_finite": frozenset({"kind", "value"}),
    "approx_eq": frozenset({"kind", "left", "right", "atol", "rtol", "require_finite"}),
    "all_between": frozenset({"kind", "value", "lower", "upper"}),
    "unchanged_argument": frozenset({"kind", "name"}),
    "repeatability": frozenset({"kind", "repeats", "seed", "comparison"}),
}
NODE_KINDS = frozenset(_FIELDS)
PREDICATE_ONLY = frozenset({"result", "after_arg", "before_arg", "unchanged_argument", "repeatability"})
BEHAVIOR_NODES = frozenset({"result", "after_arg", "unchanged_argument", "repeatability"})


class Rejection(Exception):
    """A contract or expression that is not a valid DSL v1 term."""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(f"{code} at {path or '<root>'}: {message}")
        self.code = code
        self.path = path
        self.message = message

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass
class TypeEnv:
    params: dict[str, str]  # parameter -> static type ("num_list" | "num")
    role: str  # precondition | predicate
    nodes: int = 0
    repeat: dict[str, Any] | None = None
    references: set[str] = field(default_factory=set)


def _finite_number(v: Any) -> bool:
    return (type(v) is int and abs(v) <= MAX_LITERAL) or (type(v) is float and math.isfinite(v) and abs(v) <= MAX_LITERAL)


def _depth(node: Any) -> int:
    """Iterative depth measure, so hostile nesting cannot exhaust the Python stack."""
    best, stack = 0, [(node, 1)]
    while stack:
        n, d = stack.pop()
        if d > best:
            best = d
            if best > MAX_DEPTH + 1:
                return best
        if isinstance(n, dict):
            stack.extend((v, d + 1) for v in n.values() if isinstance(v, (dict, list)))
        elif isinstance(n, list):
            stack.extend((v, d) for v in n if isinstance(v, (dict, list)))
    return best


def _name(node: dict[str, Any], path: str, env: TypeEnv) -> str:
    name = node.get("name")
    if type(name) is not str or not name.isidentifier() or len(name) > MAX_NAME:
        raise Rejection("MALFORMED_NODE", path + "/name", "name must be a Python identifier of at most 64 characters")
    if name not in env.params:
        raise Rejection("UNKNOWN_SYMBOL", path + "/name", f"{name!r} is not a parameter of the target (parameters: {sorted(env.params)})")
    return name


def _compatible_eq(a: str, b: str) -> bool:
    return a == "dyn" or b == "dyn" or a == b


def _check(node: Any, path: str, env: TypeEnv) -> tuple[dict[str, Any], str]:
    env.nodes += 1
    if env.nodes > MAX_NODES:
        raise Rejection("BUDGET_EXCEEDED", path, f"expression has more than {MAX_NODES} nodes")
    if type(node) is not dict:
        raise Rejection("MALFORMED_NODE", path, f"a DSL node must be a JSON object, got {type(node).__name__}")
    kind = node.get("kind")
    if type(kind) is not str or kind not in NODE_KINDS:
        raise Rejection("UNKNOWN_NODE", path, f"unknown node kind {kind!r}; allowed: {sorted(NODE_KINDS)}")
    extra = set(node) - _FIELDS[kind]
    missing = _FIELDS[kind] - set(node)
    if extra:
        raise Rejection("MALFORMED_NODE", path, f"{kind}: unexpected field(s) {sorted(extra)}")
    if missing:
        raise Rejection("MALFORMED_NODE", path, f"{kind}: missing field(s) {sorted(missing)}")
    if env.role == "precondition" and kind in PREDICATE_ONLY:
        raise Rejection("TYPE_ERROR", path, f"{kind} describes behavior and is not allowed in a precondition (it may only constrain inputs)")
    if kind in BEHAVIOR_NODES:
        env.references.add(kind)

    def sub(field_name: str) -> tuple[dict[str, Any], str]:
        return _check(node[field_name], f"{path}/{field_name}", env)

    if kind == "arg":
        n = _name(node, path, env)
        return {"kind": "arg", "name": n}, env.params[n]
    if kind == "before_arg":
        n = _name(node, path, env)
        return {"kind": "before_arg", "name": n}, env.params[n]
    if kind == "after_arg":
        return {"kind": "after_arg", "name": _name(node, path, env)}, "dyn"
    if kind == "result":
        return {"kind": "result"}, "dyn"
    if kind == "literal":
        v = node["value"]
        if v is None:
            return {"kind": "literal", "value": None}, "none"
        if type(v) is bool:
            return {"kind": "literal", "value": v}, "bool"
        if not _finite_number(v):
            raise Rejection("TYPE_ERROR", path + "/value", "a literal must be null, a boolean, or a finite int/float with magnitude <= 2^53")
        return {"kind": "literal", "value": v}, "num"
    if kind in ("eq", "lt", "le", "gt"):
        left, lt_ = sub("left")
        right, rt_ = sub("right")
        if kind == "eq":
            if not _compatible_eq(lt_, rt_):
                raise Rejection("TYPE_ERROR", path, f"eq compares {lt_} with {rt_}")
            return {"kind": "eq", "left": left, "right": right}, "bool"
        for t, side in ((lt_, "left"), (rt_, "right")):
            if t not in ("num", "dyn"):
                raise Rejection("TYPE_ERROR", f"{path}/{side}", f"{kind} needs numbers, got {t}")
        if kind == "gt":  # canonical form: gt(a, b) == lt(b, a)
            return {"kind": "lt", "left": right, "right": left}, "bool"
        return {"kind": kind, "left": left, "right": right}, "bool"
    if kind in ("and", "or"):
        args = node["args"]
        if type(args) is not list or not 1 <= len(args) <= MAX_ARGS:
            raise Rejection("MALFORMED_NODE", path + "/args", f"{kind} needs 1..{MAX_ARGS} operands")
        out = []
        for i, a in enumerate(args):
            c, t = _check(a, f"{path}/args/{i}", env)
            if t not in ("bool", "dyn"):
                raise Rejection("TYPE_ERROR", f"{path}/args/{i}", f"{kind} operands must be boolean, got {t}")
            out.append(c)
        return {"kind": kind, "args": out}, "bool"
    if kind == "not":
        c, t = _check(node["arg"], path + "/arg", env)
        if t not in ("bool", "dyn"):
            raise Rejection("TYPE_ERROR", path + "/arg", f"not needs a boolean, got {t}")
        return {"kind": "not", "arg": c}, "bool"
    if kind in ("len", "sum", "all_finite"):
        c, t = sub("value")
        allowed = {"len": ("num_list", "dyn"), "sum": ("num_list", "dyn"), "all_finite": ("num_list", "num", "dyn")}[kind]
        if t not in allowed:
            raise Rejection("TYPE_ERROR", path + "/value", f"{kind} cannot take {t}")
        return {"kind": kind, "value": c}, ("bool" if kind == "all_finite" else "num")
    if kind == "approx_eq":
        left, lt_ = sub("left")
        right, rt_ = sub("right")
        for t, side in ((lt_, "left"), (rt_, "right")):
            if t not in ("num", "dyn"):
                raise Rejection("TYPE_ERROR", f"{path}/{side}", f"approx_eq needs numbers, got {t}")
        for tol in ("atol", "rtol"):
            v = node[tol]
            if not _finite_number(v) or v < 0:
                raise Rejection("TYPE_ERROR", f"{path}/{tol}", f"{tol} must be a finite non-negative number")
        if type(node["require_finite"]) is not bool:
            raise Rejection("TYPE_ERROR", path + "/require_finite", "require_finite must be a boolean (explicit NaN/infinity policy)")
        return {"kind": "approx_eq", "left": left, "right": right, "atol": node["atol"], "rtol": node["rtol"],
                "require_finite": node["require_finite"]}, "bool"
    if kind == "all_between":
        c, t = sub("value")
        if t not in ("num_list", "dyn"):
            raise Rejection("TYPE_ERROR", path + "/value", f"all_between needs a list of numbers, got {t}")
        lo, hi = node["lower"], node["upper"]
        if not _finite_number(lo) or not _finite_number(hi):
            raise Rejection("TYPE_ERROR", path, "all_between bounds must be finite numbers")
        if lo > hi:
            raise Rejection("CONTRADICTORY_DOMAIN", path, f"all_between lower bound {lo} exceeds upper bound {hi}")
        return {"kind": "all_between", "value": c, "lower": lo, "upper": hi}, "bool"
    if kind == "unchanged_argument":
        return {"kind": "unchanged_argument", "name": _name(node, path, env)}, "bool"
    # repeatability
    reps, seed, comparison = node["repeats"], node["seed"], node["comparison"]
    if type(reps) is not int or not 2 <= reps <= MAX_REPEATS:
        raise Rejection("BUDGET_EXCEEDED" if type(reps) is int and reps > MAX_REPEATS else "TYPE_ERROR", path + "/repeats",
                        f"repeats must be an int in [2, {MAX_REPEATS}]")
    if type(seed) is not int or abs(seed) >= 2 ** 63:
        raise Rejection("TYPE_ERROR", path + "/seed", "seed must be a 64-bit int")
    if comparison != "exact":
        raise Rejection("UNSUPPORTED_NODE", path + "/comparison",
                        "only comparison='exact' is supported in DSL v1 (approximate repeatability needs tolerances the v1 node does not carry)")
    if env.repeat is not None:
        raise Rejection("UNSUPPORTED_NODE", path, "at most one repeatability node per contract")
    env.repeat = {"repeats": reps, "seed": seed, "comparison": comparison}
    return {"kind": "repeatability", "repeats": reps, "seed": seed, "comparison": comparison}, "bool"


def canonicalize(node: Any) -> Any:
    """Structural canonical form (``gt(a, b)`` -> ``lt(b, a)``) without validation.

    For a valid term this equals ``check_expression(...).canonical``; it lets the
    semantic digest be computed identically for files that are indexed before their
    target can be resolved (``guard contracts import``).
    """
    stack_guard = _depth(node)
    if stack_guard > MAX_DEPTH + 1:
        return node
    if isinstance(node, list):
        return [canonicalize(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {k: canonicalize(v) for k, v in node.items()}
    if out.get("kind") == "gt" and "left" in out and "right" in out:
        return {"kind": "lt", "left": out["right"], "right": out["left"]}
    return out


@dataclass
class CheckedExpression:
    canonical: dict[str, Any]
    references: set[str]
    repeat: dict[str, Any] | None
    nodes: int


def check_expression(node: Any, params: dict[str, str], role: str, path: str) -> CheckedExpression:
    if _depth(node) > MAX_DEPTH:
        raise Rejection("BUDGET_EXCEEDED", path, f"expression nesting exceeds depth {MAX_DEPTH}")
    env = TypeEnv(params, role)
    canonical, t = _check(node, path, env)
    if t != "bool":
        raise Rejection("TYPE_ERROR", path, f"a {role} must be boolean, got {t}")
    return CheckedExpression(canonical, env.references, env.repeat, env.nodes)


# -- evaluation ---------------------------------------------------------------------------------
class _Unknown(Exception):
    pass


class _Mismatch(Exception):
    pass


_UNK = object()


@dataclass
class EvalEnv:
    args: dict[str, Any]
    result: Any = None
    has_result: bool = False
    before: dict[str, Any] | None = None  # decoded
    after: dict[str, Any] | None = None  # decoded
    before_enc: dict[str, Any] | None = None  # exact typed encodings (canonical JSON strings)
    after_enc: dict[str, Any] | None = None
    repeat_encodings: list[str] | None = None


@dataclass
class EvalResult:
    status: str  # TRUE | FALSE | UNKNOWN
    reason: str
    trace: list[str] = field(default_factory=list)


def _render(v: Any) -> str:
    if type(v) is list and len(v) > 6:
        return "[" + ", ".join(_render(x) for x in v[:6]) + f", ... ({len(v)} items)]"
    if type(v) is float:
        return repr(v)
    if type(v) is list:
        return "[" + ", ".join(_render(x) for x in v) + "]"
    return repr(v)


def _category(v: Any) -> str:
    if v is None:
        return "none"
    if type(v) is bool:
        return "bool"
    if is_num(v):
        return "num"
    return type(v).__name__


_BUILTIN_CATEGORIES = frozenset({"none", "bool", "num", "str", "list", "tuple", "dict"})


def _strict_eq(a: Any, b: Any) -> bool:
    ca, cb = _category(a), _category(b)
    if ca not in _BUILTIN_CATEGORIES or cb not in _BUILTIN_CATEGORIES:
        # Never fall back to a user-defined __eq__ (decoded values are built-ins only).
        raise _Unknown(f"eq on a non-built-in value ({ca} / {cb})")
    if ca != cb:
        return False
    if ca in ("list", "tuple"):
        return len(a) == len(b) and all(_strict_eq(x, y) for x, y in zip(a, b))
    if ca == "dict":
        return set(a) == set(b) and all(_strict_eq(a[k], b[k]) for k in a)
    return a == b


def _is_finite(x: Any) -> bool:
    return type(x) is int or (type(x) is float and math.isfinite(x))


class _Evaluator:
    def __init__(self, env: EvalEnv) -> None:
        self.env = env
        self.ops = 0
        self.trace: list[str] = []

    def charge(self, n: int = 1) -> None:
        self.ops += n
        if self.ops > MAX_EVAL_OPS:
            raise _Unknown(f"evaluation budget of {MAX_EVAL_OPS} operations exceeded")

    def num(self, v: Any, what: str) -> int | float:
        if isinstance(v, Opaque):
            raise _Unknown(f"{what} is an unsupported value ({v.type_name}: {v.why})")
        if not is_num(v):
            raise _Mismatch(f"{what} is {_category(v)} {_render(v)}, not a number")
        return v

    def numlist(self, v: Any, what: str) -> list[int | float]:
        if isinstance(v, Opaque):
            raise _Unknown(f"{what} is an unsupported value ({v.type_name}: {v.why})")
        if type(v) not in (list, tuple):
            raise _Mismatch(f"{what} is {_category(v)} {_render(v)}, not a list")
        self.charge(len(v))
        for x in v:
            if isinstance(x, Opaque):
                raise _Unknown(f"{what} contains an unsupported value ({x.type_name})")
            if not is_num(x):
                raise _Mismatch(f"{what} contains {_category(x)} {_render(x)}, not a number")
        return list(v)

    def boolean(self, v: Any, what: str) -> bool:
        if v is _UNK:
            return v
        if isinstance(v, Opaque):
            raise _Unknown(f"{what} is an unsupported value")
        if type(v) is not bool:
            raise _Mismatch(f"{what} is {_category(v)} {_render(v)}, not a boolean")
        return v

    def ev(self, n: dict[str, Any]) -> Any:
        self.charge()
        k = n["kind"]
        env = self.env
        if k == "arg":
            return env.args[n["name"]]
        if k == "before_arg":
            if env.before is None:
                raise _Unknown("argument snapshot before the call is unavailable")
            return env.before[n["name"]]
        if k == "after_arg":
            if env.after is None:
                raise _Unknown("argument snapshot after the call is unavailable")
            return env.after[n["name"]]
        if k == "result":
            if not env.has_result:
                raise _Unknown("no result was observed")
            if isinstance(env.result, Opaque):
                raise _Unknown(f"result is an unsupported value ({env.result.type_name}: {env.result.why})")
            return env.result
        if k == "literal":
            return n["value"]
        if k == "eq":
            a, b = self.ev(n["left"]), self.ev(n["right"])
            if contains_opaque(a) or contains_opaque(b):
                raise _Unknown("eq on an unsupported value")
            self.charge(len(a) if type(a) in (list, tuple) else 1)
            r = _strict_eq(a, b)
            self.trace.append(f"eq({_render(a)}, {_render(b)}) = {r}")
            return r
        if k in ("lt", "le"):
            a = self.num(self.ev(n["left"]), "left operand of " + k)
            b = self.num(self.ev(n["right"]), "right operand of " + k)
            r = a < b if k == "lt" else a <= b
            self.trace.append(f"{k}({_render(a)}, {_render(b)}) = {r}")
            return r
        if k in ("and", "or"):
            # Kleene logic with ill-typed operands (audit A3-02): a decisive operand (False
            # for and, True for or) settles the connective even if a sibling is ill-typed
            # or undecided -- e.g. or(eq(result, None), lt(result, 5)) holds for None.
            # Otherwise an undecided operand makes it UNKNOWN (it could still decide), and
            # only then does a type mismatch propagate (a violation at the top level).
            unknown = None
            mismatch = None
            vals = []
            for a in n["args"]:
                try:
                    vals.append(self.boolean(self.ev(a), f"operand of {k}"))
                except _Unknown as exc:
                    unknown = str(exc)
                    vals.append(_UNK)
                except _Mismatch as exc:
                    mismatch = mismatch or str(exc)
                    vals.append(_UNK)
            decisive = (k == "and" and False in vals) or (k == "or" and True in vals)
            if decisive:
                return k == "or"
            if unknown is not None:
                raise _Unknown(unknown)
            if mismatch is not None:
                raise _Mismatch(mismatch)
            if _UNK in vals:
                raise _Unknown(f"operand of {k} undecided")
            return k == "and"
        if k == "not":
            return not self.boolean(self.ev(n["arg"]), "operand of not")
        if k == "len":
            v = self.ev(n["value"])
            if isinstance(v, Opaque):
                raise _Unknown("len of an unsupported value")
            if type(v) not in (list, tuple):
                raise _Mismatch(f"len of {_category(v)} {_render(v)}")
            return len(v)
        if k == "sum":
            xs = self.numlist(self.ev(n["value"]), "operand of sum")
            r = sum(xs)
            self.trace.append(f"sum({_render(xs)}) = {_render(r)}")
            return r
        if k == "all_finite":
            v = self.ev(n["value"])
            if type(v) in (list, tuple):
                xs = self.numlist(v, "operand of all_finite")
                return all(_is_finite(x) for x in xs)
            return _is_finite(self.num(v, "operand of all_finite"))
        if k == "approx_eq":
            a = self.num(self.ev(n["left"]), "left operand of approx_eq")
            b = self.num(self.ev(n["right"]), "right operand of approx_eq")
            r = approx_eq(a, b, n["atol"], n["rtol"], n["require_finite"])
            self.trace.append(f"approx_eq({_render(a)}, {_render(b)}, atol={n['atol']}, rtol={n['rtol']}) = {r}")
            return r
        if k == "all_between":
            xs = self.numlist(self.ev(n["value"]), "operand of all_between")
            r = all(n["lower"] <= x <= n["upper"] for x in xs)
            self.trace.append(f"all_between({_render(xs)}, {n['lower']}, {n['upper']}) = {r}")
            return r
        if k == "unchanged_argument":
            if env.before_enc is None or env.after_enc is None:
                raise _Unknown("argument snapshots are unavailable")
            b, a = env.before_enc.get(n["name"]), env.after_enc.get(n["name"])
            if b is None or a is None or '"opaque"' in b or '"opaque"' in a:
                raise _Unknown(f"argument {n['name']} has no supported serialization before/after the call")
            r = a == b
            self.trace.append(f"unchanged_argument({n['name']}): before={_render(env.before[n['name']]) if env.before else '?'} "
                              f"after={_render(env.after[n['name']]) if env.after else '?'} -> {r}")
            return r
        if k == "repeatability":
            encs = env.repeat_encodings
            if not encs or len(encs) < n["repeats"]:
                raise _Unknown("repeated results are unavailable")
            if any('"opaque"' in e for e in encs):
                raise _Unknown("a repeated result has no supported serialization")
            r = all(e == encs[0] for e in encs)
            self.trace.append(f"repeatability: {len(encs)} repeats, {len(set(encs))} distinct result(s) -> {r}")
            return r
        raise _Unknown(f"node kind {k!r} is not evaluable")  # unreachable for validated terms


def approx_eq(a: int | float, b: int | float, atol: float, rtol: float, require_finite: bool) -> bool:
    fa, fb = _is_finite(a), _is_finite(b)
    if require_finite and not (fa and fb):
        return False
    if (type(a) is float and math.isnan(a)) or (type(b) is float and math.isnan(b)):
        return False
    if not (fa and fb):
        return a == b
    diff = abs(a - b)
    return diff <= max(rtol * max(abs(a), abs(b)), atol)


def evaluate(node: dict[str, Any], env: EvalEnv) -> EvalResult:
    """Evaluate a validated canonical expression. Never raises for runtime values."""
    ev = _Evaluator(env)
    try:
        v = ev.ev(node)
        if type(v) is not bool:
            raise _Mismatch(f"expression produced {_category(v)}, not a boolean")
    except _Unknown as exc:
        return EvalResult("UNKNOWN", str(exc), ev.trace[-8:])
    except _Mismatch as exc:
        return EvalResult("FALSE", f"type mismatch: {exc}", ev.trace[-8:])
    except (ArithmeticError, RecursionError) as exc:  # e.g. float overflow in sum of huge values
        return EvalResult("UNKNOWN", f"evaluation error: {type(exc).__name__}: {exc}", ev.trace[-8:])
    return EvalResult("TRUE" if v else "FALSE", "holds" if v else "does not hold", ev.trace[-8:])


__all__ = ["DSL_VERSION", "NODE_KINDS", "canonicalize", "Rejection", "check_expression", "CheckedExpression", "EvalEnv", "EvalResult", "evaluate", "approx_eq"]
