def ratio(a: int, b: int):
    d = a - b
    if d == 0:
        return None  # or some other appropriate value or handling
    return 100 / d


def normalize(xs):
    total = sum(xs)
    return [x / total for x in xs]
