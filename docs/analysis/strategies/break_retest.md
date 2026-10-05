# Break & Retest

`break_retest` is the independent M5 structural break-and-retest thesis.
Each closed-bar evaluation checks trendline candidates first, nearest to the
current price, then canonical key levels. A BUY requires a broken resistance
that is retested and held above, or a key level accepted above and retested as
support. SELL is mirrored.

The current candle must also provide the frozen rejection shape, the regime
must not be chop, and the premium/discount gate must allow the direction.
The strategy returns at most one candidate per evaluation. It does not reuse
the M5 box detector or the M1 scalp breakout detector.
