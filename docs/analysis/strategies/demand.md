# Demand (Phase S7)

## Strategy ID / version

`demand`, `v2`. Implemented in `analysis-engine/internal/strategy/demand`.
Algorithm versions: `structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

## Purpose / thesis

A still-valid, currently price-relevant canonical Demand zone is a
standing buy reaction zone — the buy-side mirror of `supply.md`'s thesis,
independently implemented (not shared code; see Known old-engine false
positive below for why that independence matters).

## Supported instruments / timeframes

Any symbol; single timeframe `M5`.

## Required regime / structure / liquidity / location

- Zone `Kind == KindDemand`.
- `State` is `Fresh` or `Touched`.
- `Relevance` is `Immediate` or `Nearby`.
- `Strength >= minimum_strength`.
- A `LiquidityBuySide` pool exists at least `minimum_target_distance_atr`
  above the zone's high.
- `ctx.Volatility.ATR > 0`.

## Formation / trigger / confirmation

Owned by `internal/zone`. This strategy re-evaluates structural validity
+ current relevance fresh on every call.

## Entry / invalidation / target

- **Entry**: the zone's own band.
- **Invalidation**: `zone.Low - invalidation_buffer_atr * ATR`.
- **Target**: the nearest `LiquidityBuySide` pool's high, at least
  `minimum_target_distance_atr * ATR` from entry.

## Expiry

`expiry_hours` after the zone's own most recent real timestamp.

## Failure cases / anti-patterns

Same shape as Supply's: below-threshold strength, no opposing liquidity,
zero ATR — all reject rather than fabricate a degraded candidate.

## Quality components / evidence codes

`zone_strength_quality`, `relevance_quality`, `freshness_quality` — the
same three dimension *names* as Supply recur here, but each strategy
computes its own values independently (source task §40); this is not
shared strategy code, just a coincidence of two theses reasoning about
comparable zone facts.

Evidence codes: `m5_demand_zone_<state>`, `m5_demand_zone_relevance_<relevance>`,
`m5_demand_zone_layer_<layer>`.

## Bullish / bearish examples

Demand is Buy-only. Example: a Fresh, Immediate, `Strength=0.8` demand
zone at 2020–2022 with a buy-side pool at 2031–2032 and ATR=1.0 produces
a Buy candidate with invalidation at 2019.5 and target 2032.

## Valid / invalid examples

Valid: as above. Invalid: `State=Invalidated`, `Relevance=Dormant`, or no
buy-side liquidity above (`test/strategy/demand/demand_test.go`).

## Known old-engine false positive

Same root cause as `supply.md`: the legacy `supply_demand_technique_reaction`
detector served both directions from one function. `DemandStrategy` must
never import `internal/strategy/supply` or vice versa — enforced
structurally by `test/architecture/dependency_test.go`'s rank-7
strategy-subpackage rule, not just a naming convention.
