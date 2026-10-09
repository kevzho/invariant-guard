import math


def reduce_ratio(a: int, b: int):
    g = math.gcd(a, b)
    return (a // g, b // g)
