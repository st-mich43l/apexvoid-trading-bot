# XAU Key Level: publish only after the level has been swept (key_level v6)

Branch `agent/keylevel-sweep-first`, base `master` at `9cb13660`. Go engine only, XAU only. No Python, stop envelope, floor, cap, ladder, sizing, enablement, arbitration, quality or `min_confluence` changed, and no other strategy or instrument.

## 1. The owner report (2026-10-28)

XAU's manipulation and V-shaped runs of the last two weeks have hurt the bot. A replay of the committed XAU captures through the engine as it stood (the candidates the AE publishes today; the XAU ladder 1R-4R at 40/20/20/20, break-even after 1R, an executed stop of max(structural, 50 pips), candidates above 70 pips skipped; R measured above 20 random entries with the same stop and ladder; symbol-day cluster bootstrap) showed:

- **The market.** 12-21 Oct: median M5 ATR 34 pips (43 in 14-18 Sep), but more swing legs per day (15 vs 12) and more fast V reversals per day, a leg of at least 100 pips completed in 2 hours or less and then 75% or more retraced in 2 hours or less (4.8 vs 3.8).
- **Key Level, about 65% of XAU volume.** The split that matters is whether the confirming reaction **traded beyond the level band's far edge**. That is exactly when `strategyutil.ReactionAnchoredInvalidation` returns `anchored == true` and the candidate carries `invalidation_beyond_reaction_extreme`.

Reported by the owner (n, edge in R above random, 95% CI, stop-out rate), deduplicated per level and 2 hours. **This is an in-sample replay, mostly October; the September column is the out-of-regime check, not a holdout.**

| period | swept | not swept |
|---|---|---|
| 14-18 Sep | 38, +0.39 [+0.06,+0.68], 24% | 51, +0.27 [+0.12,+0.47], 35% |
| 1-11 Oct | 83, +0.49 [+0.26,+0.76], 22% | 82, +0.18 [+0.01,+0.39], 43% |
| 12-22 Oct | 124, +0.32 [+0.15,+0.54], 29% | 109, +0.00 [-0.21,+0.22], 47% |

Without deduplication the split is the same: September +0.60 vs +0.38, 1-11 Oct +0.55 vs +0.07, 12-22 Oct +0.25 vs +0.06.

- Swept setups hold their edge in every period.
- Unswept setups are where the edge is lost: in the manipulative period they earn nothing, with nearly half stopped out.
- In September the gap is small, so this is a structural rule rather than a fit to October.

These figures are the owner's; this change does not re-derive them. What this change measures is the engine's behaviour (section 4).

## 2. The rule

On XAU, Key Level publishes a confirmed candidate **only when its reaction swept the level**: the lowest low (BUY) or highest high (SELL) of the bars from the touch to the confirmation lies **strictly beyond** the reaction band's far edge. It reads the same `reactLow`/`reactHigh` that go to `ReactionAnchoredInvalidation`, so "swept" is exactly "anchored"; a low exactly on the edge is not beyond it. No threshold, buffer, envelope, ladder or sizing moved.

- `Config.RequireReactionSweep` (override-only parameter `require_reaction_sweep`, parsed like `require_explicit_role`: absent means false, a non-boolean is an error).
- Set by `config/instruments.yml`, `XAU.overrides.auto_algo.strategies.reaction.key_level.require_reaction_sweep: true`, read by `applyKeyLevelOverrides`. FX is not set (it was not measured).
- A candidate published under the rule carries the evidence code `key_level_reaction_swept` (`invalidation_beyond_reaction_extreme` stays as it is). `Version` is `v6`; `config/analysis.yml` `key_level.version: v6`.
- Order inside `candidate()`: reaction-anchored invalidation, then the sweep requirement, then the stop budget, then the target.

## 3. A second change in the same function: the per-attempt selection

v5 chose the single best attempt by stars across all levels **first** and only then called `candidate()`, which can refuse it (the stop budget). The refusal dropped every other valid attempt of that evaluation. The sweep rule would have made this worse, so v6 builds the full candidate for **each** confirmed attempt (one per level; a level where both directions confirm is still a contradiction and is skipped) and keeps the highest-stars **published** one, the first at equal stars. That also fixes the stop-budget defect: a best-stars attempt over the cap no longer suppresses an in-budget one on another level. It applies with or without the rule; without a stop budget (the parity replays) it changes nothing.

Tests (`internal/strategy/keylevel/sweep_test.go`):
- an unswept confirmation publishes nothing and a swept one publishes with the evidence code; a low on the edge is refused; the SELL mirror;
- without the flag the behaviour is the previous behaviour apart from the version (a swept candidate differs only by its one evidence code);
- an unswept best-stars attempt no longer suppresses a swept lower-stars attempt on another level;
- a stop-budget refusal of the best attempt no longer suppresses an in-budget one;
- the two refusal counters are separate and reset when read;
- both defects were reintroduced one at a time to check that these tests fail.

## 4. What it does to the published set

`analysis-engine/test/keylevelsweep/measure_test.go` (`KEYLEVEL_SWEEP_OUT=<dir>`) replays all 21 committed M5 captures twice with the engine as configured (stop budget on): without the rule (the v5 behaviour for XAU) and with it. Unit: one XAU Key Level confirmed candidate per opportunity id, first sight across overlapping captures. Result: [keylevel-sweep-first-results.json](keylevel-sweep-first-results.json).

| period (confirmation time, UTC) | XAU Key Level candidates | swept | not swept | refused by the rule | recovered by the per-attempt selection |
|---|---:|---:|---:|---:|---:|
| before 2026-09-30 | 174 | 59 | 115 (66%) | 115 | 0 |
| 2026-10-01 to 10-11 | 342 | 143 | 199 (58%) | 199 | 1 |
| from 2026-10-12 | 558 | 233 | 325 (58%) | 324 | 9 |
| **all** | **1,074** | **435** | **639 (59.5%)** | **638** | **10** |

- **The rule removes 638 of 1,074 XAU Key Level candidates (59%)**; the swept 435 are published exactly as before (same entry band, same invalidation; the only difference is the one evidence code).
- **Recovered: 10 opportunities (34 evaluated-bar sightings)** are published only because the per-attempt selection lets a swept lower-stars attempt through when a higher-stars unswept attempt on another level used to take the evaluation and then be refused. They are swept by construction and are not part of the 1,074. With them the published set is 446.
- One opportunity of the "from 12 Oct" row is unswept at its first sight and published under the rule: its id reappears at a later bar with a swept reaction. The table counts first sight, so refused is 324 and not 325. The asserted invariants are per evaluated bar and hold without exception: every candidate published under the rule is swept and carries the evidence; a swept candidate v5 published is published unchanged; an unswept one is not; what only the rule publishes appears where v5 had no swept candidate.
- **No effect off XAU:** the 7,813 FX Key Level sightings (evaluated bar and id) are identical with and without the rule (asserted: same count, same entry band and invalidation).

Real-case regression (`test/keylevelsweep/regression_test.go`, `replay-xau-production-capture-20261022.json`; times are the first M5 bar after the confirmation; the outcomes in the last column are the owner's replay, this change asserts only what is published):

| setup | v5 (rule off) | v6 |
|---|---|---|
| SELL 2026-10-14T20:40 | published | **refused** (unswept; stopped out in replay) |
| SELL 2026-10-14T23:25 | published | **refused** (unswept; stopped out in replay) |
| BUY 2026-10-15T05:25 | published | **refused** (unswept; stopped out in replay) |
| BUY 2026-10-15T17:35 | published | published, same entry band and invalidation, + `key_level_reaction_swept` (reached 2R or more in replay) |
| BUY 2026-10-15T18:00 | published | published, same entry band and invalidation, + `key_level_reaction_swept` (reached 2R or more in replay) |

## 5. Alternatives that were measured and do not work

The edge belongs to the reaction that took the liquidity itself. Neither of these was built:

- a deeper limit at the level's far edge with a fixed 50-pip stop: -0.06 R pooled;
- waiting for a later sweep and reclaim after an unswept confirmation: +0.06 R pooled.

## 6. Counters

`telemetry.CounterStrategyKeyLevelUnsweptRefused` (`strategy_key_level_unswept_refused`, labels symbol and timeframe) counts candidates refused for want of a sweep, recorded in `engine/worker.go` after every evaluation next to `strategy_stop_budget_refused`. It is kept apart from the stop-budget counter (a separate `strategyutil.SweepRefusalCounter`, drained through `strategy.UnsweptReporter`, reported on `Evaluation.UnsweptRefused`). A counter never blocks an evaluation. It is not listed in `docs/redis-contract.md` (Go counters are not), so nothing changed there.

## 7. Parity and goldens

The frozen-Python parity replays must not see the rule. `engine.WithoutKeyLevelSweepRequirement` removes `require_reaction_sweep`; `replaycapture.Options.WithoutStopBudget` (set by every parity replay: the frozen publisher had neither the stop budget nor the sweep rule) applies it, and `Options.WithoutKeyLevelSweep` applies it alone (the measurement test uses that). `test/keylevelparity`, `techniqueparity`, `legacyparity` and `detectorparity` pass without regenerating any parity golden.

Non-parity goldens that moved, and why (regenerated, reviewed, nothing else changed):

| golden | before | after | cause |
|---|---|---|---|
| `testdata/replay-go-xau-20260921.meta.json` (`TestRealCaptureReplayIsDeterministicAndMatchesTheCommittedGolden`) | 503 discovered opportunities, envelope hash `9714a857...` | 399 (-104, -20.7%), hash `152a9496...` | the XAU sweep rule (the 12 golden supply/demand confirmed envelopes are unchanged) |
| `testdata/confluence-zone-other-strategies-baseline-master-f18179f.json`, XAU 2026-09-21 capture | 1,657 candidates | 1,518 (-139) | the XAU sweep rule |
| same, XAU 2026-10-13 capture | 3,419 | 3,121 (-298) | the XAU sweep rule |
| same, EURUSD / GBPUSD 2026-10-13 | 4,406 / 6,007 | 4,406 / 6,007 (hash changed) | per-attempt selection only (the rule is XAU-only, asserted): a different, in-budget candidate is published where the best attempt used to be refused |
| same, USDJPY 2026-10-13 | 5,956 | 5,957 (+1) | per-attempt selection only |
| same, GBPJPY 2026-10-13 | 4,439 | 4,439 (identical) | none |
| `docs/strategies/confluence-zone-v3-replay-results.json` | the six hashes above | the six new ones | derived from the same replays; its Confluence Zone figures are unchanged (only the `other_strategies_candidates*` fields differ) |

That baseline file certifies that the Confluence Zone v3 change did not touch other strategies. Like the reaction-anchored-stop and stop-budget changes before it, a deliberate change to Key Level moves it, and the new values are the baseline from here on.

`test/stopbudget` (`replayOne`) now replays with `WithoutKeyLevelSweep`: it isolates the stop budget, and the rule refuses setups before the budget could classify them. Its committed results (`legacy-stop-budget-results.json`) stay valid for that reason.

## 8. Deferred (owner decisions)

- **Python's deeper risk-targeted XAU entry.** Deeper entries measured negative here (the deeper-limit alternative, -0.06 R pooled). Not touched.
- **FX.** The rule was not measured on FX; no FX override is set. Applying it is a decision that needs an FX measurement.
- **The zone strategies (supply, demand, order block, fvg).** The gap there was small (September +0.61 vs +0.72, 12-22 Oct +0.28 vs +0.19), so no rule.

## 9. Release

Production Go engine and the instrument config together. Opportunity ids change once at deploy (the strategy version is part of the id; the thesis id does not). If the production instrument config is rendered from `st-mich43l/ansible-library` group_vars, the leaf to add is `instruments.XAU.overrides.auto_algo.strategies.reaction.key_level.require_reaction_sweep: true` (that repository was not edited). Rollback: set the leaf to `false` (or remove it) and restart the analysis engine; v6 without the rule publishes what v5 did, apart from the per-attempt selection.
