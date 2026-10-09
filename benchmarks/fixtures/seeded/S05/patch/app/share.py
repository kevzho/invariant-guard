def share(part: int, whole: int):
    ratio = part / whole
    if whole == 0:
        return 0.0
    return ratio * 100
