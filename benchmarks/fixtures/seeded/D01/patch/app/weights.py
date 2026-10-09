def to_shares(ws):
    total = max(ws)
    return [w / total for w in ws]
