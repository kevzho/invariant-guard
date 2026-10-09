"""Integer interval lattice plus a separate nonzero fact (docs/05).

Bounds are Python ints or +/-infinity. ``AbsInt`` pairs an interval with a ``nonzero``
flag because one interval cannot express a gap around zero (e.g. ``d != 0`` over
``[-inf, inf]``). Bottom means "no concrete value" (unreachable).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

NEG_INF = -math.inf
POS_INF = math.inf

Bound = "int | float"  # float only for +/-inf


def _is_inf(x: object) -> bool:
    return isinstance(x, float) and math.isinf(x)


def _mul(a, b):
    # 0 * inf = 0 for interval bound arithmetic.
    if a == 0 or b == 0:
        return 0
    return a * b


# Finite bounds are kept below this magnitude so that repeated squaring of constants
# (``x = x * x`` many times) cannot make interval arithmetic itself take unbounded time
# and memory (audit finding A-02). Clamping only ever widens an interval, so it is sound:
# a lower bound above the limit becomes the limit (positivity, hence nonzero, is kept),
# a lower bound below -limit becomes -inf, and symmetrically for upper bounds.
BOUND_LIMIT = 1 << 256


def _clamp_lo(x):
    if _is_inf(x):
        return x
    if x > BOUND_LIMIT:
        return BOUND_LIMIT
    if x < -BOUND_LIMIT:
        return NEG_INF
    return x


def _clamp_hi(x):
    if _is_inf(x):
        return x
    if x < -BOUND_LIMIT:
        return -BOUND_LIMIT
    if x > BOUND_LIMIT:
        return POS_INF
    return x


@dataclass(frozen=True)
class Interval:
    lo: object  # int or -inf
    hi: object  # int or +inf
    bottom: bool = False

    def __post_init__(self) -> None:
        if not self.bottom:
            object.__setattr__(self, "lo", _clamp_lo(self.lo))
            object.__setattr__(self, "hi", _clamp_hi(self.hi))

    @staticmethod
    def top() -> "Interval":
        return Interval(NEG_INF, POS_INF)

    @staticmethod
    def bot() -> "Interval":
        return Interval(POS_INF, NEG_INF, True)

    @staticmethod
    def const(c: int) -> "Interval":
        return Interval(c, c)

    @staticmethod
    def make(lo, hi) -> "Interval":
        if lo > hi:
            return Interval.bot()
        return Interval(lo, hi)

    def is_top(self) -> bool:
        return not self.bottom and self.lo == NEG_INF and self.hi == POS_INF

    def contains(self, v: int) -> bool:
        return not self.bottom and self.lo <= v <= self.hi

    def singleton(self) -> int | None:
        if not self.bottom and self.lo == self.hi and not _is_inf(self.lo):
            return int(self.lo)  # type: ignore[arg-type]
        return None

    def join(self, other: "Interval") -> "Interval":
        if self.bottom:
            return other
        if other.bottom:
            return self
        return Interval(min(self.lo, other.lo), max(self.hi, other.hi))

    def meet(self, other: "Interval") -> "Interval":
        if self.bottom or other.bottom:
            return Interval.bot()
        return Interval.make(max(self.lo, other.lo), min(self.hi, other.hi))

    def add(self, other: "Interval") -> "Interval":
        if self.bottom or other.bottom:
            return Interval.bot()
        return Interval(self.lo + other.lo, self.hi + other.hi)

    def neg(self) -> "Interval":
        if self.bottom:
            return self
        return Interval(-self.hi, -self.lo)

    def sub(self, other: "Interval") -> "Interval":
        return self.add(other.neg())

    def mul(self, other: "Interval") -> "Interval":
        if self.bottom or other.bottom:
            return Interval.bot()
        prods = [_mul(a, b) for a in (self.lo, self.hi) for b in (other.lo, other.hi)]
        return Interval(min(prods), max(prods))

    def __repr__(self) -> str:
        if self.bottom:
            return "⊥"
        lo = "-inf" if self.lo == NEG_INF else str(self.lo)
        hi = "+inf" if self.hi == POS_INF else str(self.hi)
        return f"[{lo}, {hi}]"


@dataclass(frozen=True)
class AbsInt:
    """Interval plus separate nonzero fact. Normalized on construction via ``mk``."""

    itv: Interval
    nonzero: bool = False

    @staticmethod
    def mk(itv: Interval, nonzero: bool = False) -> "AbsInt":
        if itv.bottom:
            return AbsInt(Interval.bot(), False)
        if nonzero:
            lo, hi = itv.lo, itv.hi
            if lo == 0:
                lo = 1
            if hi == 0:
                hi = -1
            itv = Interval.make(lo, hi)
            if itv.bottom:
                return AbsInt(Interval.bot(), False)
        if not itv.contains(0):
            nonzero = True
        return AbsInt(itv, nonzero)

    @staticmethod
    def top() -> "AbsInt":
        return AbsInt(Interval.top(), False)

    @staticmethod
    def bot() -> "AbsInt":
        return AbsInt(Interval.bot(), False)

    @staticmethod
    def const(c: int) -> "AbsInt":
        return AbsInt.mk(Interval.const(c))

    @property
    def bottom(self) -> bool:
        return self.itv.bottom

    def excludes_zero(self) -> bool:
        return self.bottom or self.nonzero

    def is_zero(self) -> bool:
        return self.itv.singleton() == 0

    def join(self, other: "AbsInt") -> "AbsInt":
        if self.bottom:
            return other
        if other.bottom:
            return self
        return AbsInt.mk(self.itv.join(other.itv), self.nonzero and other.nonzero)

    def meet(self, other: "AbsInt") -> "AbsInt":
        return AbsInt.mk(self.itv.meet(other.itv), self.nonzero or other.nonzero)

    def add(self, other: "AbsInt") -> "AbsInt":
        return AbsInt.mk(self.itv.add(other.itv))

    def sub(self, other: "AbsInt") -> "AbsInt":
        return AbsInt.mk(self.itv.sub(other.itv))

    def mul(self, other: "AbsInt") -> "AbsInt":
        # Product of two nonzero integers is nonzero.
        return AbsInt.mk(self.itv.mul(other.itv), self.nonzero and other.nonzero)

    def floordiv_const(self, c: int) -> "AbsInt":
        """Python floor division by a nonzero constant (monotone in the dividend)."""
        if self.bottom:
            return self
        if c == 0:
            return AbsInt.bot()

        def f(x):
            if _is_inf(x):
                return x if c > 0 else -x
            return x // c

        a, b = f(self.itv.lo), f(self.itv.hi)
        return AbsInt.mk(Interval(min(a, b), max(a, b)))

    def mod_const(self, c: int) -> "AbsInt":
        """Python modulo by a nonzero constant: result has the divisor's sign."""
        if self.bottom:
            return self
        if c == 0:
            return AbsInt.bot()
        if c > 0:
            return AbsInt.mk(Interval(0, c - 1))
        return AbsInt.mk(Interval(c + 1, 0))

    # -- refinement by comparison with another abstract value -------------------------
    def refine(self, cmp: str, other: "AbsInt") -> "AbsInt":
        """Values of self such that ``self <cmp> v`` holds for some v in other."""
        if self.bottom or other.bottom:
            return AbsInt.bot()
        o = other.itv
        if cmp == "EQ":
            return self.meet(other)
        if cmp == "NE":
            c = o.singleton()
            if c is None:
                return self
            if c == 0:
                return AbsInt.mk(self.itv, True)
            lo, hi = self.itv.lo, self.itv.hi
            if lo == c:
                lo = c + 1
            if hi == c:
                hi = c - 1
            return AbsInt.mk(Interval.make(lo, hi), self.nonzero)
        if cmp == "LT":
            return AbsInt.mk(self.itv.meet(Interval(NEG_INF, o.hi - 1 if not _is_inf(o.hi) else POS_INF)), self.nonzero)
        if cmp == "LE":
            return AbsInt.mk(self.itv.meet(Interval(NEG_INF, o.hi)), self.nonzero)
        if cmp == "GT":
            return AbsInt.mk(self.itv.meet(Interval(o.lo + 1 if not _is_inf(o.lo) else NEG_INF, POS_INF)), self.nonzero)
        if cmp == "GE":
            return AbsInt.mk(self.itv.meet(Interval(o.lo, POS_INF)), self.nonzero)
        raise ValueError(cmp)

    def __repr__(self) -> str:
        return f"{self.itv!r}{' ≠0' if self.nonzero and self.itv.contains(0) else ''}"


NEGATE = {"EQ": "NE", "NE": "EQ", "LT": "GE", "LE": "GT", "GT": "LE", "GE": "LT"}
SWAP = {"EQ": "EQ", "NE": "NE", "LT": "GT", "LE": "GE", "GT": "LT", "GE": "LE"}
