# Momentum Ride Strategy V2

> **Quality review (2026-10-14): RETAIN** (proposal, not applied). Technical PASS, execution COMPATIBLE, performance UNPROVEN, evidence INSUFFICIENT. Scope: all instruments; no production trades exist. See [ae-quality-certification.md](ae-quality-certification.md) and [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md). Independent review: pending.

`momentum_ride` runs on the engine's detector-contract frame. It requires a
non-chop regime, an allowing premium/discount location and a directional
strong-body close (`minimum_body_fraction`) through the latest opposite swing
on the current bar. The optional velocity/acceleration gate
(`require_momentum_va`) is off in the frozen production configuration. It
prefers the highest-scored entry-valid zone and falls back to the nearest
valid-side key level, then applies the shared qualification (entry distance,
valid-side level, confluence floor). The thesis is the break itself: there is no
shared reaction confirmation. The target is the nearest opposing pool at least
`minimum_target_distance_atr` away, otherwise that distance beyond the entry.
Identity anchors both the broken swing and the selected location source.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
