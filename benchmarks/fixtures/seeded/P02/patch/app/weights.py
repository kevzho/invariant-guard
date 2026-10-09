def to_shares(ws):
    total = float(sum(ws))
    return [w / total for w in ws]
