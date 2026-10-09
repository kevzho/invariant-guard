# M2 bounds: the list grows through an alias inside unknown code; its length is not tracked
# through aliases/effects, so the index obligation stays UNKNOWN (never a false verdict).
def grow_then_read():
    xs = [1]
    ys = xs
    ys.append(2)
    return xs[1]
