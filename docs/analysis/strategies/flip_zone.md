# Flip Zone (Phase S7)

## Strategy ID / version

`flip_zone`, `v2`. Implemented in
`analysis-engine/internal/strategy/flipzone`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

## Purpose / thesis

A still-valid, currently-relevant flip zone is a standing role-reversal
reaction zone in whichever direction its own `Side` implies — the NEW
role after the flip, never the original one (the primitive only ever
exists post-flip). Two legacy detector functions
(`flip_demand_zone_reaction`/`flip_supply_zone_reaction`) map to this one
canonical V2 identity (`docs/analysis/strategy-v2-catalog.md` row 10).

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
- **Target**: nearest opposing-side pool.

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

## Known old-engine false positive

The legacy Python side already had two separately-named detector
functions resolving to one canonical name
(`flip_demand_zone_reaction`/`flip_supply_zone_reaction` →
`FLIP_ZONE`) — confirming this really is one direction-symmetric strategy,
not two, and that a naive treatment risking two divergent implementations
was already a real historical risk this rebuild closes by construction
(one Go package, one `Side`-branching `Evaluate`).
