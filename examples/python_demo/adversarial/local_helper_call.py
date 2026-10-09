# M2 instantiates the callee, but n has no declared domain: n - 1 may dispatch to user code, so the result stays UNKNOWN.
def shifted(x):
    return x - 1


def caller_ratio(n):
    return 100 / shifted(n)
