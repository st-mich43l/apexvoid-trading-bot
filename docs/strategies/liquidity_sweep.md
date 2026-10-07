# Liquidity Sweep Strategy V2

`liquidity_sweep` consumes a canonical liquidity pool swept and reclaimed on
the current M5 close. The rejection body must clear its own ATR threshold.
Entry is the reclaimed pool band, invalidation is beyond the sweep extreme and
target is configured R. Identity is the pool ID. A sweep without same-bar
reclaim, weak rejection or previously consumed objective rejects. This remains
distinct from Snap-Back. It is enabled in the live Go opportunity stream; Algo
Bot applies execution policy before any TradePlan is published.

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on FX (EURUSD +57, GBPUSD +36, GBPJPY +5, USDJPY 0 pips live). On XAU it is observe-only: 4 of 5 live trades stopped out (-165 pips) and v3 fires about twice a week, too rarely to prove an edge. It is never re-enabled automatically.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
