# KL14: XAU Key Level attribution (read-only, 25 Nov 2026)

What broke XAU Key Level after 9 Nov, and why v13 is near-silent. **No strategy, configuration, execution or schema file changed**; the tools are
`analysis-engine/cmd/klstudy` (modes `attribution` and `funnel`) and `algo-bot/scripts/kl_attribution.py` (modes `trades` and `label`).

## Findings (read the numbers with n beside them; no edge is claimed)

1. **The pre-registered result is R4: no component is named.** On identical bars, v8 -> v9 (role from structure) changes labelled expectancy by +0.020 / -0.200 /
   +0.122 / -0.025 R (Sep / 13 Nov / 17 Nov / dump) and v9 -> v10 (KL-owned confirmation) by +0.006 / +0.022 / 0.000 / +0.004 R: neither drops 0.15 R on two
   sets. v7 -> v8 publishes the same setups (it changes only the target basis, which the live-geometry label does not read).
2. **Live, the decline starts with v7 (6 Nov, deep entry), before the 9 Nov deploy**: v5 +0.34 R/trade (10), v6 +0.15 (21), v7 -0.73 (10), v10 -0.30 (27),
   v11 -0.39 (3), v12 +0.06 (10). The added v6 -> v7 replay does not reproduce it either: deep entry multiplies setups 3-8x (stops move to the 70-pip cap) while
   labelled expectancy per setup is unchanged or better (+0.001 / +0.114 / +0.126 / +0.135 R).
3. **On the bars, no version from v6 to v12 has a positive labelled expectancy on any set** (about -0.04 to -0.68 R per setup). None of the four sets covers
   28 Oct - 8 Nov (Redis M5 now starts 16 Nov, and no capture of that window exists), so the live +5.24 R of those 33 trades cannot be replayed.
4. **Part A's strongest split**: since v9, trades at levels with an explicit role (support/resistance) made -0.69 R per trade (9) against -0.08 for ambiguous
   ones (31); trades "with the approach" -0.76 (6) against -0.01 "inside" (23). By day, Friday (UTC+7) -0.46 (21). All small.
5. **v13 is near-silent by selection, not by the minor_level gate.** Of 327 v12 setups v13 drops on the four sets, 198 are at levels that are not in the v13 book
   at all (the 0.5-ATR swing clusters and the \$5 grid have no counterpart), 87 are at major levels where no M5 reaction passed (no_m5_reaction dominates the
   telemetry), and only 42 at levels the book calls minor (mostly broken session levels and too few reactions). The minor-only setups label 4 / +0.24, 12 / -0.41,
   0, 23 / -0.01 R: never >= v13's and >= 0 on two sets, so R5 reads **keep**. v13's own labelled setups: 7 / +0.50, 0, 1 / +0.35, 1 / -0.91.

## Method

- **Part A**: every closed XAU `algo_auto` trade whose snapshot strategy is Key Level since 22 Oct 2026, with net R (production, read-only). The envelope carries
  no Key Level version, so a trade's version is the newest one deployed before its opportunity was created; a version's deploy is the first production config
  fingerprint first seen at or after its merge to master. v8, v9 and v10 merged within 45 minutes on 9 Nov and share one deploy (9 Nov 11:55 UTC): **no live trade
  ran v8 or v9 alone**. Approach side = the close of the last bar before the touch bar (finest of Redis M5/M15/H1 that reaches back) against the level band
  (the published structure, else the published entry band, which contains the level): BUY from above and SELL from below are "with".
  Sessions: Asia from 07:00 Tokyo, London from 08:00 London, NY from 09:00 New York, late from 17:00 New York (empty in northern winter).
- **Part B**: `cmd/replay` built from git worktrees at v6 `b463595b` (extra: the live data pointed at v7), v7 `0901ac3`, v8 `6c9a9e9`, v9 `53737e0`,
  v10 `06f0c6b`, v12 = master with the XAU `level_model` line removed (the documented rollback), v13 = master as shipped; each with its own commit's
  configuration. The same four XAU captures for every build. No old code was modified and no converter was needed (capture format v1 throughout).
- **Labelling** (same for every build): on the M5 bars of the capture the setup was replayed on (three of the four sets hold no M1).
  *Live geometry*: today's XAU ladder from the zone's shallow edge (60/20/20, legs 1.00 apart), the published stop, 1R..4R of L1's risk closing 40/20/20/20,
  stop to L1 +/- 6 ticks after TP1, fillable from the labeller's eligibility (created_at + one bar) to expiry, closed 24 h after the first fill, spread 3 pips
  (no archived quotes), a bar touching stop and target is a stop. Setups still open when the bars end are not counted. *Labeller default*
  (`outcome_model.Assumptions()` v1: one proximal limit to the first published target, ambiguous bars at the conservative bound) is reported beside it.
- **Part C**: `klstudy funnel` replays Key Level alone as v12 (`level_model: swing`) and as v13 with refusal telemetry, builds v13's own major-level book
  (`keylevel.StudyBook`) on the bar of every v12 setup and classifies it: kept, major (the level is major; a later step refused it), minor (with the rule the
  level fails) or not in book (no book level overlaps the v12 band).

## Part A: closed XAU algo_auto Key Level trades

- Window 2026-10-22 00:00 -> 2026-11-25 08:57 UTC; 91 closed trades with net R; opportunity envelope found for 91.
- Approach side read from Redis bars (M5 2026-11-16 03:15 -> 2026-11-25 08:50, M15 2026-10-26 13:45 -> 2026-11-25 08:30, H1 2026-07-27 06:00 -> 2026-11-25 07:00); by frame: M5 27, M15 58, H1 6, none 0.
- MFE known on 16 trades (OBS1 tracks from 18 Nov).

Deploy time per version (first config fingerprint seen at or after the merge):

| version | merged (UTC) | deployed (UTC) |
|---|---|---|
| v5 | 2026-10-24 16:05 | 2026-10-27 04:10 |
| v6 | 2026-10-28 07:38 | 2026-10-28 14:15 |
| v7 | 2026-11-05 23:51 | 2026-11-06 00:30 |
| v8 | 2026-11-09 07:19 | 2026-11-09 11:55 |
| v9 | 2026-11-09 07:38 | 2026-11-09 11:55 |
| v10 | 2026-11-09 08:03 | 2026-11-09 11:55 |
| v11 | 2026-11-18 08:29 | 2026-11-19 07:55 |
| v12 | 2026-11-23 10:11 | 2026-11-23 17:55 |
| v13 | 2026-11-25 03:40 | n/a |

### A1 by version

| version | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| <=v4 | 10 | -2.77 | -0.277 | 0.40 | 0.60 | 10 | -0.277 | 0 | n/a |
| v10 | 27 | -8.11 | -0.301 | 0.37 | 0.59 | 0 | n/a | 27 | -0.301 |
| v11 | 3 | -1.16 | -0.388 | 0.33 | 0.67 | 0 | n/a | 3 | -0.388 |
| v12 | 10 | 0.56 | 0.056 | 0.60 | 0.40 | 0 | n/a | 10 | 0.056 |
| v5 | 10 | 3.43 | 0.343 | 0.60 | 0.40 | 10 | 0.343 | 0 | n/a |
| v6 | 21 | 3.14 | 0.150 | 0.57 | 0.43 | 21 | 0.150 | 0 | n/a |
| v7 | 10 | -7.32 | -0.732 | 0.20 | 0.80 | 7 | -0.622 | 3 | -0.990 |

### A2 role x version group

| group role | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| <=v7 ambiguous | 51 | -3.52 | -0.069 | 0.47 | 0.53 | 48 | -0.011 | 3 | -0.990 |
| >=v9 ambiguous | 31 | -2.48 | -0.080 | 0.48 | 0.48 | 0 | n/a | 31 | -0.080 |
| >=v9 resistance | 5 | -3.61 | -0.722 | 0.20 | 0.80 | 0 | n/a | 5 | -0.722 |
| >=v9 support | 4 | -2.62 | -0.656 | 0.25 | 0.75 | 0 | n/a | 4 | -0.656 |

### A3 confirmation pattern x version group

| group pattern | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| <=v7 engulfing | 2 | 0.92 | 0.460 | 0.50 | 0.50 | 2 | 0.460 | 0 | n/a |
| <=v7 rejection_choch | 3 | -0.26 | -0.088 | 0.67 | 0.33 | 3 | -0.088 | 0 | n/a |
| <=v7 strong_reclaim | 30 | -2.79 | -0.093 | 0.50 | 0.50 | 28 | -0.029 | 2 | -0.985 |
| <=v7 sweep_reclaim | 12 | 1.11 | 0.093 | 0.42 | 0.58 | 11 | 0.192 | 1 | -1.000 |
| <=v7 wick_rejection | 4 | -2.49 | -0.624 | 0.25 | 0.75 | 4 | -0.624 | 0 | n/a |
| >=v9 rejection_choch | 1 | -0.05 | -0.047 | 0.00 | 1.00 | 0 | n/a | 1 | -0.047 |
| >=v9 strong_reclaim | 22 | -4.54 | -0.206 | 0.41 | 0.59 | 0 | n/a | 22 | -0.206 |
| >=v9 sweep_reclaim | 17 | -4.13 | -0.243 | 0.47 | 0.47 | 0 | n/a | 17 | -0.243 |

### A4 swept vs not

| sweep | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| not swept | 17 | -2.56 | -0.150 | 0.41 | 0.59 | 17 | -0.150 | 0 | n/a |
| swept | 74 | -9.68 | -0.131 | 0.46 | 0.53 | 31 | 0.065 | 43 | -0.272 |

### A5 by session at creation

| session | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| Asia | 44 | -5.17 | -0.117 | 0.48 | 0.50 | 23 | 0.070 | 21 | -0.322 |
| London | 17 | -3.78 | -0.222 | 0.41 | 0.59 | 10 | -0.206 | 7 | -0.246 |
| NY | 30 | -3.29 | -0.110 | 0.43 | 0.57 | 15 | -0.006 | 15 | -0.213 |

### A6 HTF aligned vs not

| htf | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| aligned | 63 | -8.34 | -0.132 | 0.46 | 0.52 | 33 | -0.030 | 30 | -0.245 |
| not aligned | 28 | -3.90 | -0.139 | 0.43 | 0.57 | 15 | 0.029 | 13 | -0.333 |

### A7a day of week (UTC)

| dow UTC | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| Fri | 17 | -6.39 | -0.376 | 0.41 | 0.59 | 12 | -0.237 | 5 | -0.710 |
| Mon | 25 | -0.24 | -0.010 | 0.44 | 0.56 | 12 | 0.130 | 13 | -0.138 |
| Sun | 1 | 0.40 | 0.396 | 1.00 | 0.00 | 0 | n/a | 1 | 0.396 |
| Thu | 12 | -3.00 | -0.250 | 0.50 | 0.50 | 6 | -0.165 | 6 | -0.335 |
| Tue | 27 | -7.59 | -0.281 | 0.37 | 0.59 | 14 | -0.281 | 13 | -0.281 |
| Wed | 9 | 4.59 | 0.510 | 0.67 | 0.33 | 4 | 1.416 | 5 | -0.215 |

### A7b day of week (UTC+7)

| dow UTC+7 | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| Fri | 21 | -9.74 | -0.464 | 0.33 | 0.67 | 14 | -0.294 | 7 | -0.804 |
| Mon | 17 | 1.31 | 0.077 | 0.53 | 0.47 | 8 | 0.534 | 9 | -0.329 |
| Sat | 3 | 1.30 | 0.435 | 1.00 | 0.00 | 2 | 0.427 | 1 | 0.450 |
| Thu | 8 | -2.99 | -0.374 | 0.38 | 0.62 | 2 | -0.288 | 6 | -0.402 |
| Tue | 27 | -7.43 | -0.275 | 0.33 | 0.63 | 14 | -0.452 | 13 | -0.085 |
| Wed | 15 | 5.31 | 0.354 | 0.67 | 0.33 | 8 | 0.667 | 7 | -0.003 |

### A-extra approach side x version group

| group approach | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| <=v7 against | 21 | -5.17 | -0.246 | 0.48 | 0.52 | 19 | -0.168 | 2 | -0.985 |
| <=v7 inside | 20 | 3.79 | 0.190 | 0.55 | 0.45 | 19 | 0.252 | 1 | -1.000 |
| <=v7 with | 10 | -2.14 | -0.214 | 0.30 | 0.70 | 10 | -0.214 | 0 | n/a |
| >=v9 against | 11 | -3.89 | -0.353 | 0.36 | 0.64 | 0 | n/a | 11 | -0.353 |
| >=v9 inside | 23 | -0.25 | -0.011 | 0.52 | 0.43 | 0 | n/a | 23 | -0.011 |
| >=v9 with | 6 | -4.58 | -0.763 | 0.17 | 0.83 | 0 | n/a | 6 | -0.763 |

### A-extra structure timeframe

| structure tf | n | net R | exp R | win | full stop | H1 n | H1 exp | H2 n | H2 exp |
|---|---|---|---|---|---|---|---|---|---|
| - | 73 | -10.22 | -0.140 | 0.44 | 0.55 | 48 | -0.011 | 25 | -0.387 |
| M15 | 2 | 0.38 | 0.189 | 0.50 | 0.50 | 0 | n/a | 2 | 0.189 |
| M5 | 16 | -2.39 | -0.150 | 0.50 | 0.50 | 0 | n/a | 16 | -0.150 |

### A8 MFE of full stops closed since 18 Nov (OBS1; 9 stops)

| MFE bucket | n |
|---|---|
| <0.3 | 4 |
| 0.3-0.6 | 2 |
| 0.6-1 | 2 |
| >=1 | 0 |
| unknown | 1 |


## Part B: component replay on identical bars

- sep: H1 300 bars (2026-09-02 11:00 -> 2026-09-21 13:00), M15 600 bars (2026-09-11 02:30 -> 2026-09-21 14:15), M5 1500 bars (2026-09-14 04:35 -> 2026-09-21 14:30)
- nov13: H1 150 bars (2026-11-05 00:00 -> 2026-11-13 11:00), M15 300 bars (2026-11-10 05:30 -> 2026-11-13 11:15), M5 380 bars (2026-11-12 02:50 -> 2026-11-13 11:25)
- nov17: H1 450 bars (2026-10-20 16:00 -> 2026-11-17 06:00), M15 300 bars (2026-11-12 01:15 -> 2026-11-17 07:00), M5 394 bars (2026-11-13 20:30 -> 2026-11-17 07:15)
- dump: H1 2000 bars (2026-07-27 06:00 -> 2026-11-25 07:00), M15 2000 bars (2026-10-26 13:30 -> 2026-11-25 08:15), M5 2000 bars (2026-11-16 02:55 -> 2026-11-25 08:30)

### B1 per build and bar set (live geometry; approach and role as n/expectancy)

| set | build | setups | per day | labelled n | exp R | win | full stop | missed | default n | default exp | by approach | by role |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sep | v7 | 204 | 27.5 | 194 | -0.138 | 0.42 | 0.58 | 9 | 177 | -0.302 | with:5/-0.28 against:99/-0.12 inside:90/-0.15 | ambiguous:194/-0.14 |
| sep | v8 | 204 | 27.5 | 194 | -0.138 | 0.42 | 0.58 | 9 | 173 | -0.339 | with:5/-0.28 against:99/-0.12 inside:90/-0.15 | ambiguous:194/-0.14 |
| sep | v9 | 134 | 18.1 | 127 | -0.118 | 0.45 | 0.55 | 6 | 113 | -0.341 | with:5/-0.28 against:57/-0.09 inside:65/-0.13 | support:16/-0.41 resistance:12/-0.30 ambiguous:99/-0.05 |
| sep | v10 | 133 | 17.9 | 126 | -0.112 | 0.45 | 0.55 | 6 | 112 | -0.335 | with:5/-0.28 against:57/-0.09 inside:64/-0.12 | support:16/-0.41 resistance:12/-0.30 ambiguous:98/-0.04 |
| sep | v12 | 127 | 17.1 | 124 | -0.093 | 0.44 | 0.56 | 3 | 112 | -0.383 | with:1/-0.91 against:35/-0.18 inside:88/-0.05 | support:15/0.10 resistance:14/0.10 ambiguous:95/-0.15 |
| sep | v13 | 8 | 1.1 | 7 | 0.503 | 0.71 | 0.29 | 1 | 6 | -0.749 | with:0/n/a against:0/n/a inside:7/0.50 | ambiguous:7/0.50 |
| nov13 | v7 | 25 | 18.4 | 19 | -0.476 | 0.21 | 0.79 | 1 | 24 | -0.330 | with:0/n/a against:13/-0.28 inside:6/-0.91 | ambiguous:19/-0.48 |
| nov13 | v8 | 25 | 18.4 | 19 | -0.476 | 0.21 | 0.79 | 1 | 24 | -0.298 | with:0/n/a against:13/-0.28 inside:6/-0.91 | ambiguous:19/-0.48 |
| nov13 | v9 | 17 | 12.5 | 12 | -0.676 | 0.17 | 0.83 | 1 | 16 | -0.292 | with:0/n/a against:7/-0.51 inside:5/-0.91 | support:1/-0.88 resistance:2/-0.92 ambiguous:9/-0.60 |
| nov13 | v10 | 16 | 11.8 | 11 | -0.654 | 0.18 | 0.82 | 1 | 15 | -0.244 | with:0/n/a against:7/-0.51 inside:4/-0.91 | support:1/-0.88 resistance:2/-0.92 ambiguous:8/-0.56 |
| nov13 | v12 | 18 | 13.3 | 13 | -0.446 | 0.23 | 0.77 | 1 | 17 | -0.261 | with:0/n/a against:9/-0.24 inside:4/-0.91 | support:2/-0.90 resistance:1/-0.89 ambiguous:10/-0.31 |
| nov13 | v13 | 0 | 0.0 | 0 | n/a | n/a | n/a | 0 | 0 | n/a | with:0/n/a against:0/n/a inside:0/n/a |  |
| nov17 | v7 | 66 | 19.1 | 62 | -0.395 | 0.34 | 0.66 | 2 | 63 | -0.487 | with:2/-0.21 against:31/-0.31 inside:29/-0.50 | ambiguous:62/-0.39 |
| nov17 | v8 | 66 | 19.1 | 62 | -0.395 | 0.34 | 0.66 | 2 | 62 | -0.540 | with:2/-0.21 against:31/-0.31 inside:29/-0.50 | ambiguous:62/-0.39 |
| nov17 | v9 | 47 | 13.6 | 44 | -0.272 | 0.41 | 0.59 | 1 | 45 | -0.618 | with:2/-0.21 against:23/-0.10 inside:19/-0.49 | support:7/-0.31 resistance:5/-0.29 ambiguous:32/-0.26 |
| nov17 | v10 | 47 | 13.6 | 44 | -0.272 | 0.41 | 0.59 | 1 | 45 | -0.618 | with:2/-0.21 against:23/-0.10 inside:19/-0.49 | support:7/-0.31 resistance:5/-0.29 ambiguous:32/-0.26 |
| nov17 | v12 | 43 | 12.5 | 38 | -0.374 | 0.34 | 0.66 | 1 | 41 | -0.711 | with:1/0.49 against:13/-0.49 inside:24/-0.35 | support:8/-0.10 resistance:4/-0.56 ambiguous:26/-0.43 |
| nov17 | v13 | 2 | 0.6 | 1 | 0.353 | 1.00 | 0.00 | 1 | 1 | -1.000 | with:0/n/a against:1/0.35 inside:0/n/a | ambiguous:1/0.35 |
| dump | v7 | 294 | 31.8 | 271 | -0.041 | 0.47 | 0.52 | 23 | 257 | -0.106 | with:23/0.47 against:133/-0.11 inside:115/-0.06 | ambiguous:271/-0.04 |
| dump | v8 | 294 | 31.8 | 271 | -0.041 | 0.47 | 0.52 | 23 | 254 | -0.018 | with:23/0.47 against:133/-0.11 inside:115/-0.06 | ambiguous:271/-0.04 |
| dump | v9 | 215 | 23.3 | 199 | -0.065 | 0.47 | 0.53 | 16 | 183 | -0.056 | with:18/0.56 against:96/-0.14 inside:85/-0.12 | support:14/-0.28 resistance:14/0.03 ambiguous:171/-0.06 |
| dump | v10 | 214 | 23.2 | 198 | -0.061 | 0.47 | 0.53 | 16 | 182 | -0.051 | with:17/0.65 against:96/-0.14 inside:85/-0.12 | support:14/-0.28 resistance:14/0.03 ambiguous:170/-0.05 |
| dump | v12 | 144 | 15.6 | 141 | -0.115 | 0.45 | 0.55 | 3 | 129 | -0.084 | with:3/0.02 against:50/-0.30 inside:88/-0.01 | support:13/-0.46 resistance:7/0.30 ambiguous:121/-0.10 |
| dump | v13 | 1 | 0.1 | 1 | -0.914 | 0.00 | 1.00 | 0 | 1 | -1.000 | with:0/n/a against:1/-0.91 inside:0/n/a | ambiguous:1/-0.91 |

### B2 step deltas (live-geometry expectancy per labelled setup)

| set | step | setups from | setups to | exp from | exp to | delta | removed | removed n | removed exp | added | added n | added exp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sep | v7->v8 | 204 | 204 | -0.138 | -0.138 | 0.000 | 0 | 0 | n/a | 0 | 0 | n/a |
| sep | v8->v9 | 204 | 134 | -0.138 | -0.118 | 0.020 | 71 | 68 | -0.170 | 1 | 1 | 0.243 |
| sep | v9->v10 | 134 | 133 | -0.118 | -0.112 | 0.006 | 1 | 1 | -0.914 | 0 | 0 | n/a |
| sep | v10->v12 | 133 | 127 | -0.112 | -0.093 | 0.019 | 39 | 34 | -0.127 | 33 | 32 | -0.142 |
| sep | v12->v13 | 127 | 8 | -0.093 | 0.503 | 0.595 | 124 | 122 | -0.115 | 5 | 5 | 0.198 |
| nov13 | v7->v8 | 25 | 25 | -0.476 | -0.476 | 0.000 | 0 | 0 | n/a | 0 | 0 | n/a |
| nov13 | v8->v9 | 25 | 17 | -0.476 | -0.676 | -0.200 | 8 | 7 | -0.134 | 0 | 0 | n/a |
| nov13 | v9->v10 | 17 | 16 | -0.676 | -0.654 | 0.022 | 1 | 1 | -0.913 | 0 | 0 | n/a |
| nov13 | v10->v12 | 16 | 18 | -0.654 | -0.446 | 0.208 | 0 | 0 | n/a | 2 | 2 | 0.686 |
| nov13 | v12->v13 | 18 | 0 | -0.446 | n/a | n/a | 18 | 13 | -0.446 | 0 | 0 | n/a |
| nov17 | v7->v8 | 66 | 66 | -0.395 | -0.395 | 0.000 | 0 | 0 | n/a | 0 | 0 | n/a |
| nov17 | v8->v9 | 66 | 47 | -0.395 | -0.272 | 0.122 | 19 | 18 | -0.693 | 0 | 0 | n/a |
| nov17 | v9->v10 | 47 | 47 | -0.272 | -0.272 | 0.000 | 0 | 0 | n/a | 0 | 0 | n/a |
| nov17 | v10->v12 | 47 | 43 | -0.272 | -0.374 | -0.102 | 11 | 9 | 0.204 | 7 | 3 | -0.143 |
| nov17 | v12->v13 | 43 | 2 | -0.374 | 0.353 | 0.727 | 42 | 38 | -0.374 | 1 | 1 | 0.353 |
| dump | v7->v8 | 294 | 294 | -0.041 | -0.041 | 0.000 | 0 | 0 | n/a | 0 | 0 | n/a |
| dump | v8->v9 | 294 | 215 | -0.041 | -0.065 | -0.025 | 79 | 72 | -0.001 | 0 | 0 | n/a |
| dump | v9->v10 | 215 | 214 | -0.065 | -0.061 | 0.004 | 1 | 1 | -0.914 | 0 | 0 | n/a |
| dump | v10->v12 | 214 | 144 | -0.061 | -0.115 | -0.054 | 88 | 75 | 0.022 | 18 | 18 | -0.152 |
| dump | v12->v13 | 144 | 1 | -0.115 | -0.914 | -0.799 | 143 | 140 | -0.109 | 0 | 0 | n/a |


### B1/B2 extension: v6 -> v7 (deep entry, PR #859; the live data pointed here)

| set | build | setups | per day | labelled n | exp R | win | full stop | missed | default n | default exp | by approach | by role |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sep | v6 | 59 | 8.0 | 59 | -0.139 | 0.44 | 0.54 | 0 | 55 | -0.532 | with:0/n/a against:18/-0.13 inside:41/-0.14 | ambiguous:59/-0.14 |
| sep | v7 | 204 | 27.5 | 194 | -0.138 | 0.42 | 0.58 | 9 | 177 | -0.302 | with:5/-0.28 against:99/-0.12 inside:90/-0.15 | ambiguous:194/-0.14 |
| nov13 | v6 | 10 | 7.4 | 9 | -0.590 | 0.22 | 0.78 | 1 | 9 | -0.774 | with:0/n/a against:5/-0.34 inside:4/-0.91 | ambiguous:9/-0.59 |
| nov13 | v7 | 25 | 18.4 | 19 | -0.476 | 0.21 | 0.79 | 1 | 24 | -0.330 | with:0/n/a against:13/-0.28 inside:6/-0.91 | ambiguous:19/-0.48 |
| nov17 | v6 | 16 | 4.6 | 15 | -0.520 | 0.27 | 0.73 | 0 | 16 | -0.855 | with:0/n/a against:6/-0.39 inside:9/-0.60 | ambiguous:15/-0.52 |
| nov17 | v7 | 66 | 19.1 | 62 | -0.395 | 0.34 | 0.66 | 2 | 63 | -0.487 | with:2/-0.21 against:31/-0.31 inside:29/-0.50 | ambiguous:62/-0.39 |
| dump | v6 | 37 | 4.0 | 36 | -0.176 | 0.44 | 0.56 | 1 | 34 | -0.220 | with:2/0.70 against:14/-0.27 inside:20/-0.20 | ambiguous:36/-0.18 |
| dump | v7 | 294 | 31.8 | 271 | -0.041 | 0.47 | 0.52 | 23 | 257 | -0.106 | with:23/0.47 against:133/-0.11 inside:115/-0.06 | ambiguous:271/-0.04 |

| set | step | setups from | setups to | exp from | exp to | delta | removed | removed n | removed exp | added | added n | added exp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sep | v6->v7 | 59 | 204 | -0.139 | -0.138 | 0.001 | 2 | 2 | -0.913 | 147 | 137 | -0.147 |
| nov13 | v6->v7 | 10 | 25 | -0.590 | -0.476 | 0.114 | 1 | 1 | 0.492 | 16 | 11 | -0.295 |
| nov17 | v6->v7 | 16 | 66 | -0.520 | -0.395 | 0.126 | 0 | 0 | n/a | 50 | 47 | -0.354 |
| dump | v6->v7 | 37 | 294 | -0.176 | -0.041 | 0.135 | 0 | 0 | n/a | 257 | 235 | -0.020 |

## Part C: why v13 is near-silent

| set | M5 evals | major levels in book per eval | refused | reaction w/o sweep | stop budget | published | v12 setups | v12 class in v13 | minor: rule failed | minor-only n/exp | major-dropped n/exp | not-in-book n/exp | v13 n/exp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sep | 1500 | round|H1:0.11 session|H1:2.51 swing_h1|H1:0.31 swing_m15|M15:0.01 | minor_level:1 no_m5_reaction:2182 | 115 | 12 | 8 | 127 | kept:3 major:38 minor:4 not_in_book:82 | reactions:2 session_level_broken:2 | 4/0.244 | 38/0.031 | 80/-0.202 | 7/0.503 |
| nov13 | 380 | round|H1:0.38 session|H1:0.99 swing_h1|H1:0.11 swing_m15|M15:0.35 swing_m5|M5:0.01 | minor_level:6 no_m5_reaction:465 | 0 | 2 | 0 | 18 | major:3 minor:15 | reactions:5 round_level_broken:1 session_level_broken:9 | 12/-0.407 | 1/-0.914 | 0/n/a | 0/n/a |
| nov17 | 394 | round|H1:0.03 session|H1:2.89 swing_h1|H1:0.32 swing_m5|M5:0.01 | no_m5_reaction:689 | 18 | 5 | 2 | 43 | kept:1 major:14 not_in_book:28 | - | 0/n/a | 11/-0.655 | 27/-0.260 | 1/0.353 |
| dump | 2000 | round|H1:0.17 session|H1:2.19 swing_h1|H1:0.25 swing_m15|M15:0.38 swing_m5|M5:0.00 | minor_level:11 no_m5_reaction:3437 | 40 | 4 | 1 | 144 | kept:1 major:32 minor:23 not_in_book:88 | reactions:13 round_level_broken:1 session_level_broken:9 | 23/-0.011 | 32/0.113 | 85/-0.220 | 1/-0.914 |

## Pre-registered decision

- v8->v9 expectancy delta per set: sep 0.020, nov13 -0.200, nov17 0.122, dump -0.025 (R1 fires when <= -0.15 on >= 2 sets: no)
- v9->v10 expectancy delta per set: sep 0.006, nov13 0.022, nov17 0.000, dump 0.004 (R2: no)
- **R4: no component named; the 9 Nov drop is not reproduced on bars** (Part A's strongest split is reported above)
- v12 setups v13 drops, by class over all sets: {'major': 87, 'minor': 42, 'not_in_book': 198}; the largest is **not_in_book**
- **R5: v13 sparsity is selection, keep** (minor-only setups met the bar on no set; needs 2)

## Setups each step removes or adds

<details><summary>Setups each step removes or adds</summary>

| set | step | kind | key | role | approach | outcome | R |
|---|---|---|---|---|---|---|---|
| sep | v8->v9 | removed | SELL|1789368600|0 | ambiguous | inside | missed | n/a |
| sep | v8->v9 | removed | SELL|1789372800|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v8->v9 | removed | SELL|1789375500|0 | ambiguous | against | tp4_full | 2.29 |
| sep | v8->v9 | removed | SELL|1789386600|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789395900|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789400700|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789415100|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789423200|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789427700|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789428300|0 | ambiguous | inside | stop | -0.90 |
| sep | v8->v9 | removed | BUY|1789434600|0 | ambiguous | against | tp2_then_stop | 0.86 |
| sep | v8->v9 | removed | BUY|1789436700|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789439700|0 | ambiguous | against | stop | -0.94 |
| sep | v8->v9 | removed | BUY|1789440300|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789451700|0 | ambiguous | inside | tp4_full | 1.32 |
| sep | v8->v9 | removed | SELL|1789457700|0 | ambiguous | against | tp3_then_stop | 1.49 |
| sep | v8->v9 | removed | SELL|1789474200|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789478100|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789483500|0 | ambiguous | against | stop | -1.00 |
| sep | v8->v9 | removed | BUY|1789493400|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| sep | v8->v9 | removed | BUY|1789495200|0 | ambiguous | inside | tp2_then_stop | 0.90 |
| sep | v8->v9 | removed | SELL|1789512900|0 | ambiguous | against | tp2_then_stop | 0.48 |
| sep | v8->v9 | removed | SELL|1789516800|0 | ambiguous | inside | stop | -0.89 |
| sep | v8->v9 | removed | SELL|1789519800|0 | ambiguous | against | stop | -0.89 |
| sep | v8->v9 | removed | SELL|1789520400|0 | ambiguous | against | stop | -0.90 |
| sep | v8->v9 | removed | SELL|1789523700|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789525200|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789533600|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789537200|0 | ambiguous | inside | stop | -0.90 |
| sep | v8->v9 | removed | BUY|1789541700|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v8->v9 | removed | BUY|1789542600|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789546500|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789548900|0 | ambiguous | against | tp4_full | 1.79 |
| sep | v8->v9 | removed | BUY|1789551300|0 | ambiguous | against | tp3_then_stop | 1.49 |
| sep | v8->v9 | removed | BUY|1789553100|0 | ambiguous | against | tp2_then_stop | 0.89 |
| sep | v8->v9 | removed | BUY|1789564200|0 | ambiguous | against | stop | -1.00 |
| sep | v8->v9 | removed | BUY|1789570500|0 | ambiguous | against | stop | -1.00 |
| sep | v8->v9 | removed | BUY|1789571100|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789577400|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789580100|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789580400|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789581300|0 | ambiguous | against | stop | -1.00 |
| sep | v8->v9 | removed | BUY|1789629000|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789642500|0 | ambiguous | against | missed | n/a |
| sep | v8->v9 | removed | SELL|1789666500|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| sep | v8->v9 | removed | SELL|1789669500|0 | ambiguous | against | tp3_then_stop | 1.46 |
| sep | v8->v9 | removed | SELL|1789669800|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| sep | v8->v9 | removed | BUY|1789689000|0 | ambiguous | against | tp2_then_stop | 0.90 |
| sep | v8->v9 | removed | BUY|1789704000|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789706400|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v8->v9 | removed | BUY|1789706700|0 | ambiguous | against | tp4_full | 2.26 |
| sep | v8->v9 | removed | BUY|1789707300|0 | ambiguous | against | tp4_full | 2.29 |
| sep | v8->v9 | removed | BUY|1789717500|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | BUY|1789718400|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789719900|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789723500|0 | ambiguous | against | stop | -0.94 |
| sep | v8->v9 | removed | SELL|1789745700|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789746600|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789761600|0 | ambiguous | against | open_at_horizon | -0.27 |
| sep | v8->v9 | removed | SELL|1789941900|0 | ambiguous | inside | tp2_then_stop | 0.67 |
| sep | v8->v9 | removed | SELL|1789962900|0 | ambiguous | against | tp2_then_stop | 0.89 |
| sep | v8->v9 | removed | SELL|1789964700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v8->v9 | removed | SELL|1789966200|0 | ambiguous | against | tp2_then_stop | 0.89 |
| sep | v8->v9 | removed | SELL|1789968300|0 | ambiguous | inside | tp1_then_stop | 0.50 |
| sep | v8->v9 | removed | SELL|1789968600|0 | ambiguous | inside | tp2_then_stop | 0.67 |
| sep | v8->v9 | removed | SELL|1789977300|0 | ambiguous | against | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789982700|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789983000|0 | ambiguous | against | stop | -0.94 |
| sep | v8->v9 | removed | SELL|1789983900|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789988100|0 | ambiguous | inside | stop | -0.91 |
| sep | v8->v9 | removed | SELL|1789999200|0 | ambiguous | against | missed | n/a |
| sep | v8->v9 | added | SELL|1789993500|0 | resistance | inside | tp1_then_stop | 0.24 |
| sep | v9->v10 | removed | BUY|1789979700|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | removed | BUY|1789397700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v10->v12 | removed | BUY|1789398300|0 | ambiguous | against | tp4_full | 2.20 |
| sep | v10->v12 | removed | BUY|1789399500|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | removed | BUY|1789404300|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v10->v12 | removed | BUY|1789433100|0 | support | inside | stop | -0.90 |
| sep | v10->v12 | removed | BUY|1789434000|0 | support | inside | stop | -1.00 |
| sep | v10->v12 | removed | BUY|1789437600|0 | ambiguous | against | stop | -1.00 |
| sep | v10->v12 | removed | BUY|1789447200|0 | ambiguous | against | stop | -0.91 |
| sep | v10->v12 | removed | BUY|1789454700|0 | ambiguous | against | stop | -0.94 |
| sep | v10->v12 | removed | SELL|1789473900|0 | ambiguous | against | stop | -1.00 |
| sep | v10->v12 | removed | BUY|1789476300|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v10->v12 | removed | SELL|1789491600|0 | ambiguous | against | stop | -0.91 |
| sep | v10->v12 | removed | SELL|1789545000|0 | resistance | against | stop | -0.94 |
| sep | v10->v12 | removed | SELL|1789547400|0 | resistance | against | stop | -0.94 |
| sep | v10->v12 | removed | BUY|1789568100|0 | ambiguous | with | stop | -0.94 |
| sep | v10->v12 | removed | BUY|1789581900|0 | ambiguous | against | stop | -0.94 |
| sep | v10->v12 | removed | BUY|1789652700|0 | support | with | tp2_then_stop | 0.89 |
| sep | v10->v12 | removed | BUY|1789657800|0 | support | inside | tp2_then_stop | 0.80 |
| sep | v10->v12 | removed | BUY|1789659300|0 | support | inside | stop | -0.91 |
| sep | v10->v12 | removed | BUY|1789694100|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | removed | BUY|1789701900|0 | support | against | missed | n/a |
| sep | v10->v12 | removed | BUY|1789703100|0 | ambiguous | against | tp4_full | 2.26 |
| sep | v10->v12 | removed | BUY|1789721700|0 | support | against | stop | -0.94 |
| sep | v10->v12 | removed | BUY|1789734600|0 | ambiguous | against | stop | -1.00 |
| sep | v10->v12 | removed | SELL|1789743300|0 | ambiguous | against | tp1_then_stop | 0.41 |
| sep | v10->v12 | removed | BUY|1789744500|0 | ambiguous | against | tp4_full | 2.26 |
| sep | v10->v12 | removed | BUY|1789746000|0 | ambiguous | inside | tp4_full | 1.32 |
| sep | v10->v12 | removed | BUY|1789752600|0 | ambiguous | against | stop | -0.91 |
| sep | v10->v12 | removed | BUY|1789752900|0 | support | against | stop | -1.00 |
| sep | v10->v12 | removed | BUY|1789952700|0 | ambiguous | against | tp1_then_stop | 0.35 |
| sep | v10->v12 | removed | SELL|1789953900|0 | ambiguous | against | stop | -0.94 |
| sep | v10->v12 | removed | SELL|1789959900|0 | ambiguous | against | tp3_then_stop | 1.46 |
| sep | v10->v12 | removed | SELL|1789962600|0 | ambiguous | against | tp3_then_stop | 1.40 |
| sep | v10->v12 | removed | SELL|1789993500|0 | resistance | inside | tp1_then_stop | 0.24 |
| sep | v10->v12 | removed | BUY|1789995900|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | removed | SELL|1789997100|0 | resistance | inside | missed | n/a |
| sep | v10->v12 | removed | SELL|1789997400|0 | ambiguous | against | missed | n/a |
| sep | v10->v12 | removed | SELL|1789998600|0 | ambiguous | against | missed | n/a |
| sep | v10->v12 | removed | BUY|1790000700|0 | support | inside | open_at_bars_end | n/a |
| sep | v10->v12 | added | BUY|1789361700|0 | support | inside | tp1_then_stop | 0.49 |
| sep | v10->v12 | added | BUY|1789433400|0 | support | inside | stop | -0.90 |
| sep | v10->v12 | added | BUY|1789434900|0 | ambiguous | against | tp2_then_stop | 0.89 |
| sep | v10->v12 | added | BUY|1789469400|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789475700|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789479600|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789479900|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789482900|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789487700|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789495800|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789512900|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v10->v12 | added | BUY|1789521000|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v10->v12 | added | BUY|1789521600|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789523700|0 | ambiguous | against | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789537200|0 | support | against | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789546500|0 | support | inside | tp4_full | 2.29 |
| sep | v10->v12 | added | BUY|1789548000|0 | support | inside | tp4_full | 2.29 |
| sep | v10->v12 | added | BUY|1789548900|0 | ambiguous | inside | tp4_full | 1.79 |
| sep | v10->v12 | added | BUY|1789560300|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789569900|0 | ambiguous | against | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789580400|0 | ambiguous | against | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789629000|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789634100|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789642500|0 | ambiguous | against | missed | n/a |
| sep | v10->v12 | added | SELL|1789669500|0 | resistance | inside | tp3_then_stop | 1.49 |
| sep | v10->v12 | added | SELL|1789669800|0 | ambiguous | against | stop | -0.91 |
| sep | v10->v12 | added | BUY|1789686600|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| sep | v10->v12 | added | SELL|1789740900|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| sep | v10->v12 | added | SELL|1789743900|0 | ambiguous | inside | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789951500|0 | resistance | inside | stop | -0.91 |
| sep | v10->v12 | added | SELL|1789971300|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v10->v12 | added | SELL|1789993800|0 | resistance | inside | tp1_then_stop | 0.24 |
| sep | v10->v12 | added | BUY|1789996200|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789361700|0 | support | inside | tp1_then_stop | 0.49 |
| sep | v12->v13 | removed | SELL|1789398600|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789407000|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789410300|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789411200|0 | ambiguous | inside | tp3_then_stop | 1.15 |
| sep | v12->v13 | removed | SELL|1789415700|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789418100|0 | resistance | against | tp1_then_stop | 0.49 |
| sep | v12->v13 | removed | SELL|1789418400|0 | resistance | against | tp1_then_stop | 0.49 |
| sep | v12->v13 | removed | SELL|1789426800|0 | ambiguous | inside | tp1_then_stop | 0.50 |
| sep | v12->v13 | removed | BUY|1789430700|0 | ambiguous | inside | tp4_full | 2.31 |
| sep | v12->v13 | removed | BUY|1789431600|0 | ambiguous | inside | tp1_then_stop | 0.50 |
| sep | v12->v13 | removed | SELL|1789432200|0 | ambiguous | inside | stop | -0.90 |
| sep | v12->v13 | removed | BUY|1789432500|0 | support | against | tp4_full | 1.79 |
| sep | v12->v13 | removed | BUY|1789433400|0 | support | inside | stop | -0.90 |
| sep | v12->v13 | removed | BUY|1789434900|0 | ambiguous | against | tp2_then_stop | 0.89 |
| sep | v12->v13 | removed | BUY|1789439100|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789439400|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789445100|0 | ambiguous | inside | stop | -0.90 |
| sep | v12->v13 | removed | BUY|1789445400|0 | ambiguous | inside | stop | -0.90 |
| sep | v12->v13 | removed | BUY|1789452900|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789454100|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789455000|0 | ambiguous | inside | stop | -0.89 |
| sep | v12->v13 | removed | SELL|1789456200|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v12->v13 | removed | BUY|1789456500|0 | ambiguous | inside | stop | -0.90 |
| sep | v12->v13 | removed | BUY|1789457100|0 | ambiguous | inside | stop | -0.90 |
| sep | v12->v13 | removed | BUY|1789469400|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789475700|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789479600|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789479900|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789481400|0 | resistance | inside | tp2_then_stop | 0.67 |
| sep | v12->v13 | removed | SELL|1789482300|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789482900|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789487700|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789489800|0 | resistance | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789492200|0 | resistance | with | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789494000|0 | resistance | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789495800|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789512000|0 | resistance | against | tp2_then_stop | 0.91 |
| sep | v12->v13 | removed | SELL|1789512900|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v12->v13 | removed | SELL|1789513200|0 | ambiguous | against | tp1_then_stop | 0.49 |
| sep | v12->v13 | removed | SELL|1789514400|0 | ambiguous | inside | tp1_then_stop | 0.52 |
| sep | v12->v13 | removed | SELL|1789515300|0 | ambiguous | against | tp1_then_stop | 0.51 |
| sep | v12->v13 | removed | BUY|1789517700|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789520700|0 | support | inside | tp2_then_stop | 0.91 |
| sep | v12->v13 | removed | SELL|1789521300|0 | resistance | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789521600|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789522500|0 | resistance | inside | stop | -0.90 |
| sep | v12->v13 | removed | SELL|1789523400|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789523700|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789535100|0 | support | inside | tp1_then_stop | 0.51 |
| sep | v12->v13 | removed | BUY|1789537200|0 | support | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789546500|0 | support | inside | tp4_full | 2.29 |
| sep | v12->v13 | removed | BUY|1789548000|0 | support | inside | tp4_full | 2.29 |
| sep | v12->v13 | removed | BUY|1789548900|0 | ambiguous | inside | tp4_full | 1.79 |
| sep | v12->v13 | removed | BUY|1789560300|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789561200|0 | ambiguous | against | stop | -0.90 |
| sep | v12->v13 | removed | BUY|1789561800|0 | ambiguous | inside | tp2_then_stop | 0.48 |
| sep | v12->v13 | removed | SELL|1789567200|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789567800|0 | ambiguous | against | tp4_full | 2.29 |
| sep | v12->v13 | removed | BUY|1789568700|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789569300|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789569900|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789580400|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789581000|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789622100|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789629000|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789634100|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789637100|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789638600|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v12->v13 | removed | BUY|1789640400|0 | ambiguous | against | missed | n/a |
| sep | v12->v13 | removed | BUY|1789642500|0 | ambiguous | against | missed | n/a |
| sep | v12->v13 | removed | BUY|1789663800|0 | support | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789664100|0 | support | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789669500|0 | resistance | inside | tp3_then_stop | 1.49 |
| sep | v12->v13 | removed | SELL|1789669800|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789686600|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| sep | v12->v13 | removed | BUY|1789689900|0 | ambiguous | inside | tp2_then_stop | 0.67 |
| sep | v12->v13 | removed | BUY|1789690200|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v12->v13 | removed | BUY|1789690500|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v12->v13 | removed | BUY|1789690800|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v12->v13 | removed | BUY|1789691400|0 | ambiguous | inside | tp1_then_stop | 0.50 |
| sep | v12->v13 | removed | SELL|1789692300|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789692900|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v12->v13 | removed | BUY|1789693500|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789695000|0 | ambiguous | inside | tp2_then_stop | 0.67 |
| sep | v12->v13 | removed | BUY|1789697400|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789698000|0 | support | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789698600|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789699800|0 | ambiguous | against | tp2_then_stop | 0.67 |
| sep | v12->v13 | removed | SELL|1789700700|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v12->v13 | removed | SELL|1789701300|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789702200|0 | ambiguous | against | tp1_then_stop | 0.35 |
| sep | v12->v13 | removed | BUY|1789702800|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v12->v13 | removed | BUY|1789705200|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v12->v13 | removed | BUY|1789721400|0 | support | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789732500|0 | support | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789740900|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| sep | v12->v13 | removed | SELL|1789743900|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789747800|0 | ambiguous | against | tp4_full | 1.32 |
| sep | v12->v13 | removed | BUY|1789750800|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| sep | v12->v13 | removed | BUY|1789755000|0 | support | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789758300|0 | ambiguous | inside | stop | -0.90 |
| sep | v12->v13 | removed | BUY|1789946700|0 | ambiguous | inside | stop | -0.90 |
| sep | v12->v13 | removed | SELL|1789947600|0 | ambiguous | inside | tp2_then_stop | 0.68 |
| sep | v12->v13 | removed | SELL|1789947900|0 | ambiguous | against | tp1_then_stop | 0.51 |
| sep | v12->v13 | removed | BUY|1789950000|0 | ambiguous | inside | tp2_then_stop | 0.67 |
| sep | v12->v13 | removed | BUY|1789950900|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789951200|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789951500|0 | resistance | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789953000|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789956000|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789956300|0 | ambiguous | against | tp4_full | 1.79 |
| sep | v12->v13 | removed | BUY|1789960200|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | SELL|1789971300|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v12->v13 | removed | BUY|1789983600|0 | support | inside | tp1_then_stop | 0.49 |
| sep | v12->v13 | removed | SELL|1789986900|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789990800|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v12->v13 | removed | SELL|1789993200|0 | resistance | inside | tp4_full | 2.29 |
| sep | v12->v13 | removed | SELL|1789993800|0 | resistance | inside | tp1_then_stop | 0.24 |
| sep | v12->v13 | removed | BUY|1789994100|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v12->v13 | removed | BUY|1789994400|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v12->v13 | removed | SELL|1789994700|0 | ambiguous | against | stop | -0.91 |
| sep | v12->v13 | removed | BUY|1789995300|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| sep | v12->v13 | removed | BUY|1789996200|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | added | BUY|1789519200|0 | ambiguous | inside | stop | -0.91 |
| sep | v12->v13 | added | BUY|1789527300|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| sep | v12->v13 | added | SELL|1789565400|0 | ambiguous | inside | tp3_then_stop | 0.84 |
| sep | v12->v13 | added | SELL|1789969500|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v12->v13 | added | BUY|1789970700|0 | ambiguous | inside | stop | -0.91 |
| nov13 | v8->v9 | removed | BUY|1794455400|0 | ambiguous | inside | stop | -0.91 |
| nov13 | v8->v9 | removed | SELL|1794457500|0 | ambiguous | against | stop | -0.90 |
| nov13 | v8->v9 | removed | SELL|1794460200|0 | ambiguous | against | stop | -0.91 |
| nov13 | v8->v9 | removed | SELL|1794465000|0 | ambiguous | against | tp4_full | 1.32 |
| nov13 | v8->v9 | removed | BUY|1794530100|0 | ambiguous | against | tp4_full | 2.29 |
| nov13 | v8->v9 | removed | SELL|1794557700|0 | ambiguous | against | stop | -0.91 |
| nov13 | v8->v9 | removed | BUY|1794563700|0 | ambiguous | against | stop | -0.91 |
| nov13 | v8->v9 | removed | SELL|1794566100|0 | ambiguous | against | open_at_bars_end | n/a |
| nov13 | v9->v10 | removed | BUY|1794463200|0 | ambiguous | inside | stop | -0.91 |
| nov13 | v10->v12 | added | BUY|1794455100|0 | support | against | stop | -0.91 |
| nov13 | v10->v12 | added | BUY|1794530100|0 | ambiguous | against | tp4_full | 2.29 |
| nov13 | v12->v13 | removed | BUY|1794455100|0 | support | against | stop | -0.91 |
| nov13 | v12->v13 | removed | BUY|1794459600|0 | support | against | stop | -0.88 |
| nov13 | v12->v13 | removed | BUY|1794508500|0 | ambiguous | against | stop | -0.91 |
| nov13 | v12->v13 | removed | SELL|1794510000|0 | ambiguous | against | stop | -0.91 |
| nov13 | v12->v13 | removed | BUY|1794513600|0 | ambiguous | inside | stop | -0.91 |
| nov13 | v12->v13 | removed | SELL|1794515400|0 | ambiguous | against | tp1_then_stop | 0.49 |
| nov13 | v12->v13 | removed | SELL|1794516600|0 | ambiguous | against | stop | -0.91 |
| nov13 | v12->v13 | removed | BUY|1794527100|0 | ambiguous | against | tp1_then_stop | 0.51 |
| nov13 | v12->v13 | removed | SELL|1794529200|0 | resistance | inside | stop | -0.89 |
| nov13 | v12->v13 | removed | BUY|1794530100|0 | ambiguous | against | tp4_full | 2.29 |
| nov13 | v12->v13 | removed | SELL|1794531000|0 | ambiguous | against | stop | -0.91 |
| nov13 | v12->v13 | removed | SELL|1794543300|0 | ambiguous | against | open_at_bars_end | n/a |
| nov13 | v12->v13 | removed | BUY|1794546300|0 | ambiguous | inside | stop | -0.91 |
| nov13 | v12->v13 | removed | SELL|1794547800|0 | ambiguous | against | open_at_bars_end | n/a |
| nov13 | v12->v13 | removed | SELL|1794558600|0 | ambiguous | inside | stop | -0.91 |
| nov13 | v12->v13 | removed | SELL|1794564900|0 | resistance | inside | open_at_bars_end | n/a |
| nov13 | v12->v13 | removed | SELL|1794566700|0 | ambiguous | against | open_at_bars_end | n/a |
| nov13 | v12->v13 | removed | SELL|1794569100|0 | ambiguous | inside | missed | n/a |
| nov17 | v8->v9 | removed | SELL|1794794100|0 | ambiguous | against | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794798300|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| nov17 | v8->v9 | removed | SELL|1794799800|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794801900|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| nov17 | v8->v9 | removed | SELL|1794804600|0 | ambiguous | against | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794807000|0 | ambiguous | against | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794822600|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v8->v9 | removed | BUY|1794827700|0 | ambiguous | against | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794830700|0 | ambiguous | inside | stop | -0.90 |
| nov17 | v8->v9 | removed | SELL|1794832200|0 | ambiguous | against | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794834600|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794836100|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794836700|0 | ambiguous | against | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794852000|0 | ambiguous | against | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794861300|0 | ambiguous | against | stop | -0.91 |
| nov17 | v8->v9 | removed | SELL|1794865800|0 | ambiguous | inside | stop | -0.89 |
| nov17 | v8->v9 | removed | SELL|1794870000|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v8->v9 | removed | BUY|1794882900|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| nov17 | v8->v9 | removed | BUY|1794899100|0 | ambiguous | against | missed | n/a |
| nov17 | v10->v12 | removed | SELL|1794796200|0 | ambiguous | against | tp4_full | 2.20 |
| nov17 | v10->v12 | removed | SELL|1794838800|0 | ambiguous | against | stop | -0.91 |
| nov17 | v10->v12 | removed | BUY|1794839700|0 | ambiguous | against | tp1_then_stop | 0.49 |
| nov17 | v10->v12 | removed | SELL|1794841500|0 | resistance | against | tp2_then_stop | 0.80 |
| nov17 | v10->v12 | removed | SELL|1794842400|0 | ambiguous | against | tp1_then_stop | 0.24 |
| nov17 | v10->v12 | removed | SELL|1794844500|0 | ambiguous | against | stop | -0.91 |
| nov17 | v10->v12 | removed | BUY|1794857100|0 | ambiguous | against | stop | -0.94 |
| nov17 | v10->v12 | removed | SELL|1794876600|0 | ambiguous | against | tp1_then_stop | 0.46 |
| nov17 | v10->v12 | removed | BUY|1794879300|0 | ambiguous | against | missed | n/a |
| nov17 | v10->v12 | removed | BUY|1794880500|0 | ambiguous | against | tp1_then_stop | 0.41 |
| nov17 | v10->v12 | removed | SELL|1794898800|0 | resistance | with | open_at_bars_end | n/a |
| nov17 | v10->v12 | added | BUY|1794604500|0 | support | inside | open_at_horizon | 1.40 |
| nov17 | v10->v12 | added | SELL|1794834900|0 | ambiguous | against | stop | -0.91 |
| nov17 | v10->v12 | added | BUY|1794885600|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v10->v12 | added | SELL|1794894600|0 | ambiguous | inside | open_at_bars_end | n/a |
| nov17 | v10->v12 | added | SELL|1794895800|0 | ambiguous | against | open_at_bars_end | n/a |
| nov17 | v10->v12 | added | BUY|1794896700|0 | ambiguous | inside | open_at_bars_end | n/a |
| nov17 | v10->v12 | added | BUY|1794898800|0 | ambiguous | against | missed | n/a |
| nov17 | v12->v13 | removed | BUY|1794604500|0 | support | inside | open_at_horizon | 1.40 |
| nov17 | v12->v13 | removed | SELL|1794784500|0 | resistance | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794799200|0 | support | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794800400|0 | support | inside | tp1_then_stop | 0.49 |
| nov17 | v12->v13 | removed | BUY|1794805500|0 | support | inside | tp1_then_stop | 0.49 |
| nov17 | v12->v13 | removed | BUY|1794818700|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794821100|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| nov17 | v12->v13 | removed | SELL|1794824700|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794833400|0 | support | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794834900|0 | ambiguous | against | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794835800|0 | support | against | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794836400|0 | support | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794839100|0 | ambiguous | against | tp3_then_stop | 1.49 |
| nov17 | v12->v13 | removed | BUY|1794839400|0 | ambiguous | against | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794843600|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794850500|0 | ambiguous | against | tp3_then_stop | 0.84 |
| nov17 | v12->v13 | removed | SELL|1794851100|0 | ambiguous | against | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794854700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| nov17 | v12->v13 | removed | BUY|1794855300|0 | support | inside | tp1_then_stop | 0.50 |
| nov17 | v12->v13 | removed | SELL|1794860100|0 | ambiguous | against | tp1_then_stop | 0.35 |
| nov17 | v12->v13 | removed | SELL|1794863100|0 | ambiguous | against | stop | -0.90 |
| nov17 | v12->v13 | removed | SELL|1794873600|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| nov17 | v12->v13 | removed | SELL|1794874200|0 | ambiguous | inside | tp1_then_stop | 0.36 |
| nov17 | v12->v13 | removed | SELL|1794874500|0 | ambiguous | inside | stop | -0.89 |
| nov17 | v12->v13 | removed | SELL|1794874800|0 | ambiguous | against | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794876000|0 | ambiguous | inside | stop | -0.89 |
| nov17 | v12->v13 | removed | SELL|1794877500|0 | ambiguous | with | tp1_then_stop | 0.49 |
| nov17 | v12->v13 | removed | SELL|1794878100|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794880200|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794882300|0 | resistance | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794883500|0 | resistance | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794885600|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794886500|0 | resistance | inside | tp1_then_stop | 0.49 |
| nov17 | v12->v13 | removed | SELL|1794888000|0 | ambiguous | against | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794889500|0 | ambiguous | inside | stop | -0.90 |
| nov17 | v12->v13 | removed | BUY|1794889800|0 | ambiguous | inside | open_at_bars_end | n/a |
| nov17 | v12->v13 | removed | SELL|1794894600|0 | ambiguous | inside | open_at_bars_end | n/a |
| nov17 | v12->v13 | removed | SELL|1794895800|0 | ambiguous | against | open_at_bars_end | n/a |
| nov17 | v12->v13 | removed | SELL|1794896100|0 | ambiguous | against | stop | -0.91 |
| nov17 | v12->v13 | removed | BUY|1794896700|0 | ambiguous | inside | open_at_bars_end | n/a |
| nov17 | v12->v13 | removed | SELL|1794897900|0 | ambiguous | against | stop | -0.91 |
| nov17 | v12->v13 | removed | SELL|1794898200|0 | ambiguous | against | stop | -0.89 |
| nov17 | v12->v13 | added | BUY|1794785700|0 | ambiguous | against | tp1_then_stop | 0.35 |
| dump | v8->v9 | removed | SELL|1794822600|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | BUY|1794827700|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | BUY|1794839700|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v8->v9 | removed | SELL|1794852000|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1794861300|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1794865800|0 | ambiguous | inside | stop | -0.89 |
| dump | v8->v9 | removed | SELL|1794870000|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | BUY|1794882900|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v8->v9 | removed | BUY|1794899100|0 | ambiguous | against | stop | -1.00 |
| dump | v8->v9 | removed | SELL|1794902400|0 | ambiguous | against | tp2_then_stop | 0.89 |
| dump | v8->v9 | removed | SELL|1794972000|0 | ambiguous | against | tp1_then_stop | 0.46 |
| dump | v8->v9 | removed | SELL|1794973500|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v8->v9 | removed | SELL|1794978300|0 | ambiguous | against | stop | -1.00 |
| dump | v8->v9 | removed | SELL|1794981900|0 | ambiguous | against | tp3_then_stop | 1.15 |
| dump | v8->v9 | removed | SELL|1794982500|0 | ambiguous | against | tp3_then_stop | 1.46 |
| dump | v8->v9 | removed | SELL|1794984300|0 | ambiguous | inside | tp3_then_stop | 0.84 |
| dump | v8->v9 | removed | SELL|1794995700|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795000200|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795001400|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795002300|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795005300|0 | ambiguous | against | stop | -0.94 |
| dump | v8->v9 | removed | SELL|1795014000|0 | ambiguous | inside | missed | n/a |
| dump | v8->v9 | removed | SELL|1795014300|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v8->v9 | removed | SELL|1795017600|0 | ambiguous | against | tp4_full | 1.32 |
| dump | v8->v9 | removed | SELL|1795023600|0 | ambiguous | against | tp3_then_stop | 1.15 |
| dump | v8->v9 | removed | SELL|1795024200|0 | ambiguous | against | tp2_then_stop | 0.80 |
| dump | v8->v9 | removed | SELL|1795028700|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795038000|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795056900|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795067100|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| dump | v8->v9 | removed | SELL|1795089300|0 | ambiguous | against | tp1_then_stop | 0.35 |
| dump | v8->v9 | removed | SELL|1795094100|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795097700|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795101000|0 | ambiguous | against | missed | n/a |
| dump | v8->v9 | removed | SELL|1795117800|0 | ambiguous | against | tp4_full | 2.20 |
| dump | v8->v9 | removed | SELL|1795119900|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v8->v9 | removed | SELL|1795151700|0 | ambiguous | with | tp1_then_stop | 0.41 |
| dump | v8->v9 | removed | SELL|1795159800|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795165800|0 | ambiguous | with | tp2_then_stop | 0.86 |
| dump | v8->v9 | removed | SELL|1795178100|0 | ambiguous | against | stop | -0.94 |
| dump | v8->v9 | removed | BUY|1795181700|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v8->v9 | removed | BUY|1795182300|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795190400|0 | ambiguous | against | stop | -1.00 |
| dump | v8->v9 | removed | SELL|1795191300|0 | ambiguous | inside | missed | n/a |
| dump | v8->v9 | removed | SELL|1795193400|0 | ambiguous | against | tp4_full | 2.20 |
| dump | v8->v9 | removed | SELL|1795194000|0 | ambiguous | against | missed | n/a |
| dump | v8->v9 | removed | SELL|1795194900|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v8->v9 | removed | SELL|1795196700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v8->v9 | removed | SELL|1795198200|0 | ambiguous | with | tp3_then_stop | 1.40 |
| dump | v8->v9 | removed | SELL|1795201800|0 | ambiguous | with | stop | -0.94 |
| dump | v8->v9 | removed | SELL|1795204200|0 | ambiguous | against | tp1_then_stop | 0.46 |
| dump | v8->v9 | removed | SELL|1795206600|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| dump | v8->v9 | removed | SELL|1795207200|0 | ambiguous | with | stop | -0.91 |
| dump | v8->v9 | removed | BUY|1795401900|0 | ambiguous | with | missed | n/a |
| dump | v8->v9 | removed | BUY|1795402200|0 | ambiguous | against | missed | n/a |
| dump | v8->v9 | removed | BUY|1795411500|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v8->v9 | removed | BUY|1795417200|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | BUY|1795440000|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | BUY|1795477800|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v8->v9 | removed | SELL|1795494300|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795500000|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v8->v9 | removed | SELL|1795510500|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795511700|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795515600|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795527600|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795528500|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795545300|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v8->v9 | removed | SELL|1795550700|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795551900|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | BUY|1795562700|0 | ambiguous | against | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795564200|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795564500|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v8->v9 | removed | SELL|1795565100|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v8->v9 | removed | SELL|1795565700|0 | ambiguous | against | tp2_then_stop | 0.86 |
| dump | v8->v9 | removed | SELL|1795567200|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795579200|0 | ambiguous | inside | stop | -0.91 |
| dump | v8->v9 | removed | SELL|1795583100|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v8->v9 | removed | SELL|1795584300|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v8->v9 | removed | SELL|1795584600|0 | ambiguous | with | missed | n/a |
| dump | v9->v10 | removed | BUY|1795009500|0 | ambiguous | with | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1794823500|0 | ambiguous | against | tp2_then_stop | 0.86 |
| dump | v10->v12 | removed | SELL|1794838800|0 | ambiguous | against | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1794841500|0 | resistance | against | tp2_then_stop | 0.80 |
| dump | v10->v12 | removed | SELL|1794842400|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v10->v12 | removed | SELL|1794844500|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1794857100|0 | ambiguous | against | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1794876600|0 | ambiguous | against | tp1_then_stop | 0.46 |
| dump | v10->v12 | removed | BUY|1794879300|0 | ambiguous | against | tp2_then_stop | 0.80 |
| dump | v10->v12 | removed | BUY|1794880500|0 | ambiguous | against | tp1_then_stop | 0.41 |
| dump | v10->v12 | removed | SELL|1794898800|0 | resistance | with | tp4_full | 2.30 |
| dump | v10->v12 | removed | SELL|1794901200|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v10->v12 | removed | BUY|1794925800|0 | ambiguous | against | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1794927300|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1794928800|0 | support | against | stop | -0.94 |
| dump | v10->v12 | removed | BUY|1794929700|0 | ambiguous | inside | stop | -1.00 |
| dump | v10->v12 | removed | SELL|1794931200|0 | resistance | inside | missed | n/a |
| dump | v10->v12 | removed | SELL|1794996000|0 | ambiguous | against | tp4_full | 1.32 |
| dump | v10->v12 | removed | SELL|1795009800|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v10->v12 | removed | SELL|1795012200|0 | ambiguous | against | tp4_full | 1.32 |
| dump | v10->v12 | removed | SELL|1795013100|0 | ambiguous | with | missed | n/a |
| dump | v10->v12 | removed | SELL|1795023000|0 | resistance | inside | tp3_then_stop | 0.84 |
| dump | v10->v12 | removed | BUY|1795050300|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795050900|0 | ambiguous | against | tp1_then_stop | 0.35 |
| dump | v10->v12 | removed | SELL|1795051800|0 | ambiguous | against | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1795053600|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v10->v12 | removed | BUY|1795060800|0 | support | against | tp2_then_stop | 0.89 |
| dump | v10->v12 | removed | SELL|1795065000|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1795066200|0 | ambiguous | with | tp1_then_stop | 0.35 |
| dump | v10->v12 | removed | BUY|1795071600|0 | ambiguous | against | tp1_then_stop | 0.46 |
| dump | v10->v12 | removed | SELL|1795071900|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795075200|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795079400|0 | ambiguous | against | tp4_full | 2.20 |
| dump | v10->v12 | removed | SELL|1795080000|0 | ambiguous | against | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1795081500|0 | ambiguous | against | tp4_full | 2.20 |
| dump | v10->v12 | removed | SELL|1795086600|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v10->v12 | removed | BUY|1795094700|0 | ambiguous | against | stop | -1.00 |
| dump | v10->v12 | removed | BUY|1795168800|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v10->v12 | removed | SELL|1795174800|0 | ambiguous | against | missed | n/a |
| dump | v10->v12 | removed | SELL|1795176000|0 | ambiguous | against | tp1_then_stop | 0.41 |
| dump | v10->v12 | removed | SELL|1795180800|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795183200|0 | ambiguous | against | stop | -1.00 |
| dump | v10->v12 | removed | SELL|1795186800|0 | ambiguous | with | missed | n/a |
| dump | v10->v12 | removed | SELL|1795187100|0 | ambiguous | against | missed | n/a |
| dump | v10->v12 | removed | SELL|1795187700|0 | ambiguous | against | missed | n/a |
| dump | v10->v12 | removed | SELL|1795188000|0 | ambiguous | with | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1795196400|0 | ambiguous | with | tp3_then_stop | 0.84 |
| dump | v10->v12 | removed | SELL|1795201200|0 | resistance | inside | open_at_horizon | -6.00 |
| dump | v10->v12 | removed | BUY|1795208100|0 | ambiguous | with | open_at_horizon | 3.15 |
| dump | v10->v12 | removed | BUY|1795208700|0 | ambiguous | with | open_at_horizon | 3.14 |
| dump | v10->v12 | removed | SELL|1795209000|0 | ambiguous | inside | stop | -1.00 |
| dump | v10->v12 | removed | BUY|1795209600|0 | ambiguous | inside | missed | n/a |
| dump | v10->v12 | removed | BUY|1795211100|0 | ambiguous | against | missed | n/a |
| dump | v10->v12 | removed | SELL|1795400400|0 | resistance | with | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1795419000|0 | resistance | against | tp2_then_stop | 0.86 |
| dump | v10->v12 | removed | BUY|1795422600|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v10->v12 | removed | BUY|1795439100|0 | ambiguous | against | tp1_then_stop | 0.41 |
| dump | v10->v12 | removed | SELL|1795440600|0 | ambiguous | against | stop | -1.00 |
| dump | v10->v12 | removed | BUY|1795452900|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1795457100|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1795481700|0 | ambiguous | against | stop | -1.00 |
| dump | v10->v12 | removed | BUY|1795482600|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795482900|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1795483200|0 | ambiguous | against | stop | -1.00 |
| dump | v10->v12 | removed | SELL|1795488300|0 | ambiguous | inside | missed | n/a |
| dump | v10->v12 | removed | SELL|1795489500|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v10->v12 | removed | BUY|1795495200|0 | support | against | tp1_then_stop | 0.24 |
| dump | v10->v12 | removed | BUY|1795496400|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795497300|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v10->v12 | removed | BUY|1795498200|0 | ambiguous | against | stop | -1.00 |
| dump | v10->v12 | removed | SELL|1795499100|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| dump | v10->v12 | removed | BUY|1795500300|0 | support | with | tp1_then_stop | 0.41 |
| dump | v10->v12 | removed | BUY|1795502700|0 | support | inside | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1795509300|0 | support | against | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1795521600|0 | ambiguous | against | stop | -0.94 |
| dump | v10->v12 | removed | SELL|1795527000|0 | ambiguous | with | tp4_full | 2.20 |
| dump | v10->v12 | removed | SELL|1795532700|0 | ambiguous | inside | missed | n/a |
| dump | v10->v12 | removed | SELL|1795561500|0 | ambiguous | with | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795563300|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1795571400|0 | ambiguous | with | tp3_then_stop | 1.40 |
| dump | v10->v12 | removed | BUY|1795572900|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | removed | BUY|1795573500|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795573800|0 | resistance | with | missed | n/a |
| dump | v10->v12 | removed | SELL|1795574100|0 | ambiguous | inside | missed | n/a |
| dump | v10->v12 | removed | SELL|1795574400|0 | ambiguous | against | missed | n/a |
| dump | v10->v12 | removed | SELL|1795575300|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | removed | SELL|1795577700|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v10->v12 | removed | SELL|1795593000|0 | resistance | against | tp1_then_stop | 0.49 |
| dump | v10->v12 | removed | SELL|1795593600|0 | ambiguous | inside | stop | -0.94 |
| dump | v10->v12 | added | BUY|1794805500|0 | support | inside | tp1_then_stop | 0.35 |
| dump | v10->v12 | added | BUY|1794836400|0 | support | inside | stop | -0.91 |
| dump | v10->v12 | added | BUY|1794885600|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | added | SELL|1794894600|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v10->v12 | added | SELL|1794895800|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v10->v12 | added | BUY|1794896700|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | added | BUY|1794898800|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | added | BUY|1794908700|0 | ambiguous | against | stop | -0.90 |
| dump | v10->v12 | added | SELL|1794910200|0 | ambiguous | against | stop | -0.91 |
| dump | v10->v12 | added | BUY|1794915000|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v10->v12 | added | BUY|1794923700|0 | support | inside | stop | -0.91 |
| dump | v10->v12 | added | SELL|1794936600|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v10->v12 | added | BUY|1794947400|0 | support | inside | stop | -0.91 |
| dump | v10->v12 | added | BUY|1795048500|0 | ambiguous | inside | stop | -0.91 |
| dump | v10->v12 | added | BUY|1795484100|0 | support | inside | stop | -0.91 |
| dump | v10->v12 | added | SELL|1795500000|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| dump | v10->v12 | added | SELL|1795545600|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v10->v12 | added | SELL|1795565700|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794805500|0 | support | inside | tp1_then_stop | 0.35 |
| dump | v12->v13 | removed | SELL|1794806700|0 | resistance | inside | tp4_full | 1.79 |
| dump | v12->v13 | removed | BUY|1794819600|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794820200|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1794821100|0 | resistance | against | tp1_then_stop | 0.24 |
| dump | v12->v13 | removed | BUY|1794825000|0 | ambiguous | against | tp1_then_stop | 0.35 |
| dump | v12->v13 | removed | SELL|1794830400|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1794834900|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794836400|0 | support | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794838500|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v12->v13 | removed | BUY|1794839100|0 | ambiguous | against | tp3_then_stop | 1.49 |
| dump | v12->v13 | removed | BUY|1794839400|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794843600|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794850500|0 | ambiguous | against | tp3_then_stop | 0.84 |
| dump | v12->v13 | removed | SELL|1794851100|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794854700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | BUY|1794855300|0 | support | inside | tp1_then_stop | 0.50 |
| dump | v12->v13 | removed | SELL|1794860100|0 | ambiguous | against | tp1_then_stop | 0.35 |
| dump | v12->v13 | removed | SELL|1794863100|0 | ambiguous | against | stop | -0.90 |
| dump | v12->v13 | removed | SELL|1794873600|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| dump | v12->v13 | removed | SELL|1794874200|0 | ambiguous | inside | tp1_then_stop | 0.36 |
| dump | v12->v13 | removed | SELL|1794874500|0 | ambiguous | inside | stop | -0.89 |
| dump | v12->v13 | removed | SELL|1794874800|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794876000|0 | ambiguous | inside | stop | -0.89 |
| dump | v12->v13 | removed | SELL|1794877500|0 | ambiguous | with | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1794878100|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794880200|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794882300|0 | resistance | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794883500|0 | resistance | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794885600|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794886500|0 | resistance | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1794888000|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794889500|0 | ambiguous | inside | stop | -0.90 |
| dump | v12->v13 | removed | BUY|1794889800|0 | ambiguous | inside | tp2_then_stop | 0.48 |
| dump | v12->v13 | removed | SELL|1794894600|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v12->v13 | removed | SELL|1794895800|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v12->v13 | removed | SELL|1794896100|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794896700|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794897900|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794898200|0 | ambiguous | against | stop | -0.89 |
| dump | v12->v13 | removed | SELL|1794903300|0 | resistance | inside | stop | -0.89 |
| dump | v12->v13 | removed | BUY|1794905100|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794905400|0 | ambiguous | inside | tp3_then_stop | 1.16 |
| dump | v12->v13 | removed | SELL|1794905700|0 | ambiguous | inside | tp1_then_stop | 0.52 |
| dump | v12->v13 | removed | SELL|1794906900|0 | ambiguous | against | stop | -0.90 |
| dump | v12->v13 | removed | BUY|1794908400|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794908700|0 | ambiguous | against | stop | -0.90 |
| dump | v12->v13 | removed | SELL|1794909600|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v12->v13 | removed | SELL|1794910200|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794915000|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v12->v13 | removed | BUY|1794915600|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| dump | v12->v13 | removed | BUY|1794915900|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794917400|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1794918000|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| dump | v12->v13 | removed | SELL|1794918300|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v12->v13 | removed | SELL|1794918600|0 | ambiguous | against | tp3_then_stop | 1.49 |
| dump | v12->v13 | removed | BUY|1794920100|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794920400|0 | support | inside | stop | -0.89 |
| dump | v12->v13 | removed | BUY|1794923700|0 | support | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794926100|0 | ambiguous | with | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1794927600|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794929100|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| dump | v12->v13 | removed | BUY|1794929400|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794934200|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1794936600|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v12->v13 | removed | SELL|1794941100|0 | ambiguous | against | tp2_then_stop | 0.48 |
| dump | v12->v13 | removed | BUY|1794943500|0 | support | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794943800|0 | support | inside | stop | -0.88 |
| dump | v12->v13 | removed | BUY|1794947400|0 | support | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1794951000|0 | ambiguous | inside | stop | -0.89 |
| dump | v12->v13 | removed | BUY|1794957600|0 | ambiguous | inside | stop | -0.88 |
| dump | v12->v13 | removed | SELL|1794958200|0 | ambiguous | inside | missed | n/a |
| dump | v12->v13 | removed | SELL|1795004700|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795007700|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795026900|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795035300|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795043100|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795044300|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795045800|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795048200|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | BUY|1795048500|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795050000|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795051500|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795052400|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795054500|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795055400|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v12->v13 | removed | SELL|1795056000|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1795056300|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795063800|0 | support | inside | tp2_then_stop | 0.89 |
| dump | v12->v13 | removed | BUY|1795064400|0 | support | inside | tp2_then_stop | 0.48 |
| dump | v12->v13 | removed | BUY|1795065300|0 | ambiguous | against | tp2_then_stop | 0.67 |
| dump | v12->v13 | removed | SELL|1795068300|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795070100|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795071000|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795073700|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795077000|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795084200|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| dump | v12->v13 | removed | BUY|1795085100|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795086000|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v12->v13 | removed | BUY|1795087800|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795088400|0 | ambiguous | against | tp2_then_stop | 0.48 |
| dump | v12->v13 | removed | SELL|1795093200|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1795093800|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795172100|0 | ambiguous | against | tp4_full | 1.32 |
| dump | v12->v13 | removed | SELL|1795175700|0 | ambiguous | inside | tp3_then_stop | 1.15 |
| dump | v12->v13 | removed | SELL|1795177800|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v12->v13 | removed | BUY|1795179300|0 | support | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795211700|0 | ambiguous | inside | missed | n/a |
| dump | v12->v13 | removed | BUY|1795423200|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v12->v13 | removed | SELL|1795431900|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795436400|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795437300|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795441800|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v12->v13 | removed | BUY|1795453800|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795475400|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v12->v13 | removed | BUY|1795478100|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v12->v13 | removed | SELL|1795478700|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795479000|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | BUY|1795479600|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795480200|0 | ambiguous | inside | tp2_then_stop | 0.48 |
| dump | v12->v13 | removed | SELL|1795481400|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795484100|0 | support | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795495500|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795500000|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| dump | v12->v13 | removed | SELL|1795518300|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795520400|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795523400|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1795526100|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| dump | v12->v13 | removed | SELL|1795532400|0 | ambiguous | inside | missed | n/a |
| dump | v12->v13 | removed | SELL|1795545600|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | BUY|1795557300|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| dump | v12->v13 | removed | SELL|1795561200|0 | ambiguous | with | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795561800|0 | ambiguous | against | stop | -0.91 |
| dump | v12->v13 | removed | BUY|1795562400|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795563000|0 | resistance | inside | tp4_full | 2.29 |
| dump | v12->v13 | removed | SELL|1795563600|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1795563900|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1795565700|0 | ambiguous | inside | stop | -0.91 |
| dump | v12->v13 | removed | SELL|1795568400|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1795575000|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v12->v13 | removed | SELL|1795580400|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v12->v13 | removed | SELL|1795581000|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v12->v13 | removed | BUY|1795583700|0 | support | inside | stop | -0.91 |

</details>


<details><summary>Setups each step removes or adds</summary>

| set | step | kind | key | role | approach | outcome | R |
|---|---|---|---|---|---|---|---|
| sep | v6->v7 | removed | BUY|1789968000|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | removed | SELL|1789984500|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789368600|0 | ambiguous | inside | missed | n/a |
| sep | v6->v7 | added | SELL|1789372800|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v6->v7 | added | SELL|1789375500|0 | ambiguous | against | tp4_full | 2.29 |
| sep | v6->v7 | added | SELL|1789386600|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789395900|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789397700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v6->v7 | added | BUY|1789398300|0 | ambiguous | against | tp4_full | 2.20 |
| sep | v6->v7 | added | SELL|1789398600|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789399500|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789400700|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789404300|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | SELL|1789407000|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789410300|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789411200|0 | ambiguous | inside | tp3_then_stop | 1.15 |
| sep | v6->v7 | added | SELL|1789415100|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789418400|0 | ambiguous | against | tp1_then_stop | 0.49 |
| sep | v6->v7 | added | SELL|1789427700|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789434000|0 | ambiguous | inside | stop | -1.00 |
| sep | v6->v7 | added | BUY|1789434600|0 | ambiguous | against | tp2_then_stop | 0.86 |
| sep | v6->v7 | added | BUY|1789436700|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789437600|0 | ambiguous | against | stop | -1.00 |
| sep | v6->v7 | added | BUY|1789439100|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789439700|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | BUY|1789440300|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789447200|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789451700|0 | ambiguous | inside | tp4_full | 1.32 |
| sep | v6->v7 | added | BUY|1789452900|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789454700|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | SELL|1789457700|0 | ambiguous | against | tp3_then_stop | 1.49 |
| sep | v6->v7 | added | SELL|1789473900|0 | ambiguous | against | stop | -1.00 |
| sep | v6->v7 | added | SELL|1789474200|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789476300|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | SELL|1789478100|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789481400|0 | ambiguous | inside | tp2_then_stop | 0.67 |
| sep | v6->v7 | added | SELL|1789482300|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | SELL|1789483500|0 | ambiguous | against | stop | -1.00 |
| sep | v6->v7 | added | SELL|1789489800|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789491600|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789492200|0 | ambiguous | with | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789493400|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| sep | v6->v7 | added | SELL|1789494000|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789513200|0 | ambiguous | against | tp1_then_stop | 0.49 |
| sep | v6->v7 | added | SELL|1789523400|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789523700|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789525200|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789542600|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789545000|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | SELL|1789547400|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | BUY|1789548900|0 | ambiguous | against | tp4_full | 1.79 |
| sep | v6->v7 | added | BUY|1789551300|0 | ambiguous | against | tp3_then_stop | 1.49 |
| sep | v6->v7 | added | BUY|1789553100|0 | ambiguous | against | tp2_then_stop | 0.89 |
| sep | v6->v7 | added | BUY|1789561800|0 | ambiguous | with | tp2_then_stop | 0.48 |
| sep | v6->v7 | added | BUY|1789564200|0 | ambiguous | against | stop | -1.00 |
| sep | v6->v7 | added | SELL|1789566600|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | SELL|1789567200|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789567800|0 | ambiguous | against | tp4_full | 2.29 |
| sep | v6->v7 | added | BUY|1789568100|0 | ambiguous | with | stop | -0.94 |
| sep | v6->v7 | added | BUY|1789568700|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789569300|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789570500|0 | ambiguous | against | stop | -1.00 |
| sep | v6->v7 | added | BUY|1789571100|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789577400|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789580100|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789580400|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789581000|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789581300|0 | ambiguous | against | stop | -1.00 |
| sep | v6->v7 | added | BUY|1789581900|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | BUY|1789614300|0 | ambiguous | against | missed | n/a |
| sep | v6->v7 | added | SELL|1789622100|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789629000|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789638600|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v6->v7 | added | BUY|1789640400|0 | ambiguous | against | missed | n/a |
| sep | v6->v7 | added | BUY|1789642500|0 | ambiguous | against | missed | n/a |
| sep | v6->v7 | added | BUY|1789652700|0 | ambiguous | with | tp2_then_stop | 0.89 |
| sep | v6->v7 | added | BUY|1789657800|0 | ambiguous | inside | tp2_then_stop | 0.80 |
| sep | v6->v7 | added | BUY|1789659300|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789663800|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789664100|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789666500|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| sep | v6->v7 | added | SELL|1789669500|0 | ambiguous | against | tp3_then_stop | 1.46 |
| sep | v6->v7 | added | SELL|1789669800|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| sep | v6->v7 | added | BUY|1789690500|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | BUY|1789690800|0 | ambiguous | against | tp1_then_stop | 0.49 |
| sep | v6->v7 | added | SELL|1789692300|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789693500|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789694100|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789695000|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789698000|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789698600|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789699800|0 | ambiguous | against | tp2_then_stop | 0.80 |
| sep | v6->v7 | added | SELL|1789700700|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | SELL|1789701300|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789701900|0 | ambiguous | against | missed | n/a |
| sep | v6->v7 | added | BUY|1789702200|0 | ambiguous | against | tp1_then_stop | 0.35 |
| sep | v6->v7 | added | BUY|1789702800|0 | ambiguous | inside | tp4_full | 2.29 |
| sep | v6->v7 | added | BUY|1789703100|0 | ambiguous | against | tp4_full | 2.26 |
| sep | v6->v7 | added | BUY|1789704000|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789706400|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | BUY|1789706700|0 | ambiguous | against | tp4_full | 2.26 |
| sep | v6->v7 | added | BUY|1789707300|0 | ambiguous | against | tp4_full | 2.29 |
| sep | v6->v7 | added | BUY|1789717500|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789718400|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789719900|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789721700|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | SELL|1789723500|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | BUY|1789734600|0 | ambiguous | against | stop | -1.00 |
| sep | v6->v7 | added | SELL|1789743300|0 | ambiguous | against | tp1_then_stop | 0.41 |
| sep | v6->v7 | added | BUY|1789744500|0 | ambiguous | against | tp4_full | 2.26 |
| sep | v6->v7 | added | SELL|1789745700|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789746000|0 | ambiguous | inside | tp4_full | 1.32 |
| sep | v6->v7 | added | SELL|1789746600|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789747800|0 | ambiguous | against | tp4_full | 1.32 |
| sep | v6->v7 | added | BUY|1789750800|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| sep | v6->v7 | added | BUY|1789752600|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789752900|0 | ambiguous | against | stop | -1.00 |
| sep | v6->v7 | added | BUY|1789755000|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789950000|0 | ambiguous | inside | tp2_then_stop | 0.67 |
| sep | v6->v7 | added | BUY|1789950900|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789951200|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789952700|0 | ambiguous | against | tp1_then_stop | 0.35 |
| sep | v6->v7 | added | BUY|1789953000|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789953900|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | BUY|1789956000|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789956300|0 | ambiguous | against | tp4_full | 1.79 |
| sep | v6->v7 | added | SELL|1789959900|0 | ambiguous | against | tp3_then_stop | 1.46 |
| sep | v6->v7 | added | BUY|1789960200|0 | ambiguous | with | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789962600|0 | ambiguous | against | tp3_then_stop | 1.40 |
| sep | v6->v7 | added | SELL|1789962900|0 | ambiguous | against | tp2_then_stop | 0.89 |
| sep | v6->v7 | added | SELL|1789964700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v6->v7 | added | SELL|1789968600|0 | ambiguous | inside | tp2_then_stop | 0.67 |
| sep | v6->v7 | added | SELL|1789977300|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789979700|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789982700|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789983000|0 | ambiguous | against | stop | -0.94 |
| sep | v6->v7 | added | BUY|1789983600|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| sep | v6->v7 | added | SELL|1789983900|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789993200|0 | ambiguous | against | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | BUY|1789994100|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| sep | v6->v7 | added | BUY|1789994400|0 | ambiguous | against | tp1_then_stop | 0.41 |
| sep | v6->v7 | added | SELL|1789994700|0 | ambiguous | against | stop | -0.91 |
| sep | v6->v7 | added | BUY|1789995300|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| sep | v6->v7 | added | BUY|1789995900|0 | ambiguous | inside | stop | -0.91 |
| sep | v6->v7 | added | SELL|1789997100|0 | ambiguous | inside | missed | n/a |
| sep | v6->v7 | added | SELL|1789997400|0 | ambiguous | against | missed | n/a |
| sep | v6->v7 | added | SELL|1789998600|0 | ambiguous | against | missed | n/a |
| sep | v6->v7 | added | SELL|1789999200|0 | ambiguous | against | missed | n/a |
| sep | v6->v7 | added | BUY|1790000700|0 | ambiguous | inside | open_at_bars_end | n/a |
| nov13 | v6->v7 | removed | SELL|1794515700|0 | ambiguous | against | tp1_then_stop | 0.49 |
| nov13 | v6->v7 | added | BUY|1794455400|0 | ambiguous | inside | stop | -0.91 |
| nov13 | v6->v7 | added | SELL|1794460200|0 | ambiguous | against | stop | -0.91 |
| nov13 | v6->v7 | added | SELL|1794465000|0 | ambiguous | against | tp4_full | 1.32 |
| nov13 | v6->v7 | added | BUY|1794508500|0 | ambiguous | against | stop | -0.91 |
| nov13 | v6->v7 | added | SELL|1794510000|0 | ambiguous | against | stop | -0.91 |
| nov13 | v6->v7 | added | BUY|1794513600|0 | ambiguous | inside | stop | -0.91 |
| nov13 | v6->v7 | added | SELL|1794515400|0 | ambiguous | against | tp1_then_stop | 0.49 |
| nov13 | v6->v7 | added | SELL|1794516600|0 | ambiguous | against | stop | -0.91 |
| nov13 | v6->v7 | added | BUY|1794530100|0 | ambiguous | against | tp4_full | 2.29 |
| nov13 | v6->v7 | added | SELL|1794531000|0 | ambiguous | against | stop | -0.94 |
| nov13 | v6->v7 | added | SELL|1794543300|0 | ambiguous | against | open_at_bars_end | n/a |
| nov13 | v6->v7 | added | SELL|1794547800|0 | ambiguous | against | open_at_bars_end | n/a |
| nov13 | v6->v7 | added | SELL|1794557700|0 | ambiguous | against | stop | -0.91 |
| nov13 | v6->v7 | added | SELL|1794564900|0 | ambiguous | inside | open_at_bars_end | n/a |
| nov13 | v6->v7 | added | SELL|1794566100|0 | ambiguous | against | open_at_bars_end | n/a |
| nov13 | v6->v7 | added | SELL|1794566700|0 | ambiguous | against | open_at_bars_end | n/a |
| nov17 | v6->v7 | added | SELL|1794784500|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794794100|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794796200|0 | ambiguous | against | tp4_full | 2.20 |
| nov17 | v6->v7 | added | SELL|1794798300|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| nov17 | v6->v7 | added | BUY|1794799200|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794799800|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794800400|0 | ambiguous | against | tp1_then_stop | 0.49 |
| nov17 | v6->v7 | added | SELL|1794801900|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| nov17 | v6->v7 | added | SELL|1794804600|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794805500|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| nov17 | v6->v7 | added | SELL|1794807000|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794818700|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794821100|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| nov17 | v6->v7 | added | SELL|1794824700|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794832200|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794833400|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794835800|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794836100|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794836400|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794836700|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794838800|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794839100|0 | ambiguous | against | tp3_then_stop | 1.49 |
| nov17 | v6->v7 | added | BUY|1794839400|0 | ambiguous | against | stop | -0.94 |
| nov17 | v6->v7 | added | BUY|1794839700|0 | ambiguous | against | tp1_then_stop | 0.49 |
| nov17 | v6->v7 | added | SELL|1794841500|0 | ambiguous | against | tp2_then_stop | 0.80 |
| nov17 | v6->v7 | added | SELL|1794842400|0 | ambiguous | against | tp1_then_stop | 0.24 |
| nov17 | v6->v7 | added | BUY|1794843600|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794844500|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794850500|0 | ambiguous | against | tp3_then_stop | 0.84 |
| nov17 | v6->v7 | added | SELL|1794851100|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794852000|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794854700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| nov17 | v6->v7 | added | BUY|1794857100|0 | ambiguous | against | stop | -0.94 |
| nov17 | v6->v7 | added | SELL|1794861300|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794870000|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794876600|0 | ambiguous | against | tp1_then_stop | 0.46 |
| nov17 | v6->v7 | added | SELL|1794877500|0 | ambiguous | with | tp1_then_stop | 0.49 |
| nov17 | v6->v7 | added | SELL|1794878100|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794879300|0 | ambiguous | against | missed | n/a |
| nov17 | v6->v7 | added | SELL|1794880200|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794880500|0 | ambiguous | against | tp1_then_stop | 0.41 |
| nov17 | v6->v7 | added | SELL|1794882300|0 | ambiguous | inside | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794882900|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| nov17 | v6->v7 | added | SELL|1794883500|0 | ambiguous | with | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794886500|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| nov17 | v6->v7 | added | SELL|1794888000|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794889800|0 | ambiguous | inside | open_at_bars_end | n/a |
| nov17 | v6->v7 | added | SELL|1794896100|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | SELL|1794897900|0 | ambiguous | against | stop | -0.91 |
| nov17 | v6->v7 | added | BUY|1794899100|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | SELL|1794806700|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v6->v7 | added | BUY|1794819600|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794820200|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1794821100|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v6->v7 | added | BUY|1794823500|0 | ambiguous | against | tp2_then_stop | 0.86 |
| dump | v6->v7 | added | BUY|1794825000|0 | ambiguous | against | tp1_then_stop | 0.35 |
| dump | v6->v7 | added | SELL|1794834900|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | BUY|1794838500|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v6->v7 | added | SELL|1794838800|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | BUY|1794839100|0 | ambiguous | against | tp3_then_stop | 1.49 |
| dump | v6->v7 | added | BUY|1794839400|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794839700|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1794841500|0 | ambiguous | against | tp2_then_stop | 0.80 |
| dump | v6->v7 | added | SELL|1794842400|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v6->v7 | added | BUY|1794843600|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794844500|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794850500|0 | ambiguous | against | tp3_then_stop | 0.84 |
| dump | v6->v7 | added | SELL|1794851100|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794852000|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794854700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | BUY|1794857100|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | SELL|1794861300|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794870000|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794876600|0 | ambiguous | against | tp1_then_stop | 0.46 |
| dump | v6->v7 | added | SELL|1794877500|0 | ambiguous | with | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1794878100|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794879300|0 | ambiguous | against | tp2_then_stop | 0.80 |
| dump | v6->v7 | added | SELL|1794880200|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794880500|0 | ambiguous | against | tp1_then_stop | 0.41 |
| dump | v6->v7 | added | SELL|1794882300|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794882900|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1794883500|0 | ambiguous | with | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794886500|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1794888000|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794889800|0 | ambiguous | inside | tp2_then_stop | 0.48 |
| dump | v6->v7 | added | SELL|1794896100|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794897900|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794899100|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | SELL|1794901200|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | BUY|1794905100|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794915900|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794918300|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v6->v7 | added | SELL|1794918600|0 | ambiguous | against | tp3_then_stop | 1.49 |
| dump | v6->v7 | added | BUY|1794920100|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794925800|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | SELL|1794926100|0 | ambiguous | with | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1794927300|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794927600|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794928800|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | BUY|1794929100|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| dump | v6->v7 | added | BUY|1794929400|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1794929700|0 | ambiguous | inside | stop | -1.00 |
| dump | v6->v7 | added | SELL|1794931200|0 | ambiguous | inside | missed | n/a |
| dump | v6->v7 | added | SELL|1794934200|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794941100|0 | ambiguous | against | tp2_then_stop | 0.48 |
| dump | v6->v7 | added | SELL|1794972000|0 | ambiguous | against | tp1_then_stop | 0.46 |
| dump | v6->v7 | added | SELL|1794973500|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v6->v7 | added | SELL|1794978300|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | SELL|1794981900|0 | ambiguous | against | tp3_then_stop | 1.15 |
| dump | v6->v7 | added | SELL|1794982500|0 | ambiguous | against | tp3_then_stop | 1.46 |
| dump | v6->v7 | added | SELL|1794984300|0 | ambiguous | inside | tp3_then_stop | 0.84 |
| dump | v6->v7 | added | SELL|1794995700|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1794996000|0 | ambiguous | against | tp4_full | 1.32 |
| dump | v6->v7 | added | SELL|1795000200|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795001400|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795002300|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795004700|0 | ambiguous | against | tp3_then_stop | 1.15 |
| dump | v6->v7 | added | SELL|1795005300|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | SELL|1795007700|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795009500|0 | ambiguous | with | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795009800|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v6->v7 | added | SELL|1795012200|0 | ambiguous | against | tp4_full | 1.32 |
| dump | v6->v7 | added | SELL|1795013100|0 | ambiguous | with | missed | n/a |
| dump | v6->v7 | added | SELL|1795014000|0 | ambiguous | inside | missed | n/a |
| dump | v6->v7 | added | SELL|1795014300|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v6->v7 | added | SELL|1795017600|0 | ambiguous | against | tp4_full | 1.32 |
| dump | v6->v7 | added | SELL|1795023000|0 | ambiguous | inside | tp3_then_stop | 0.84 |
| dump | v6->v7 | added | SELL|1795023600|0 | ambiguous | against | tp3_then_stop | 1.15 |
| dump | v6->v7 | added | SELL|1795024200|0 | ambiguous | against | tp2_then_stop | 0.80 |
| dump | v6->v7 | added | BUY|1795026900|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795028700|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795035300|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795038000|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795044300|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795048200|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| dump | v6->v7 | added | SELL|1795050000|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795050300|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795050900|0 | ambiguous | against | tp1_then_stop | 0.35 |
| dump | v6->v7 | added | SELL|1795051500|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795051800|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | BUY|1795052400|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795053600|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v6->v7 | added | BUY|1795054500|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795055400|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v6->v7 | added | SELL|1795056000|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795056300|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | SELL|1795056900|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795060800|0 | ambiguous | against | tp2_then_stop | 0.89 |
| dump | v6->v7 | added | BUY|1795063800|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| dump | v6->v7 | added | BUY|1795064400|0 | ambiguous | inside | tp2_then_stop | 0.48 |
| dump | v6->v7 | added | SELL|1795065000|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795065300|0 | ambiguous | against | tp2_then_stop | 0.67 |
| dump | v6->v7 | added | BUY|1795066200|0 | ambiguous | with | tp1_then_stop | 0.35 |
| dump | v6->v7 | added | SELL|1795067100|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| dump | v6->v7 | added | SELL|1795068300|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795070100|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795071000|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795071600|0 | ambiguous | against | tp1_then_stop | 0.46 |
| dump | v6->v7 | added | SELL|1795071900|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795073700|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795075200|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795077000|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795079400|0 | ambiguous | against | tp4_full | 2.20 |
| dump | v6->v7 | added | SELL|1795080000|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | SELL|1795081500|0 | ambiguous | against | tp4_full | 2.20 |
| dump | v6->v7 | added | BUY|1795084200|0 | ambiguous | inside | tp1_then_stop | 0.24 |
| dump | v6->v7 | added | BUY|1795085100|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | SELL|1795086000|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v6->v7 | added | SELL|1795086600|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v6->v7 | added | BUY|1795087800|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795088400|0 | ambiguous | against | tp2_then_stop | 0.48 |
| dump | v6->v7 | added | SELL|1795093200|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795093800|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795094100|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795094700|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | SELL|1795097700|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795101000|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | SELL|1795117800|0 | ambiguous | against | tp4_full | 2.20 |
| dump | v6->v7 | added | SELL|1795119900|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v6->v7 | added | SELL|1795151700|0 | ambiguous | with | tp1_then_stop | 0.41 |
| dump | v6->v7 | added | SELL|1795159800|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795165800|0 | ambiguous | with | tp2_then_stop | 0.86 |
| dump | v6->v7 | added | BUY|1795168800|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795172100|0 | ambiguous | against | tp4_full | 1.32 |
| dump | v6->v7 | added | SELL|1795174800|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | SELL|1795175700|0 | ambiguous | inside | tp3_then_stop | 1.15 |
| dump | v6->v7 | added | SELL|1795176000|0 | ambiguous | against | tp1_then_stop | 0.41 |
| dump | v6->v7 | added | SELL|1795177800|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v6->v7 | added | SELL|1795178100|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | BUY|1795179300|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795180800|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795181700|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v6->v7 | added | BUY|1795182300|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795183200|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | SELL|1795186800|0 | ambiguous | with | missed | n/a |
| dump | v6->v7 | added | SELL|1795187100|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | SELL|1795187700|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | SELL|1795188000|0 | ambiguous | with | stop | -0.94 |
| dump | v6->v7 | added | SELL|1795190400|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | SELL|1795191300|0 | ambiguous | inside | missed | n/a |
| dump | v6->v7 | added | SELL|1795193400|0 | ambiguous | against | tp4_full | 2.20 |
| dump | v6->v7 | added | SELL|1795194000|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | SELL|1795194900|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v6->v7 | added | SELL|1795196400|0 | ambiguous | with | tp3_then_stop | 0.84 |
| dump | v6->v7 | added | SELL|1795196700|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795198200|0 | ambiguous | with | tp3_then_stop | 1.40 |
| dump | v6->v7 | added | SELL|1795201200|0 | ambiguous | inside | open_at_horizon | -6.00 |
| dump | v6->v7 | added | SELL|1795201800|0 | ambiguous | with | stop | -0.94 |
| dump | v6->v7 | added | SELL|1795204200|0 | ambiguous | against | tp1_then_stop | 0.46 |
| dump | v6->v7 | added | SELL|1795206600|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| dump | v6->v7 | added | SELL|1795207200|0 | ambiguous | with | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795208100|0 | ambiguous | with | open_at_horizon | 3.15 |
| dump | v6->v7 | added | BUY|1795208700|0 | ambiguous | with | open_at_horizon | 3.14 |
| dump | v6->v7 | added | SELL|1795209000|0 | ambiguous | inside | stop | -1.00 |
| dump | v6->v7 | added | BUY|1795209600|0 | ambiguous | inside | missed | n/a |
| dump | v6->v7 | added | BUY|1795211100|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | BUY|1795211700|0 | ambiguous | inside | missed | n/a |
| dump | v6->v7 | added | SELL|1795400400|0 | ambiguous | with | stop | -0.94 |
| dump | v6->v7 | added | BUY|1795401900|0 | ambiguous | with | missed | n/a |
| dump | v6->v7 | added | BUY|1795402200|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | BUY|1795411500|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v6->v7 | added | BUY|1795417200|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795419000|0 | ambiguous | against | tp2_then_stop | 0.86 |
| dump | v6->v7 | added | BUY|1795422600|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v6->v7 | added | BUY|1795423200|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v6->v7 | added | SELL|1795431900|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795436400|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795437300|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795439100|0 | ambiguous | against | tp1_then_stop | 0.41 |
| dump | v6->v7 | added | BUY|1795440000|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795440600|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | BUY|1795441800|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v6->v7 | added | BUY|1795452900|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795453800|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795457100|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795475400|0 | ambiguous | with | tp1_then_stop | 0.35 |
| dump | v6->v7 | added | BUY|1795477800|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v6->v7 | added | BUY|1795478100|0 | ambiguous | against | tp4_full | 2.29 |
| dump | v6->v7 | added | SELL|1795478700|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795479000|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | BUY|1795479600|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795480200|0 | ambiguous | inside | tp2_then_stop | 0.48 |
| dump | v6->v7 | added | SELL|1795481400|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795481700|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | BUY|1795482600|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795482900|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795483200|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | SELL|1795488300|0 | ambiguous | inside | missed | n/a |
| dump | v6->v7 | added | SELL|1795489500|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v6->v7 | added | SELL|1795494300|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795495200|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v6->v7 | added | BUY|1795495500|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795496400|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795497300|0 | ambiguous | inside | tp3_then_stop | 1.49 |
| dump | v6->v7 | added | BUY|1795498200|0 | ambiguous | against | stop | -1.00 |
| dump | v6->v7 | added | SELL|1795499100|0 | ambiguous | inside | tp1_then_stop | 0.46 |
| dump | v6->v7 | added | SELL|1795500000|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v6->v7 | added | BUY|1795500300|0 | ambiguous | with | tp1_then_stop | 0.41 |
| dump | v6->v7 | added | BUY|1795502700|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795509300|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | SELL|1795510500|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795511700|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795515600|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795518300|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795520400|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795521600|0 | ambiguous | against | stop | -0.94 |
| dump | v6->v7 | added | BUY|1795523400|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795526100|0 | ambiguous | inside | tp2_then_stop | 0.89 |
| dump | v6->v7 | added | SELL|1795527000|0 | ambiguous | with | tp4_full | 2.20 |
| dump | v6->v7 | added | SELL|1795527600|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795528500|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795532400|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | SELL|1795532700|0 | ambiguous | inside | missed | n/a |
| dump | v6->v7 | added | SELL|1795545300|0 | ambiguous | inside | tp1_then_stop | 0.35 |
| dump | v6->v7 | added | SELL|1795550700|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795551900|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795561500|0 | ambiguous | with | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795561800|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795562400|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795562700|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795563300|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795563600|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795563900|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795564200|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795564500|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795565100|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v6->v7 | added | SELL|1795565700|0 | ambiguous | against | tp2_then_stop | 0.86 |
| dump | v6->v7 | added | SELL|1795567200|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795568400|0 | ambiguous | with | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | BUY|1795571400|0 | ambiguous | with | tp3_then_stop | 1.40 |
| dump | v6->v7 | added | BUY|1795572900|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | BUY|1795573500|0 | ambiguous | against | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795573800|0 | ambiguous | with | missed | n/a |
| dump | v6->v7 | added | SELL|1795574100|0 | ambiguous | inside | missed | n/a |
| dump | v6->v7 | added | SELL|1795574400|0 | ambiguous | against | missed | n/a |
| dump | v6->v7 | added | SELL|1795575000|0 | ambiguous | inside | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795575300|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795577700|0 | ambiguous | against | tp1_then_stop | 0.24 |
| dump | v6->v7 | added | SELL|1795579200|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795580400|0 | ambiguous | inside | tp4_full | 1.32 |
| dump | v6->v7 | added | SELL|1795581000|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v6->v7 | added | SELL|1795583100|0 | ambiguous | inside | tp4_full | 2.29 |
| dump | v6->v7 | added | BUY|1795583700|0 | ambiguous | inside | stop | -0.91 |
| dump | v6->v7 | added | SELL|1795584300|0 | ambiguous | inside | tp4_full | 1.79 |
| dump | v6->v7 | added | SELL|1795584600|0 | ambiguous | with | missed | n/a |
| dump | v6->v7 | added | SELL|1795593000|0 | ambiguous | against | tp1_then_stop | 0.49 |
| dump | v6->v7 | added | SELL|1795593600|0 | ambiguous | inside | stop | -0.94 |

</details>

