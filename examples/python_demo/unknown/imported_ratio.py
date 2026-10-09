from vendor_math import external_transform  # never imported or resolved by guard


def imported_ratio(a, b):
    d = external_transform(a, b)
    return 100 / d
