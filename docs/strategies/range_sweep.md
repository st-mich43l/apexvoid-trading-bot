# Range Sweep Strategy V2

> **Quality review (2026-10-14): RETIRE** (proposal, not applied). Technical FAIL, execution PARTIAL, performance NEGATIVE, evidence MODERATE. Scope: propose retirement of the strategy; keep historical attribution and journal decoding; do not delete trade data. See [ae-quality-certification.md](ae-quality-certification.md) and [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md). Independent review: pending.

`range_sweep` requires canonical M5 range state, then an M1 excursion beyond
one established edge by the configured ATR distance and a close back inside.
Entry is the reclaimed edge band, invalidation is beyond the M1 sweep and the
opposite range edge is the target. Identity anchors the M5 range and side.
No sweep/reclaim or non-range structure rejects. It is enabled in the live Go
opportunity stream; Algo Bot applies execution policy before any TradePlan is
published.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: XAU only.
- **Execution**: Trades on XAU only, with the M1 scalp stop book and ladder. Proven against the frozen scalp lane on real and synthetic M1 captures.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
