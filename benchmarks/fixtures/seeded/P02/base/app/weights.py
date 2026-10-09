def to_shares(ws):
    total = sum(ws)
    return [w / total for w in ws]
