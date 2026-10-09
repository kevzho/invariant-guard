def safe_div(a: int, b: int):
    if not b:
        return 0
    return a // b
