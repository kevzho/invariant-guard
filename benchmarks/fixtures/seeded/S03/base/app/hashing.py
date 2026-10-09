def slot(key: int, buckets: int):
    if buckets > 0:
        return key % buckets
    return 0
