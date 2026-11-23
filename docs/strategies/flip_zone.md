# Flip Zone


**Contract.** Restored to the frozen `flip_demand_zone_reaction` / `flip_supply_zone_reaction`: a zone carrying the flip source, unmitigated and entry-valid, anchored to a key level whose closed-bar role is broken resistance (BUY) or broken support (SELL), with a confirmed structural reaction **after** the structure break that made the zone: the break bar built it and is no reaction to it (Q2). An ordinary zone touch is not a flip. Of the 91 oracle decisions (`test/legacyparity`) 80 were the break bar itself "rejecting" the band it closed through; Go publishes the other 11, each identical to the oracle's decision (a strict subset, asserted bar for bar).

## Strategy ID / version

`flip_zone`, `v6`. Implemented in
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

Any symbol. The flip structure is read on M5, M15 and H1 (`structure_timeframes: [M5, M15, H1]`); the retest rejection, the stop and the entry are read on M5; M1 is execution only (the parser refuses it).

**Structure from M5, M15 and H1 (Q3, v6; owner rule 23 Nov 2026).** For each listed frame the flip level (its role broken by `breakout_accept_bars` closes of THAT frame), the break and the zone are that frame's primitives, so the age, the strength and the relevance of the zone are measured in that frame's bars and ATR. The confirmation is always an **M5** reaction: a rejection (`confirmReaction`) at or after the first M5 bar that opens once the bar that completed the flip zone has closed (on M5 itself, the zone's own bar index as before), so a higher-frame zone is never confirmed by a bar that was still forming when it was built. `require_confirmation: true` (shipped, the strategy's own switch, overriding the global `analysis.require_candle_confirmation: false`): a bare touch no longer publishes; there is no `candle_confirmation_optional` Flip Zone. The stop is the structure's far edge or the M5 reaction extreme, whichever is further out, plus `invalidation_buffer_atr` M5 ATRs (v4 rule, unchanged); the instrument stop envelope is unchanged. **One candidate per flip-level thesis** across frames (same direction, flip level within the wider of the two zone widths, found on different frames): `strategyutil.PreferHigherTimeframe`, i.e. the higher frame, then more stars, then the nearer entry; the others are counted as `duplicate_thesis`. The published `structure` is the zone on its own frame (`structure.timeframe` and `structure_timeframe`; evidence `structure_tf_m5`, `structure_tf_m15` or `structure_tf_h1`), and a close of THAT frame beyond the zone's far edge by more than `structure_close_buffer_atr` (shipped 0.1) ATRs of that frame withdraws it (`STRUCTURE_CLOSED_THROUGH`, `go_opportunity_structure_closed_through{strategy="flip_zone"}`); a wick that closes back inside never does.

Refusals are counted as `strategy_refused{strategy="flip_zone", structure_timeframe, reason}`: `no_m5_reaction` (a flip zone qualifies on its frame but no M5 rejection confirms it), `both_sides_confirmed`, `duplicate_thesis`, plus the existing detector reasons (`entry_zone_below_minimum_width`, ...). `strategy_published{strategy, structure_tf}` counts every opportunity by frame.

**What the replay showed (production bars, 23 Nov; 2,000 bars per timeframe, before = v5 at the Q2 tip, after = v6).**

| Instrument | v5 published | v6 published | identical | dropped (v5 only) | new (v6 only) | v6 by frame M5 / M15 / H1 | v6 stop (pips) by frame |
|---|---|---|---|---|---|---|---|
| XAU | 8 | 2 | 1 | 7 | 1 | 1 / 1 / 0 | 68.8 / 69.0 / - |
| EURUSD | 6 | 2 | 1 | 1 | 0 | 1 / 1 / 0 | 2.2 / 2.8 / - |
| GBPUSD | 20 | 3 | 3 | 2 | 0 | 2 / 1 / 0 | 3.0, 5.1 / 5.4 / - |
| USDJPY | 21 | 5 | 1 | 20 | 4 | 1 / 2 / 2 | 7.0 / 4.8, 11.1 / 5.3, 6.3 |
| GBPJPY | 15 | 3 | 1 | 13 | 2 | 1 / 1 / 1 | 7.6 / 8.7 / 12.8 |
| **all** | **70** | **15** | | | | | |

(The published stops here are before execution widens them to the instrument floor.) The fall is the confirmation requirement, not the frames: 64 of the 70 v5 setups were bare touches (`candle_confirmation_optional`) and 6 had a rejection; all 6 v5 setups that had a rejection are published again with the identical entry and stop, and the 7 setups v6 adds are all M15 or H1 zones. `no_m5_reaction` is the dominant refusal in every instrument (XAU 76, EURUSD 38, GBPUSD 108, USDJPY 89, GBPJPY 76 zone-evaluations over the capture, M5 + M15 + H1); the higher frames supply 9 of the 15 published setups, 3 of them on H1 (2 USDJPY, 1 GBPJPY). One USDJPY M15 zone was withdrawn by a close of M15 beyond its far edge. Real XAU regression (`internal/replaycapture/flip_zone_multi_tf_test.go`, bars 14-18 Nov): the v5 bare touch of 18 Nov 23:10 UTC (BUY 3728.87-3733.87, stop 3727.17) is replaced by the confirmed M15 reading of the same zone at 23:25 (BUY 3728.99-3734.00, stop 3727.10); the touch of 08:50 that never got a rejection no longer publishes. A sample this small (15 setups in 5-7 days) proves nothing about the edge; it shows what is published and why the rest is refused.

## Required regime / structure / liquidity / location

- Zone `Kind == KindFlip`, on a listed frame.
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

**Reaction-anchored invalidation (v4).** A candidate with a confirmed reaction publishes the far edge of the structure or, if the bars from the touch to the confirmation traded beyond it, that extreme, in both cases one `invalidation_buffer_atr*ATR` further out (Buy: `min(structure low, lowest low touch..confirm) - buffer`; Sell: `max(structure high, highest high touch..confirm) + buffer`). A sweep or strong reclaim is confirmed by a wick that pierces the band, and a stop inside that wick has already been traded. The buffer is unchanged and has no pip floor; when the reaction's extreme set the price the candidate carries the evidence code `invalidation_beyond_reaction_extreme`. A resting candidate (no confirmation) keeps the structural price, and the fixed-reward:risk fallback target, the quality geometry and the published invalidation follow the new price. Decisions and entry are unchanged (`LEGACY_PARITY_PROVEN`); the invalidation differs from the frozen publisher by design.
- **Target**: nearest opposing-side pool.

**Instrument stop budget (v5).** A candidate whose structural stop (distance from the proximal entry edge to the invalidation above) exceeds the instrument stop cap (XAU 70, EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28 pips) is **not published**: refused, never tightened, and neither the entry nor the invalidation is moved to fit. One inside [floor, cap] carries `stop_budget_structural`; one below the floor carries `stop_budget_floor` and execution widens it.

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

Evidence codes: `structure_tf_<m5|m15|h1>`, `m5_flip_zone_<state>`, `m5_flip_zone_relevance_<relevance>`,
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
