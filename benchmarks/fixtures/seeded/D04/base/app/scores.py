def clamp_scores(xs: list[int]):
    return [min(max(x, 0), 100) for x in xs]
