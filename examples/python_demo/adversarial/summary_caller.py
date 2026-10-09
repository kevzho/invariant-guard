# M2 call summaries (Fixture D, candidate side): the callee is instantiated at the call site.
def denominator(x):
    return x


def scaled(x):
    if x < 0:
        return 0.0
    return 100 / denominator(x)
