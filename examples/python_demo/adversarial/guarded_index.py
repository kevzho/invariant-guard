# M2 bounds: a nonempty list admits xs[-1] (Python negative indexes count from the end).
def last_or_zero(xs):
    if xs:
        return xs[-1]
    return 0
