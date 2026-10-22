# Q5: Opportunity quality validation and accounting

Scope: accounting correctness, technical-validity audit, quality diagnostics, validation of the deployed score against realized R, and the October loss diagnosis.
No strategy, stop, threshold, weight, sizing or enablement setting was changed.

## 1. Production and code state (checked 2026-10-22)

| Fact | Evidence |
| --- | --- |
| All three services run image `679a5cc` (Q2–Q4 included) | redeployed 2026-10-22 06:12–06:13Z (docker tags and start times) |
| The Q2 purge went live around 2026-10-20 08:10Z, not 2026-10-22 | deployed config mtimes and per-day published-opportunity counts in the analysis-engine logs |
| Q4 scoring (`Quality.Overall` = mean of `score_*`) is live since 2026-10-22 06:13Z | image tag |

Consequence: October results before 2026-10-20 08:10Z come from the pre-purge book, October 20–22 from the purged book with the old score, and nothing in October was produced by the Q4 score.
An earlier report that called all of October "pre-purge" was wrong and is superseded by this section.

## 2. Accounting (Phase 2)

`auto_trade_results.result_pips` is the highest TP archived (an owner directive), not realized profit. `realized_pips` is the executor's volume-weighted pips, but its denominator `risk_pips` excludes the 0.05 lot reaction risk leg that the numerator includes, and `realized_pips` was 0 on full stops until fix `9a4b9749` (2026-10-14).

Added, additive and idempotent (no history is rewritten):

- ctrader-engine: the position-closed event carries `volume_weighted_risk_pips` = Σ(|fill − original stop| × filled volume) / Σ filled volume over the legs that actually filled. Net R = `volume_weighted_pips / volume_weighted_risk_pips`.
- algo-bot: `app/autotrade/result_accounting.py`; three nullable columns on `auto_trade_results` (`weighted_risk_pips`, `net_r`, `result_method`). On conflict, the three move together and are never overwritten by NULL.
- `result_method` says how the figure was obtained: `fills_v1`, `fill_record_risk`, `legacy_loss_unbooked`, `reconstructed_ladder_v1`, `legacy_highest_tp`. Reports must state the method mix of any window.

Tests: XAU 40/20/20/20, FX 50/50, TP1→BE, full stop, partial stop, multi-leg fills, re-delivery, legacy rows (`TradePlanRuntimeTests.NetR.cs`, `test_result_accounting.py`, `test_auto_trade_stats_ingestion.py`).

## 3. Technical-validity audit (Phases 3 and 4)

Confirmed against code:

- Hard rejections already exist for: target-room against opposing structure (`evaluate_structural_target_room` in `worker.py`, including zone containment/overlap), duplicate thesis ownership, same-direction/opposite exposure, adapter checks (`go_opportunity_policy.py`: expiry, degenerate entry band or stop, target not beyond entry, missing reaction confirmation), and invalid target geometry.
- **Advisory only**: `min_confluence`, regime permission, zone width, `min_target_room_atr` and `min_reward_risk` are `preference_allow` results in `execution_policy.py`. They admit the trade and record `preference_notes`. The earlier statement that `min_confluence` is a quality gate is therefore wrong: no quality value gates admission today.
- `evaluate_execution_policy` is called with `available_target_room_pips=None` (since #499), so its `fixed_rr_room_insufficient` rejection cannot fire from the worker. The worker's own target-room check is what enforces room. Not changed: no loss was traced to it.

Observations from the replay (13 strategies, `analysis-engine/test/aequality`), not proven defects and not acted on:

- Several strategies publish more than one opportunity ID per structural thesis (for example Key Level 981 of 2,196 theses, Demand 330/527, Supply 325/510). Python's thesis ownership is what stops duplicate plans.
- Key Level FX technical risk is small (p10/p50 = 3.8/6.3 pips, 29% under 5 pips) against a 15-pip stop floor, so the executed stop is a median 2.3× the technical risk. Realized R is then measured against the widened stop.

Diagnostics added (Go): the `opportunity published` log line now also carries `quality_overall`, `quality_components`, `config_version` and `config_fingerprint`. With the strategy, direction, ID, structural ID, evidence codes, stars and invalidation already on that line, a score can be reproduced from the log alone. The Go engine does not know the Python admission thresholds, so the admission side stays in `preference_notes` on the decision record.

## 4. Validation against realized R (Phase 5)

Accepted trades only (rejected opportunities have no observed outcome and are not counted as losses), October 2026, net R by the estimator in §2, cluster bootstrap by symbol-day.

- The production score of the time (stars, `quality.overall`, `v2_raw`) is non-monotone against realized R, with near-zero rank correlation.
- The Q4 score has no October outcomes (it went live after the window); its replay-only separation against matched random entries is unchanged from #804 (informational, not ready for ranking).
- No threshold was changed. With this sample no threshold can be justified.

## 5. Loss diagnosis (Phase 6)

| Group | Window | n | Σ net R | mean R | 95% interval |
| --- | --- | --- | --- | --- | --- |
| All groups | Oct (whole month) | 595 | −24.5 | −0.041 | [−0.151, +0.081] |
| All groups | Oct 8+ | 364 | −9.2 | −0.025 | [−0.165, +0.129] |
| USDJPY | Oct (whole month) | 91 | −5.8 | −0.063 | [−0.408, +0.297] |
| USDJPY | Oct 8+ | 56 | −13.8 | −0.246 | [−0.546, +0.156] |
| FX Supply/Demand | Oct (whole month) | 62 | −4.4 | −0.071 | [−0.342, +0.246] |
| FX Supply/Demand | Oct 8+ | 40 | −10.9 | −0.273 | [−0.601, +0.123] |

The figures in the brief (USDJPY −18.3 R over 53, FX Supply/Demand −11.0 R over 40) depend on the window and the estimator. Every interval above includes zero, so neither group can be called a loser on this sample, and neither was disabled.

The Oct 14–15 cluster (−26.6 R in the brief) is explained by three things, none of them a proven code defect:

1. stacked same-direction FX exposure (up to 4 concurrent EURUSD groups; the same-direction exposure work in #774–#776 was not deployed at the time);
2. Range Edge scalps, purged on ~2026-10-20;
3. XAU Key Level counter-bias SELLs into an uptrend (valid stops, regime loss).

## 6. Remaining risks and insufficient data

- `legacy_highest_tp` and `reconstructed_ladder_v1` rows are estimates; any window dominated by them is approximate.
- One month, 595 groups, wide intervals: nothing here supports a threshold, weight or enablement change.
- Q4-scored trades need weeks of accepted trades before the score can be validated; revisit with the new `net_r` column once there are enough (the interval for a group must exclude zero before it justifies action).
- The advisory-only status of `min_confluence`, `min_reward_risk` and target-room preferences is a policy decision for the owner, not a defect fix.

## 7. Release requirements

- Additive migration: three nullable columns on `auto_trade_results` (created by the existing `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` path in `store.py`; no backfill).
- Deploy order: algo-bot reads the new event field optionally, so ctrader-engine and algo-bot can ship in either order; analysis-engine only gains log fields.
- Executor-event contract fixture (`contracts/autotrade/go-derived-plan-executor-events.json`) gains `volume_weighted_risk_pips`.
- Normal review and release process; nothing was deployed.
