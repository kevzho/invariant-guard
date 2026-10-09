"""Sequence helpers (M4 held-out evaluation fixture; written for this evaluation)."""


def dedupe_sorted(xs: list[int]) -> list[int]:
    """Sorted list of the distinct values in xs. The input list is not modified."""
    xs.sort()
    out = []
    for x in xs:
        if not out or out[-1] != x:
            out.append(x)
    return out


def pairwise_diffs(xs: list[int]) -> list[int]:
    """Differences between consecutive elements: [xs[1] - xs[0], xs[2] - xs[1], ...]."""
    return [b - a for a, b in zip(xs, xs[1:])]


def clip_negatives(xs: list[int]) -> list[int]:
    """Replace every negative value by zero; all results are >= 0."""
    return [x if x >= -1 else 0 for x in xs]


def rotate_left(xs: list[int]) -> list[int]:
    """Move the first element to the end. Same length and same elements as the input."""
    if not xs:
        return []
    return xs[1:] + xs[:1]
