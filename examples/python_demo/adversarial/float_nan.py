# Float semantics (NaN, infinities, rounding) are not modeled in M1.
def float_gap(x: float):
    d = x - x
    return 1.0 / d
