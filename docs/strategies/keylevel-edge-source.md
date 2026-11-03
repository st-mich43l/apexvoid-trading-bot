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
