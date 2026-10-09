def unique_sorted(xs: list[int]):
    xs.sort()
    return [x for i, x in enumerate(xs) if i == 0 or xs[i - 1] != x]
