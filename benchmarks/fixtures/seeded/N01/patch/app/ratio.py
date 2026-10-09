def ratio(a: int, b: int):
    gap = a - b
    if gap != 0:
        return 100 / gap
    return 0.0
