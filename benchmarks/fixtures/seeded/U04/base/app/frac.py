import math


def reduce_ratio(a: int, b: int):
    g = math.gcd(a, b)
    if g == 0:
        return (0, 0)
    return (a // g, b // g)
