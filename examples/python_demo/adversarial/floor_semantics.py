# Python // floors toward negative infinity: -1 // 2 == -1 (truncation would give 0).
def floor_bucket(a):
    q = a // 2
    if q == -1:
        return 100 // (a + 1)
    return 0
