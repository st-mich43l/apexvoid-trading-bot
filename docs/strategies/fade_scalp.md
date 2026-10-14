# Fade Scalp Strategy V2

> **Quality review (2026-10-14): RETAIN** (proposal, not applied). Technical PASS, execution PARTIAL, performance CONFLICTING, evidence WEAK. Scope: all instruments; EURUSD flagged for the owner (replay interval below zero, 2 production trades). See [ae-quality-certification.md](ae-quality-certification.md) and [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md). Independent review: pending.

`fade_scalp` is the independent equal-level sweep reversal thesis. It runs on
the engine's detector-contract frame. BUY requires an equal-low pool (a
sell-side pool with at least two touches); SELL an equal-high pool. The pool
must have a graded A/B sweep, the compat entry zone around the level must show
the shared closed-bar structural reaction, and the strict premium/discount
location must allow the direction. In chop the setup must sit at the correct
edge of the range and the grab must be Grade A. Swing-only (single-touch) pools
and Grade-C sweeps never qualify. When several levels qualify, the highest
confluence wins, then the nearest. The candidate carries the pool identity, grab
grade, reaction pattern, touch/confirmation timestamps and the nearest opposing
pool as its target.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
