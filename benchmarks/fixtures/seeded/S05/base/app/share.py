def share(part: int, whole: int):
    if whole == 0:
        return 0.0
    ratio = part / whole
    return ratio * 100
