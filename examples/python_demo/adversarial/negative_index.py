# xs[-1] is valid for a nonempty built-in list. M2: the index is checked (annotation-only list, so conditional); element values are not modeled, so the division stays UNKNOWN.
def last_ratio(xs: list[int]):
    return 100 / xs[-1]
