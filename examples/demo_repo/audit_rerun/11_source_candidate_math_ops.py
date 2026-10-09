def ratio(a: int, b: int):
    d = a - b
    return 100 / d


def normalize(xs):
    total = sum(xs) + 1.0
    return [x / total for x in xs]
