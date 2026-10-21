# Q3b: September baseline recovery and trading-behaviour regression

Scope: why the Python-era system looked good in the profitable September week and the Go-era system looks worse in October. Method: the frozen Python (`a1c77584`, the end of the profitable week) run as a test oracle in isolated worktrees, the current Go engine, the same committed closed-bar captures, and the existing replay tools (`test/keylevelparity`, `test/aequality`, `test/migrationaudit`). No live behaviour, configuration, sizing, execution or strategy catalogue changed. Reproducible inputs: [manifest](q3-september-baseline-manifest.json), [results](q3-september-baseline-results.json), [Key Level decision ledger](q3-sep-baseline-key-level-ledger.jsonl.gz) (5,111 rows, one per Go opportunity, joined to the Python decision on the same closed bar).

## 0. Verdict

| # | Question | Answer | Evidence level |
|---|---|---|---|
| 1 | What did the September Python bot do differently? | **Not in what it detects.** Key Level detection, confirmation, geometry and (with the frozen session factor) stars are bit-identical to the Go engine on every capture: 8,129 + 3,310 decisions, 0 mismatches. It differed downstream of the detector: it traded about a third as many Key Level XAU setups from the same signal volume (9 in the week against 33-40 per week now), it ran a tighter XAU stop cap (60 pips; 70 since 9 Oct), and the numbers that made the week look good were recorded on a basis that overstates winners (sections 4, 5). | proven (parity), measured (trade rate), proven (basis) |
| 2 | Which differences are genuine regressions? | **None confirmed in analysis.** No detection, confirmation, zone-lifecycle or geometry regression was found for Key Level. The one reporting defect is that `result_pips` records the highest TP archived, not the net result, and `realized_pips` is exactly 0 on every full stop. | proven |
| 3 | Which are intentional technical improvements? | Q2/Q3: no formation-bar self-confirmation, measured session context (one event counted once), Fade v3 gates, constants removed from published quality. All change the stars/acceptance only, not detection; their effect on expectancy is neutral (section 3). | measured |
| 4 | Would the old Python have done better on October data? | **No evidence it would.** The Python-equivalent Key Level set is -0.067 R per setup on October XAU (-0.030 R against matched random entries, interval -0.131 to +0.081) and -0.082 R on October FX. | measured, hypothetical |
| 5 | Would the current Go have captured the September opportunities? | **Yes.** It publishes 350 of the 472 Python-equivalent XAU opportunities of 14-21 Sep (74%); the 122 it does not publish are the confluence floor, and they were not worse (+0.18 R against random, interval -0.22 to +0.45, 74 filled). | measured |
| 6 | How much is execution rather than detection? | Detection explains none of it (parity). Execution changed for XAU on 9 Oct (owner): the stop cap 60 -> 70 pips and a 30-50 pip entry band; the ladder (1R-4R, 40/20/20/20) and the 50-pip floor did not. Replaying the identical Key Level signals under the 60-pip cap, the setups the 70-pip cap newly admits (risk 60-70 pips, 196 filled) are **0.20 R worse than the narrower ones [-0.35, -0.06]**, same sign in both windows; 9 live trades in that bucket went the other way (+1.0 R), so this is a hypothesis, not a finding to act on. FX envelopes widened after the profitable week without any change against random entries. The entry-band rule and sizing cannot be replayed. | measured / not replayable |
| 7 | What should be fixed first? | (1) Make `auto_trade_results` record the net group result so performance can be judged on one basis; (2) explain the 3x higher Key Level XAU admission rate (needs the admission funnel logged per thesis); (3) revisit the XAU 70-pip cap only with more live data (replay and 9 live trades disagree). No analysis change is supported by evidence. | recommendation |
| 8 | What cannot be concluded? | Whether September was profitable in account terms; whether the 9 Key Level trades were skill or luck (win rate 95% interval 35-88%); anything about FX in September; why Python admitted so few theses. | unknowable from the retained data |

## 1. Baseline recovery (deliverable A)

| Item | Status |
|---|---|
| Python code `a1c77584` (19 Sep 2026) and `1c9f3230` (last Python analysis) | recovered as read-only worktrees; the detector runs in a plain venv (pandas 3.0.3) and reproduces the committed golden **exactly** (638/638 XAU decisions, identical) |
| Effective Python settings | `config/trading-bot.yml` at `a1c77584` (single file; the current tree splits it into `analysis`, `auto-algo`, `execution`, `instruments`, `runtime`). Recorded settings per the earlier audit: zigzag 0, swing fractal 2, ATR 14, `key_level_min_touches` 2 (GBPJPY 3), `zone_reconcile_mode: shadow` |
| September market data | **XAU only**: `replay-xau-production-capture-20260921` = M5 2026-09-14 04:35 to 09-21 14:30 UTC (1,500 bars), M15 from 09-11, H1 from 09-02. The Python needs a full M5 lookback, so valid evaluation bars start about 12 hours in (1,351 bars). It covers the whole profitable week (14-18 Sep) |
| September FX | none: every FX M5 capture starts 2026-09-28 or later. No September FX replay is possible |
| October market data | 14 captures, XAU M5 from 09-28 to 10-14, FX M5 from 09-28 to 10-14 (listed in the manifest with hashes) |
| September trades | **not recoverable**: `auto_trade_results` starts 2026-09-29 20:32 UTC (24 groups on 29-30 Sep); `pips_log` has 10 rows; no backup found; Redis holds 2,000 M1 bars. The reference figures (42 trades, 17 wins, +501 pips; Key Level 9 trades, 6W/3L, +567 pips) are reports, not a ledger |
| Python-era execution behaviour | the executor and plan builder of `a1c77584` are readable but cannot be replayed bar by bar (broker, Redis and plan state are not reproducible); compared by configuration and code diff instead (section 4) |
| Not reconstructible | the Python admission funnel (which of its 416 XAU Key Level theses became plans and why), its fills, its sizing in account terms |

## 2. Four-way replay (deliverable B)

All four scenarios use identical candles, instrument pip units, closed-bar decisions, the same warm-up (the Python's own, evaluation limited to the bars it evaluated), the same hypothetical fill model and the same costs. "Python" is the Go engine under `frozen_session_context`, which `TestKeyLevelMatchesTheProfitableWeekPython*` proves equal to the Python on every bar. Outcomes are the candle-causal ladder simulation with costs (limit at the proximal edge, XAU 1R-4R, FX 1R-2R, 24 h, assumed constant spreads) and the matched random control (40 draws). They are hypothetical and un-thinned (several identities per thesis hour); intervals are symbol-day cluster bootstraps.

Key Level, signal-only (common execution):

| Engine | Market | Opportunities | Filled | Win | Mean R | Profit factor | Mean R minus random [95%] |
|---|---|---:|---:|---:|---:|---:|---|
| A Python | Sep 14-21, XAU | 472 | 307 | 49.2% | -0.003 | 0.99 | +0.058 [-0.077, +0.213] |
| B Python | Sep 28 - Oct 14, XAU | 1,164 | 945 | 46.5% | -0.067 | 0.88 | -0.030 [-0.131, +0.081] |
| C Go (current) | Sep 14-21, XAU | 350 | 233 | 49.4% | -0.037 | 0.93 | +0.019 [-0.201, +0.186] |
| D Go (current) | Sep 28 - Oct 14, XAU | 909 | 737 | 46.3% | -0.081 | 0.85 | -0.045 [-0.139, +0.070] |
| B' Python | Sep 28 - Oct 14, FX | 3,475 | 3,190 | 45.2% | -0.082 | 0.85 | -0.040 [-0.100, +0.042] |
| D' Go (current) | Sep 28 - Oct 14, FX | 2,566 | 2,368 | 44.1% | -0.118 | 0.79 | -0.068 [-0.148, +0.024] |

Differences that matter:

| Comparison | Difference in mean R minus random | Reading |
|---|---:|---|
| Python on XAU, September week minus October | +0.088 [-0.092, +0.262] | the September week is not distinguishable from October |
| Go-current published minus all Python opportunities, XAU | -0.021 [-0.060, +0.016] | the Go acceptance neither helps nor hurts |
| same, FX | -0.029 [-0.087, +0.030] | same |
| Go-published minus Go-removed, XAU | -0.092 [-0.263, +0.073] | the 282 removed are not worse |
| same, FX | -0.112 [-0.333, +0.112] | the 822 removed are not worse |

Power: the September XAU week has 307 filled setups with a standard deviation of about 1.1 R and strong within-day correlation; a difference of 0.09 R is far below what this sample can resolve (the interval half-width is 0.18 R). The right reading of the whole table is "no measurable difference", not "equal".

Not delivered, with the reason: the **full-execution** half of the four-way comparison (each architecture's own stops, sizing, partials and position management) cannot be replayed; and FX has no September market. Execution is compared by configuration in section 4 and by counterfactual in 4.2.

Geometry of the market itself differs, which is regime, not engine: the median ATR of the XAU Key Level reference opportunities is 4.19 in the September week and 3.58 in October, the median technical risk 67 against 58 pips, and the share of setups whose stop exceeds the 70-pip cap (not executable) 23% (108 of 472) against 11% (133 of 1,164).

## 3. Key Level decision diff (deliverable C)

Machine-readable: [q3-sep-baseline-key-level-ledger.jsonl.gz](q3-sep-baseline-key-level-ledger.jsonl.gz), built by `algo-bot/tools/q3b_key_level_ledger.py`. Each row is one Go opportunity of the reference behaviour (market time, symbol, direction, thesis: level/kind/role/touches/confirmation/touch and confirmation bars; the Python decision; the Go reference and current decisions; earliest divergent stage and why; geometry: entry band, invalidation, targets, technical R:R, executable risk, ATR; confluence factors; evidence; outcome with MFE, MAE, matched random). Matching is by market context (symbol, closed bar, direction) and entry band, never by opportunity id.

| Stage | Meaning | Count (all periods) |
|---|---:|---:|
| detection | Python fired and the reference Go did not, or the reverse | **0** |
| confirmation | same bar, other direction / reaction | **0** |
| geometry | same bar and direction, other entry band | **0** |
| quality | published by both, other star count (the session factor, now measured) | 1,390 |
| acceptance | Python-equivalent opportunity not published now (confluence floor) | 1,286 |
| none | identical | 2,435 |

Python decision bars that no reference opportunity explains: 9 of 6,971 (listed in the summary); capture-window dependence of the Python itself: 4 bars of 17,652 (EURUSD, between the 6 Oct and 13 Oct captures). Current-engine opportunities with no reference counterpart: 117 (2.3%), sibling selection under the measured stars.

Divergence is entirely at the **acceptance** stage: before Q2 the session factor was constant, so 2 stars was reached on almost any reaction; today it has to be earned. That is the 5,444 -> 4,174 drop of Q2, and its effect on outcomes is the retained-minus-removed row above (not worse). Nothing in this table supports restoring the constant, and none of the divergences is a missed valid opportunity: the removed setups' hypothetical outcome is as good as the retained.

Why parity did not reproduce the September performance: the detector never differed. What differed is what happened after publication.

## 4. Execution (deliverable A/B, section 6 of the brief)

### 4.1 Configuration and code at `a1c77584` against `master`

Compared by flattening `config/trading-bot.yml` at the profitable-week commit against the six current files:

| Setting | `a1c77584` | now |
|---|---|---|
| XAU target ladder (pack) | 1R, 2R, 3R, 4R, close 40/20/20/20, break-even after 1R (`xau_fixed_2r_v1`, a misnomer for 4R) | identical (`xau_fixed_4r_v1`) |
| FX ladder | 1R, 2R, 50/50, break-even after 1R | identical |
| XAU stop envelope | 50-60 pips (pack leaf) | **50-70** since 9 Oct (`86f68323`, owner: "we are making profit by the RR") |
| XAU zone strategies' entry band | the zone as published (up to 100 pips wide) | **30-50 pips** since 9 Oct: widened toward the stop to 30 (invalidation follows), trimmed to 50 nearest the near edge (stop stays); shallow leg at the near edge, deep leg at the band midpoint |
| XAU stacking | one position per symbol (`maximum_per_symbol: 1`) | one same-direction group per direction since 15 Oct (`6cb17883`) |
| Strategy independence | confluence suppression and strategy families | removed 8 Oct (`8e5a20a2`; +1,642 publications the frozen publisher suppressed) |
| EURUSD / GBPUSD / GBPJPY / USDJPY stop envelope | 10-18 / 12-22 / 15-30 / 10-18 pips | **12-20 / 15-25 / 22-35 / 18-28** (raised after the profitable week) |
| Reaction entry | 80% market / 20% scale at 0.1 ATR, `scale_invalid_policy: single_market` | identical |
| XAU risk leg | wired 16 Sep (kill switch) | enabled, 0.05 lot 15 pips from the stop |
| Group stop over the envelope maximum | rejected, never pulled inward | rejected (`stop_exceeds_go_invalidation_envelope`) |
| Single-leg Python-origin plans over the maximum | clamped inward into the envelope (`_plan_base_stop`, unchanged) | Go-origin plans never use it: rejected |
| Opposite exposure | `non_hedged_opposite_policy: broker_netting`, one position per symbol | per-pack: FX never, XAU from 150 pips (`440f1513`, 5 Oct) |
| Technique gates | `execution.technique.*` (strict premium/discount, killzone and publish-window gates, all off except strict PD) | removed from the execution layer; analysis-side only |
| Activation | `m5_confirmation_maximum_age_bars: 6`, `reaction_trigger_maximum_age_bars: 2`, `maximum_chase_distance_pips: 40` | removed from the YAML (moved or dropped; not verified in code) |

Code: 237 files differ under `algo-bot/app` (10,782 insertions, 66,655 deletions); `autotrade/worker.py`, `zone_execution_cutover.py` and `trend.py` are the largest. Planning of the stop is the one function pair checked line by line: `plan_go_invalidation_stop` (Go invalidation as the technical floor, widened to the envelope floor, rejected above the cap) replaced the Python-derived stop for Go-origin plans.

### 4.2 Stop-envelope counterfactual (same signals, the profitable-week envelopes)

`AEQ_CONFIG` replays the real captures under a copy of the configuration with the `a1c77584` envelopes (XAU cap 60, USD-major pack 10-18, GBPUSD 12-22, GBPJPY 15-30, USDJPY 10-18). The Key Level signals are identical (identical id sets, 1,746 XAU theses); only execution moves (`tools/q3b_execution_counterfactual.py`):

| Symbol | Envelope now / profit week | Not executable now / then | Mean R now / then | Mean R minus random now / then |
|---|---|---|---|---|
| XAU | 50-70 / 50-60 | 274 / 564 | -0.054 / -0.017 | -0.009 / +0.035 |
| EURUSD | 12-20 / 10-18 | 0 / 0 | -0.029 / -0.078 | -0.033 / -0.047 |
| GBPUSD | 15-25 / 12-22 | 1 / 1 | -0.137 / -0.180 | -0.087 / -0.087 |
| USDJPY | 18-28 / 10-18 | 1 / 3 | -0.077 / -0.098 | -0.008 / -0.003 |

FX: a wider envelope lifts raw R by 0.02-0.05 and leaves the difference to the matched random control unchanged (it changes the risk per trade, not the quality of the entries).

XAU, the profit-week instrument, is where it matters. Restricting to the opportunities of the ledger and splitting them by whether the 60-pip cap would have executed them (outcomes taken from the 70-pip run for both groups):

| Window | Setups with execution risk 60-70 pips (admitted since 9 Oct) | Setups within 60 pips | Difference in mean R minus random [95%] |
|---|---|---|---|
| September week | 90 filled, mean R -0.191 | 217 filled, +0.075 | -0.202 [-0.436, +0.131] |
| October | 106 filled, -0.217 | 839 filled, -0.048 | -0.251 [-0.456, -0.100] |
| pooled | 196 | 1,056 | **-0.198 [-0.346, -0.059]** |

In the high-volatility September week, 49% of the Python-equivalent XAU setups (231 of 472) have a technical stop beyond 60 pips and would not have been executed; the 159 that were filled average +0.158 R (+0.271 R against random, win rate 58%, profit factor 1.38, interval -0.022 to +0.467). Under the 70-pip cap the same week is flat (-0.003 R). That is a candidate mechanism for "September looked better": a tighter envelope selected the narrower structures, which did better in this sample, in a week in which the market was more volatile. It is a hypothesis, not a finding to act on: the September effect alone is not significant, the production check goes the other way (9 live XAU trades with stops of 60-70 pips since 9 Oct average +1.03 R corrected, against -0.09 R for stops up to 50 pips and +0.15 R for 50-60), and the owner raised the cap deliberately. The 30-50 pip entry band of the same change is a multi-leg rule (shallow leg at the near edge, deep leg at the band midpoint) that the single-leg harness cannot represent; with the 50-pip envelope floor already setting the risk it changes nothing here, so it is not claimed to be neutral in production.

### 4.3 Downstream admission

Production funnel for Key Level on XAU (`analysis_opportunities`, `auto_trade_results`, `algo_auto`, read-only): published per week 587 / 637 / 542 / 293 (partial) from 28 Sep; trades closed per week 11 / 33 / 40 / 18. In the profitable week the Python-equivalent detector produced 416 XAU theses (472 Go identities) and **9** Key Level trades closed: about 2% admission against about 6% now, and 9 against 33-40 trades a week. The cause cannot be established from the retained data (no Python-era plan or rejection logs; the Algo Bot file logs keep 14 days). Candidates, each checkable once the funnel is logged per thesis: the Python's one-position-per-symbol limit and 45-minute entry-overlap corridor against today's per-pack group limits; activation windows (`m5_confirmation_maximum_age_bars`, `reaction_trigger_maximum_age_bars`, `maximum_chase_distance_pips`, no longer in the YAML); the Oct 8 removal of confluence suppression and strategy families. The target-room gate is not a candidate: F-7 measured the Go book as the stricter one (37.6% of evaluations blocked against 1.3-5.3%). This is the largest unexplained behavioural difference found.

## 5. What the reference numbers measure (reporting defect)

`auto_trade_results.result_pips` stores *the highest take-profit actually archived* whenever a TP booked (`persistence/store.py`, owner directive for `/trade_stats`). A group that books TP1 (40% of an XAU position) and is stopped at break-even is recorded as a full TP1 win. `realized_pips` (volume-weighted) exists only from 2026-10-08, and is exactly 0.0 on every full stop. Over the 317 groups since 8 Oct the sum of `realized_pips` is +2,541 against +3,710 recorded; XAU +1,936 against +3,480.

Consequences: (i) the profitable-week figures (+501 pips, Key Level +567) were almost certainly on the recorded basis; (ii) on the corrected basis (full stop = -1 R, otherwise `realized_pips / risk_pips`) October since 8 Oct is **-0.062 R per group [-0.219, +0.094]** (XAU +0.098 [-0.081, +0.287], FX -0.164 [-0.351, +0.062], n=324); (iii) putting September on the October XAU ratio (weighted / recorded = 0.556) would turn +501 recorded pips into about +280 weighted pips (+6.6 per trade) against +15.4 weighted pips per trade for October XAU. That last arithmetic is illustrative only (different exit mixes, an assumption about the basis of the September figure), but it shows that "September good, October bad" is not established on a common basis.

## 6. Opposing-zone and target-room (section 5 of the brief)

Already investigated in F-7 ([audit](python-go-migration-forensic-audit.md) 7.5 and [ground truth](f7-opposing-barrier-ground-truth.md)): the Go book blocks 37.6% of evaluations against 1.3-5.3% for the frozen one; the difference is the touch rule (frozen: first touch spends a zone; Go: held until 30 retests), not the zone source; neither book matches independent structural labels better than chance; only thin-room blocks are informative in a hypothetical race. The fail-closed behaviour when the book is missing was kept (PR #793). Nothing in this phase's evidence changes that conclusion, and nothing here weakens the filters: the Key Level setups carry no systematic relation between room and outcome in the replay either (Q1, Q3).

## 7. Other strategies

Only Key Level has a Python oracle for the profitable-week commit; the others are proven against `1c9f323` (Flip Zone, Fade Scalp on three symbols, the technique publishers) or rebuilt (iFVG, CRT, Trendline, Break & Retest, Confluence Zone). No September market exists for them except XAU, and none of them has a September trade record. They were not re-replayed: Q3 (quality validation) already measured them against matched random entries and found no supported edge or quality ordering. Purged strategies stay purged. Breakout Retest Scalp (the second contributor of the profitable week, +150) is proven bar by bar on M1 only (`test/scalpparity`) and could not be added to the M5 comparison.

## 8. Root-cause ranking (deliverable D)

| Rank | Cause | Class | Technical correctness | Economic impact | Reproducible | Breadth | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | The September figures and the October figures are on different bases (`result_pips` highest-TP against weighted net) and the reference is 9-42 trades | Insufficient evidence / reporting | defect in the metric, not in the strategy | explains most of the apparent gap: October is -0.06 R on the corrected basis, the September +501 pips is on the inflated one | yes (`store.py`, 324 corrected rows) | all instruments | high |
| 2 | Market regime: higher volatility in the profitable week (ATR 4.19 vs 3.58, risk 67 vs 58 pips); Python-equivalent signals are about zero in both periods | Market regime | n/a | +0.088 R [-0.09, +0.26] September minus October for the identical detector, not distinguishable from zero | yes (ledger) | XAU (FX has no September) | medium |
| 3 | Admission rate downstream of the detector (9 against 33-40 Key Level XAU trades per week) | Quality/acceptance or risk concentration (unknown) | unverified | unknown; more trades at an expectancy near zero add variance and cost, not return | observed, cause not reproducible | XAU Key Level | low on cause |
| 4 | Confluence floor now rejects setups whose session factor is not earned | Quality/acceptance | correct (Q2/Q3: no duplicated or asserted evidence) | -0.021 R [-0.06, +0.016] XAU, -0.029 R [-0.087, +0.030] FX; neutral | yes | all | high |
| 5 | Execution geometry after the profitable week: XAU cap 60 -> 70 and 30-50 band (9 Oct), FX envelopes | Execution geometry | intended (owner) | XAU cap: setups newly admitted are -0.20 R [-0.35, -0.06] worse in replay, +1.0 R (n=9) live; FX: none against random | yes (replay), no (band) | XAU, FX | low (XAU), high (FX) |
| 6 | Detection, confirmation, zone lifecycle, geometry | - | **exact** | none | yes (11,439 decisions, 0 mismatches) | 5 symbols | high |
| 7 | Opposing-barrier book | Zone lifecycle | unverified correctness (F-7) | the largest filter in production, no outcome evidence either way | yes | all | medium |

## 9. Changes made

No runtime change. Evidence supports none: the detector is exact, the only acceptance difference is neutral, and the unexplained difference (admission rate) has no identified defect. What was added is test infrastructure and evidence:

* `test/keylevelparity`: the proof now covers the 11 later captures too (`key-level-oracle-later-captures.json`, 8,129 decisions, 0 mismatches), generator `CAPTURE_SET=later`.
* `test/aequality`: `AEQ_PARAMS` (strategy parameter override, e.g. the frozen session factor) and `AEQ_CONFIG` (replay under a copy of the configuration) so the same harness answers "same signals, one historical setting changed".
* `tools/q3b_key_level_ledger.py` and `tools/q3b_execution_counterfactual.py`, with tests (`tests/test_q3b_*`).

**Proposals, not applied** (each needs an owner decision):
1. Record the net group result. Fix `volume_weighted_pips` to include the stop-out leg and store it as `net_pips`/`net_r` in `auto_trade_results`; keep `result_pips` for `/trade_stats`. Impact: none on trading; all later performance judgments move to one basis.
2. Log the admission funnel per thesis (published, plan built, rejected with reason, traded) so the 2% -> 6% admission difference can be explained. Impact: none on trading.
3. Do not restore the constant session factor, and do not re-enable any purged strategy: nothing in the replay supports either.

## 10. What cannot be concluded

* Whether the September week was profitable in account terms, or by how much. The figures are reports on the inflated basis and no ledger survives.
* Whether the 9 Key Level trades (6W/3L) reflect an edge: the win rate 95% interval is 35-88%; at the replay's 45-49% win rate, 6 or more wins out of 9 happen 17-24% of the time.
* Anything about FX in September, and whether the Python admitted fewer trades because of a gate or of its position limit.
* The effect of sizing and correlated exposure in September (volumes are not retained).
* Execution-level causes of the admission difference, until the funnel is logged.

## 11. Reproduce

```
# Python oracle (read-only worktree, any venv with pandas): the golden for the later captures
git worktree add --detach /oracle a1c77584
ENGINE_DIR=$PWD/analysis-engine APEXVOID_CONFIG_FILE=/oracle/config/trading-bot.yml CAPTURE_SET=later WORKERS=8 \
  python analysis-engine/test/keylevelparity/generate_oracle_golden.py
# Go replays (reference behaviour and current), records and matched random controls
cd analysis-engine
AEQ_PARAMS='{"key_level":{"frozen_session_context":true}}' AEQ_DUMP=/tmp/ref go test ./test/aequality -run TestReplayAllStrategies
AEQ_PARAMS='{"key_level":{"frozen_session_context":true}}' AEQ_RANDOM_OUT=/tmp/ref go test ./test/aequality -run TestMatchedRandomBaseline
AEQ_DUMP=/tmp/cur go test ./test/aequality -run TestReplayAllStrategies
AEQ_RANDOM_OUT=/tmp/cur go test ./test/aequality -run TestMatchedRandomBaseline
cd ../algo-bot
python tools/q3b_key_level_ledger.py --golden ../analysis-engine/testdata/key-level-oracle.json ../analysis-engine/testdata/key-level-oracle-later-captures.json \
  --frozen /tmp/ref --current /tmp/cur --ledger ledger.jsonl --summary summary.json
```
