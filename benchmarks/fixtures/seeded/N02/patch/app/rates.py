def nonzero(d: int):
    if d == 0:
        return 1
    return d


def per_unit(total: int, n: int):
    if n == 0:
        return 0.0
    return total / nonzero(n)
