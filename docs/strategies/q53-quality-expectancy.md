# Q5.3: does a higher Go technical quality score identify a better opportunity?

Baseline: `master` at `5e555c1e` (#806). Production runs image `5e555c1` (started 2026-10-22 14:35 UTC). Scoring rule: model v1 (`internal/quality`, #804).
No formula, weight, threshold, stop, ladder, admission rule, ranking switch or `min_confluence` changed. The only code change is a provenance tag (section 8).

## 0. Answer

**No. The evidence does not support the claim that a higher Go quality score identifies a better opportunity, and pooled it points the other way.**

* On a window the rule had never seen (2026-10-19 to 2026-10-22, 1,021 opportunities, evaluated with the Q4 rule, cuts and unit unchanged) the pooled high-minus-low separation, in R above matched random entries, is **-0.133** [-0.285, +0.047].
  The three windows (development, out of sample, forward) read -0.041, -0.103, -0.133; over all 4,597 opportunities **-0.087 [-0.157, -0.017]**, from a design that could have resolved +-0.10 (section 5).
* On the closed groups that were really traded, a score cannot be validated yet: **13 closed groups carry a model-v1 score**, 7 with a fill-derived R. The scores production published before 2026-10-22 06:12 UTC are a different rule (stars / 3 and other constants) and are not the Q4 score (section 4).
* Technical reward:risk, the basis of `score_room`, overstates the executable one on FX by a median of 1.5 to 3.5 times, because the executor's stop is the instrument floor (section 6). The room dimension itself is neutral against outcome (-0.018 [-0.101, +0.063]).
* **No mathematical, normalization or causality defect was found** (section 2). The weak discrimination is possibility B of the brief (a reasonable formula that does not predict) with a part of C (most groups are too small to resolve anything); it is not an implementation defect, so no formula was touched.
* **Quality-based ranking is not ready and must stay off.** Nothing here supports inverting the score either: three dimensions carry the negative sign, each is technically coherent with its strategy's methodology, and re-weighting them against these outcomes would be tuning on the sample.

## 1. Deployment boundaries (UTC, from image start times on the host and merge times)

| Change | Merged | Live |
|---|---|---|
| #804 Q4 scoring (`Quality.Overall` = mean of `score_*`, informational) | 2026-10-22 06:08 | **06:12** |
| #805 Q5.1 net R accounting, quality diagnostics | 10:20 | **10:25** |
| #806 Q5.2 executable room diagnostic, counter-bias exception removed | 14:30 | **14:35** |

The Q4 score is therefore one day old in production. Closed groups before 06:12 were scored by earlier rules; net R is a measurement only from 10:25.

## 2. Audit of the scoring implementation

**Mathematics (checked on every replayed opportunity).** Over 17,632 replayed opportunities (every strategy, resting and confirmed) 0 violate: every `score_*` finite and in [0, 1]; `Overall` equal to the arithmetic mean of the `score_*` components to 1e-9 (`quality.Verify`, asserted by the replay on each one). The primitives (`Ramp`, `Fall`, `Peak`, `Room`, `LocationScore`, `StructureScore`) are bounded, treat a non-finite input as missing evidence and score it 0, and refuse a malformed range (unit tests in `internal/quality`). A dimension scored twice and a measurement named like a dimension panic.

**Causality.** The prefix-invariance check (`TestNoStrategyUsesFutureBars`) now compares not only which opportunities exist but their published score and every component, to nine decimals, with the capture cut at a time and replayed again. A score read from a later bar would differ.

**Which dimensions carry information.** On the 4,597 confirmed, thinned opportunities (one per symbol, strategy, direction and hour), a dimension that sits on one value for most of a group adds a constant to the mean and none to the ordering:

| Group | Dimension at one value for most of the group |
|---|---|
| Key Level FX / XAU (1,500 / 559) | `level_significance` 82% at 1.0 (episodes inside the 3 to 12 plateau); FX `htf_level` 71% at 1.0 |
| Supply, Demand FX (658 / 647) | `zone_freshness` 69% / 70% at 1.0 (first or second retest) |
| FVG FX (186) | `zone_precision` 77%, `zone_freshness` 61%, `entry_location` 62% at 1.0 |
| Order Block FX / XAU (332 / 67) | `zone_precision` 76% / 84% at 1.0 |
| Confluence Zone FX / XAU (208 / 65) | `source_breadth` 65% / 95% on one value |
| iFVG FX (90) | none |

These are the strategies' own typical geometry landing on the plateau of the documented ranges, not errors; a range would be moved only by a new, frozen version.

**Duplicate or correlated dimensions.** The largest pairwise rank correlation inside a group is 0.59: Confluence FX `overlap_precision` against `source_breadth` (-0.59, the more sources, the looser the overlap: they offset by construction). Everywhere else it is 0.25 to 0.40 (Key Level `level_integrity` against `structure` -0.26 / -0.33). No pair is a duplicate and no fact is credited twice.

**Validity against quality.** The conditions every published setup meets by construction are not scored (a constant is not evidence; the replay fails if a strategy publishes a constant component, `TestNoStrategyAssertsAConstantAsAMeasurement`). What remains scored is the strategy's own measured strength.

**Per strategy.**

| Strategy | Dimensions (model v1) | Audit finding | Evidence |
|---|---|---|---|
| Key Level | significance, HTF level, integrity, rejection, entry location, structure, room | No defect. Repeated touches do not reward a weakening level: significance peaks at 3 to 12 episodes and falls to 0 at 30, and integrity (share of closes through the level since its first visit, 0 under ten bars of history) is separate and rises with outcome (+0.061 [-0.025, +0.143], 2 groups). | 2,059 setups |
| Supply, Demand, Order Block, FVG | zone departure, precision, freshness, rejection, entry location, structure, room | No defect. FX room is technical (section 6). `zone_departure` carries a negative sign (section 7). | 1,823 FX setups |
| iFVG, Flip Zone | gap, inversion, timing, retest, integrity (iFVG); significance, break, timing (Flip) + shared | No defect; samples too small to rank (118, 37) | |
| CRT | range, sweep, reclaim, shift, position + shared | No defect; **1 replayed setup**: not assessable | |
| Fade Scalp | pool, sweep, extension, failed continuation, rejection, entry, room; **no trend dimension** | No defect; no trend credit as required; 31 setups | |
| Trendline | validation, integrity, significance, approach + shared | No defect; 10 setups | |
| Confluence Zone | source breadth, overlap precision, structure integrity + shared | No defect (offsetting pair above); 273 setups | |
| Break & Retest, Impulse Pullback | as in `quality-scoring.md` | No defect; 54 and 1 setups | |

**Missing evidence never earns credit** (unit tests per strategy and on the primitives); technical evidence is preserved end to end (`quality.components` in the creation envelope, `StrategyMatch.quality_components`, the `opportunity published` log line, the stored creation event).

## 3. Data lineage

`tools/q53_lineage.py` joins every closed group to its opportunity (`v8:go_opp_<h>` is `opp_<h>`: one group, one opportunity, so a thesis that re-published under several ids is never counted twice) and reads the scoring rule off the envelope, the configuration fingerprint and the result method.

| | |
|---|---|
| Closed groups with a result | 608 (10 more have no stored envelope) |
| Scoring rule | legacy 595; **model v1 13** (all produced after 06:12 UTC; **0** disagreements between the rule read from the components and the publish time) |
| Result method | `reconstructed_ladder_v1` 249, `fill_record_risk` 253, `legacy_loss_unbooked` 98, **`fills_v1` 8** |
| Configuration fingerprints in the groups' envelopes | 37 distinct (one per deploy that changed the configuration) |
| Current strategies / purged strategies | 401 / 207 |

Until Q5.3 the rule was identifiable only by the presence of `score_*` components. From now on the engine publishes `quality_model_version` in the components (section 8), so a later v2 is not guessed from a date.

## 4. Realized R of the traded groups

Estimator: `result_accounting.estimate` (never rewritten). Method mix disclosed; uncertainty is a symbol-day cluster bootstrap.

| Group | n | Mean R | 95% interval | Sum R | Profit factor | Full stops |
|---|---:|---:|---|---:|---:|---:|
| All closed groups | 608 | -0.053 | [-0.170, +0.065] | -32.2 | 0.90 | 55% |
| Current strategies only | 401 | -0.006 | [-0.127, +0.128] | -2.4 | 0.99 | 55% |

By method (current strategies): `fill_record_risk` 202 groups, +0.275; `reconstructed_ladder_v1` 143, -0.051; `legacy_loss_unbooked` 48, -1.0 by assumption; `fills_v1` 8, -0.341. **The method means are not comparable**: before 2026-10-14 the executor booked no stop-out loss, so a loser is an assumed -1 and a winner a measured positive; only the pooled figure means anything, and it rests on the assumption. Per strategy and instrument group (n >= 10) every interval includes zero except Confluence Zone FX (+0.413 [+0.027, +0.899], 20 groups).

**Score against realized R.** Within a strategy and instrument group (never across), with at least 40 groups, only Key Level qualifies, and only with the legacy score: FX rho -0.136 [-0.336, +0.096] (62), XAU -0.075 [-0.245, +0.075] (110); the stars alone -0.087 and -0.052. Both are indistinguishable from zero. **The model-v1 score has 13 closed groups, 7 of them with a fill-derived R, and cannot be validated**: at 17.6 closed groups a day across current strategies and a standard deviation of R of 1.24, a high-against-low comparison needs about **384 groups per side to resolve 0.25 R** (2,394 for 0.10 R), so roughly six weeks of all strategies pooled and many months per strategy and instrument group.
Selection: only opportunities that passed the two-star floor and every admission gate were traded, so outcomes are conditional on that.

## 5. Chronological replay of the frozen rule

Unit and rule are Q4's, unchanged (the quality-scoring document, section 6): a filled, thinned, confirmed opportunity; outcome = its hypothetical R (the real ladder with costs) minus the mean R of its matched random entries; the cut of a group is its development median; a group is testable with at least 25 on each side; pooled groups are centred on their own low-score mean. New: the captures of 2026-10-22 (5 files, the closed bars to 16:10 UTC) were added after the freeze; the **forward window opens 2026-10-19 00:00 UTC** so that its first bar has more than three trading days of history. Standard errors and `mde80` (2.8 standard errors, 80% power at the 5% level) are reported so a null can be told from a lack of power.

| Window | Opportunities | Pooled separation | 95% interval | Resolvable (mde80) |
|---|---:|---:|---|---:|
| Development (< 10-07) | 1,758 | -0.041 | [-0.127, +0.050] | 0.126 |
| Out of sample (10-07 to 10-18) | 1,818 | -0.103 | [-0.215, +0.017] | 0.165 |
| **Forward (from 10-19)** | 1,021 | **-0.133** | [-0.285, +0.047] | 0.240 |
| All | 4,597 | **-0.087** | **[-0.157, -0.017]** | 0.102 |

Forward by group (separation, mde80): Key Level FX -0.083 (0.33), Key Level XAU -0.038 (0.55), Supply FX +0.011 (0.56), Demand FX -0.331 (0.57), Order Block FX -0.216 (0.59); the other groups have under 25 on a side. No group separates positively in a way its own window could resolve.

By fixed score band (no fitted cut), mean outcome above random: development 0.00-0.40 +0.036, 0.40-0.55 +0.007, 0.55-0.70 -0.034, 0.70-1.00 -0.073; out of sample +0.110, -0.005, -0.097, -0.110; forward +0.001, +0.014, -0.105, -0.056. The highest band is the worst or second worst in each window; the raw R by band moves the same way (development 0.70-1.00: -0.131 against -0.033 for the lowest band).

A limit to hold in mind: the matched baseline and the fills are the replay's (single-leg limit fills at the proximal edge, assumed constant spreads, the captured bars are bid-only), not production's market entry inside the zone. Production enters inside the zone at market and its fills differ; the conclusion is about this replay.

## 6. Technical against executable room

`score_room` is the reward:risk to a structural objective with the risk measured to **Go's invalidation**. The executor's stop is that invalidation widened to the strategy's floor (`plan_go_invalidation_stop`), and its ladder is fixed multiples of that stop. Two measurements, both from data:

| FX group | Tech risk to stop floor, median widening | Technical RR (median) | First target over the executable stop (median) | First target inside 1R of the executable stop | Mean `score_room` / read on the executable risk |
|---|---:|---:|---:|---:|---|
| Key Level | 2.4x | 1.48 | 0.61 | 75% | 0.320 / 0.088 |
| Demand | 1.6x | 1.13 | 0.63 | 70% | 0.243 / 0.109 |
| Supply | 1.5x | 1.16 | 0.79 | 60% | 0.282 / 0.130 |
| FVG | 2.9x | 1.54 | 0.53 | 75% | 0.384 / 0.089 |
| iFVG | 2.6x | 1.96 | 0.78 | 67% | 0.522 / 0.071 |
| Order Block | 2.6x | 1.59 | 0.58 | 73% | 0.424 / 0.121 |
| Confluence Zone | 1.3x | 2.76 | 2.03 | 0% | 0.754 / 0.539 |

On XAU the stop is the technical risk to within 15% (median ratio 1.0 in the replay, 0.85 to 1.1 in the traded groups), so the two agree (traded Key Level 1.5 against 1.73; the executable risk is slightly the smaller because the stop is measured from the volume-weighted fill).
For the traded FX groups the same holds (Key Level technical risk 6.5 pips, stop 14.8; technical RR 1.59, executable 0.66; FVG 2.75x; iFVG 3.5x). Against realized R neither reading separates: Key Level FX technical RR +0.167 [-0.057, +0.395], executable +0.199 [-0.011, +0.412] (66 groups); XAU +0.061 and -0.016.

Classification: **a scoring-interpretation difference that is a normal consequence of the existing stop floor**, not a strategy target-definition or execution-policy defect. The target (Go's nearest opposing liquidity) is a legitimate technical objective; the floor is deliberate noise protection (FX technical stops are 3 to 8 pips); the score says "technical reward:risk" and means exactly that. The consequence is that a third to two thirds of the FX opportunities that earn room credit (Supply 32%, Demand 47%, Order Block 63%, Key Level 64%, FVG 66%, iFVG 67%) have their first technical objective inside 1R of the stop that will really be used. Nothing was narrowed, no ladder or threshold was touched, and the Q5.2 `executable_room` diagnostic stays advisory. Reading `score_room` on the executable risk would be a new model version, and since the room dimension is neutral against outcome (-0.018 [-0.101, +0.063]) it has no evidential case to be made one now; it is an option for the owner.

## 7. Why the score does not separate

Pooled over all windows, each dimension alone, high against low, in the groups that publish it (diagnostic: it selects nothing and changes no weight):

| Dimension | Groups | Separation | 95% interval |
|---|---:|---:|---|
| `zone_departure` | 5 | -0.144 | [-0.246, -0.042] |
| `structure` | 10 | -0.116 | [-0.186, -0.040] |
| `entry_location` | 10 | -0.062 | [-0.111, -0.012] |
| `rejection` | 9 | -0.062 | [-0.132, +0.005] |
| `room` | 10 | -0.018 | [-0.101, +0.063] |
| `level_integrity` | 2 | +0.061 | [-0.025, +0.143] |
| `htf_level` | 2 | +0.042 | [-0.018, +0.101] |
| `zone_freshness`, `zone_precision`, `level_significance` | 4, 4, 2 | +0.006, -0.055, -0.026 | each includes 0 |

The sign is in the strategy's own outcomes, not in the baseline: for `structure` the matched random baseline barely moves (+0.015 [-0.008, +0.043]) while raw R moves -0.100 [-0.171, -0.025]; the same holds for departure (random -0.012, raw -0.156) and entry location (-0.008, -0.071). What the rule rewards is trading with the local structure, after a forceful departure, close to the edge. In these captures the replayed reactions do better when they go against the local structure, from zones that left more timidly, from entries price has moved a little away from.
That is a statement about this replay and these weeks. It is **not** a defect:

* A. *Is the formula mathematically defective?* No (section 2).
* B. *Is it technically reasonable and yet does not predict realized expectancy?* **Yes**: each dimension implements its strategy's methodology (with the structure, a forceful departure, an entry at the edge) and in this sample the relation is negative or null.
* C. *Is the sample insufficient?* Partly: per group the resolvable effect is 0.33 to 0.59 R, so no single group can say anything; pooled it is 0.10 R and the answer is negative. Three of the 19 dimension tests exclude zero, against about one expected by chance: not enough to fix a direction, and the single-group dimensions have intervals too wide to say anything.

Not done, on purpose: removing or inverting `structure`, `zone_departure` or `entry_location`, re-weighting, or fitting ranges to this period. Every one of those would be tuning on the October sample, and the pooled estimate already contains this window.

## 8. Change

`quality.Model.Components()` publishes `quality_model_version` (the number in the model name's `.v<N>` suffix: `key_level.quality.v1` publishes 1). It is provenance: it is not a dimension, never enters `Overall`, a measurement cannot be given that name, and a rule without a `.v<N>` suffix publishes none. It is a number inside the existing free `components` map (the Kafka contract and the schema are unchanged), so an envelope carries the rule that scored it and a join no longer infers it from a date or from the presence of `score_*`. The replay's constant-component guard exempts it by name.
Tests: `internal/quality` (version published, never scored, still verifies, no invented version, impersonation refused), the prefix-invariance test now includes the score, `tests/test_q53_tools.py` (windows, saturation, the rule read off the envelope, the join, the sample size).

## 9. Recommendation and open questions

* **Quality-based ranking: not ready. Keep `quality_ranking: false`; `min_confluence` unchanged; no score threshold.** The three conditions of Q4's readiness test fail again (pooled out-of-sample separation not positive; no group positive out of sample that its window could resolve; no better than the stars it replaces, which did not separate either).
* **Not inverting.** A negative pooled association is not an instruction to rank by the opposite of the score: it is one replay, three dimensions, five weeks.
* **Open, and what would answer it:** (1) model-v1 outcomes in the traded groups, which need about 384 closed groups per side for 0.25 R (a model-v1 run since 2026-10-22 06:12; read `q53_lineage.py` weekly, and only the `fills_v1` and `fill_record_risk` rows); (2) whether the replay's retrace entry, rather than production's entry inside the zone, explains the negative `entry_location` (it needs production quote history, which the captures do not have); (3) whether `score_room` should be read on the executable risk (a v2 decision for the owner, with no outcome evidence for it); (4) per-strategy samples for CRT (1), Impulse Pullback (1), Trendline (10), Fade Scalp (31), Flip Zone (37), iFVG (118): insufficient, nothing to conclude.
* **Owner decisions:** none required for this PR. Optional: the room-on-executable-risk reading; and whether the dimensions that carry the negative sign should be reviewed against their strategy contracts rather than their outcomes.

## 10. Reproduce

```
# lineage (read-only exports of auto_trade_results and the creation envelopes of those groups; see tools/q53_lineage.py)
python algo-bot/tools/q53_lineage.py --results results.jsonl --opps exec_opps.jsonl --out lineage.json
# replay and the frozen evaluation
cd analysis-engine && AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies
                      AEQ_RANDOM_OUT=/tmp/aeq go test ./test/aequality -run TestMatchedRandomBaseline
cd ../algo-bot && python tools/q53_quality_forward.py --records /tmp/aeq/records.jsonl --random /tmp/aeq/random.jsonl --out q53.json
```

Results: [q53-quality-expectancy-results.json](q53-quality-expectancy-results.json) (the replay evaluation and the lineage).
Captures added: `analysis-engine/testdata/replay-*-production-capture-20261022.json` (closed bars to 2026-10-22 16:10 UTC, taken read-only with `tools.capture_bars`).
