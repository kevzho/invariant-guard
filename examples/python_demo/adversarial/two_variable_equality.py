# Interval facts on a and b alone cannot show a - b != 0; relational (SMT) reasoning can.
def guarded_difference(a, b):
    if a == b:
        return 0
    return 100 / (a - b)
