# Trendline Strategy V2

**Contract.** Restored to the frozen V2 `trendline_reaction`: immutable causal anchors, forward validation, health (not tentative, broken, degraded, exhausted or stale), chop-regime quality gates, a reclaimed live interaction and a confirmed reaction inside the interaction band. Proven on 8 oracle decisions (`test/legacyparity`); the gate is narrow, so the golden holds few decisions.

`trendline` consumes immutable causal M5 anchors and the canonical interaction
classifier. A line needs the configured validation touches, a valid approach,
and a reclaimed support/resistance close. Entry is the interaction band,
invalidation is a close-violation buffer, and target is strategy-owned R.
Identity is the anchor pair, never evaluation time. Broken/exhausted lines and
testing without reclaim reject. It is enabled in the live Go opportunity
stream; Algo Bot applies execution policy before any TradePlan is published.

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. Pinned by build, causality and interaction tests. Against the Python `trendline_reaction` it matched 6 of 8 oracle decisions, too few to prove parity.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
