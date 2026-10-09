PAGE_SIZE = 0


def page_count(items: int):
    return (items + PAGE_SIZE - 1) // PAGE_SIZE
