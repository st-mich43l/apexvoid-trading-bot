# Fair Value Gap


## Strategy ID / version

`fvg`, `v5`. Implemented in `analysis-engine/internal/strategy/fvg`.
Algorithm versions: `structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

**Reaction after formation (Q2).** A fair value gap is anchored on its third candle, so it exists only once that candle has closed. A touch or confirmation on that candle or earlier (the gap's own impulse candle, which spans the gap by construction) is its formation, not a reaction: the frozen publisher accepted it for 69% of the replayed confirmed FVG opportunities (480 of 695). The reaction must come on a later bar (`Instance.TouchableFrom`, the boundary `MarkMitigation` already counts touches from).

## Purpose / thesis

A still-valid, currently-relevant fair value gap is a standing
imbalance-fill reaction zone in whichever direction its own `Side`
implies. `internal/zone` owns the gap's geometry (bounds, direction,
origin, fill percentage, mitigation); this strategy owns tradeability
only.

## Supported instruments / timeframes

Any symbol; single timeframe `M5`.

## Required regime / structure / liquidity / location

- Zone `Kind == KindFVG` (never `KindIFVG` — inverse FVG is a distinct,
  deferred V2 identity per the catalog).
- `State` is `Fresh` or `Touched`.
- `Relevance` is `Immediate` or `Nearby`.
- `Strength >= minimum_strength`.
- `(High - Low) >= minimum_gap_atr*ATR` (a gap under the fraction of ATR is noise even when a large middle candle lifts its strength).
- An opposing-side liquidity pool exists at the required distance.
- `ctx.Volatility.ATR > 0`.

## Formation / trigger / confirmation

Owned by `internal/zone`. Direction read from `z.Side.Direction()`.

## Entry / invalidation / target

- **Entry**: the gap's own band.
- **Invalidation**: Buy → `Low - invalidation_buffer_atr*ATR`; Sell →
  `High + invalidation_buffer_atr*ATR`.

**Reaction-anchored invalidation (v3).** A candidate with a confirmed reaction publishes the far edge of the structure or, if the bars from the touch to the confirmation traded beyond it, that extreme, in both cases one `invalidation_buffer_atr*ATR` further out (Buy: `min(structure low, lowest low touch..confirm) - buffer`; Sell: `max(structure high, highest high touch..confirm) + buffer`). A sweep or strong reclaim is confirmed by a wick that pierces the band, and a stop inside that wick has already been traded. The buffer is unchanged and has no pip floor; when the reaction's extreme set the price the candidate carries the evidence code `invalidation_beyond_reaction_extreme`. A resting candidate (no confirmation) keeps the structural price, and the fixed-reward:risk fallback target, the quality geometry and the published invalidation follow the new price. Measured on the committed captures in [reaction-anchored-stop.md](reaction-anchored-stop.md). Decisions and entry are unchanged (`LEGACY_PARITY_PROVEN`); the invalidation differs from the frozen publisher by design.
- **Target**: nearest opposing-side pool, at least
  `minimum_target_distance_atr*ATR` away.

**Instrument stop budget (v4).** A candidate whose structural stop (distance from the proximal entry edge to the invalidation above) exceeds the instrument stop cap (XAU 70, EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28 pips) is **not published**: refused, never tightened, and neither the entry nor the invalidation is moved to fit. One inside [floor, cap] carries `stop_budget_structural`; one below the floor carries `stop_budget_floor` and execution widens it. Measured, with the Python side, in [legacy-stop-budget.md](legacy-stop-budget.md).

**Higher-timeframe structure (v5).** The setup is found on M15/H1 structure and confirmed and executed on M5 (`higher_timeframes: [M15, H1]`). The higher frame's zones are judged as facts (spent, invalidated, width, fill and minimum, body and BOS), not by its last close; the M5 price decides whether the entry is valid. The M5 reaction confirms, the entry band is the proximal slice, and the stop is beyond the whole higher-timeframe zone and the M5 reaction extreme, plus `invalidation_buffer_atr` x the M5 detector ATR. The candidate carries `StructureTimeframe` and the evidence `htf_zone_<tf>`. Measured in [htf-zone-structure.md](htf-zone-structure.md).

## Expiry

`expiry_hours` after the gap's own most recent real timestamp.

## Failure cases / anti-patterns

Below-threshold strength, wrong lifecycle state, not currently relevant,
no opposing liquidity, zero ATR.

## Quality components / evidence codes

`fill_quality` only (`Overall` is that value; Q3 removed `gap_strength_quality` and
`relevance_quality`, constants of the admission rule — see
[quality](../analysis-engine.md#acceptance-and-quality)): this strategy's own
independent reasoning, the opposite polarity from Supply/Demand/
OrderBlock's freshness dimension
(`1.0 - 0.25*TouchCount`, floored at 0.1): for an FVG, `TouchCount` is a
**fill** signal, not a validation signal — more touches mean the
imbalance is more consumed, not more confirmed
(`test/strategy/fvg/fvg_test.go`'s
`TestFVG_MoreFillsLowersQualityRatherThanRaisingIt` asserts this directly
against `FlipZoneStrategy`'s opposite-polarity retest reasoning).

Evidence codes: `m5_fvg_<state>`, `m5_fvg_relevance_<relevance>`,
`m5_fvg_side_<side>`.

## Bullish / bearish examples

Both directions, driven by `Side`. Demand-side gap at 2020–2022 with a
buy-side pool at 2031–2032 → Buy. Supply-side gap at 2020–2022 with a
sell-side pool at 2010–2011 → Sell.

## Valid / invalid examples

Valid: either direction, fresh (`TouchCount=0`). Invalid: `State=Mitigated`,
`Kind=KindIFVG` (a different primitive entirely — must be ignored, not
guessed at), or a heavily-filled gap (still produces a candidate, but at
markedly lower quality — see `TestFVG_MoreFillsLowersQualityRatherThanRaisingIt`).

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([catalog](README.md#catalog)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument. Minimum gap 0.5 ATR (0.36 ATR gaps traded as noise on XAU).
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by execution eligibility, then freshness, then id trades
  (no quality score is part of the order) and the rest are suppressed ([execution](../execution.md#same-thesis-arbitration)).
