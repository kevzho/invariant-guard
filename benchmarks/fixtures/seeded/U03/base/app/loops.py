def mean_of(xs: list[int]):
    if not xs:
        return 0.0
    total = 0
    for x in xs:
        total += x
    return total / len(xs)
