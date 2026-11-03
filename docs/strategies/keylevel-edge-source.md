# Key Level: is the edge in the level or in the reaction? (PR-K1)

Status: **pre-registered**. The numbers have not been run when this file is committed; the results are added in a later commit of the same PR.

## The question

`key_level` reads `LegacyFrame.SwingLevels` (zigzag-filtered fractals clustered within 0.5 x the median window ATR). Measured against random levels on real XAU prices
that definition adds nothing at any frame (M5: +0.8 +/- 2.1 pp bounce, -1.5 +/- 2.4 pp break), its bands cover about half the recent price range, and the owner's sweep-first
replay (`keylevel-sweep-first.md`) found swept reactions +0.3 to +0.5 R above random entries. So either the edge is in the **level** or in the **reaction** (the sweep and
the confirmation) the strategy requires at it. This PR separates the two with the strategy's own logic and costs, and changes no production behaviour.

## The test-only hook

`replaycapture.Options.KeyLevelSource func(frame *LegacyFrame, seed int64) []techniquezone.Level` replaces the levels `key_level` reads, for that strategy's evaluation only
(the frame and every other strategy keep theirs). `seed` is the latest closed bar's open time. No production package sets it (`test/architecture`).

## Variants (each runs the unchanged v6 strategy)

| Variant | Level source |
|---|---|
| A, live | `SwingLevels` as in production. |
| B, swing areas | Every zigzag swing of the window as its own level: price = the swing, band = 0.5 x median window ATR, kind `reaction`; `minimum_touches` 1; the touches of every level fixed at A's median so the touches confluence factor hands out no stars. |
| C, random | Per evaluation as many levels as A has, prices uniform in the window's [low, high], A's band, A's median touches, `minimum_touches` 1; 20 seeds, seed = hash(capture, bar time, i). |
| D, v7 preview | The PR-K2 definition: cluster width 0.25 x median ATR, round step 10 (>= 2 swings within max(tol, 0.25 ATR at the swing)), dedupe to the stronger member's price (never an average), origin kind (majority lows -> `support`, highs -> `resistance`, tie -> `reaction`) which `keylevel.Role` and the strategy's direction logic already read; `minimum_touches` 2. |

## Outcomes

Every committed XAU M5 capture, first sight across overlaps, the `test/aequality` candle-causal simulation with costs (`oneR_cost`, `twoR_cost`, `ladder_cost`, XAU spread),
one candidate per level per 2 h (as in the sweep-first study), sweep rule on. Per variant: candidates; n, win rate at 1R, mean R at 1R / 2R / ladder with a symbol-day cluster
bootstrap 95 % CI; the edge over the matched random entries (same stop and ladder). By period: before 30 Sep, 1-11 Oct, 12-22 Oct, 23 Oct-3 Nov. Paired differences A - B,
A - C (mean over seeds, with the seed spread) and D - A. The table is printed even if it contradicts the research.

## Decision rule (pre-registered, before any number is computed)

| Case | Condition | Consequence |
|---|---|---|
| Level adds nothing | the 95 % CI of (A - C) on the ladder includes 0 **and** A - B < +0.05 R | The edge is the reaction. PR-K2 ships as hygiene; a later PR may simplify `key_level` to a sweep-reclaim of recent swing areas (owner decides). |
| Level adds value | A - C > 0 with the CI above 0 | PR-K2 must not lose it: D - A >= -0.05 R on the ladder, or PR-K2 stops and reports. |

Cases that fit neither row (for example the CI of A - C above 0 with A - B >= +0.05 R is "level adds value"; the CI of A - C including 0 with A - B >= +0.05 R, or the CI below 0)
are reported as such with the table and are the owner's call; no row is bent to fit them.

"Ladder" is `ladder_cost` (XAU 1R-4R ladder, constant spread). The unit of a difference is the mean R per opportunity; the CI resamples symbol-days.

## Results (added after the rule above was committed)

Run: `KEYLEVEL_EDGE_OUT=... go test ./test/aequality -run TestKeyLevelEdgeSource` (14 min on 16 cores); raw numbers in `keylevel-edge-source-results.json`. XAU only, the 8 committed XAU M5 captures (14 Sep - 3 Nov), first sight across overlaps, one candidate per level per 2 h, the `test/aequality` candle-causal simulation with the XAU spread, sweep rule on, mean R over filled trades, 95 % intervals = symbol-day cluster bootstrap (2000 draws). Periods: before 1 Oct (includes 30 Sep), 1-11 Oct, 12-22 Oct, 23 Oct-3 Nov.

### All periods

| Variant | candidates (published sightings -> after dedupe) | trades (ladder) | win rate at 1R | mean R at 1R | at 2R | at ladder | edge over matched random (ladder) |
|---|---|---|---|---|---|---|---|
| A live | 1707 -> 487 | 478 | 0.493 | -0.015 [-0.097, +0.068] | -0.096 [-0.228, +0.047] | -0.070 [-0.166, +0.032] | -0.014 [-0.113, +0.096] |
| B swing areas | 3595 -> 1019 | 1004 | 0.462 | -0.076 [-0.135, -0.019] | -0.115 [-0.207, -0.018] | -0.111 [-0.199, -0.019] | -0.050 [-0.147, +0.046] |
| D v7 preview | 1034 -> 269 | 265 | 0.509 | +0.019 [-0.118, +0.149] | +0.064 [-0.126, +0.249] | -0.016 [-0.166, +0.141] | +0.045 [-0.103, +0.192] |
| C random (mean of 20 seeds; seed sd 0.026 on the ladder) | ~1351 -> ~1342 per seed | ~1320 per seed | — | -0.078 | -0.139 | -0.096 [-0.175, -0.010] | -0.034 [-0.105, +0.043] |

Edge over matched random at 1R / 2R: A +0.063 [-0.016, +0.143] / -0.028 [-0.160, +0.113]; B -0.013 [-0.072, +0.046] / -0.039 [-0.140, +0.065]; D +0.092 [-0.043, +0.226] / +0.161 [-0.031, +0.342].

### Paired differences (mean R per trade; days resampled together)

| | at 1R | at 2R | at ladder |
|---|---|---|---|
| A - B (live minus every swing) | +0.062 [-0.021, +0.148] | +0.019 [-0.082, +0.137] | +0.041 [-0.050, +0.134] |
| A - C (live minus random levels, mean over seeds) | +0.064 [-0.024, +0.151] | +0.043 [-0.091, +0.184] | +0.027 [-0.075, +0.130] |
| D - A (v7 preview minus live) | +0.033 [-0.138, +0.204] | +0.160 [-0.055, +0.370] | +0.054 [-0.123, +0.239] |

### By period (ladder)

| Period | A trades, mean R | A - B | A - C | D - A | D trades, mean R |
|---|---|---|---|---|---|
| before 1 Oct | 76, -0.032 [-0.307, +0.245] | -0.014 [-0.247, +0.229] | +0.095 [-0.153, +0.379] | +0.032 [-0.589, +0.545] | 43, +0.000 [-0.426, +0.390] |
| 1-11 Oct | 115, -0.054 [-0.233, +0.184] | -0.040 [-0.206, +0.175] | -0.087 [-0.253, +0.135] | -0.062 [-0.359, +0.170] | 62, -0.116 [-0.392, +0.146] |
| 12-22 Oct | 165, -0.103 [-0.240, +0.066] | +0.105 [-0.096, +0.289] | +0.058 [-0.092, +0.212] | +0.262 [-0.039, +0.627] | 98, +0.159 [-0.100, +0.469] |
| 23 Oct - 3 Nov | 122, -0.064 [-0.261, +0.176] | +0.063 [-0.033, +0.222] | +0.138 [+0.032, +0.316] | -0.139 [-0.432, +0.064] | 62, -0.203 [-0.391, -0.058] |

### Which row of the decision rule

**"Level adds nothing" fires.** The 95 % interval of A - C on the ladder is [-0.075, +0.130] and includes 0, and A - B is +0.041 R, under the +0.05 R line. As pre-registered, the consequence is: the edge is the reaction; PR-K2 ships as hygiene; a later PR may simplify `key_level` to a sweep-reclaim of recent swing areas, and the owner decides.

### What the table does and does not say

- **No variant, the live one included, beats matched random entries here.** A's edge over random is -0.014 R [-0.113, +0.096] on the ladder (+0.063 [-0.016, +0.143] at 1R); B -0.050, C -0.034, D +0.045, every interval includes 0. A's mean R is negative on the ladder in every period. So "the edge is the reaction" is the row that the rule selects, but these data do not show an edge in the reaction either: a sweep-reclaim at random levels (C, -0.096 R) does about as well as at the live levels (A, -0.070 R).
- **The sweep-first result is not reproduced under this simulator.** `keylevel-sweep-first.md` reports swept reactions +0.32 [+0.15, +0.54] R above random entries for 12-22 Oct (124 candidates); this run gives A -0.026 [-0.198, +0.186] (165 candidates) for the same period. The XAU executed stop (envelope 50 / 70 pips) and the 1R-4R ladder are the same; what differs, and which of it explains the gap, was not isolated here: the engine state, the XAU spread cost, the random-entry design (20 draws there, 20 per opportunity here with another seed), and the candidate count (165 vs 124). Until that is reconciled the +0.3 to +0.5 R of the sweep-first study should be treated as unconfirmed.
- **Power is low.** The interval of A - C is about +/- 0.10 R wide; an effect of a few hundredths of an R from the level cannot be seen in 478 trades. "Includes 0" is absence of evidence, not evidence of absence. The one period whose A - C interval excludes 0 (23 Oct - 3 Nov, +0.138 [+0.032, +0.316], n = 122) is one of four and was not pre-registered; it is reported, not used.
- **C is not a like-for-like sample size.** Random levels are redrawn at every evaluation, so the same market situation produces a new level id and a new candidate each time: C yields about 1,320 trades per seed against A's 478, and its trades are strongly correlated. The mean is per trade and the interval resamples days, which accounts for that dependence, but the counts should not be compared.
- **D (the v7 preview)** trades 265 times against A's 478 (it publishes fewer, more selective candidates: a narrower band and a stricter round step); D - A is +0.054 [-0.123, +0.239] on the ladder and +0.160 [-0.055, +0.370] at 2R. Nothing here says v7 loses what A has (the guard of the other row does not apply), and nothing says it adds.
- **B** (every zigzag swing as a level) is worse than A at 1R (-0.076 [-0.135, -0.019]); its edge over random still includes 0.

### Forward labels

Every published `key_level` candidate now carries `level_kind`, `level_round_class`, `level_origin`, `level_age_bars`, `level_band_atr` and `level_coverage` as measurements (see `key_level.md`). `TestKeyLevelForwardLabelsArePresentFiniteAndInRange` asserts them on every candidate of the committed captures. They reach `auto_trade_plan_snapshots.quality_components` (Go components -> Kafka `quality.components` -> `match.quality_components` -> the plan snapshot row); a candidate that never becomes a published plan has no row. No join was built. The decision to cut outcomes by these labels waits for at least 150 labelled outcomes per cell.
