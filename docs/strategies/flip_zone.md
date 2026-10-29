# Flip Zone


**Contract.** Restored to the frozen `flip_demand_zone_reaction` / `flip_supply_zone_reaction`: a zone carrying the flip source, unmitigated and entry-valid, anchored to a key level whose closed-bar role is broken resistance (BUY) or broken support (SELL), with a confirmed structural reaction **after** the structure break that made the zone: the break bar built it and is no reaction to it (Q2). An ordinary zone touch is not a flip. Of the 91 oracle decisions (`test/legacyparity`) 80 were the break bar itself "rejecting" the band it closed through; Go publishes the other 11, each identical to the oracle's decision (a strict subset, asserted bar for bar).

## Strategy ID / version

`flip_zone`, `v5`. Implemented in
`analysis-engine/internal/strategy/flipzone`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

## Purpose / thesis

A still-valid, currently-relevant flip zone is a standing role-reversal
reaction zone in whichever direction its own `Side` implies — the NEW
role after the flip, never the original one (the primitive only ever
exists post-flip). Two legacy detector functions
(`flip_demand_zone_reaction`/`flip_supply_zone_reaction`) map to this one
canonical V2 identity — direction-symmetric, one strategy, not two.

## Supported instruments / timeframes

Any symbol; single timeframe `M5`.

## Required regime / structure / liquidity / location

- Zone `Kind == KindFlip`.
- `State` is `Fresh` or `Touched`.
- `Relevance` is `Immediate` or `Nearby`.
- `Strength >= minimum_strength` (no positive-strength floor required —
  `minimum_strength` may legitimately be 0, unlike the other zone-anchored
  strategies; a flip's validity is really the primitive's own lifecycle
  state, not a strength score).
- An opposing-side liquidity pool exists at the required distance.
- `ctx.Volatility.ATR > 0`.

## Formation / trigger / confirmation

Owned by `internal/zone`. Direction read from `z.Side.Direction()`.

## Entry / invalidation / target

- **Entry**: the flip zone's own band.
- **Invalidation**: Buy → `Low - invalidation_buffer_atr*ATR`; Sell →
  `High + invalidation_buffer_atr*ATR` — a confirmed close back through
  the flipped level.

**Reaction-anchored invalidation (v4).** A candidate with a confirmed reaction publishes the far edge of the structure or, if the bars from the touch to the confirmation traded beyond it, that extreme, in both cases one `invalidation_buffer_atr*ATR` further out (Buy: `min(structure low, lowest low touch..confirm) - buffer`; Sell: `max(structure high, highest high touch..confirm) + buffer`). A sweep or strong reclaim is confirmed by a wick that pierces the band, and a stop inside that wick has already been traded. The buffer is unchanged and has no pip floor; when the reaction's extreme set the price the candidate carries the evidence code `invalidation_beyond_reaction_extreme`. A resting candidate (no confirmation) keeps the structural price, and the fixed-reward:risk fallback target, the quality geometry and the published invalidation follow the new price. Measured on the committed captures in [reaction-anchored-stop.md](reaction-anchored-stop.md). Decisions and entry are unchanged (`LEGACY_PARITY_PROVEN`); the invalidation differs from the frozen publisher by design.
- **Target**: nearest opposing-side pool.

**Instrument stop budget (v5).** A candidate whose structural stop (distance from the proximal entry edge to the invalidation above) exceeds the instrument stop cap (XAU 70, EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28 pips) is **not published**: refused, never tightened, and neither the entry nor the invalidation is moved to fit. One inside [floor, cap] carries `stop_budget_structural`; one below the floor carries `stop_budget_floor` and execution widens it. Measured, with the Python side, in [legacy-stop-budget.md](legacy-stop-budget.md).

## Expiry

`expiry_hours` after the zone's own most recent real timestamp.

## Failure cases / anti-patterns

`State=Invalidated` (the flip itself failed), not currently relevant, no
opposing liquidity, zero ATR.

## Quality components / evidence codes

`flip_strength_quality`, `relevance_quality`, and — this strategy's own
reasoning, the opposite polarity from FVG's fill-erosion dimension —
`retest_quality`: 0 touches = 0.4 (unconfirmed, lower confidence until a
real retest happens), 1–2 touches = 1.0 (a successfully re-tested flip is
the strongest read), >2 touches = 0.5 (over-tested, the level is being
worn down). `test/strategy/flipzone/flipzone_test.go`'s
`TestFlipZone_AConfirmedRetestScoresHigherThanAnUnconfirmedFlip` asserts
this three-way ordering directly.

Evidence codes: `m5_flip_zone_<state>`, `m5_flip_zone_relevance_<relevance>`,
`m5_flip_zone_new_side_<side>`.

## Bullish / bearish examples

Both directions, driven by the post-flip `Side`. Demand-side (new role
after a bearish-to-bullish flip) at 2020–2022 with a buy-side pool above →
Buy. Supply-side (bullish-to-bearish flip) → Sell.

## Valid / invalid examples

Valid: 1–2 touches (confirmed retest), either direction. Invalid:
`State=Invalidated`, `Kind` is any other zone kind (e.g. a plain Order
Block, never treated as a flip).

## Status and execution

- **Certification**: `GO_NATIVE_VALIDATED` ([catalog](README.md#catalog)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. The Python predecessor (`flip_*_zone_reaction`) is not reproduced: same bar and side on 4 of 91 oracle decisions. Behaviour is pinned by its own contract tests only. One live XAU trade so far (a stop-out), too few to judge.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by execution eligibility, then freshness, then id trades
  (no quality score is part of the order) and the rest are suppressed ([execution](../execution.md#same-thesis-arbitration)).
