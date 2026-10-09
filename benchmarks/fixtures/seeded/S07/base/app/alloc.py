from app.limits import capacity


def per_slot(total: int, level: int):
    if level < 0:
        return 0
    return total // capacity(level)
