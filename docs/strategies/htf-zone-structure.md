# Zone strategies find their setup on M15/H1 structure, confirmed and executed on M5

Owner direction, 2026-10-24: setups are found on M15/H1 structure; M5 is used only to confirm and execute, or where a strategy's thesis genuinely needs M5. This change applies it to `supply`, `demand`, `order_block` and `fvg`
(versions supply v6, demand v6, order_block v5, fvg v5). It builds on the reaction-anchored stop (#810) and the stop budget (#811): a structural stop wider than the instrument cap is refused in the detector, so a wide H1 zone is never rescued downstream by a deeper entry.

## What changed

1. **OB and FVG are exported from the higher frames.** `higherTimeframeInstanceSource` exports `supply_demand`, `order_block` and `fvg` instances (was `supply_demand`); CRT, iFVG and everything else stay on the execution frame. Every instance carries its timeframe.
2. **Higher-timeframe structure is judged as a fact, not by the higher frame's last close.** Instances are built with `IgnoreProximity`: spent, invalidated, width cap, FVG fill and minimum, and the order block's body fraction and BOS still apply; the retest-proximity test is left to the M5
   `EntryValid` check in `TechniqueSource.publish`, where the trade is executed. Before, an H1 zone was offered only if the last CLOSED H1 bar closed in it or at its proximal edge, so an M5 wick into the zone in the middle of an hour found no instance.
3. **Configuration:** `higher_timeframes: [M15, H1]` for `supply`, `demand`, `order_block` and `fvg` (was `[M15]` for supply/demand and absent for OB/FVG). No per-instrument override.

Unchanged: the M5 decision for M5 structure (the M5 population is identical before and after: 3,876 candidates in both runs); the confirmation (the M5 reaction off the zone, bound `TouchableFrom`); the stop (the zone's full structural far edge and the reaction extreme, plus `invalidation_buffer_atr` x the **M5**
detector ATR); the entry band (proximal slice clipped by `fvg_entry_max_width_price`); targets; the M15 (250) and H1 (400) windows.

## Measurement

`analysis-engine/test/htfzones/measure_test.go` (`HTF_ZONES_OUT=<dir> go test ./test/htfzones -run TestMeasureHTFZones`) replays the 21 committed M5 captures. Before = the merge-base tree; after = this tree; the stop budget is off in every run so the stop distribution is whole (the budget is proven in
`test/stopbudget`). Candidates are distinct opportunities; stop distance is from the proximal entry edge to the published invalidation; "per symbol-day" divides by the distinct UTC days each symbol's captures cover. Full cells (per instrument too) in
[htf-zone-structure-results.json](htf-zone-structure-results.json). Nothing here is an outcome and nothing is tuned; no profit is implied.

### XAU (cap 70, floor 50)

| strategy | structure | before: n (per symbol-day) | after: n (per symbol-day) | stop pips median / p75 | floor / in budget / over cap | overlaps an M5 candidate (1h) | patterns (after) |
|---|---|---|---|---|---|---|---|
| supply | M5 | 243 (9.35) | 243 (9.35) | 94.6 / 125 | 6% / 16% / 78% | - | sweep_reclaim 87, wick_rejection 73, strong_reclaim 52, engulfing 27, rejection_choch 4 |
| supply | M15 | 157 (6.04) | 256 (9.85) | 123.3 / 153.7 | 0% / 4% / 96% | 44% | wick_rejection 89, sweep_reclaim 81, engulfing 61, strong_reclaim 16, rejection_choch 9 |
| supply | H1 | 0 (0) | 37 (1.42) | 268.6 / 339.6 | 0% / 0% / 100% | 30% | wick_rejection 14, engulfing 11, sweep_reclaim 10, rejection_choch 2 |
| demand | M5 | 243 (9.35) | 243 (9.35) | 97.5 / 121.7 | 4% / 13% / 84% | - | sweep_reclaim 93, wick_rejection 70, strong_reclaim 45, engulfing 31, rejection_choch 4 |
| demand | M15 | 182 (7) | 254 (9.77) | 142.9 / 177.2 | 1% / 4% / 94% | 46% | sweep_reclaim 115, wick_rejection 80, engulfing 44, strong_reclaim 10, rejection_choch 5 |
| demand | H1 | 0 (0) | 29 (1.12) | 518.2 / 519.7 | 0% / 0% / 100% | 48% | wick_rejection 14, sweep_reclaim 8, engulfing 6, rejection_choch 1 |
| order_block | M5 | 146 (5.62) | 146 (5.62) | 65.4 / 83.8 | 25% / 32% / 43% | - | sweep_reclaim 72, strong_reclaim 52, wick_rejection 17, engulfing 5 |
| order_block | M15 | 0 (0) | 82 (3.15) | 80.5 / 100.2 | 2% / 37% / 61% | 26% | sweep_reclaim 28, strong_reclaim 23, wick_rejection 17, engulfing 14 |
| order_block | H1 | 0 (0) | 9 (0.35) | 70.9 / 77.5 | 0% / 44% / 56% | 0% | sweep_reclaim 4, strong_reclaim 2, wick_rejection 2, rejection_choch 1 |
| fvg | M5 | 52 (2) | 52 (2) | 59.1 / 72.2 | 27% / 44% / 29% | - | sweep_reclaim 24, wick_rejection 20, engulfing 8 |
| fvg | M15 | 0 (0) | 204 (7.85) | 64 / 88.1 | 36% / 22% / 42% | 8% | wick_rejection 79, engulfing 51, sweep_reclaim 50, strong_reclaim 24 |
| fvg | H1 | 0 (0) | 37 (1.42) | 96.5 / 134.6 | 16% / 14% / 70% | 8% | strong_reclaim 14, wick_rejection 12, engulfing 6, sweep_reclaim 4, rejection_choch 1 |

### FX (EURUSD 20, GBPUSD 25, GBPJPY 35, USDJPY 28; floors 12 / 15 / 22 / 18)

| strategy | structure | before: n (per symbol-day) | after: n (per symbol-day) | stop pips median / p75 | floor / in budget / over cap | overlaps an M5 candidate (1h) | patterns (after) |
|---|---|---|---|---|---|---|---|
| supply | M5 | 1124 (12.77) | 1124 (12.77) | 9.6 / 13.7 | 88% / 11% / 1% | - | sweep_reclaim 425, strong_reclaim 262, wick_rejection 238, engulfing 186, rejection_choch 13 |
| supply | M15 | 813 (9.24) | 947 (10.76) | 16 / 23.8 | 48% / 40% / 12% | 65% | sweep_reclaim 326, wick_rejection 271, engulfing 230, strong_reclaim 88, rejection_choch 32 |
| supply | H1 | 0 (0) | 192 (2.18) | 28.3 / 38.1 | 12% / 26% / 61% | 60% | wick_rejection 65, engulfing 60, sweep_reclaim 50, strong_reclaim 15, rejection_choch 2 |
| demand | M5 | 1107 (12.58) | 1107 (12.58) | 9.1 / 13.3 | 89% / 10% / 1% | - | sweep_reclaim 374, wick_rejection 273, strong_reclaim 259, engulfing 192, rejection_choch 9 |
| demand | M15 | 1006 (11.43) | 1162 (13.2) | 15.5 / 28.5 | 49% / 28% / 23% | 53% | sweep_reclaim 435, wick_rejection 353, engulfing 222, strong_reclaim 125, rejection_choch 27 |
| demand | H1 | 0 (0) | 252 (2.86) | 31.2 / 43.8 | 4% / 26% / 70% | 56% | sweep_reclaim 102, wick_rejection 82, engulfing 63, rejection_choch 5 |
| order_block | M5 | 694 (7.89) | 694 (7.89) | 6.9 / 10.2 | 98% / 2% / 0% | - | strong_reclaim 259, sweep_reclaim 242, wick_rejection 104, engulfing 85, rejection_choch 4 |
| order_block | M15 | 0 (0) | 415 (4.72) | 8.8 / 12.3 | 91% / 5% / 3% | 32% | sweep_reclaim 153, wick_rejection 104, strong_reclaim 87, engulfing 63, rejection_choch 8 |
| order_block | H1 | 0 (0) | 77 (0.88) | 29 / 30.4 | 38% / 12% / 51% | 0% | sweep_reclaim 37, wick_rejection 18, strong_reclaim 14, engulfing 7, rejection_choch 1 |
| fvg | M5 | 267 (3.03) | 267 (3.03) | 5.6 / 7.9 | 100% / 0% / 0% | - | wick_rejection 138, engulfing 73, sweep_reclaim 49, rejection_choch 7 |
| fvg | M15 | 0 (0) | 684 (7.77) | 8.4 / 12.6 | 92% / 8% / 1% | 20% | wick_rejection 279, engulfing 197, sweep_reclaim 143, strong_reclaim 58, rejection_choch 7 |
| fvg | H1 | 0 (0) | 172 (1.95) | 16.8 / 22.7 | 60% / 28% / 12% | 6% | wick_rejection 59, sweep_reclaim 42, engulfing 39, strong_reclaim 29, rejection_choch 3 |

### Higher-timeframe candidates by instrument (after)

| instrument | structure | after: n | per symbol-day | stop pips median / p75 | floor / in budget / over cap | overlaps M5 |
|---|---|---:|---:|---|---|---|
| XAU | M15 | 796 | 30.62 | 111.4 / 148.9 | 10% / 12% / 78% | 34% |
| XAU | H1 | 112 | 4.31 | 261.6 / 339.2 | 5% / 8% / 87% | 25% |
| EURUSD | M15 | 718 | 32.64 | 9.4 / 13.2 | 67% / 24% / 9% | 55% |
| EURUSD | H1 | 117 | 5.32 | 24 / 27.9 | 14% / 27% / 59% | 48% |
| GBPUSD | M15 | 786 | 35.73 | 10.5 / 14.4 | 78% / 18% / 3% | 47% |
| GBPUSD | H1 | 157 | 7.14 | 23.9 / 26.4 | 24% / 39% / 36% | 36% |
| GBPJPY | M15 | 777 | 35.32 | 23.4 / 30.2 | 45% / 37% / 18% | 38% |
| GBPJPY | H1 | 164 | 7.45 | 36.4 / 48 | 20% / 27% / 52% | 38% |
| USDJPY | M15 | 927 | 42.14 | 15.2 / 21.8 | 62% / 19% / 19% | 48% |
| USDJPY | H1 | 255 | 11.59 | 29.8 / 36.6 | 31% / 13% / 56% | 36% |

### The proximity fix on its own

- M15 supply/demand confirmed candidates: **2,158 before**, **2,701 with the proximity fix alone** (`higher_timeframes` kept at [M15]; +543, +25%), 2,619 with H1 added as well.
  707 of the 2,701 are on zones the old last-close test would not have offered (the rest replace candidates the old test offered under a different best-of selection).
  This changes the existing M15 S/D population: the owner should read the M15 rows above as the new population.
- Across all four strategies and both frames, **1,618 higher-timeframe candidates are on zones the old test would not have offered.**
- Adding H1 lowers the M15 candidate count (4,104 to 4,004): each strategy takes the single best higher-timeframe decision per technique (stars, then distance), so a better H1 zone replaces an M15 one. That selection is existing behaviour and is not changed here.

### M15 ageing (the 250-bar window)

Replaying the M15 frame with a 1000-bar window instead of 250: 1,893 distinct M15 zones are offered with the production window and 1,855 with 1000 bars (identity = timeframe, technique, side, origin time);
**only 1 zone is live in the wide window and absent from the production one** (1 supply_demand); H1 (400 bars): 0. The zones the 250-bar window drops are almost always spent or invalidated by then. The wide window does produce
more M15 candidates (4,351 against 4,004); that difference is not aged zones but the window-dependent chain and the higher-timeframe bias the window also moves (the reason the window is not changed here). Evidence for a later window decision, nothing more.

### Overlap with M5 candidates

The "overlaps an M5 candidate" column is the share of higher-timeframe candidates that coincide, same strategy and side with an overlapping entry band, with an M5-structure candidate confirmed within an hour: the thesis overlap the arbitration and exposure layers will see.

## What the numbers say (descriptive)

- **XAU:** 174 M15 and 15 H1 candidates are within the 50-70 pip budget (published); the rest are over the cap or below the floor. FX higher-timeframe candidates are mostly below the floor (published as before) or over the cap (refused).
- The research basis (replay figures from the timeframe research, not a reason to expect profit): XAU M15 structure with M5 confirmation and a 50-70 pip stop +0.46 R above random (n=38, [+0.06, +0.99]); FX H1 structure with M5 confirmation -0.16 R (n=162, [-0.36, +0.03]). That is why the per-instrument table is the decision input for a per-instrument timeframe choice, which this change does not make.

## Observability

The existing `htf_zone_<tf>` evidence and `StructureTimeframe` carry the timeframe through Go. Python counts `go_structure_timeframe` (dimensions `strategy`, `timeframe`; `M5` when the opportunity carries none) and the Go evaluation counters (`go_invalidation_reaction_anchored`, `_too_wide`, `go_stop_budget_*`) gain the same `timeframe` dimension; see `docs/redis-contract.md`.

## Parity and causality

The parity probes build their `TechniqueSource` from Break & Retest's legacy settings, which carry no `higher_timeframes`, so they read the M5 population only, exactly as the frozen oracle did; nothing needed overriding. `test/techniqueparity`, `test/keylevelparity`, `test/legacyparity` and `test/detectorparity` pass untouched and
**no parity golden was regenerated**. Higher-timeframe instances are bound causally by `boundTouchableFrom`; the causality and prefix-invariance suites, including `TestNoStrategyUsesFutureBars`, pass unchanged.

## Deferred (not in this change)

- **Per-instrument timeframe choice** (for example XAU [M15, H1] and FX [M15]): the owner's decision from the per-instrument numbers above.
- **The M15 window size.**
- **iFVG** from M15/H1, since it is its own detector.
- **Key Level, flip zone, trendline and break & retest** on M15/H1 structure.
- **Higher-timeframe targets:** opposing M15/H1 liquidity instead of the nearest M5 pool.
- **Nesting M5 candidates under an enclosing higher-timeframe zone as one thesis.**
