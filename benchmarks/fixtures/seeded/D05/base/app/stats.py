def mean(xs: list[int]):
    if len(xs) == 0:
        return 0.0
    return sum(xs) / len(xs)
