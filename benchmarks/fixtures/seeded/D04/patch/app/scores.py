def clamp_scores(xs: list[int]):
    return [min(x, 100) for x in xs]
