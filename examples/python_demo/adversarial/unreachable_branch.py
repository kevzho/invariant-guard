# The bad branch requires x > 5 and x < 3 at once.
def unreachable_bad_branch(x):
    if x > 5:
        if x < 3:
            return 100 // 0
    return 1
