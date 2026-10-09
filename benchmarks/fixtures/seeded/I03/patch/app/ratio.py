def ratio(a: int, b: int):
    if b == 0:
        raise ValueError("b must be nonzero")
    return a / b
