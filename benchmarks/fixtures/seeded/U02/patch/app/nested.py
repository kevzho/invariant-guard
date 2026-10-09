def outer(a: int, b: int):
    def inner():
        return 10 / (a - b)
    return inner()
