# Trendline Strategy V4

> **Quality review (2026-10-14): reworked in v4.** The audit found the v3 target was `entry +/- 2 x |entry - band edge|`,
> unrelated to the published stop (median room cover 0.05; technical RR below 1 in every setup). v4 publishes only with a real
> opposing-liquidity target; see "Target" below and [ae-quality-certification.md](ae-quality-certification.md). Independent review: pending.

**Contract.** Restored to the frozen V2 `trendline_reaction`: immutable causal anchors, forward validation, health (not tentative, broken, degraded, exhausted or stale), chop-regime quality gates, a reclaimed live interaction and a confirmed reaction inside the interaction band. Proven on 8 oracle decisions (`test/legacyparity`); the gate is narrow, so the golden holds few decisions.

`trendline` consumes immutable causal M5 anchors and the canonical interaction
classifier. A line needs the configured validation touches, a valid approach,
and a reclaimed support/resistance close. Entry is the interaction band,
invalidation is a close-violation buffer, and the target is the nearest opposing liquidity (see below).
Identity is the anchor pair, never evaluation time. Broken/exhausted lines and
testing without reclaim reject. It is enabled in the live Go opportunity
stream; Algo Bot applies execution policy before any TradePlan is published.

## Target (v4)

The target is the nearest unswept opposing liquidity pool at least `minimum_target_distance_atr` (1.0) ATR from
the entry's proximal edge. With none there is no technical objective, so nothing is published; there is no fixed
reward:risk fallback and no projection. v3 reported twice the distance from the entry to the interaction band edge, which
was unrelated to the published invalidation. Measured on the replay captures (16 setups): median technical RR 3.4 (v3: below
1 in every setup), median room cover against the executable stop 0.84 (v3: 0.05). `Quality.Overall` is unchanged
(`stars / 3`, always 1.0 here) and is a known weakness: changing it is arbitration-facing and needs its own impact report.

Parity: the frozen oracle took 8 decisions; v4 matches 7 and stays silent on the one with no opposing liquidity, so
`trendline_reaction` is measured only in `test/legacyparity` (as `session_level_reaction` is).

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. Pinned by build, causality and interaction tests. Against the Python `trendline_reaction` it matched 6 of 8 oracle decisions, too few to prove parity.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
