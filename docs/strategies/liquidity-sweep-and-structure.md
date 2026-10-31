# Liquidity sweep and structure break: evidence for FVG and order block (fvg v9, order_block v9)

Research of 31 Oct 2026 on master `a5fa868` (fvg v8, order_block v8), and the measurements of the pull request that acted on it. Two questions: is a liquidity sweep, or a structure break (BOS/CHoCH), a strong condition for an FVG or order block trade, and what does the engine do about it.

> **The research result did not replicate on the pull request's own measurement.** With this package's own sweep test and causal breaks, FVG setups **with** a prior sweep were *not* better than FVG setups without one at 1R. In the engine's real setups (gate off, 141 with / 46 without) the 1R mean R after spread was **-0.16 with a sweep against +0.13 without** (difference -0.29, p = 0.14, interval [-0.69, +0.17]); with room and extension relaxed (368 / 97) it was -0.12 against +0.02 (p = 0.27). At 2R the sign flips in the first sample (+0.02 against -0.13) and is flat in the second. None of this is significant, and none of it supports the gate. By owner decision the gate stays on and is listed under "decide after forward labels" (section 7). BOS/CHoCH: no split is better than another in either strategy; the order block BOS against CHoCH difference (+0.41 R at 2R, p = 0.06) points the other way from the research and is not significant either.

> **The research result did not replicate on the pull request's own measurement.** With this package's own sweep test and causal breaks, FVG setups **with** a prior sweep were *not* better than FVG setups without one at 1R. In the engine's real setups (gate off, 141 with / 46 without) the 1R mean R after spread was **-0.16 with a sweep against +0.13 without** (difference -0.29, p = 0.14, interval [-0.69, +0.17]); with room and extension relaxed (368 / 97) it was -0.12 against +0.02 (p = 0.27). At 2R the sign flips in the first sample (+0.02 against -0.13) and is flat in the second. None of this is significant, and none of it supports the gate. By owner decision the gate stays on and is listed under "decide after forward labels" (section 7). BOS/CHoCH: no split is better than another in either strategy; the order block BOS against CHoCH difference (+0.41 R at 2R, p = 0.06) points the other way from the research and is not significant either.

**Verdict of the research**

- **BOS/CHoCH:** the evidence does not support making it a requirement. In no test did a structure break improve results, and several tests point the other way.
- **Sweep:** the only candidate, and only for FVG, with weak evidence that is exposed to multiple comparisons.
- **Baseline:** every setup family stays at or below zero expectancy after spread.

**What the engine does (v9, one pull request, live on demo)**

| | fvg v9 | order_block v9 |
|---|---|---|
| prior liquidity sweep | **gate** (`require_prior_sweep: true`, refusal `no_prior_sweep`), by owner decision | measured evidence only |
| structure break (BOS/CHoCH) | measured evidence only, computed causally by the package | the block's definition, computed causally by the package; its kind is measured evidence |
| evidence | `m5_fvg_prior_sweep_<kind>`, `m5_fvg_break_bos` / `m5_fvg_break_choch` | `m5_order_block_prior_sweep_<kind>`, `m5_order_block_break_bos` / `m5_order_block_break_choch` |
| quality measures | `prior_sweep`, `sweep_depth_atr`, `sweep_reclaimed`, `break_kind` | `prior_sweep`, `sweep_depth_atr`, `break_kind` |
| counters | `strategy_refused{reason="no_prior_sweep"}`, `strategy_feature{feature="prior_sweep"\|"break_kind"}` | `strategy_feature{feature="prior_sweep"\|"break_kind"}` |

Neither fact is a confluence factor, a star, a score dimension or a ranking input. Turning the order block sweep or any BOS/CHoCH rule into a gate is deferred until at least 150 forward-labelled outcomes per cell exist.

## 1. What the literature says

| Source | Type | Finding | Bearing |
|---|---|---|---|
| Osler 2005, "Stop-loss orders and price cascades" (J. Int. Money & Finance; FRBNY SR 150) | peer-reviewed, FX high-frequency | Rates trend unusually fast after reaching stop-loss clusters. Stop-driven moves are larger and last longer than take-profit-driven ones; significant for hours. | Triggered stops **propagate** the move. A sweep is a continuation fuel unless price immediately fails back. |
| Osler 2000 / 2003 (FRBNY EPR; J. Finance) | peer-reviewed | Levels where orders rest interrupt trends; crossed levels accelerate them. | Same mechanism: take-profits reverse, stops continue. |
| Mahadzva 2026, "Smart Money or Costly Folklore?" (SSRN 7430998) | working paper, G10 FX, walk-forward | No evidence ICT/SMC rules work as taught. Three independent sweep constructions agree, in 65 of 66 fold selections, that a swept level predicts **continuation, not reversal**. | Directly contradicts "sweep, then reverse" as a standalone premise. |
| IndicatorEdge, EURUSD M1 2019-2025, 54 variants of sweep, structure shift, FVG | practitioner, look-ahead-free, costs | Every variant negative after 0.5 pip cost: expectancy -0.05 to -0.25 R, profit factor 0.63-0.89. | The full ICT chain did not produce an edge mechanically. |
| MPM Markets, ES/NQ/GC/SI 2019-2026, ~40k gaps | practitioner, 1-minute exit resolution | FVG reaction ~5 pp above random; no construction profitable after costs; 5m/15m worst, gold weakest. | A gap alone is weak; filters must add a lot to matter. |
| Brock, Lakonishok & LeBaron 1992 (J. Finance) | peer-reviewed, DJIA 1897-1986 | Trading-range breaks carried information (in that era and market). | Breaks matter as momentum, not as a reason to fade back into a zone. |
| Claims of 85-97 % win rates for "OB after sweep", PF 1.9 on 49 gold trades, and similar | vendor / educator pages | Small or unspecified samples, no costs, no out-of-sample. | Not evidence. |

## 2. Our own data

**Data:** all committed production captures (XAU, EURUSD, GBPUSD, USDJPY, GBPJPY; M5/M15/H1; roughly 21 Sep to 22 Oct 2026), merged per symbol.

**Features**, computed causally on the structure frame (M15/H1):

- **Sweep:** in the 6 bars up to the impulse's first candle, a bar trades through a confirmed, still-unswept swing low/high (2-bar fractal) or the previous day's low/high.
- **Sweep + reclaim:** the same bar also closes back.
- **BOS:** an impulse candle closes beyond the most recent confirmed swing. Split into BOS (with the prior structure) and CHoCH (against it). **External BOS** is beyond the extreme swing of the last 48 bars.

**Lenses**

- **A. Independent event study.** Every M15/H1 FVG (the repo's definition) and order block (no break requirement, so the break can be tested). First M5 touch within 24 h; limit at the proximal edge; stop = far edge +/- 0.5 M5 ATR; 1R and 2R; stop first on ambiguous bars; spread charged.
- **A'.** The same, but enter only after an M5 50 % wick rejection within 3 bars.
- **B. The engine's own published `fvg` / `order_block` opportunities** from the `test/aequality` causal simulation with costs: (B1) as configured; (B2) room and leg-extension refusals relaxed, for a larger sample.

**Expectancy in R after spread** (`yes` vs `no`; p = two-sided permutation test)

| Feature | Lens | FVG yes / no (n) | p | OB yes / no (n) | p |
|---|---|---|---|---|---|
| Sweep, 1R | A | -0.09 / -0.14 (396/318) | 0.46 | -0.04 / -0.20 (215/112) | 0.19 |
| Sweep, 1R | A' | -0.28 / -0.03 (114/73) | 0.09 | +0.30 / -0.04 (64/25) | 0.12 |
| Sweep, 1R | B1 | -0.03 / -0.21 (107/61) | 0.20 | -0.09 / +0.05 (92/80) | 0.43 |
| Sweep, 1R | B2 | -0.02 / -0.21 (264/157) | **0.04** | -0.11 / -0.13 (198/151) | 0.83 |
| Sweep, 2R | B2 | +0.17 / -0.36 | | -0.35 / -0.05 | |
| BOS, 1R | A | -0.12 / -0.08 (527/187) | 0.63 | -0.17 / +0.01 (196/131) | 0.11 |
| BOS, 1R | A' | -0.19 / -0.16 | 0.85 | +0.07 / +0.37 | 0.12 |
| External BOS, 2R | A | -0.24 / -0.04 (275/439) | 0.06 | -0.08 / -0.02 | 0.71 |
| BOS with trend, 1R | B2 | -0.27 / -0.05 (74/347) | 0.07 | -0.14 / -0.10 | 0.75 |
| CHoCH, 2R | B1 | -0.21 / +0.05 | | -0.44 / +0.19 | |
| Sweep & BOS, 1R | B2 | +0.05 / -0.17 (160/261) | **0.02** | -0.10 / -0.14 | 0.66 |

The sweep effect holds for 3- and 6-bar lookbacks and disappears at 12 bars, where about 75 % of events "have" a sweep. About 70 comparisons were run: at p < 0.05, three or four "significant" results are expected by chance, so these p-values are not proof.

## 3. What this means

1. **A structure break is not a strong condition here.** Every BOS variant (internal, external, with-trend, CHoCH) was neutral or worse. A gap or block printed by a big break is a pullback inside a stretched leg, and the first retest pays less. Order block keeps its break as part of the definition, but no test says that requirement adds value. CHoCH-born blocks were the worst cell at 2R in both engine runs.
2. **A prior sweep is the only candidate, and only for FVG.** Better in A and in B1/B2 (the engine's real setups), worse in A'. Mechanism, consistent with Osler: a sweep immediately reversed by a displacement is a failed stop cascade whose trapped stop-out flow is the fuel; a sweep followed by acceptance is a continuation (Mahadzva), which our candidates never trade because the displacement already went the other way.
3. **For order blocks the sweep did not help** in the engine's own setups (worse at 2R).
4. **Nothing is profitable on its own** after spread at 1R. The best engine cells reach about 0 R at 1R and +0.17 R at 2R. The sample is about a month of five instruments in one regime.

## 4. The sweep test (each package owns its copy)

For an impulse whose first candle is bar *s* of the structure frame *F* (an FVG: the gap's first candle; an order block: the first candle of the displacement leg that broke structure), in favour of a side. BUY is described; SELL mirrors.

- **Window:** bars `[s - sweep_lookback_bars, s]` of *F* (`sweep_lookback_bars: 6`). Every bar is closed at or before the decision bar.
- **Levels** a window bar *k* may sweep, each known before *k*:
  - `swing`: a fractal low of *F* (`sweep_pivot_bars: 2` each side), confirmed at or before *k - 1*, formed within `sweep_level_max_age_bars: 96` bars of *k*, and not traded below by any bar between its confirmation and *k*;
  - `pdl` (`pdh` for a SELL): the previous trading day's low, finalized, with `AvailableAt` at or before the open of bar *k* (from `frame.Sessions`, built from the full history);
  - `session`: a finalized Asia or London low (high) available before *k*.
- **Sweep:** some bar *k* has `low < level`. The earliest such bar decides the reported kind; on the same bar `pdl` outranks `session` outranks `swing`, and within a kind the deepest sweep wins. `sweep_depth_atr = (level - low_k) / ATR_F(k)`; `sweep_reclaimed = close_k > level`.
- **No look-ahead:** nothing after the decision bar is read; a swing confirmed after *k* is never a level for *k*.

A pdl/pdh or session level that price has been trading beyond for a long time counts as swept by every bar beyond it (the test does not ask that the level was untouched before the window, as it does for a swing). That is the construction the research measured; it is recorded here as a known property.

## 5. Causal structure breaks

Every BOS/CHoCH the zone strategies read today comes from `legacyread.Stage`: `techniquezone.StructureBreaks(swings, bars, false, ...)`, the look-ahead labelling. It reads nothing after the evaluated bar, so it is not a live future leak, but:

- **it dates breaks up to `fractal_n` bars early:** a break at bar *i* may use a swing whose right-hand bars close after *i*;
- **it labels BOS against CHoCH with today's trend:** the kind is classified with `MarketStructure(all swings)`, not with the trend as of bar *i*.

`fvg` and `order_block` now compute their own breaks (`breaks.go`, a copy of `techniquezone.breaksCausal`): a swing joins the reference set only at the bar that confirms it, a break is the first close beyond the most recent confirmed swing of the other side, and its kind is the structure of the swings confirmed before that bar. `order_block` uses them for the block definition, the `rejection_choch` confirmation on M5 and the structure score; a block exists from the close of the structure bar at which its break became knowable. `fvg` uses them only for the measured `m5_fvg_break_*` evidence (its quality `structure` score still reads the frame's shared breaks, unchanged).

`legacyread`'s shared `frame.Breaks` is not touched: supply, demand and confluence zone still read it for `rejection_choch` and get their own causal-break changes later.

## 6. Measurements of the pull request

Published setups and simulated outcomes. No claim of profit: the simulation is `test/aequality` (closed candles, causal fills, assumed constant spreads, single-leg entry at the proximal edge, the instrument's stop envelope).

### 6.1 Published setups: v8 against v9 (`test/sweepbos`)

Unique published setups over the 24 committed captures, on the same engine contexts, against the vendored v8 (`test/sweepbos/fvgv8`, `obv8`).

| symbol | structure timeframe | fvg v8 | fvg v9 | fvg v9 gate off | order_block v8 | order_block v9 |
|---|---|---:|---:|---:|---:|---:|
| EURUSD | H1 | 10 | 7 | 10 | 0 | 0 |
| EURUSD | M15 | 54 | 31 | 54 | 51 | 51 |
| GBPJPY | H1 | 15 | 10 | 15 | 51 | 51 |
| GBPJPY | M15 | 50 | 43 | 50 | 38 | 38 |
| GBPUSD | H1 | 26 | 15 | 26 | 10 | 10 |
| GBPUSD | M15 | 54 | 45 | 54 | 61 | 61 |
| USDJPY | H1 | 6 | 3 | 6 | 14 | 14 |
| USDJPY | M15 | 62 | 57 | 62 | 63 | 63 |
| XAU | H1 | 0 | 0 | 0 | 6 | 6 |
| XAU | M15 | 39 | 33 | 39 | 26 | 26 |
| **total** | | 316 | 244 | 316 | 320 | 320 |

order_block setups: in both 320, v8 only 0, v9 only 0

The refusals of fvg v9 (counted once per evaluated M5 bar a structure stays refused) include `no_prior_sweep`: 122 on M15 and 55 on H1, against 1,481,449 / 2,395,843 `gap_closed_through` and 136,947 / 187,663 `gap_small` of the gap definition. `fvg v9 gate off` publishes exactly the setups of v8 (asserted by the test), so the 72 setups the gate removes (316 to 244, -23%) are the cost of the gate; the sweep and break evidence is the only other difference.

### 6.2 Order blocks that moved because the breaks are causal

order blocks of the structure frames (M15, H1), unique over the replays: v8 406, v9 406

| | blocks |
|---|---:|
| identical formation | 406 |
| formed later (the break was knowable later) | 0 |
| formed earlier | 0 |
| no longer a block | 0 |
| newly a block | 0 |
| same formation, other kind (BOS/CHoCH) | 157 |

No block was formed earlier or later, none appeared or vanished: published order block setups are identical (320 against 320). That is what the geometry predicts: a close cannot exceed a fractal swing high inside its own right-hand bars, so a break dated at a swing's confirmation bar and one dated at the bar after the swing's formation coincide for fractal swings. The effect of the causal breaks on this data is the **kind**: 157 of 406 blocks (39%) are labelled differently (BOS against CHoCH), because the kind is now the structure as of the breaking bar and not the trend as of the frame's last bar.

One consequence: a block that exists only under the causal breaks would carry no zone score (the score is read from the frame's zones, which still use the shared breaks). None appeared in these captures.

### 6.3 Simulated outcomes (`test/aequality`, `algo-bot/tools/sweep_split.py`)

Hypothetical opportunity outcomes of the engine's published setups: closed candles, causal fills, assumed constant spreads, single-leg entry at the proximal edge. `win 1R` is the share of filled opportunities that reached the 1R target; the intervals are seeded cluster bootstraps (symbol and UTC day); `p` is the two-sided permutation p-value of the difference of the means (it ignores the clustering and is optimistic). About 36 comparisons are in the three tables below: at p < 0.05, two are expected by chance.

**As configured (v9, gate on):** every published FVG has a sweep, so the sweep split of FVG is empty; the order block and break splits are in the table.

**fvg**: 143 confirmed opportunities

| split | cell | n | filled | win 1R | mean R 1R [95%] | mean R 2R [95%] | mean R ladder [95%] |
|---|---|---:|---:|---:|---|---|---|
| all | all | 143 | 129 | 42% | -0.16 [-0.42, +0.13] | +0.02 [-0.33, +0.38] | -0.16 [-0.41, +0.12] |
| prior_sweep | sweep | 143 | 129 | 42% | -0.16 [-0.41, +0.12] | +0.02 [-0.33, +0.39] | -0.16 [-0.43, +0.12] |
| break_kind | BOS | 30 | 30 | 40% | -0.20 [-0.57, +0.17] | -0.04 [-0.62, +0.50] | -0.28 [-0.67, +0.16] |
| break_kind | CHoCH | 46 | 41 | 39% | -0.22 [-0.62, +0.24] | +0.02 [-0.58, +0.64] | -0.22 [-0.61, +0.24] |
| break_kind | no break | 67 | 58 | 45% | -0.10 [-0.53, +0.32] | +0.03 [-0.54, +0.69] | -0.06 [-0.49, +0.42] |
| break_kind test | BOS vs CHoCH / oneR_cost | | | | difference +0.02 [-0.58, +0.59] | p = 1.000 | |
| break_kind test | BOS vs CHoCH / twoR_cost | | | | difference -0.06 [-0.96, +0.74] | p = 1.000 | |
| break_kind test | BOS vs CHoCH / ladder_cost | | | | difference -0.05 [-0.67, +0.52] | p = 0.824 | |
| break_kind test | BOS vs no break / oneR_cost | | | | difference -0.10 [-0.59, +0.41] | p = 0.809 | |
| break_kind test | BOS vs no break / twoR_cost | | | | difference -0.07 [-0.98, +0.76] | p = 1.000 | |
| break_kind test | BOS vs no break / ladder_cost | | | | difference -0.22 [-0.78, +0.34] | p = 0.372 | |
| break_kind test | CHoCH vs no break / oneR_cost | | | | difference -0.12 [-0.69, +0.49] | p = 0.681 | |
| break_kind test | CHoCH vs no break / twoR_cost | | | | difference -0.01 [-0.87, +0.89] | p = 1.000 | |
| break_kind test | CHoCH vs no break / ladder_cost | | | | difference -0.17 [-0.77, +0.50] | p = 0.460 | |

**order_block**: 193 confirmed opportunities

| split | cell | n | filled | win 1R | mean R 1R [95%] | mean R 2R [95%] | mean R ladder [95%] |
|---|---|---:|---:|---:|---|---|---|
| all | all | 193 | 172 | 49% | -0.02 [-0.24, +0.25] | -0.06 [-0.37, +0.28] | -0.08 [-0.31, +0.18] |
| prior_sweep | no sweep | 47 | 41 | 44% | -0.12 [-0.59, +0.30] | -0.12 [-0.70, +0.46] | -0.27 [-0.73, +0.19] |
| prior_sweep | sweep | 146 | 131 | 50% | +0.01 [-0.27, +0.35] | -0.04 [-0.42, +0.41] | -0.03 [-0.30, +0.30] |
| break_kind | BOS | 98 | 84 | 54% | +0.07 [-0.26, +0.37] | +0.15 [-0.27, +0.54] | +0.07 [-0.28, +0.41] |
| break_kind | CHoCH | 95 | 88 | 44% | -0.11 [-0.45, +0.29] | -0.26 [-0.69, +0.36] | -0.24 [-0.54, +0.18] |
| prior_sweep test | sweep vs no sweep / oneR_cost | | | | difference +0.13 [-0.42, +0.71] | p = 0.479 | |
| prior_sweep test | sweep vs no sweep / twoR_cost | | | | difference +0.08 [-0.70, +0.88] | p = 0.836 | |
| prior_sweep test | sweep vs no sweep / ladder_cost | | | | difference +0.24 [-0.34, +0.89] | p = 0.236 | |
| break_kind test | BOS vs CHoCH / oneR_cost | | | | difference +0.18 [-0.33, +0.63] | p = 0.291 | |
| break_kind test | BOS vs CHoCH / twoR_cost | | | | difference +0.41 [-0.42, +1.04] | p = 0.057 | |
| break_kind test | BOS vs CHoCH / ladder_cost | | | | difference +0.31 [-0.31, +0.82] | p = 0.064 | |

**fvg v9 with `require_prior_sweep: false`** (the same population as v8, with the measures): the sweep split of FVG.

**fvg**: 187 confirmed opportunities

| split | cell | n | filled | win 1R | mean R 1R [95%] | mean R 2R [95%] | mean R ladder [95%] |
|---|---|---:|---:|---:|---|---|---|
| all | all | 187 | 168 | 45% | -0.10 [-0.31, +0.11] | -0.02 [-0.30, +0.29] | -0.12 [-0.34, +0.12] |
| prior_sweep | no sweep | 46 | 39 | 56% | +0.13 [-0.18, +0.40] | -0.13 [-0.56, +0.31] | +0.03 [-0.29, +0.38] |
| prior_sweep | sweep | 141 | 129 | 42% | -0.16 [-0.40, +0.10] | +0.02 [-0.32, +0.40] | -0.16 [-0.43, +0.12] |
| break_kind | BOS | 43 | 41 | 42% | -0.17 [-0.48, +0.14] | -0.13 [-0.60, +0.36] | -0.26 [-0.59, +0.11] |
| break_kind | CHoCH | 57 | 52 | 42% | -0.15 [-0.50, +0.20] | -0.02 [-0.53, +0.57] | -0.16 [-0.50, +0.23] |
| break_kind | no break | 87 | 75 | 49% | -0.01 [-0.34, +0.32] | +0.04 [-0.39, +0.52] | -0.01 [-0.38, +0.36] |
| prior_sweep test | sweep vs no sweep / oneR_cost | | | | difference -0.29 [-0.69, +0.17] | p = 0.142 | |
| prior_sweep test | sweep vs no sweep / twoR_cost | | | | difference +0.14 [-0.47, +0.73] | p = 0.645 | |
| prior_sweep test | sweep vs no sweep / ladder_cost | | | | difference -0.19 [-0.65, +0.26] | p = 0.321 | |
| break_kind test | BOS vs CHoCH / oneR_cost | | | | difference -0.02 [-0.48, +0.44] | p = 1.000 | |
| break_kind test | BOS vs CHoCH / twoR_cost | | | | difference -0.11 [-0.86, +0.56] | p = 0.815 | |
| break_kind test | BOS vs CHoCH / ladder_cost | | | | difference -0.10 [-0.54, +0.36] | p = 0.646 | |
| break_kind test | BOS vs no break / oneR_cost | | | | difference -0.16 [-0.57, +0.26] | p = 0.441 | |
| break_kind test | BOS vs no break / twoR_cost | | | | difference -0.17 [-0.81, +0.54] | p = 0.587 | |
| break_kind test | BOS vs no break / ladder_cost | | | | difference -0.25 [-0.68, +0.21] | p = 0.234 | |
| break_kind test | CHoCH vs no break / oneR_cost | | | | difference -0.14 [-0.61, +0.35] | p = 0.476 | |
| break_kind test | CHoCH vs no break / twoR_cost | | | | difference -0.06 [-0.74, +0.68] | p = 0.829 | |
| break_kind test | CHoCH vs no break / ladder_cost | | | | difference -0.15 [-0.65, +0.37] | p = 0.442 | |

**order_block**: 193 confirmed opportunities

| split | cell | n | filled | win 1R | mean R 1R [95%] | mean R 2R [95%] | mean R ladder [95%] |
|---|---|---:|---:|---:|---|---|---|
| all | all | 193 | 172 | 49% | -0.02 [-0.25, +0.23] | -0.06 [-0.36, +0.29] | -0.08 [-0.34, +0.17] |
| prior_sweep | no sweep | 47 | 41 | 44% | -0.12 [-0.58, +0.30] | -0.12 [-0.70, +0.55] | -0.27 [-0.74, +0.23] |
| prior_sweep | sweep | 146 | 131 | 50% | +0.01 [-0.27, +0.32] | -0.04 [-0.43, +0.40] | -0.03 [-0.30, +0.31] |
| break_kind | BOS | 98 | 84 | 54% | +0.07 [-0.23, +0.39] | +0.15 [-0.24, +0.52] | +0.07 [-0.28, +0.41] |
| break_kind | CHoCH | 95 | 88 | 44% | -0.11 [-0.42, +0.30] | -0.26 [-0.70, +0.38] | -0.24 [-0.55, +0.18] |
| prior_sweep test | sweep vs no sweep / oneR_cost | | | | difference +0.13 [-0.41, +0.71] | p = 0.489 | |
| prior_sweep test | sweep vs no sweep / twoR_cost | | | | difference +0.08 [-0.71, +0.87] | p = 0.844 | |
| prior_sweep test | sweep vs no sweep / ladder_cost | | | | difference +0.24 [-0.34, +0.85] | p = 0.237 | |
| break_kind test | BOS vs CHoCH / oneR_cost | | | | difference +0.18 [-0.39, +0.65] | p = 0.294 | |
| break_kind test | BOS vs CHoCH / twoR_cost | | | | difference +0.41 [-0.38, +1.09] | p = 0.053 | |
| break_kind test | BOS vs CHoCH / ladder_cost | | | | difference +0.31 [-0.31, +0.82] | p = 0.062 | |

**Room and leg-extension refusals relaxed, for a larger sample** (`AEQ_PARAMS='{"fvg":{"require_prior_sweep":false,"minimum_target_r":0.01,"minimum_leg_retracement":0},"order_block":{"minimum_target_r":0.01}}'`):

**fvg**: 465 confirmed opportunities

| split | cell | n | filled | win 1R | mean R 1R [95%] | mean R 2R [95%] | mean R ladder [95%] |
|---|---|---:|---:|---:|---|---|---|
| all | all | 465 | 421 | 46% | -0.09 [-0.25, +0.09] | -0.03 [-0.21, +0.17] | -0.09 [-0.26, +0.07] |
| prior_sweep | no sweep | 97 | 84 | 51% | +0.02 [-0.21, +0.24] | -0.05 [-0.42, +0.34] | +0.01 [-0.26, +0.29] |
| prior_sweep | sweep | 368 | 337 | 44% | -0.12 [-0.32, +0.10] | -0.02 [-0.26, +0.23] | -0.12 [-0.31, +0.09] |
| break_kind | BOS | 93 | 85 | 48% | -0.04 [-0.31, +0.23] | +0.10 [-0.32, +0.52] | -0.04 [-0.33, +0.29] |
| break_kind | CHoCH | 138 | 132 | 50% | -0.00 [-0.28, +0.27] | -0.11 [-0.45, +0.23] | -0.08 [-0.34, +0.16] |
| break_kind | no break | 234 | 204 | 42% | -0.17 [-0.37, +0.06] | -0.02 [-0.31, +0.31] | -0.12 [-0.36, +0.11] |
| prior_sweep test | sweep vs no sweep / oneR_cost | | | | difference -0.14 [-0.45, +0.18] | p = 0.271 | |
| prior_sweep test | sweep vs no sweep / twoR_cost | | | | difference +0.02 [-0.44, +0.48] | p = 0.886 | |
| prior_sweep test | sweep vs no sweep / ladder_cost | | | | difference -0.13 [-0.48, +0.21] | p = 0.303 | |
| break_kind test | BOS vs CHoCH / oneR_cost | | | | difference -0.04 [-0.41, +0.37] | p = 0.885 | |
| break_kind test | BOS vs CHoCH / twoR_cost | | | | difference +0.21 [-0.33, +0.75] | p = 0.292 | |
| break_kind test | BOS vs CHoCH / ladder_cost | | | | difference +0.04 [-0.35, +0.47] | p = 0.781 | |
| break_kind test | BOS vs no break / oneR_cost | | | | difference +0.13 [-0.19, +0.45] | p = 0.367 | |
| break_kind test | BOS vs no break / twoR_cost | | | | difference +0.12 [-0.38, +0.64] | p = 0.537 | |
| break_kind test | BOS vs no break / ladder_cost | | | | difference +0.09 [-0.26, +0.45] | p = 0.538 | |
| break_kind test | CHoCH vs no break / oneR_cost | | | | difference +0.17 [-0.19, +0.48] | p = 0.144 | |
| break_kind test | CHoCH vs no break / twoR_cost | | | | difference -0.09 [-0.54, +0.36] | p = 0.550 | |
| break_kind test | CHoCH vs no break / ladder_cost | | | | difference +0.04 [-0.26, +0.37] | p = 0.708 | |

**order_block**: 380 confirmed opportunities

| split | cell | n | filled | win 1R | mean R 1R [95%] | mean R 2R [95%] | mean R ladder [95%] |
|---|---|---:|---:|---:|---|---|---|
| all | all | 380 | 349 | 44% | -0.12 [-0.30, +0.11] | -0.23 [-0.47, +0.05] | -0.17 [-0.38, +0.06] |
| prior_sweep | no sweep | 77 | 71 | 35% | -0.30 [-0.66, +0.09] | -0.32 [-0.75, +0.26] | -0.42 [-0.76, +0.06] |
| prior_sweep | sweep | 303 | 278 | 46% | -0.07 [-0.28, +0.19] | -0.21 [-0.49, +0.17] | -0.11 [-0.35, +0.17] |
| break_kind | BOS | 192 | 176 | 43% | -0.15 [-0.47, +0.17] | -0.14 [-0.50, +0.31] | -0.14 [-0.48, +0.21] |
| break_kind | CHoCH | 188 | 173 | 46% | -0.09 [-0.36, +0.23] | -0.33 [-0.67, +0.18] | -0.20 [-0.50, +0.15] |
| prior_sweep test | sweep vs no sweep / oneR_cost | | | | difference +0.22 [-0.25, +0.68] | p = 0.109 | |
| prior_sweep test | sweep vs no sweep / twoR_cost | | | | difference +0.12 [-0.57, +0.71] | p = 0.555 | |
| prior_sweep test | sweep vs no sweep / ladder_cost | | | | difference +0.30 [-0.24, +0.79] | p = 0.034 | |
| break_kind test | BOS vs CHoCH / oneR_cost | | | | difference -0.06 [-0.55, +0.38] | p = 0.596 | |
| break_kind test | BOS vs CHoCH / twoR_cost | | | | difference +0.19 [-0.46, +0.76] | p = 0.181 | |
| break_kind test | BOS vs CHoCH / ladder_cost | | | | difference +0.07 [-0.49, +0.60] | p = 0.572 | |

Reading: nothing here is profitable after spread; the best cells are about 0 R at 1R. The FVG sweep, the research's one candidate, points the wrong way at 1R in both samples; the order block sweep points the right way (relaxed sample, ladder: +0.30 R, p = 0.034) and is, with the other comparisons, within what chance produces. No split is a reason to change a gate.

## 7. Forward decision

- The FVG gate stays on (owner decision) although section 6.3 did not reproduce its benefit; it is confirmed or removed from at least 150 forward-labelled FVG outcomes with and without a sweep (the gate must be switched off, , for a period to label the no-sweep side), using the `strategy_feature{feature="prior_sweep"}` tally to see the published mix and the outcome labels the program already needs (realized R, MFE/MAE).
- No BOS/CHoCH rule and no order block sweep rule becomes a gate before at least 150 forward-labelled outcomes per cell.
- Causal breaks for supply, demand and confluence zone are their own changes.

## 8. Reproduce

```
cd analysis-engine
SWEEPBOS_REPORT=/tmp/sweepbos.md go test ./test/sweepbos -run TestV8ToV9 -v      # published counts, refusals, blocks that moved
AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies           # fvg v9 and order_block v9, gate on
AEQ_DUMP=/tmp/aeq-off AEQ_PARAMS='{"fvg":{"require_prior_sweep":false}}' go test ./test/aequality -run TestReplayAllStrategies
AEQ_DUMP=/tmp/aeq-relaxed AEQ_PARAMS='{"fvg":{"require_prior_sweep":false,"minimum_target_r":0.01,"minimum_leg_retracement":0},"order_block":{"minimum_target_r":0.01}}' \
  go test ./test/aequality -run TestReplayAllStrategies
python3 algo-bot/tools/sweep_split.py /tmp/aeq/records.jsonl                   # n, win rate, mean R, interval, permutation p per split
```
