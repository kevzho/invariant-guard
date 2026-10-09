def wrap(a, n):
    if n > 0:
        return a % n
    return a % (n - 1)
