def to_shares(ws):
    """Return each weight as a percentage of the total (API v2: percentages, not fractions)."""
    total = sum(ws)
    return [100.0 * w / total for w in ws]
