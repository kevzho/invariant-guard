# isinstance(x, int) admits bool and int subclasses that may override __sub__.
def ratio_isinstance(a, b):
    if isinstance(a, int) and isinstance(b, int):
        return 100 / (a - b)
    return 0.0
