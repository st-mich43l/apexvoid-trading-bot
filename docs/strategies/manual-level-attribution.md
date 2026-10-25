# Which levels does the owner trade? Manual-book level attribution

Measurement only: no strategy, engine, configuration or runtime change. Production was read through `BEGIN READ ONLY` (Postgres) and Redis reads; the exported signals and their per-signal attribution stay local. This page and
[manual-level-attribution-results.json](manual-level-attribution-results.json) hold aggregates only (counts, shares, quantiles).

## Read this first: the manual book is tiny

- **The study's population (`trade_stream = 'manual'`, no parent) is 5 signals**, all XAU, all `cancelled` and never filled, between 2026-10-05 and 2026-10-20. `manual_signals` holds 15 rows in total and nothing before 2026-10-05: there is no older manual history in Postgres.
- The other ten rows are `trade_stream = 'algo_manual'`: manual signals the owner armed for the algo. All are XAU and all filled and closed (their `auto_trade_results` rows link by group id `manual:<signal id>:<leg>`). They are the same kind of owner-placed zone order, so the tables below use
  **all 15** (15 covered) and give the 5 of the study's own query beside them. Every conclusion is therefore about **15 XAU signals over 14 days**, descriptive only. **There is no FX manual signal at all.**
- Coverage: 15 of 15 signals have an engine state on the last closed **M5** bar before `ts` (age at most 299 s); the M15 and H1 frames are available for all of them. The M5 series used is the merge of the six XAU captures (five committed, one deep Redis capture) from 2026-10-01 01:55 to 2026-10-23 20:55.

## What the owner's orders look like

- **Entry:** a 30 pip zone order (`order_type zone`, 30 pips wide). The proximal edge is the top for a BUY, the bottom for a SELL.
- **Stop: exactly 50 pips from the proximal edge on 15 of 15 signals** (the 5: 5 of 5), i.e. 1.31 M5 ATR at the median (1.14-1.68). It is a fixed distance, not placed from a structure. The analysis engine's XAU envelope is 50 to 70 pips, so the owner sits on the floor of it.

## Entry: which structures explain it

A family *explains* the entry when the proximal edge is inside the structure or within 0.25 M5 ATR of it (about 12 pips on XAU). **Read the explained share against the chance column**: the same test with the entry moved by 0.5 to 3 M5 ATR in either direction. Zones and
higher-timeframe levels are wide and dense on XAU (an H1 swing level is a band about 15 dollars wide), so most families explain most prices.

| family and timeframe | explained, 15 signals | by chance | distance to the nearest, M5 ATR median (p25-p75) | explained, the 5 |
|---|---|---|---|---|
| fvg H1 | 14 / 15 (93%) | 93% | 0 (0-0) | 5 / 5 |
| fvg M15 | 13 / 15 (87%) | 75% | 0 (0-0.04) | 5 / 5 |
| fvg M5 | 9 / 15 (60%) | 46% | 0 (0-2.57) | 4 / 5 |
| supply_demand H1 | 14 / 15 (93%) | 87% | 0 (0-0) | 5 / 5 |
| supply_demand M15 | 13 / 15 (87%) | 79% | 0 (0-0) | 5 / 5 |
| supply_demand M5 | 10 / 15 (67%) | 51% | 0 (0-2.39) | 4 / 5 |
| order_block H1 | 5 / 15 (33%) | 31% | 1.14 (0.21-1.51) | 2 / 5 |
| order_block M15 | 10 / 15 (67%) | 55% | 0 (0-1.14) | 4 / 5 |
| order_block M5 | 5 / 15 (33%) | 29% | 0.62 (0.24-3.03) | 3 / 5 |
| swing_level H1 | 10 / 15 (67%) | 68% | 0 (0-0.42) | 3 / 5 |
| swing_level M15 | 13 / 15 (87%) | 82% | 0 (0-0) | 5 / 5 |
| swing_level M5 | 8 / 15 (53%) | 43% | 0.21 (0-3.04) | 3 / 5 |
| swing_level_fractal H1 | 10 / 15 (67%) | 68% | 0 (0-0.42) | 3 / 5 |
| swing_level_fractal M15 | 13 / 15 (87%) | 82% | 0 (0-0) | 5 / 5 |
| swing_level_fractal M5 | 8 / 15 (53%) | 43% | 0.21 (0-3.04) | 3 / 5 |
| liquidity_pool H1 | 9 / 15 (60%) | 65% | 0 (0-0.98) | 3 / 5 |
| liquidity_pool M15 | 11 / 15 (73%) | 45% | 0 (0-0.32) | 5 / 5 |
| liquidity_pool M5 | 3 / 15 (20%) | 30% | 1.05 (0.44-1.96) | 1 / 5 |
| round_number | 7 / 15 (47%) | 35% | 0.32 (0.21-0.49) | 3 / 5 |
| session_high_low Asia | 3 / 15 (20%) | 7% | 2.74 (1.96-3.04) | 1 / 5 |
| session_high_low London | 0 / 15 (0%) | 9% | 1.76 (0.85-2.12) | 0 / 5 |
| session_high_low NY | 4 / 15 (27%) | 7% | 1.38 (0.49-3.74) | 1 / 5 |
| pdh_pdl | 1 / 15 (7%) | 5% | 3.16 (2.11-6.25) | 0 / 5 |
| pwh_pwl | 0 / 15 (0%) | 1% | 10.51 (8.37-12.62) | 0 / 5 |
| trendline H1 | 0 / 15 (0%) | 3% | 4.69 (3.6-5.62) | 0 / 5 |
| trendline M15 | 2 / 15 (13%) | 7% | 2.14 (1.5-3.89) | 1 / 5 |
| trendline M5 | 1 / 14 (7%) | 6% | 3.44 (0.58-4.74) | 1 / 5 |

Reading it:

- **The wide families cannot discriminate**: H1/M15 swing levels, H1/M15 supply/demand and FVG explain 67-93% of the entries and 68-93% of the shifted prices (they contain almost any price).
- **Above their chance rate:** M15 liquidity pools (11 of 15 (73% against 45% by chance)), M5 supply/demand (10 of 15 (67% against 51% by chance)), M5 FVG (9 of 15 (60% against 46% by chance)), round numbers (7 of 15 (47% against 35% by chance), the $5 round step), and, with very few signals, the Asia high/low (3 of 15 (20% against 7% by chance)) and NY high/low (4 of 15 (27% against 7% by chance)).
- **At or below chance:** H1/M15 swing levels (H1 10 of 15 (67% against 68% by chance)), London high/low (0 of 15 (0% against 9% by chance)), previous-day high/low (1 of 15 (7% against 5% by chance)), previous-week high/low (0 of 15 (0% against 1% by chance)) and trendlines (H1/M15/M5: 0, 2 and 1 of 15). **Most entries are not on a session, day or week extreme.**
- Entries explained by at least one family: 15 of 15, which says little because the families are dense.

### The structure the stop protects

The nearest structure on the entry side of the stop, per family (gap = its far edge to the stop):

| family and timeframe | signals | gap, M5 ATR median | gap within 0.5 ATR |
|---|---|---|---|
| fvg M15 | 11 | 0.11 | 10 |
| supply_demand M15 | 11 | 0.45 | 8 |
| swing_level_fractal M15 | 12 | 0.34 | 8 |
| swing_level M15 | 12 | 0.34 | 8 |
| round_number | 15 | 0.59 | 7 |
| fvg H1 | 12 | 0.54 | 6 |
| fvg M5 | 10 | 0.51 | 5 |
| supply_demand M5 | 8 | 0.46 | 5 |
| liquidity_pool M15 | 9 | 0.74 | 4 |
| liquidity_pool M5 | 5 | 0.28 | 4 |

The family with the smallest gap, per signal: round_number 5, fvg|H1 2, fvg|M15 2, liquidity_pool|M5 1, order_block|M5 1, supply_demand|H1 1, supply_demand|M15 1, swing_level_fractal|M15 1, swing_level|M5 1. The stop is a fixed 50 pips, so these gaps are whatever the structure happens to leave, not a deliberate buffer.

### Side and acceptance

Whether the level the signal sits on (nearest horizontal level that explains the entry: swing level, round number, session, PDH/PDL, PWH/PWL, pool) was crossed with acceptance (two consecutive H1 closes beyond it) in the 48 H1 bars before.

| side | signals | on a horizontal level | crossed with acceptance (flip) | first touch |
|---|---|---|---|---|
| BUY (from above) | 6 | 6 | 2 | 4 |
| SELL (from below) | 9 | 9 | 9 | 0 |

The 5 of the study's query: BUY 1 signal (1 first touch), SELL 4 (4 flips). Every SELL sits on a level price had already traded through; BUYs are mostly first touches. The H1 level bands are wide, which makes "crossed" easy to satisfy.

## Does the AE see them?

A candidate from **any** strategy, same side, whose entry band is within 0.25 M5 ATR of the manual proximal edge, published in the 12 M5 bars before or after the signal.

- **AE recall: 12 of 15 (80%); the chance rate for the same test is 56%.** For the 5 of the study's query: 4 of 5 (chance 66%). **Key Level matched 5 of 15.**
  The window holds 17 to 137 candidates per signal, so a match is easy; the margin over chance is the information.
- Matching strategies (a signal can match several): supply 7, key_level 5, fvg 4, demand 2, order_block 2, ifvg 1.

| primary explaining family | signals | AE matched |
|---|---|---|
| fvg | 14 | 11 |
| round_number | 1 | 1 |

The 3 misses are explained by: fvg 3, supply_demand 3, liquidity_pool 2, order_block 2, session_high_low 2, swing_level 2, swing_level_fractal 2, round_number 1, trendline 1 (a miss can be explained by several). None is unexplained.

By stream and by the owner's `setup_type`:

| stream | signals | AE matched | Key Level matched |
|---|---|---|---|
| algo_manual | 10 | 8 | 3 |
| manual | 5 | 4 | 2 |

| setup_type | signals | explained by a family | stop 50-70 pips | AE matched | Key Level matched | chance |
|---|---|---|---|---|---|---|
| demand | 2 | 2 | 2 | 2 | 0 | 50% |
| key-level | 13 | 13 | 13 | 10 | 5 | 57% |

## What this implies for Key Level (#7b), as findings

1. **The owner is not mainly a session/day/week-level trader.** Previous-day and previous-week levels explain 1 of 15 (7% against 5% by chance) and 0 of 15 (0% against 1% by chance); the London high/low 0 of 15 (0% against 9% by chance). Only the Asia and NY highs/lows are above their chance rate, on 3 and 4 signals. This is consistent with the replay finding that no level family beats random entries on its own, and it does not support rebuilding Key Level around session/day/week levels.
2. **Higher-timeframe swing levels are not distinguishable from chance for the owner's entries.** An H1 swing level explains 10 of 15 (67% against 68% by chance) and an M15 one 13 of 15 (87% against 82% by chance); the engine's own band for them is about 15 dollars wide on H1. Moving Key Level from M5 clusters to M15/H1 swing levels is not supported by the manual book either.
3. **What is above chance is local structure and liquidity:** M5 supply/demand and FVG, M15 liquidity pools and the $5 round numbers. A candidate source for #7b to test, not a conclusion (n=15).
4. **The stop is a fixed 50 pips (about 1.1 to 1.7 M5 ATR, interquartile).** Nothing in the manual book says to anchor a Key Level stop on a level; the owner's number is the floor of the AE's XAU envelope.
5. **The AE already publishes a candidate near most of these entries** (80% against 56% by chance); Key Level is one of 6 strategies that match, 5 of 15 times. The manual entries Key Level misses are matched by supply, demand, FVG, order block and iFVG, so the difference between the owner's book and Key Level is not the location alone.
6. **SELLs are retests of a level price already crossed**; BUYs are mostly first touches. A Key Level that kept only first touches would drop every SELL of this book (9 of 9), although the H1 level bands are wide enough to make "crossed" easy to satisfy.

## What could not be measured, and why

- **FX:** there is no FX manual signal in Postgres. Nothing here says anything about FX.
- **History before 2026-10-05:** `manual_signals` has no older rows, so the "manual book since September" is not recoverable from this database.
- **Sample size:** 5 signals in the study's query (none filled) and 15 with the algo-armed ones. Shares move by 7 points per signal; no interval is reported because none would be informative.
- **Results:** `manual_signals.result_pips` and the linked `auto_trade_results` rows exist only for the ten algo-armed signals (`net_r` is not populated for them; the link is the group id `manual:<id>:<leg>`). The five cancelled signals have no result. Outcomes are not analysed here.
- **Owner notes:** the `note` column is empty on all 15 rows.
- **M5 for older signals:** all 15 signals are inside the merged M5 coverage (every state is the last closed M5 bar, at most 299 s old). Signals older than the deepest M5 capture would be attributed on M15/H1; there are none.
- **PDH/PDL and weekly levels:** the M5 detector frame holds 150 bars, so a previous-day high/low is in it only early in the day (12 of 15 signals had one there). The M15 and H1 frames' windows cover them, and the study unions the three frames.
- **Trendlines** are taken at the state bar from the unbroken lines of each frame; **round numbers** use the instrument's `round_step` (XAU 5.0) and are not a detector structure.
