# Order Block (Phase S7)

## Strategy ID / version

`order_block`, `v2`. Implemented in
`analysis-engine/internal/strategy/orderblock`. Algorithm versions:
`structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

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
- **Target**: nearest opposing-side pool, at least
  `minimum_target_distance_atr*ATR` from the block's own near edge.

## Expiry

`expiry_hours` after the block's own most recent real timestamp.

## Failure cases / anti-patterns

Below-threshold strength, wrong lifecycle state, not currently relevant,
no opposing liquidity, zero ATR — all reject outright.

## Quality components / evidence codes

`zone_strength_quality`, `relevance_quality`, `freshness_quality` — own
independently-computed values (source task §40), same dimension shape as
Supply/Demand's for the same underlying reason (both read `Strength`,
`Relevance`, `TouchCount` off a `zone.Zone`).

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

## Known old-engine false positive

`docs/analysis/strategy-v2-catalog.md` row 4 flags the legacy heuristic
risk directly: "last bearish candle before bullish move" alone is not a
sufficient OB definition. `internal/zone`'s own OB primitive requires the
origin-candle/displacement/structure-break relationship, which this
strategy inherits rather than re-deriving from a naive last-opposite-candle
heuristic.
