# docs/04 example lowering: the division is guarded by d != 0.
def ratio_nonzero(a: int, b: int):
    d = a - b
    if d != 0:
        return 100 / d
    return 0.0
