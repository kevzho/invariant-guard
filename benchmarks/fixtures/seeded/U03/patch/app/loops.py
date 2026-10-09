def mean_of(values: list[int]):
    if not values:
        return 0.0
    acc = 0
    for v in values:
        acc += v
    return acc / len(values)
