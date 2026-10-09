def outer(a: int, b: int):
    def inner():
        if a == b:
            return 0.0
        return 10 / (a - b)
    return inner()
