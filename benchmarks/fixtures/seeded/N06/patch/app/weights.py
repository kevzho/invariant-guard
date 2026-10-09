def to_shares(ws):
    total = sum(ws)
    return list(map(lambda w: w / total, ws))
