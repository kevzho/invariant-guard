# assert is skipped under `python -O`, so it is not a trusted runtime guard.
def checked_ratio(a, b):
    assert b != 0
    return a / b
