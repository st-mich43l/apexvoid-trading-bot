# Box Breakout Strategy V2

`box_breakout` is the M5 compression thesis, separate from the M1 scalp. A
bounded-width box must be followed by an accepted close outside and a later M5
retest that holds the broken edge. Entry is the retest band, invalidation is
back inside the box and target is configured R. Identity anchors the box window
and direction. Wide boxes, wick-only breaks and failed retests reject. It is
enabled in the live Go opportunity stream; Algo Bot applies execution policy
before any TradePlan is published.

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. It and `scalp_breakout_retest` can retest the same broken box edge; same-thesis arbitration lets exactly one trade, so a breakout never executes twice.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
