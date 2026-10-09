"""A caller of the statistics helpers (gives the context packets real callers)."""

from heldout.stats import mean_of, spread


def summary_score(xs: list[float]) -> float:
    """Mean plus spread of the scores."""
    return mean_of(xs) + spread(xs)
