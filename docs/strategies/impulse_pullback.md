# Impulse Pullback Strategy V2

**Contract.** Ported from the Python scalp lane's `discover_impulse_pullback`: 4 ATR displacement with 0.5 body dominance, a corrective pullback (below 0.7 of the impulse body, 25-75% retracement, confirmed extreme), an unmitigated M5 zone or nearby key level whose closed-bar role agrees, location in the dealing range, an M1 confirmation at the reference, the structural stop and the corridor target. XAU only. The real XAU capture contains no Python decision, so parity is proven on seeded synthetic captures only; XAU stays observe-only.

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
- **Execution**: Observe-only everywhere: net -49 pips over 11 live trades (XAU -29, GBPJPY -9, USDJPY -11) and it is not a port of the Python scalp-lane `discover_impulse_pullback`. It is analysed and published, never traded.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
