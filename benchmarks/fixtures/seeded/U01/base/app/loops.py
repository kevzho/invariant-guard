def mean_step(n: int):
    total = 0
    for i in range(n):
        total += i
    if total == 0:
        return 0.0
    return 100 / total
