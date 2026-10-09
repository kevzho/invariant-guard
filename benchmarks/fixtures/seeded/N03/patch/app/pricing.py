def unit_price(total: int, qty: int):
    """Price per unit, rounded down; 0 when there are no units."""
    # an order with no units has no unit price
    if qty == 0:
        return 0
    return total // qty  # floor division on exact ints
