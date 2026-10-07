# Supply

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
this strategy's own `SETUP_EXPIRED` window, distinct from Algo Bot's
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

## Higher-timeframe zones

The tradable path is the confirmed reaction: the frozen technique publisher
reads the M5 frame's supply instances and a rejection on M5 bars (the resting
zone above is an observation only; Algo Bot executes confirmed reactions).
The frozen Python publishers read the M5 zone population alone. With
`higher_timeframes: [M15]` (set in `config/analysis.yml`) the M15 frame's own
supply zones, built by the same collection on M15 bars and ATR, are also
evaluated against the M5 closed bars: same reaction confirmation, entry
validity, entry clip and stop rules.

- A higher-timeframe zone is anchored to the M5 bar of the same open time (an
  origin older than the M5 window is dropped) and never joins the confluence
  bands, so every M5 decision is unchanged: it is a second, separate candidate
  (`technique:supply_demand:<side>:<origin>@M15`) carrying the extra evidence code
  `htf_zone_m15`, and arbitration treats it like any other same-thesis
  competitor.
- This is the one deliberate extension of the frozen contract; there is no
  Python oracle for it. Evidence: the replay of the 14-21 Sep and the 7 Oct XAU
  captures keeps every previous envelope byte-identical (none removed or
  changed) and adds M15 reactions only; that shows what is emitted, not whether
  it earns. Remove `higher_timeframes` to turn it off.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
