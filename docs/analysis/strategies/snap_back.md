# Snap-Back Strategy V2

`snap_back` is mean reversion after M5 price extends by configured ATR from the
nearest canonical key level. The confirmation candle must turn toward that
anchor. Entry is the confirmation bar, invalidation lies beyond its extreme,
and target retraces a configured fraction toward the anchor. It does not imply
or require a liquidity sweep. Identity is the anchor level ID and extension
side. Under-extension or absent reversal rejects. Enabled for Go shadow
publication; it does not alter Python live-trading policy.
