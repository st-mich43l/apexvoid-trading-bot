# Q3: does the quality the engine assigns separate better opportunities from worse ones?

Decision map and code paths: [q3-quality-decision-map.md](q3-quality-decision-map.md). Machine-readable results: [q3-quality-validation-results.json](q3-quality-validation-results.json).
Tool: `algo-bot/tools/q3_quality_validation.py` (it reuses the Q1 replay records, the matched random baseline and the calibration vocabulary; no second backtester).

**Conclusion.** The existing data does not support a reliable quantitative ranking, and none is shipped. 143 within-strategy, within-instrument-group splits of facts known at the decision were tried; **none is supported** (rule below);
only 48 of the 82 that could be read in both windows kept their sign, i.e. chance. The technical gates are validated and kept; the confluence floor, the only quality-based acceptance, is **not shown to select better opportunities**, and for Fade Scalp it points the wrong way.
What is corrected is evidence that was counted twice or asserted: it changes 11 of 11,835 replayed opportunities and no live behaviour beyond that.

## 1. Method (fixed before looking)

Replay of the 17 committed production captures (closed candles, the real 1R/2R ladder, assumed spreads, 24 h), thinned to one opportunity per symbol, strategy, direction and hour. Outcome = the opportunity's hypothetical R minus the mean R of 40 matched random entries
(same instrument, direction, technical risk, cost, ladder, horizon): this removes the instrument, the stop size and the drift of the period, which a raw R comparison between quality groups cannot. Groups: strategy x (XAU | FX). Windows: development before 2026-10-07 UTC, out of sample after.
Cluster (symbol-day) bootstrap intervals. A split (a fact known at the decision, high against low; boolean as it is, number at its group median, fixed in advance) is **supported** only if both sides have >= 25 observations in both windows, the development difference exceeds the
Bonferroni-adjusted bound for all splits tried, the out-of-sample difference has the same sign, at least half the size, and an interval not lying on the other side of zero. Hypothetical outcomes only: no broker behaviour.

New measurements in the replay: maximum favourable / adverse excursion in R (`mfe`, `mae`), cost drag (`ladder` against `ladder_cost`), win rate, average win/loss, profit factor.

## 2. Where quality can be measured at all

4,353 filled thinned observations; groups with >= 50 in each window: Key Level FX (970) and XAU (401), Supply FX (613), Demand FX (579), FVG FX (653) and XAU (171), Order Block FX (282), Confluence Zone FX (211). **Everything else is insufficient:**
iFVG (FX 63, XAU 22 in total), CRT (1), Trendline (5 FX, 2 XAU), Flip Zone (19, 7), Break & Retest (32, 4), Fade Scalp (16 FX, 4 XAU thinned; 41 as theses), Confluence Zone XAU, Supply/Demand/Order Block XAU.
Power: the difference-to-random has SD 1.09 R and a symbol-day intra-cluster correlation of 0.025 (about 54 observations per cluster). Detecting a 0.3 R difference between two quality bins (80% power, 5%) needs about 480 filled observations per bin,
0.5 R about 170, 0.2 R about 1,070. The best groups here reach the 0.2-0.3 R range; iFVG, CRT, Trendline, Flip Zone and Fade Scalp are an order of magnitude short.

## 3. The stars and `quality.overall`

| Question | Result |
|---|---|
| Does any fact separate outcomes inside a strategy and instrument group? | 0 of 143 splits supported |
| Is the direction at least stable? | 48 of 82 same sign in both windows (59%; chance is 50%) |
| Do 3-star opportunities beat 2-star ones of the same strategy and group? | **No, slightly worse:** stratified difference -0.083 R [-0.176, +0.009] development, -0.081 R [-0.177, +0.005] out of sample (13 and 12 groups; 706/1,557 and 570/1,109 observations) |
| Is HTF alignment (worth 4 of 20.5 points) a positive? | No: 6 of 8 groups have it negative in development (-0.1 to -0.4 R, intervals touch 0), mixed out of sample |
| The existing quality terciles (calibration tool, whole population) | high tercile worst in both windows (mean R -0.103 / -0.197 against -0.032 / -0.122 for the low one) |

Group means (all windows, difference to random, win rate, profit factor, mean MFE / MAE in R, cost drag): Key Level FX -0.024 (46%, 0.89, 1.16/0.75, 0.056), Key Level XAU -0.121 [-0.222, -0.004] (44%, 0.69, 1.53/0.77, 0.050),
Supply FX -0.036 (49%, 1.02), Demand FX -0.073 (38%, 0.63), FVG FX -0.043 (44%, 0.82), FVG XAU -0.081, Order Block FX -0.138 (41%, 0.66), Confluence Zone FX -0.048 (43%, 0.79). Transaction costs cost 0.03-0.09 R per trade (0.05-0.09 outside Confluence Zone) (ladder with costs against without),
about the size of the best differences seen, so no sub-group edge can be assumed to survive them. Nothing is better than a matched random entry in a way the data can resolve; Key Level XAU is the one group significantly worse.

Reading: the scale does not rank. It stays published as a measurement; ranking and sizing already ignore it (`quality_ranking: false`, tier ignored by volume, `min_confluence` a preference note), which this evidence supports. No weights are proposed because no feature earned one.

## 4. Key Level (5,444 -> 4,174 in Q2)

The Q2 rule removed the constant session point, so a reaction now reaches two stars from real evidence only; in practice that means HTF alignment (71-76% of the retained against 5-6% of the removed).
Were the removed technically inferior? **No** (hypothetical, difference to random): FX removed +0.109 development / -0.125 out of sample against retained -0.079 / +0.012; XAU removed +0.081 / -0.053 against retained -0.056 / -0.228.
Retained minus removed: FX -0.187 [-0.439, +0.039] then +0.137 [-0.187, +0.471]; XAU -0.137 [-0.469, +0.199] then -0.175 [-0.600, +0.194]: **a sign flip on FX, intervals include zero everywhere**. The retained set is not shown to be stronger, and it is not shown to be weaker.
The Q2 change removed unconditional credit; it is a validity correction and an unproven selection rule. Nothing restores the constant. Because HTF alignment is now the de-facto admission criterion and has never shown a positive relation to outcomes, that criterion is
the open decision for the owner (see section 8).

Q3's duplicate-evidence fix touches 11 Key Level opportunities (4,174 -> 4,165 plus two replaced by their sibling; 9 executable, hypothetical -0.13 R): the sweep of a session level on the reaction's own candle is the reaction (already credited as the reclaim) and no longer also earns the session point.

## 5. iFVG

Within-strategy quality cannot be tested (FX 63, XAU 22 observations in total). Q2's out-of-sample result stands as reported and is not promoted: retained FX +0.056 (n=36) development, +0.436 [+0.025, +0.910] (n=25) out of sample; the 8 removed observations are +0.41 and +1.34 (n=5, 3). Thirty-five out-of-sample observations are a hypothesis.
Component review: gap width, origin displacement, inversion displacement/body/close, retest penetration/touches, rejection wick, zone integrity, target room are measured, each describes a different candle or event (the original gap, the inversion, the retest), none is credited twice;
`confluence_stars` is a diagnostic copy of the stars. `session_context` now excludes the retest candle's own wick. Needed: roughly 200 FX observations per window to resolve a 0.5 R difference between inversion strengths; at the current ~5 per week that is months.

## 6. Fade Scalp (priority)

Every Fade Scalp is a pullback entry, not a countertrend fade: its direction is the engine's structural direction (`StructuralDirection`), the pool it fades is the one the pullback swept, and the detector never tests for exhaustion: it asks "was a graded sweep reclaimed, did a reaction confirm, did the sweep not continue".
It therefore cannot distinguish an exhaustion reversal from a temporary pullback in a continuing move; it only knows the sweep was reclaimed and has not (yet) been exceeded. That is the finding behind "worse than random".

Measured on all 890 confirmed sweep reactions (theses) of the captures with the gate that refused each (`fadescalp.Analyze`, `TestFadeConsiderations`); difference to random, development / out of sample:

| Set | n (filled) | Difference to random | Win rate, PF | Reading |
|---|---|---|---|---|
| Published | 41 | -0.129 [-0.631, +0.379]; -0.338 / +0.070 | 41%, 0.52 | |
| Published FX | 27 | **-0.431 [-0.818, -0.008]**; -0.255 / -0.687 | 33%, 0.25, MFE 0.88 R, MAE 0.76 R | worse than random entries in the same direction |
| Published XAU | 14 | +0.453 [-0.672, +1.209] | 57%, 1.33 | insufficient |
| Refused: continued past the sweep | 84 | +0.007 [-0.302, +0.269]; -0.119 / +0.123 | 44% | the gate neither avoids losers nor winners |
| Refused: reward:risk below 1 | 76 | **-0.489 [-0.770, -0.097]**; -0.204 / **-0.880 [-0.976, -0.706]** | 21%, 0.27 | **avoided losers**, same sign in both windows |
| Refused: confluence floor only | 392 | +0.130 [-0.060, +0.347]; -0.116 / **+0.369 [+0.108, +0.622]** | 50%, 1.10 | unstable: refused losers in development, winners out of sample |
| ... of those, with the room their risk demands (cover >= 1) | 161 | +0.185; -0.178 / +0.509 | | |

Floor-refused with room minus published: development +0.161 [-0.312, +0.752], out of sample +0.439 [-0.412, +1.606]; FX alone +0.171 [-0.361, +0.869] then **+1.307 [+0.474, +1.844]**, XAU out of sample -0.734 [-1.256, +0.286] (opposite sign; only 4 published XAU in development, no development estimate). Same sign pooled, development not significant, XAU opposite: **not supported** by the rule, reported as a strong hint
that the stars floor does not select Fade setups (consistent with section 3). Facts known at the sweep (body and wick ratios, penetration, run into the sweep in ATR, range expansion, closes against before the sweep, pool touches, grade, reaction displacement, HTF): 0 supported;
the ones that earn stars (grade A -0.39 / -0.60, reaction displacement -0.51 / -0.65, pool touches > 3 -0.27 / -0.64 on FX) are negative in both windows with intervals touching zero: a hypothesis (an entry after a displacement is a late entry), not a result. Two sign flips show how little to trust any of it: HTF alignment on FX is +0.24 in development and -1.04 [-1.52, -0.49] out of sample, and room cover >= 1 is -0.10 then +0.39 [+0.07, +0.72]. XAU theses refused by any gate (104) are worse than random, -0.26 [-0.44, -0.07]; FX refused (448) +0.09 [-0.08, +0.31].

What Q3 changed for Fade Scalp: nothing that publishes more or fewer setups. The account of why a setup was refused is now exported (`Analyze`, `Consideration`: reason, geometry, facts) so the next data can answer it. **Proposal, not applied** (explicit owner approval needed): disable Fade Scalp on FX only.
Impact on the two weeks replayed: 27 filled published FX theses, mean -0.50 R (-0.43 R against random, interval -0.82 to -0.01), i.e. about -13 R hypothetical that would not have been taken; XAU's 14 (+0.14 R, interval wide) would continue. This is a judgement on a sample of 27; the alternative is to wait for more data.
The R:R gate is the one Fade rule the evidence supports, and is kept.

## 7. CRT and Trendline

CRT: 1 replayed setup (3 in Q2 before the shift-displacement rule). Quality reflects its requirements structurally (reference range ATR, sweep depth, reclaim depth, shift displacement/body/close, range position, technical R:R are all published as measurements; the stars add only HTF, the sweep wick and the shift), but there is nothing to measure: **insufficient**.
Needed: about 60 setups per window for a 0.5 R comparison at best, roughly a year of H1 anchors at the current rate.
Trendline: 16 setups (FX 5, XAU 2 thinned); anchors, health, touches and the reclaim interaction are mandatory gates (not scored); the published quality is the stars. **Insufficient.** The two replay figures (+0.44 development n=6, +1.87 out of sample n=1) mean nothing.

## 8. Acceptance impact of Q3 and open owner decisions

Decisions that change in the replay: **11 Key Level opportunities** (9 executable, -0.13 R hypothetical) lose a duplicated session credit and fall under the floor, **0** winners that were not already marginal are lost deliberately; Fade Scalp, iFVG, CRT, Trendline, Supply/Demand/Order Block/FVG/Flip Zone publish the same opportunities. Published factor flags change for
`rejection_choch` confirmations (wick + choch instead of structural + choch; same points) and the resting zones publish only `freshness_quality` / `fill_quality` (and `Overall` equal to it) instead of components fabricated from `Strength = 1` and `Relevance = Immediate`.

Owner decisions the evidence leaves open: (1) Fade Scalp on FX (disable / keep and collect); (2) whether HTF alignment should remain the practical Key Level admission criterion (it has never shown a positive relation); (3) whether the confluence floor should stay a quality gate at all, given 0 supported splits and a hint of inversion.
Neither (2) nor (3) is changed here: the data cannot justify a new rule in either direction.

## 9. Reproduce

```
cd analysis-engine
AEQ_DUMP=/tmp/q3 go test ./test/aequality -run TestReplayAllStrategies
AEQ_RANDOM_OUT=/tmp/q3 go test ./test/aequality -run TestMatchedRandomBaseline
AEQ_FADE_OUT=/tmp/q3 go test ./test/aequality -run TestFadeConsiderations
cd ../algo-bot && python tools/q3_quality_validation.py --records /tmp/q3/records.jsonl --random /tmp/q3/random.jsonl \
  --fade /tmp/q3/fade.jsonl --fade-random /tmp/q3/fade_random.jsonl --out q3.json
```
