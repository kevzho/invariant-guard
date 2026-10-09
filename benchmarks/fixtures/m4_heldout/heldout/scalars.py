"""Scalar helpers (M4 held-out evaluation fixture; written for this evaluation)."""


def abs_diff(a: int, b: int) -> int:
    """Absolute difference |a - b|; never negative and symmetric in a and b."""
    return a - b if a >= b else b - a


def clamp_percent(p: float) -> float:
    """Clamp a percentage into the closed range [0.0, 100.0]."""
    return min(p, 100.0)


def floor_div_or_zero(a: int, b: int) -> int:
    """a // b, or 0 when b is 0."""
    if b == 0:
        return 0
    return a // b
