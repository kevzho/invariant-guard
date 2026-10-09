# No declared domain: the int annotations are not enforced at runtime.
def ratio_annotated(a: int, b: int):
    d = a - b
    return 100 / d
