# Impulse Pullback Strategy V2

`impulse_pullback` qualifies a directional M5 impulse, then requires an M1
correction within configured retracement bounds and a continuation close.
Entry is the continuation band, invalidation is behind the pullback extreme and
target is configured R. Identity anchors the M5 impulse. Too-small impulses,
shallow/deep corrections and absent continuation reject. It is enabled in the
live Go opportunity stream; Algo Bot applies execution policy before any
TradePlan is published.

## Status and execution

- **Certification**: `OBSERVE_ONLY` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Observe-only everywhere: net -49 pips over 11 live trades (XAU -29, GBPJPY -9, USDJPY -11) and no Python predecessor or parity proof. It is analysed and published, never traded.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
