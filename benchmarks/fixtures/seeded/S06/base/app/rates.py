def denominator(n: int):
    return n + 1


def per_item(total: int, n: int):
    if n < 0:
        return 0.0
    return total / denominator(n)
