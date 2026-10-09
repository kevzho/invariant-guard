def lookup(k: int):
    if k < 0:
        return None
    return k * 2


def width(k: int):
    return lookup(k).bit_length()
