def safe_ratio(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return 0.0
