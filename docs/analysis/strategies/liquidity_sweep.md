# Liquidity Sweep Strategy V2

`liquidity_sweep` consumes a canonical liquidity pool swept and reclaimed on
the current M5 close. The rejection body must clear its own ATR threshold.
Entry is the reclaimed pool band, invalidation is beyond the sweep extreme and
target is configured R. Identity is the pool ID. A sweep without same-bar
reclaim, weak rejection or previously consumed objective rejects. This remains
distinct from Snap-Back. Enabled for Go shadow publication; it does not alter
Python live-trading policy.
