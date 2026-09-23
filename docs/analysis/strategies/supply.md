# Supply (Phase S7)

## Strategy ID / version

`supply`, `v2`. Implemented in `analysis-engine/internal/strategy/supply`.
Algorithm versions this thesis was validated against: `structure=v2`,
`liquidity=v1`, `zone=v1`, `config=3` (source task §13 — bumps
independently of those primitives' own versions).

## Purpose / thesis

A still-valid, currently price-relevant canonical Supply zone is a
standing sell reaction zone. `internal/zone` already decides zone
geometry (displacement-based origin, `Kind`/`Side`, lifecycle `State`,
`Relevance`); this strategy decides *tradeability* only — it never
re-derives zone geometry from raw candles.

## Supported instruments / timeframes

Any symbol `internal/zone`/`internal/liquidity` run for (XAU, non-JPY FX,
JPY FX — the strategy makes no symbol-scale assumption; entry/invalidation/
target are all computed from the zone's own price band and ATR, which are
already in the instrument's native price units). Single timeframe: `M5`
(`RequiredTimeframes()`).

## Required regime / structure / liquidity / location

- Zone `Kind == KindSupply`.
- Zone `State` is `Fresh` or `Touched` (never `PartiallyMitigated`,
  `Mitigated`, or `Invalidated`).
- Zone `Relevance` is `Immediate` or `Nearby` (never `Remote`/`Dormant`).
- Zone `Strength >= minimum_strength`.
- A `LiquiditySellSide` pool exists at least `minimum_target_distance_atr`
  below the zone's low (the technical objective — source task §48: never
  account-based TP sizing).
- `ctx.Volatility.ATR > 0` (every ATR-relative threshold is otherwise
  undefined).

## Formation / trigger / confirmation

Formation and confirmation are entirely owned by the `internal/zone`
primitive (displacement-based Supply detection, lifecycle state machine).
This strategy's own "trigger" is structural validity + current relevance,
re-evaluated fresh on every `Evaluate` call — it is a resting/limit
thesis, not a "price just touched it this bar" reaction.

## Entry / invalidation / target

- **Entry**: the zone's own band (`Low`..`High`).
- **Invalidation**: `zone.High + invalidation_buffer_atr * ATR` — a
  confirmed close beyond the zone's own far edge plus a buffer (a bare
  touch of the edge is not itself invalidation).
- **Target**: the nearest `LiquiditySellSide` pool's low, at least
  `minimum_target_distance_atr * ATR` from entry.

## Expiry

`expiry_hours` after the zone's own most recent real timestamp
(`LastTouchedAt` if more recent than `CreatedAt`, else `CreatedAt`) —
this strategy's own `SETUP_EXPIRED` window
(`docs/analysis/opportunity-lifecycle-v2.md`), distinct from Algo Bot's
execution-age policy.

## Failure cases / anti-patterns

- A weak/marginal zone (`Strength < minimum_strength`) is not a tradeable
  thesis — rejected outright, not down-weighted.
- A zone with no opposing liquidity target is rejected (no fabricated
  target).
- Zero ATR (a fresh symbol with no volatility reading yet) yields no
  candidates — every ATR-relative threshold is undefined otherwise.

## Quality components / evidence codes

`Quality.Components`: `zone_strength_quality` (`Strength` itself),
`relevance_quality` (1.0 Immediate / 0.6 Nearby), `freshness_quality`
(`1.0 - 0.15*TouchCount`, floored at 0.2 — a fresh zone is the cleanest
read, each touch erodes confidence). `Overall` is the unweighted mean.

Evidence codes: `m5_supply_zone_<state>`, `m5_supply_zone_relevance_<relevance>`,
`m5_supply_zone_layer_<layer>`.

## Bullish / bearish examples

Supply is Sell-only — see `demand.md` for the Buy-side sibling. A
qualifying example: a Fresh, Immediate, `Strength=0.8` supply zone at
2020–2022 with a sell-side liquidity pool at 2010–2011 and ATR=1.0
produces a Sell candidate with invalidation at 2022.5 and target 2010.

## Valid / invalid examples

Valid: as above. Invalid: the same zone but `State=Mitigated` (already
failed structurally), or `Relevance=Remote` (not currently near price),
or no liquidity pool below (`test/strategy/supply/supply_test.go` covers
all three).

## Known old-engine false positive

The legacy `supply_demand_technique_reaction` detector
(`detectors.py`) served both directions from one function
(`docs/analysis/strategy-v2-catalog.md` row 3) — a documented risk there
was direction bugs from shared conditional branches. `SupplyStrategy` and
`DemandStrategy` are fully independent Go packages (verified by
`test/architecture/dependency_test.go`'s strategy-isolation rank rule),
eliminating that class of bug by construction.
