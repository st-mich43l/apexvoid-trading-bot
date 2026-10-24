# Order Block

> **Quality review (2026-10-14): RETAIN_RESTRICTED** (proposal, not applied). Technical PASS, execution PARTIAL, performance NEGATIVE, evidence MODERATE. Scope: propose observe-only on EURUSD and GBPJPY; XAU and USDJPY unchanged. See [ae-quality-certification.md](ae-quality-certification.md) and [ae-strategy-retirement-review.md](ae-strategy-retirement-review.md). Independent review: pending.

## Strategy ID / version

`order_block`, `v4`. Implemented in
`analysis-engine/internal/strategy/orderblock`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

**Reaction after formation (Q2).** An order block exists once the break of structure that qualifies it has closed (`Zone.BreakIndex`); a touch or confirmation on bars up to that break is its formation, not a reaction to it (20 of 549 replayed confirmed Order Block opportunities).

## Purpose / thesis

A still-valid, currently-relevant Order Block zone is a standing reaction
zone in whichever direction its own `Side` implies
(`zone.Side.Direction()` — Demand-side OB expects a bounce/buy,
Supply-side OB expects a rejection/sell). `internal/zone` already decides
what an order block IS (origin candle/displacement/structure-break
relationship, not "last opposite candle before a move" alone); this
strategy decides tradeability only.

## Supported instruments / timeframes

Any symbol; single timeframe `M5`.

## Required regime / structure / liquidity / location

- Zone `Kind == KindOrderBlock`.
- `State` is `Fresh` or `Touched`.
- `Relevance` is `Immediate` or `Nearby`.
- `Strength >= minimum_strength`.
- An opposing-side liquidity pool exists at the required distance
  (`LiquidityBuySide` for a Demand-side block, `LiquiditySellSide` for a
  Supply-side block).
- `ctx.Volatility.ATR > 0`.

## Formation / trigger / confirmation

Owned by `internal/zone`. Direction is read from `z.Side.Direction()`,
never re-derived.

## Entry / invalidation / target

- **Entry**: the block's own band.
- **Invalidation**: Buy → `Low - invalidation_buffer_atr*ATR`; Sell →
  `High + invalidation_buffer_atr*ATR`.

**Reaction-anchored invalidation (v3).** A candidate with a confirmed reaction publishes the far edge of the structure or, if the bars from the touch to the confirmation traded beyond it, that extreme, in both cases one `invalidation_buffer_atr*ATR` further out (Buy: `min(structure low, lowest low touch..confirm) - buffer`; Sell: `max(structure high, highest high touch..confirm) + buffer`). A sweep or strong reclaim is confirmed by a wick that pierces the band, and a stop inside that wick has already been traded. The buffer is unchanged and has no pip floor; when the reaction's extreme set the price the candidate carries the evidence code `invalidation_beyond_reaction_extreme`. A resting candidate (no confirmation) keeps the structural price, and the fixed-reward:risk fallback target, the quality geometry and the published invalidation follow the new price. Measured on the committed captures in [reaction-anchored-stop.md](reaction-anchored-stop.md). Decisions and entry are unchanged (`LEGACY_PARITY_PROVEN`); the invalidation differs from the frozen publisher by design.
- **Target**: nearest opposing-side pool, at least
  `minimum_target_distance_atr*ATR` from the block's own near edge.

**Instrument stop budget (v4).** A candidate whose structural stop (distance from the proximal entry edge to the invalidation above) exceeds the instrument stop cap (XAU 70, EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28 pips) is **not published**: refused, never tightened, and neither the entry nor the invalidation is moved to fit. One inside [floor, cap] carries `stop_budget_structural`; one below the floor carries `stop_budget_floor` and execution widens it. Measured, with the Python side, in [legacy-stop-budget.md](legacy-stop-budget.md).

## Expiry

`expiry_hours` after the block's own most recent real timestamp.

## Failure cases / anti-patterns

Below-threshold strength, wrong lifecycle state, not currently relevant,
no opposing liquidity, zero ATR — all reject outright.

## Quality components / evidence codes

`freshness_quality` only (`Overall` is that value). Q3: `zone_strength_quality` (it republished `Strength = 1`) and `relevance_quality` (a constant for `Immediate`) are no longer published: they described the detector's admission rule, not how good this zone is, and made `Overall` look like a measurement. See [q3-quality-validation.md](q3-quality-validation.md).

Evidence codes: `m5_order_block_<state>`, `m5_order_block_relevance_<relevance>`,
`m5_order_block_side_<side>`.

## Bullish / bearish examples

Both directions, driven by `Side`. Demand-side example: block at
2020–2022, buy-side pool at 2031–2032, ATR=1.0 → Buy, invalidation 2019.5.
Supply-side example: block at 2020–2022, sell-side pool at 2010–2011 →
Sell, invalidation 2022.5.

## Valid / invalid examples

Valid: either direction as above. Invalid: `Kind != KindOrderBlock` (e.g.
a Breaker zone), `State=Mitigated`, `Strength` below floor
(`test/strategy/orderblock/orderblock_test.go`).

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
