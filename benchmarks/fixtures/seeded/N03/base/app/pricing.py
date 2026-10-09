def unit_price(total: int, qty: int):
    if qty == 0:
        return 0
    return total // qty
