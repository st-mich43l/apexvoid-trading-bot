# Fair Value Gap (Phase S7)

## Strategy ID / version

`fvg`, `v2`. Implemented in `analysis-engine/internal/strategy/fvg`.
Algorithm versions: `structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`.

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
- An opposing-side liquidity pool exists at the required distance.
- `ctx.Volatility.ATR > 0`.

## Formation / trigger / confirmation

Owned by `internal/zone`. Direction read from `z.Side.Direction()`.

## Entry / invalidation / target

- **Entry**: the gap's own band.
- **Invalidation**: Buy → `Low - invalidation_buffer_atr*ATR`; Sell →
  `High + invalidation_buffer_atr*ATR`.
- **Target**: nearest opposing-side pool, at least
  `minimum_target_distance_atr*ATR` away.

## Expiry

`expiry_hours` after the gap's own most recent real timestamp.

## Failure cases / anti-patterns

Below-threshold strength, wrong lifecycle state, not currently relevant,
no opposing liquidity, zero ATR.

## Quality components / evidence codes

`gap_strength_quality`, `relevance_quality`, and — this strategy's own
independent reasoning, the opposite polarity from Supply/Demand/
OrderBlock's freshness dimension — `fill_quality`
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

## Known old-engine false positive

No specific documented false-positive note beyond the general
`TECHNIQUE_ONLY` primitive split (`docs/analysis/strategy-v2-catalog.md`
row 5) — the legacy detector conflated gap *geometry* with gap
*tradeability* in one function; V2 separates them so a filled-but-still-open
gap doesn't score identically to a fresh one.
