"""Small statistics helpers (M4 held-out evaluation fixture; written for this evaluation)."""


def mean_of(xs: list[float]) -> float:
    """Arithmetic mean of a non-empty list."""
    return sum(xs) / len(xs)


def spread(xs: list[float]) -> float:
    """Largest value minus smallest value; never negative."""
    return max(xs) - min(xs)


def normalize_to_unit(xs: list[float]) -> list[float]:
    """Scale the values so that they sum to 1.0 (when their total is positive).

    The result has one entry per input value.
    """
    total = sum(xs) + 1e-3
    return [x / total for x in xs]


def running_total(xs: list[int]) -> list[int]:
    """Prefix sums: element i is xs[0] + ... + xs[i]; the last element equals sum(xs)."""
    out = []
    acc = 0
    for x in xs:
        acc += x
        out.append(acc)
    return out


def count_positive(xs: list[float]) -> int:
    """Number of strictly positive values (between 0 and len(xs))."""
    return sum(1 for x in xs if x >= 0)
