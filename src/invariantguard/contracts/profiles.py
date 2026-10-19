"""Bounded input profiles for contract probes (M3, docs/06 "Minimal typed property DSL").

A profile is the generator-side half of a contract's input domain. It fixes the exact
built-in type and the bounds of a parameter, so a probe input is always a finite,
serializable built-in value. A contract names one profile for every parameter, or one
profile per parameter (:class:`ProfileAssignment`; DECISIONS I-146). The contract precondition then narrows the
profile; an input is admitted only if it satisfies both (``admitted``). JSON alone is
not a complete generator (docs/06), so generation is explicit here:

* boundary inputs derived from the profile and from simple precondition conjuncts
  (``all_between``, comparisons of ``len(arg)`` or a scalar ``arg`` with a literal);
* seeded pseudo-random inputs (``random.Random(seed)``; deterministic across runs);
* shrink candidates that are strictly simpler under :func:`complexity`.

Hints only steer generation. Admission is always re-checked against the full profile and
the full precondition, so a wrong or missing hint can cost coverage, never soundness of
the domain (shrinking never leaves the domain: tests/unit/test_profiles.py).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable

PROFILE_VERSION = "profiles-1"


@dataclass(frozen=True)
class InputProfile:
    name: str
    kind: str  # num_list | int_list | int | float
    description: str
    min_len: int = 0
    max_len: int = 16
    lo: float = -1e6
    hi: float = 1e6

    @property
    def dsl_type(self) -> str:
        return "num_list" if self.kind in ("num_list", "int_list") else "num"

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "description": self.description, "min_len": self.min_len, "max_len": self.max_len,
                "lo": self.lo, "hi": self.hi, "version": PROFILE_VERSION}


PROFILES: dict[str, InputProfile] = {
    p.name: p
    for p in (
        InputProfile("bounded_builtin_numeric_list_v1", "num_list",
                     "every parameter is a nonempty built-in list (1..16 items) of exact built-in int or float values (bool excluded), finite, within [-1e6, 1e6]",
                     min_len=1, max_len=16),
        InputProfile("bounded_builtin_int_list_v1", "int_list",
                     "every parameter is a built-in list (0..16 items) of exact built-in ints (bool excluded) within [-1000, 1000]",
                     min_len=0, max_len=16, lo=-1000, hi=1000),
        InputProfile("bounded_builtin_ints_v1", "int",
                     "every parameter is an exact built-in int (bool excluded) within [-1000000, 1000000]", lo=-1_000_000, hi=1_000_000),
        InputProfile("bounded_builtin_finite_floats_v1", "float",
                     "every parameter is an exact built-in float, finite, within [-1e6, 1e6]"),
    )
}


class ProfileAssignment:
    """The input domain of a contract: exactly one profile per target parameter.

    ``spec`` is the revision's ``input_profile`` as written: a profile name (the same
    profile for every parameter, the original form) or an object ``{parameter: name}``.
    """

    def __init__(self, spec: str | dict[str, str], by_param: dict[str, InputProfile]) -> None:
        self.spec = spec
        self.by_param = dict(by_param)

    @property
    def uniform(self) -> bool:
        return isinstance(self.spec, str)

    @property
    def name(self) -> str:
        """Display label: the profile name, or ``{p: name, ...}`` in parameter order."""
        if isinstance(self.spec, str):
            return self.spec
        return "{" + ", ".join(f"{p}: {prof.name}" for p, prof in self.by_param.items()) + "}"

    @property
    def names(self) -> list[str]:
        return sorted({prof.name for prof in self.by_param.values()})

    def of(self, param: str) -> InputProfile:
        return self.by_param[param]

    def check(self, param: str, value: Any) -> str | None:
        return check_value(self.by_param[param], value)


def profile_label(spec: Any) -> str:
    """Text rendering of a revision's ``input_profile`` (a name or ``{parameter: name}``)."""
    if type(spec) is dict:
        return "{" + ", ".join(f"{p}: {n}" for p, n in spec.items()) + "}"
    return str(spec)


def per_param(profile: "InputProfile | ProfileAssignment | dict[str, InputProfile]", params: list[str]) -> dict[str, InputProfile]:
    if isinstance(profile, InputProfile):
        return {p: profile for p in params}
    by = profile.by_param if isinstance(profile, ProfileAssignment) else profile
    return {p: by[p] for p in params}


def is_num(x: Any) -> bool:
    return type(x) is int or type(x) is float


def _finite(x: Any) -> bool:
    return type(x) is int or (type(x) is float and math.isfinite(x))


def check_value(profile: InputProfile, value: Any) -> str | None:
    """None if ``value`` belongs to the profile, else the reason it does not."""
    if profile.kind in ("num_list", "int_list"):
        if type(value) is not list:
            return f"expected a built-in list, got {type(value).__name__}"
        if not profile.min_len <= len(value) <= profile.max_len:
            return f"list length {len(value)} outside [{profile.min_len}, {profile.max_len}]"
        for x in value:
            if profile.kind == "int_list" and type(x) is not int:
                return f"element {x!r} is not an exact built-in int"
            if not is_num(x):
                return f"element {x!r} is not an exact built-in int or float"
            if not _finite(x):
                return f"element {x!r} is not finite"
            if not profile.lo <= x <= profile.hi:
                return f"element {x!r} outside [{profile.lo}, {profile.hi}]"
        return None
    if profile.kind == "int":
        if type(value) is not int:
            return f"expected an exact built-in int, got {type(value).__name__}"
    elif profile.kind == "float":
        if type(value) is not float:
            return f"expected an exact built-in float, got {type(value).__name__}"
        if not math.isfinite(value):
            return "value is not finite"
    if not profile.lo <= value <= profile.hi:
        return f"value {value!r} outside [{profile.lo}, {profile.hi}]"
    return None


@dataclass
class Hint:
    """Bounds suggested by precondition conjuncts (steer generation only)."""

    elem_lo: float = -math.inf
    elem_hi: float = math.inf
    len_lo: int = 0
    len_hi: int = 1 << 30
    lo: float = -math.inf
    hi: float = math.inf


def _lit(node: dict[str, Any]) -> float | None:
    if node.get("kind") == "literal" and is_num(node.get("value")):
        return node["value"]
    return None


def _arg_of(node: dict[str, Any], wrapper: str | None) -> str | None:
    if wrapper is None:
        return node["name"] if node.get("kind") == "arg" else None
    if node.get("kind") == wrapper and node["value"].get("kind") == "arg":
        return node["value"]["name"]
    return None


def domain_hints(precondition: dict[str, Any], params: list[str]) -> dict[str, Hint]:
    """Hints from the top-level conjuncts of a canonical precondition."""
    hints = {p: Hint() for p in params}
    conj = precondition["args"] if precondition.get("kind") == "and" else [precondition]
    for c in conj:
        k = c.get("kind")
        if k == "all_between":
            p = _arg_of(c["value"], None)
            if p in hints:
                hints[p].elem_lo = max(hints[p].elem_lo, c["lower"])
                hints[p].elem_hi = min(hints[p].elem_hi, c["upper"])
            continue
        if k not in ("lt", "le", "eq"):
            continue
        left, right = c["left"], c["right"]
        strict = k == "lt"
        for wrapper in ("len", None):
            lp, rp = _arg_of(left, wrapper), _arg_of(right, wrapper)
            lv, rv = _lit(left), _lit(right)
            if lp in hints and rv is not None:  # f(arg) <= v  /  f(arg) == v
                h = hints[lp]
                if wrapper == "len":
                    bound = math.floor(rv) - (1 if strict and float(rv).is_integer() else 0)
                    h.len_hi = min(h.len_hi, bound)
                    if k == "eq":
                        h.len_lo = max(h.len_lo, math.ceil(rv))
                else:
                    h.hi = min(h.hi, rv)
                    if k == "eq":
                        h.lo = max(h.lo, rv)
            if rp in hints and lv is not None:  # v <= f(arg)
                h = hints[rp]
                if wrapper == "len":
                    bound = math.ceil(lv) + (1 if strict and float(lv).is_integer() else 0)
                    h.len_lo = max(h.len_lo, bound)
                    if k == "eq":
                        h.len_hi = min(h.len_hi, math.floor(lv))
                else:
                    h.lo = max(h.lo, lv)
                    if k == "eq":
                        h.hi = min(h.hi, lv)
    return hints


def _elem_range(profile: InputProfile, h: Hint, scalar: bool) -> tuple[float, float]:
    lo = max(profile.lo, h.lo if scalar else h.elem_lo)
    hi = min(profile.hi, h.hi if scalar else h.elem_hi)
    if lo > hi:  # contradictory hint: fall back to the profile (admission decides)
        return profile.lo, profile.hi
    return lo, hi


def _len_range(profile: InputProfile, h: Hint) -> tuple[int, int]:
    lo = max(profile.min_len, h.len_lo)
    hi = min(profile.max_len, h.len_hi)
    if lo > hi:
        return profile.min_len, profile.max_len
    return lo, hi


def _gen_number(rng: random.Random, lo: float, hi: float, ints_only: bool, want_float: bool | None = None) -> int | float:
    ilo, ihi = math.ceil(lo), math.floor(hi)
    r = rng.random()
    if ints_only or (r < 0.35 and ilo <= ihi and want_float is not True):
        if ilo > ihi:
            return ilo
        small_lo, small_hi = max(ilo, -10), min(ihi, 10)
        if small_lo <= small_hi and rng.random() < 0.5:
            return rng.randint(small_lo, small_hi)
        return rng.randint(ilo, ihi)
    if r < 0.5:
        for v in (0.0, 1.0, lo, hi, 0.5):
            if lo <= v <= hi and rng.random() < 0.3:
                return float(v)
    v = lo + (hi - lo) * rng.random()
    if r < 0.85:
        v = round(v, rng.choice((0, 1, 2)))
    v = float(min(max(v, lo), hi))
    return v


def generate(profile: InputProfile, rng: random.Random, h: Hint) -> Any:
    if profile.kind in ("num_list", "int_list"):
        llo, lhi = _len_range(profile, h)
        n = rng.randint(llo, min(lhi, llo + 7))
        elo, ehi = _elem_range(profile, h, scalar=False)
        return [_gen_number(rng, elo, ehi, profile.kind == "int_list") for _ in range(n)]
    lo, hi = _elem_range(profile, h, scalar=True)
    if profile.kind == "int":
        return _gen_number(rng, lo, hi, True)
    return float(_gen_number(rng, lo, hi, False, want_float=True))


def boundary(profile: InputProfile, h: Hint) -> list[Any]:
    out: list[Any] = []
    if profile.kind in ("num_list", "int_list"):
        llo, lhi = _len_range(profile, h)
        elo, ehi = _elem_range(profile, h, scalar=False)
        ints = profile.kind == "int_list"
        cands = [ehi, elo, 1, 0, -1] if ints else [1.0, ehi, elo, 0.5, 1, 0.0]
        vals = []
        for c in cands:
            c = (math.floor(c) if c >= 0 else math.ceil(c)) if ints and type(c) is float else c
            if elo <= c <= ehi and c not in vals:
                vals.append(c if not ints else int(c))
        for n in sorted({llo, max(llo, min(2, lhi)), min(lhi, max(llo, 3))}):
            for v in vals:
                out.append([v] * n)
            if n >= 2 and len(vals) >= 2:
                out.append([vals[i % len(vals)] for i in range(n)])
                out.append([vals[(i + 1) % len(vals)] for i in range(n)][::-1])
        return out
    lo, hi = _elem_range(profile, h, scalar=True)
    cands = [0, 1, -1, lo, hi] if profile.kind == "int" else [0.0, 1.0, -1.0, 0.5, lo, hi]
    for c in cands:
        c = int(c) if profile.kind == "int" and math.isfinite(c) and float(c).is_integer() else c
        if profile.kind == "float":
            c = float(c)
        if lo <= c <= hi and c not in out:
            out.append(c)
    return out


def _num_cost(x: int | float) -> float:
    a = min(abs(x), 1e12)
    digits = 0
    if type(x) is float:
        frac = repr(x).split(".")[-1] if "." in repr(x) else ""
        digits = len(frac.rstrip("0"))
    return a + (0.5 if type(x) is float else 0.0) + 0.01 * digits


def complexity(value: Any) -> tuple[float, float]:
    """Well-founded simplicity measure: shorter lists, smaller magnitudes, ints before floats."""
    if type(value) in (list, tuple):
        return (float(len(value)), sum(_num_cost(x) for x in value))
    if is_num(value):
        return (0.0, _num_cost(value))
    return (0.0, 0.0)


def _simpler_numbers(x: int | float, ints_only: bool) -> list[int | float]:
    out: list[int | float] = [0, 1, -1] if ints_only else [0, 1, 0.0, 1.0, -1]
    if type(x) is float and math.isfinite(x):
        if x.is_integer():
            out.append(int(x))
        out += [float(round(x)), round(x, 1), float(int(x / 2))]
        if abs(x) >= 1e-6:
            out.append(round(x, 2))
    elif type(x) is int:
        out += [x // 2, -(abs(x) // 2) if x < 0 else x // 2, x - 1 if x > 0 else x + 1]
    return out


def shrink_candidates(profile: InputProfile, value: Any) -> list[Any]:
    """Candidates strictly simpler than ``value`` (admission is checked by the caller)."""
    base = complexity(value)
    cands: list[Any] = []
    if type(value) is list:
        for i in range(len(value)):
            cands.append(value[:i] + value[i + 1:])
        for i, x in enumerate(value):
            for s in _simpler_numbers(x, profile.kind == "int_list"):
                cands.append(value[:i] + [s] + value[i + 1:])
    elif is_num(value):
        cands = list(_simpler_numbers(value, profile.kind == "int"))
        if profile.kind == "float":
            cands = [float(c) for c in cands]
    out, seen = [], set()
    for c in cands:
        key = repr(c)
        if key in seen or check_value(profile, c) is not None or complexity(c) >= base:
            continue
        seen.add(key)
        out.append(c)
    return out


@dataclass
class Admission:
    inputs: list[tuple[dict[str, Any], str]] = field(default_factory=list)  # (args, origin)
    excluded: list[tuple[dict[str, Any], str, str]] = field(default_factory=list)  # (args, origin, why)
    attempts: int = 0


def admit_inputs(profile: InputProfile | ProfileAssignment | dict[str, InputProfile], params: list[str],
                 precondition_holds: Callable[[dict[str, Any]], tuple[bool | None, str]],
                 hints: dict[str, Hint], *, seed: int, limit: int, examples: list[dict[str, Any]] | None = None,
                 max_attempts: int | None = None) -> Admission:
    """Examples, then boundary inputs, then seeded random inputs; each must satisfy every
    parameter's profile and the precondition (``precondition_holds`` is the pure DSL evaluator)."""
    profs = per_param(profile, params)
    adm = Admission()
    seen: set[str] = set()
    max_attempts = max_attempts if max_attempts is not None else max(64, limit * 8)

    def offer(args: dict[str, Any], origin: str) -> None:
        key = repr(sorted(args.items()))
        if key in seen:
            return
        seen.add(key)
        adm.attempts += 1
        for p in params:
            why = check_value(profs[p], args.get(p))
            if why is not None:
                adm.excluded.append((args, origin, f"outside input profile {profs[p].name}: {p}: {why}"))
                return
        ok, why = precondition_holds(args)
        if ok is True:
            adm.inputs.append((args, origin))
        else:
            adm.excluded.append((args, origin, "precondition " + ("is false" if ok is False else f"is undecided ({why})")))

    for ex in examples or []:
        offer(dict(ex), "example")
    b = {p: boundary(profs[p], hints[p]) for p in params}
    width = max((len(v) for v in b.values()), default=0)
    for i in range(width):
        if len(adm.inputs) >= limit:
            break
        offer({p: b[p][i % len(b[p])] for p in params if b[p]}, "boundary")
    rng = random.Random(seed)
    draws = 0
    while len(adm.inputs) < limit and draws < max_attempts:
        draws += 1  # counted before deduplication, so a tiny domain cannot loop forever
        offer({p: generate(profs[p], rng, hints[p]) for p in params}, "random")
    adm.inputs = adm.inputs[:limit]
    return adm


__all__ = ["PROFILES", "InputProfile", "ProfileAssignment", "per_param", "profile_label", "check_value", "domain_hints", "generate", "boundary", "shrink_candidates", "complexity", "admit_inputs",
           "Hint", "Admission", "PROFILE_VERSION", "is_num"]
