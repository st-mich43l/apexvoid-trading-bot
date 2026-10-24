# Supply

> **Quality review (2026-10-14): adjusted in v3.** The audit found technical RR below 1 in most setups and a weak hypothetical result; v3 removes spent zones (below). The geometry finding is unchanged and deliberately not "fixed": it does not separate outcomes in the replay (see below). See [ae-quality-certification.md](ae-quality-certification.md). Independent review: pending.

## Spent zones (v3)

A confirmed reaction on a zone already touched more than `maximum_zone_touches` (2) times no longer publishes; the frozen Python publishers had no such limit (the parameter is opt-in, default 0 = unlimited, and the parity golden still proves the frozen contract with it off). Replay of the 2026-09-14 to 10-14 captures, hypothetical causal fills, per side and half (dev before 2026-10-07 / out of sample after): zones touched 3 or more times -0.06 / -0.21R; touched at most once +0.04 / +0.12R. Hypothetical mean over all filled setups moved from -0.01R to +0.03R, 95% interval [-0.10, +0.17]. The rule was chosen after looking at both halves, so the out-of-sample figure is a consistency check, not an independent test.

What this does not fix: Demand stays negative in the replay in every slice (fresh zones, either timeframe, either half); Supply is flat. A one-month, one-regime sample cannot say whether that is the detector or the month, so no further tuning was made. The technical-RR-below-1 geometry finding was left alone: technical RR does not separate outcomes in the replay (setups with RR below 1 averaged -0.07R, 2 or more -0.12R), so a filter on it would be a logic change without evidence.

**Reaction after formation (Q2).** The zone exists once its displacement leg has ended (`Zone.BreakIndex`); a touch or confirmation on the leg's own bars is its formation, not a reaction to it (222 of 1,742 replayed confirmed Supply opportunities were of that kind; a later valid reaction replaced 34).

## Strategy ID / version

`supply`, `v6`. Implemented in `analysis-engine/internal/strategy/supply`.
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

**Reaction-anchored invalidation (v4).** A candidate with a confirmed reaction publishes the far edge of the structure or, if the bars from the touch to the confirmation traded beyond it, that extreme, in both cases one `invalidation_buffer_atr*ATR` further out (Buy: `min(structure low, lowest low touch..confirm) - buffer`; Sell: `max(structure high, highest high touch..confirm) + buffer`). A sweep or strong reclaim is confirmed by a wick that pierces the band, and a stop inside that wick has already been traded. The buffer is unchanged and has no pip floor; when the reaction's extreme set the price the candidate carries the evidence code `invalidation_beyond_reaction_extreme`. A resting candidate (no confirmation) keeps the structural price, and the fixed-reward:risk fallback target, the quality geometry and the published invalidation follow the new price. Measured on the committed captures in [reaction-anchored-stop.md](reaction-anchored-stop.md). Decisions and entry are unchanged (`LEGACY_PARITY_PROVEN`); the invalidation differs from the frozen publisher by design.
- **Target**: the nearest `LiquiditySellSide` pool's low, at least
  `minimum_target_distance_atr * ATR` from entry.

**Instrument stop budget (v5).** A candidate whose structural stop (distance from the proximal entry edge to the invalidation above) exceeds the instrument stop cap (XAU 70, EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28 pips) is **not published**: refused, never tightened, and neither the entry nor the invalidation is moved to fit. One inside [floor, cap] carries `stop_budget_structural`; one below the floor carries `stop_budget_floor` and execution widens it. Measured, with the Python side, in [legacy-stop-budget.md](legacy-stop-budget.md).

**Higher-timeframe structure (v6).** The setup is found on M15/H1 structure and confirmed and executed on M5 (`higher_timeframes: [M15, H1]`). The higher frame's zones are judged as facts (spent, invalidated, width, fill and minimum, body and BOS), not by its last close; the M5 price decides whether the entry is valid. The M5 reaction confirms, the entry band is the proximal slice, and the stop is beyond the whole higher-timeframe zone and the M5 reaction extreme, plus `invalidation_buffer_atr` x the M5 detector ATR. The candidate carries `StructureTimeframe` and the evidence `htf_zone_<tf>`. Measured in [htf-zone-structure.md](htf-zone-structure.md).

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

`Quality.Components`: `freshness_quality`
(`1.0 - 0.15*TouchCount`, floored at 0.2 — a fresh zone is the cleanest
read, each touch erodes confidence). `Overall` is that value. Q3: `zone_strength_quality` (it republished `Strength = 1`) and `relevance_quality` (a constant for `Immediate`) are no longer published: they described the detector's admission rule, not how good this zone is, and made `Overall` look like a measurement. See [q3-quality-validation.md](q3-quality-validation.md).

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
