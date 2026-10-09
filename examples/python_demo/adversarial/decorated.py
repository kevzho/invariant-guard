import functools


@functools.lru_cache(maxsize=None)
def cached_inverse(a):
    return 1 / a
