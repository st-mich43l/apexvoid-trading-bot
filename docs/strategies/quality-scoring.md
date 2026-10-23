# Opportunity quality scoring (model v1)

`Quality.Overall` used to be the confluence stars divided by three for most strategies (twelve distinct values over 11,826 replayed opportunities), a constant for Break & Retest (0.75), and for the resting zones the touch count. It is now a strategy-specific technical score built from measured facts of the setup, explained dimension by dimension. Code: [`internal/quality`](../../analysis-engine/internal/quality/quality.go) (the rule and the primitives) and one `quality.go` per strategy (its dimensions). Evaluation: [quality-scoring-results.json](quality-scoring-results.json), tool `algo-bot/tools/q4_quality_eval.py`.

**Ready for live ranking: no.** The score is shipped as informational quality. It is deterministic, causal and explainable, but no relation to outcomes has been established (section 6). `quality_ranking` stays `false`, `min_confluence` is unchanged, and no acceptance threshold reads the score.

## 1. Three different things

| Concept | Question | Where it lives |
|---|---|---|
| Technical validity | Is this a legitimate setup of its strategy? | The strategy's mandatory rules; an invalid setup is rejected before any score exists. The score cannot rescue it. |
| Technical quality | How strong is this valid setup by its own methodology? | `Quality.Overall` in [0, 1], and its `score_*` components. Not a probability: 0.90 is strong evidence by the rubric, not a 90% win rate. |
| Statistical performance | Do higher scores do better? | Measured separately, against matched random entries (section 6). |

The legacy confluence stars are kept as a compatibility diagnostic (`Technical.Confluence`, the `confluence_stars` measurement where the strategy published one). They are not part of `Overall`, and `min_confluence` remains the only quality-based acceptance, exactly as before.

## 2. The rule

* A strategy defines a few **dimensions**. Each is a bounded, continuous function of facts available when the opportunity is created (closed bars only), normalised by the instrument's own volatility or structure, with a documented range.
* A dimension whose evidence is missing scores **0**. A non-finite input is missing evidence. Nothing is credited because it was not measured.
* `Overall` is the **unweighted arithmetic mean** of the dimensions, published exactly. No statistical weights: a weight is a tuning knob.
* `score_*` components are the dimensions. Every other component is a raw measurement for audit (its unit is in its name) and never enters `Overall`. `quality.Verify` checks the agreement; the replay harness asserts it on every published opportunity.
* "More is not always better": counts and sizes use `Peak` (a trapezoid) where an excess is a defect: a level visited every 25 minutes is a chop zone, a spike candle is exhausted, a late retest is another market.
* One candle is credited in one dimension. A dimension is a different event or fact from the others (level history, reaction candles, entry timing, market direction, room).
* A fixed reward:risk target is not structure: it earns no `room`. A condition every published setup meets by construction (a validity rule) is not scored, because a constant is not evidence; the replay fails if a strategy publishes a constant component (`TestNoStrategyAssertsAConstantAsAMeasurement`).
* The version of the rule is the model name (`key_level.quality.v1`). A change of a dimension or range is a new version, frozen before it is evaluated. The Kafka contract is unchanged (`components` is a free map); the model name is not carried on the wire.

Shared dimensions (`quality` package): **rejection** = half Ramp(refusal wick of the touch bar; 0.2, 0.6) + half Ramp(close strength of the confirmation bar; 0.5, 0.9); **entry_location** = Fall(distance of the latest close from the proximal edge in ATR; 0.25, 1.5); **structure** = graded context (with the local structure and the higher timeframe 1.0 / 0.7; a confirmed reversal 0.9 / 0.7; a range 0.6 / 0.5; against it with no reversal 0.3 / 0.1), so a confirmed reversal is not outranked by the four generic points of an HTF-aligned trade; **room** = Ramp(reward:risk to a structural objective; 1, 3). `Ramp`, `Fall` and `Peak(x; a, b, c, d)` are linear: 0 to 1 over (lo, hi), 1 to 0, and 0, rising to 1 at b, flat to c, falling to 0 at d.

## 3. Dimensions per strategy

| Strategy | Dimensions (all in [0, 1]) |
|---|---|
| **Key Level** | `level_significance` Peak(independent episodes incl. this one; 1, 3, 12, 30); `htf_level` the same price is an H1 (full) or M15 (half) level, Ramp(its touches; 1, 3); `level_integrity` share of bars since the first visit that closed beyond the defended side, Fall(share; 0.05, 0.4), 0 under ten bars of history; `rejection`; `entry_location`; `structure`; `room` |
| **iFVG** | `gap_imbalance` (half Peak(width ATR; 0.15, 0.4, 1.5, 3.5), half Ramp(origin body ATR; 0.5, 1.5)); `inversion_strength` (half Ramp(close beyond the far bound ATR; 0.05, 0.5), half Peak(body ATR; 0.3, 0.8, 2, 3.5)), the only dimension that reads the inverting candle; `inversion_timing` Peak(bars gap to inversion; 0, 2, 12, 36); `retest_freshness`; `retest_rejection` (retest candle only); `zone_integrity`; `entry_location`; `structure`; `room` |
| **CRT** | `range_validity` Peak(H1 range in H1 ATR; 0.6, 1.2, 3.5, 7); `sweep_depth` Peak(ATR; 0.05, 0.2, 1, 2.5); `reclaim` (depth and speed); `structure_shift` Ramp(displacement ATR; 0.3, 1.2), halved when the shift is the reclaim candle itself, 0 without one; `range_position` Fall(p; 0.25, 0.7) (buy low, sell high); `entry_location`; `structure`; `room`. Invalidation beyond the manipulation is a validity rule, not scored |
| **Fade Scalp** | `pool_credibility` (a third each: Peak(touches; 1, 2, 4, 8), Fall(pool band / ATR; 0.1, 0.6), Peak(bars from the pool's last swing to the sweep; 0, 3, 40, 120)); `sweep` (half Peak(penetration ATR; 0.02, 0.1, 0.6, 1.5), half grade A 1 / B 0.5); `extension` Peak(move into the sweep ATR; 0.5, 1.5, 4, 8); `failed_continuation` Ramp(close back through the pool ATR; 0.05, 0.8); `rejection`; `entry_location`; `room`. **No trend-alignment dimension**: a fade is as good against the move as with it |
| **Trendline** | `line_validation` Peak(validation touches; 0, 2, 4, 8); `line_integrity` 1 - Ramp(2 x close + wick violations; 0, 4); `line_significance` (half Ramp(span bars; 20, 100), half Fall(slope ATR per bar; 0.05, 0.4)); `approach` Ramp(distance travelled into the line ATR; 0.3, 1.5); `rejection`; `entry_location`; `structure`; `room` |
| **Supply, Demand, Order Block, FVG** (confirmed) | `zone_departure` Ramp(how far price left the zone in eight bars, ATR; 1, 3); `zone_precision` Peak(width ATR; 0.1, 0.4, 1.5, 3.5); `zone_freshness` Fall(touches incl. this one; 1, 5); `rejection`; `entry_location`; `structure`; `room` |
| **Flip Zone** | `level_significance` Peak(touches of the flipped level; 1, 3, 5, 9); `break_strength` Ramp(break candle body ATR; 0.5, 1.5); `retest_timing` Peak(bars break to retest; 0, 1, 6, 30); `rejection`; `entry_location`; `structure`; `room`. Zone freshness is a validity rule (published only unmitigated), so it is not scored |
| **Confluence Zone** | `source_breadth` Ramp(independent source groups; 1, 4); `overlap_precision` Peak(overlap ATR; 0.1, 0.3, 1.2, 3); `structure_integrity` (mean of overlap and source integrity); `rejection`; `entry_location`; `structure`; `room` |
| **Break & Retest** | `reference_significance` Peak(touches; 1, 3, 5, 9); `break_force` (half Peak(body ATR; 0.4, 0.9, 2.5, 4), half Ramp(close beyond ATR; 0.1, 0.8)); `retest_quality` (depth and timing); `retest_rejection`; `entry_location`; `structure`; `room`. Accepted closes are a validity rule (every setup has the configured count) and are a measurement |
| **Impulse Pullback** | `impulse_strength` Peak(displacement over its minimum; 0.8, 1.2, 2.5, 4); `body_dominance` Ramp(b; minimum, 0.9); `pullback_quality` Fall(pullback body / impulse body; 0.1, ceiling); `reference_quality` Peak(touches; 1, 2, 4, 8); `entry_location`; `room` |
| Resting zones (Supply, Demand, Order Block, FVG, Confluence Zone) | Not executable, no reaction yet: one or two measurable dimensions (`zone_freshness` / `gap_fill`, `source_breadth`, `overlap_precision`). Not comparable with confirmed reactions |

Replaced fakes: `confluence` and `touch_quality` (touches / 5, "more is better") for Key Level; the stars / 3 `Overall` of iFVG, CRT, Fade Scalp, Confluence Zone and the technique family; Break & Retest's configurable constant `published_overall_quality` (the config key is removed); Impulse Pullback's `displacement / (2 x minimum)`. Existing raw measurements (gap width, sweep depth, reward:risk and so on) are still published, now outside `Overall`.

## 4. Real before / after examples (XAU Key Level, replay)

| | Old | New | Why |
|---|---|---|---|
| Strongest | 2 stars, 0.67 | **0.97** | 7 episodes at an H1 level with 6 touches, never failed a close, wick rejection with a 0.91 reaction, entry 0.4 ATR from the edge, 4.6R to structural liquidity, with the structure |
| Median | 3 stars, 1.00 | **0.63** | the level is sound (significance 1, H1 1, structure 0.9) but it has failed 34% of its closes (integrity 0.16), the reaction is mediocre (0.36) and the liquidity is only 0.96R away (room 0) |
| Weakest | 2 stars, 0.67 | **0.29** | an M5 level visited 14 times, entry 1.5 ATR from the edge (0), 0.63R of room (0), against the structure with no reversal (0.1) |

The stars carried no information about this: on XAU Key Level the median new score of 3-star setups (0.61) is *below* that of 2-star setups (0.64), and 60% of the 3-star setups score under the 2-star median.

## 5. Replay distributions (17 captures, thinned filled opportunities)

| Strategy | n | Old distinct / sd | New distinct / sd | New p10 / p50 / p90 |
|---|---:|---|---|---|
| break_retest | 36 | 1 / 0.00 | 36 / 0.101 | 0.57 / 0.71 / 0.82 |
| confluence_zone | 190 | 2 / 0.042 | 190 / 0.102 | 0.48 / 0.61 / 0.76 |
| demand | 437 | 2 / 0.070 | 436 / 0.152 | 0.34 / 0.56 / 0.75 |
| fade_scalp | 20 | 2 / 0.073 | 20 / 0.106 | 0.47 / 0.66 / 0.77 |
| flip_zone | 26 | 2 / 0.162 | 26 / 0.104 | 0.60 / 0.72 / 0.86 |
| fvg | 143 | 2 / 0.061 | 143 / 0.114 | 0.52 / 0.67 / 0.79 |
| ifvg | 85 | 2 / 0.139 | 85 / 0.083 | 0.63 / 0.74 / 0.84 |
| key_level | 1,371 | 2 / 0.128 | 1,342 / 0.111 | 0.51 / 0.65 / 0.79 |
| order_block | 251 | 2 / 0.055 | 251 / 0.123 | 0.50 / 0.67 / 0.81 |
| supply | 467 | 2 / 0.066 | 466 / 0.156 | 0.32 / 0.55 / 0.74 |
| trendline | 7 | 2 / 0.165 | 7 / 0.069 | 0.66 / 0.73 / 0.86 |
| impulse_pullback | 1 | | | |

Over all 11,826 published opportunities the old `Overall` took 12 distinct values and the new one 4,408. CRT (1 replayed setup) and Impulse Pullback (1) have no distribution to speak of. Independence of the dimensions: the largest pairwise rank correlation inside a strategy and instrument group is 0.57 (iFVG XAU, Confluence Zone FX), 0.25-0.46 for most; no pair is a duplicate.

## 6. Quality against outcome (rule frozen before the outcomes were read, run once)

Unit: a filled, thinned opportunity of one strategy and instrument group; outcome = its hypothetical R (the real ladder, with costs) minus the mean R of its matched random entries (same instrument, direction, risk, cost, ladder, horizon). Windows: development before 7 Oct, out of sample after. Separation = mean outcome of the high-score setups (above the group's development median; for a two-valued old score, the top value) minus the rest, with a symbol-day cluster-bootstrap interval; pooled across groups with each group centred on its own low-score mean. Ready for ranking only if the pooled out-of-sample separation is positive with an interval above zero, development agrees in sign, at least half of the testable groups are positive out of sample, and the new score beats the old one.

| Pooled separation (high minus low score, 3,034 setups) | Development | Out of sample |
|---|---|---|
| New technical quality | -0.041 [-0.127, +0.050] (761 / 766) | **-0.100 [-0.246, +0.044]** (553 / 472) |
| Old stars | -0.139 [-0.337, +0.041] (164 / 661) | -0.136 [-0.350, +0.063] (84 / 462) |

Key Level (the strategy with enough data): XAU +0.057 [-0.132, +0.246] then -0.095 [-0.341, +0.212]; FX -0.045 [-0.248, +0.137] then -0.060 [-0.307, +0.195] (old stars: XAU -0.028 then -0.018, FX -0.201 then -0.192). Supply FX and Demand FX are negative out of sample (-0.064, -0.238). 0 of 5 testable groups separate positively out of sample. Per-dimension rank correlations with the outcome change sign between windows (for example Key Level XAU `level_integrity` +0.08 then +0.32, `entry_location` +0.04 then -0.14); none is stable enough to claim a feature.

**Result: the new score does not separate outcomes in a way these samples can resolve, in either direction, and it does not separate them better than the stars it replaces (which did not separate them either).** It is not ready for live ranking and is not used by any gate. This is the same conclusion as Q3 for the stars (0 of 143 splits supported): the data available cannot show that any generic or strategy-specific measure of technical quality predicts hypothetical outcomes against matched random entries. Iterating on the formula until the evaluation window agreed would have manufactured the relation the brief forbids.

What was corrected before the outcomes were read, from input distributions only (disclosed so the single evaluation is not mistaken for the first look): zone strategies' resting observations are excluded from the evaluation; Key Level's integrity is a share since the level's first visit with a minimum history, and its significance range was widened from (1, 3, 5, 9) to (1, 3, 12, 30), because with a 150-bar window the original ranges scored 54-92% of levels zero; Flip Zone's retest timing was moved to (0, 1, 6, 30) and its constant freshness dimension removed; Fade's pool credibility gained the pool's tightness and age, since touches alone sat on the plateau; Break & Retest's constant acceptance dimension was removed. Two earlier replays were read for input distributions and the constant-component guard; the outcome evaluation ran once, on the final rule.

## 7. Limits

* Hypothetical outcomes on two weeks of captures; single-leg fills, constant spreads, no sizing or position limits.
* Most strategies have too few setups for a quality ordering to be resolvable (CRT 1, Impulse Pullback 1, Trendline 7, Fade Scalp 20, Flip Zone 26). Key Level (1,371 thinned) is the only strategy with a usable sample, and it shows nothing.
* Dimension ranges are documented judgements about the setups' natural scales (the strategy's own thresholds and the 5-minute 12-hour window), not fitted values. They are frozen in model v1.
* The score is not a probability and has not been calibrated to one.
* The model version is on the wire from Q5.3 as `quality_model_version` in the components (a provenance number, not a dimension); envelopes before it are told apart by the presence of `score_*`. The outcome evidence for the score is in [q53-quality-expectancy.md](q53-quality-expectancy.md): a frozen forward window and the traded groups, again no positive separation.

## 8. Live behaviour

Unchanged: no stop, target, ladder, sizing, risk, enablement or Kafka field. `Quality.Overall` values change (and with them what Algo Bot displays as quality); arbitration ignores quality (`quality_ranking: false`); the confluence floor still gates on stars. Opportunity IDs and counts are identical to the previous model (11,826 on the 17 captures).
