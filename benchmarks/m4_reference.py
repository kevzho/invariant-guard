"""Ground truth for the M4 held-out set: intended implementations of the seeded-bug functions.

Kept OUTSIDE benchmarks/fixtures/m4_heldout so neither discovery source (templates or the
model) can see it. A counterexample "exposes the seeded bug" when the fixture function and
the reference differ on it in result or in argument mutation (benchmarks/model_eval.py).
"""


def normalize_to_unit(xs):
    total = sum(xs)
    return [x / total for x in xs]


def count_positive(xs):
    return sum(1 for x in xs if x > 0)


def dedupe_sorted(xs):
    return sorted(set(xs))


def clip_negatives(xs):
    return [x if x >= 0 else 0 for x in xs]


def clamp_percent(p):
    return min(max(p, 0.0), 100.0)
