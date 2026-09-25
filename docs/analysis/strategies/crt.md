# CRT Strategy V2

`crt` treats the prior H1 impulse candle as an owned range. M5 must sweep one
range edge and close back inside it; BUY enters between the reclaimed low and
confirmation close, SELL mirrors at the high. Invalidation is beyond the sweep
plus ATR buffer and target is the opposite H1 edge. Identity is the H1 anchor
time. Insufficient H1 range or no reclaim rejects. Enabled for Go shadow
publication; it does not alter Python live-trading policy.
