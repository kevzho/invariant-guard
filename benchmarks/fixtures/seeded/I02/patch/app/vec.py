def scale_all(xs: list[int]):
    """API v2: scales the list in place (documented) and returns it."""
    for i in range(len(xs)):
        xs[i] = xs[i] * 2
    return xs
