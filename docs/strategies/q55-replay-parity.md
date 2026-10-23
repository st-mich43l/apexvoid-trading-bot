# Q5.5: production ground truth and replay fidelity

Baseline: `master` at `dd7e7993` (#808). Production runs image `97864dc` (#807, all three services started 2026-10-23 08:48 UTC); #808 changed documents only.
This change adds the durable plan snapshot (not yet deployed) and an offline replay-versus-production comparison. **No admission, score, stop, ladder, threshold, enablement or sizing behaviour changed, and no replay assumption was altered:** the Q5.3 and Q5.4 results are untouched and every figure below is new.

## 0. Answer

* **New plans are now joined, durably, to the exact geometry they were admitted with** (section 1): `auto_trade_plan_snapshots`, one immutable row per published plan, keyed by the `plan_id` that the fills and the result carry. Nothing depends on the six-day Redis list any more.
* **For FX the replay measures what production does.** On the 58 exactly matched, measured, fully booked FX groups, production earned -0.081 R and the replay -0.066 R: paired difference **-0.016 R [-0.19, +0.15]**, win/loss agreement 90%, exits of the same kind. The FX fill is at the proximal edge or 0.7 pips deeper (median), the executed stop is 0.99 of the replay's. So the negative FX replay expectancy is **not** a replay artifact: production's own matched FX groups are equally slightly negative, with an interval that includes zero.
* **For XAU the replay entry is wrong, and it is optimistic.** Production enters 16 pips deeper than the replay's limit at the proximal edge (46% of the band, risk-targeted entry), with a stop 0.85 of the replay's. On the 38 matched XAU groups the replay earns +0.56 R and production +0.36 R (-0.20 R [-0.44, +0.05]; Key Level -0.39 R [-0.59, -0.20], n=28). Re-simulating the same bars with the production fill and stop gives +0.16 R: the entry assumption explains the gap and the exits, within noise, the rest.
* **Replay candidates are not the executed set.** Only 40 of 941 replay-admitted FX observations since 10-15 (4%) were traded: the replay models the barrier gates and the two-star floor, production also applies exposure, thesis ownership, expiry and restarts (section 4).
* **Q5.6 should continue forward collection with the new snapshot and repair the XAU replay entry model; it should not rework a strategy or revalidate quality v1 yet** (section 6).

## 1. Production evidence contract

One row per published TradePlan, written once when the plan reaches the stream (`worker._publish_trade_plan_v8` -> `store.record_plan_snapshot`), after every admission decision and never read back by one.

| | |
|---|---|
| Key | `plan_id` (`v8:go_opp_<h>`) = `group_id` of `auto_trade_fills` and `auto_trade_results` |
| Opportunity | `opportunity_id` (`opp_<h>`) joins to `analysis_opportunity_events` (`event_kind='creation'`): Go's exact envelope, **Go's `config_fingerprint` and `config_version`** (the Python plan's own `provenance.config_fingerprint` is empty for Go-origin plans, so it is stored as NULL, not invented) |
| Identity and time | `symbol`, `strategy`, `direction`, `thesis_id`, `match_id`, `opportunity_created_at`, `admitted_at` |
| Scoring lineage | `quality_model_version` (Q5.3 tag; NULL for a pre-Q5.3 score, which names no rule), `quality_overall`, `quality_components` |
| Technical geometry | `technical_entry_low/high`, `technical_invalidation`, `technical_stop_pips`, `technical_targets_pips` (pips from the proximal edge; exact prices from the envelope) |
| Executable geometry | `entry_route`, `planned_entry_price`, `final_stop_price`, `final_stop_pips`, `stop_widening_ratio`, `stop_adjustment`, `targets` (id, price, close ratio of the planned ladder), `plan` (the full TradePlan) |
| Barrier and room | `barrier_present/side/low/high/tier/members`, `raw_room_pips`, `usable_room_pips`, `room_r`, `room_classification` (the Q5.2 `executable_room`, kept) |
| Costs known at admission | `execution_cost_pips` (config), `max_spread_ticks`, `max_slippage_ticks`, `executable_quote`, `spot_bid/ask`, `spot_spread_pips`, `spot_ts` |
| Everything else the policy measured | `admission` (route, legs, stop contract, target ladder, `target_room`, `executable_room`) |

Not available, therefore NULL and never derived: commission, slippage, and the broker quote at the fill (the executor, not the plan, has them; historical quotes are not stored). The fill itself comes from `auto_trade_fills`.

```sql
SELECT s.plan_id, s.strategy, s.stop_widening_ratio, s.room_r, e.envelope->>'config_fingerprint' AS go_config_fingerprint,
       f.entry_price, r.net_r, r.result_method
FROM auto_trade_plan_snapshots s
JOIN analysis_opportunity_events e ON e.opportunity_id = s.opportunity_id AND e.event_kind = 'creation'
LEFT JOIN auto_trade_fills f ON f.group_id = s.plan_id
LEFT JOIN auto_trade_results r ON r.group_id = s.plan_id AND r.trade_stream = 'algo_auto';
```

Guarantees, each tested: **idempotent and immutable** (`ON CONFLICT (plan_id) DO NOTHING`: a redelivery, a second publish or a restart leaves the first row, even when the repeat carries other numbers); **no loss with Redis retention** (the row survives deleting every lifecycle key); **a persistence failure never blocks or undoes the plan** (caught, logged, counted as `plan_snapshot_failed`); **restart-safe** (`CREATE TABLE IF NOT EXISTS`); **compatible** (additive table, no existing column or row touched, history is not back-filled).
Residual gap: a crash between the stream publish and the insert loses that one snapshot (the plan itself, in Redis `execution:plan:<id>`, is untouched); a snapshot is not written for plans published before this deploys.

Migration: one `CREATE TABLE IF NOT EXISTS` and one index at start-up (`store.init_db`). Rollback: revert the code (the worker stops writing; the table is harmless) and, if wanted, `DROP TABLE auto_trade_plan_snapshots`. Deploy order: any; only algo-bot changes.

## 2. Replay against production on matched opportunities

Pairing: a closed group (`algo_auto`, Go origin, current strategy) is paired with the replay record of the *same technical opportunity*: same symbol, strategy, direction and creation time, and the same entry band, invalidation and first target to half a pip (148 of the 163 pairs also share the opportunity id). Primary evidence: **measured methods only (`fills_v1`, `fill_record_risk`) and groups closed from 2026-10-15**, when full stops began to carry their realized pips (#9a4b9749, 10-14). Earlier, a loser was stored as an assumed -1 and excluded from "measured", which biases a measured-only mean upward (FX -0.03 R against -0.13 R with the assumed losers; shown, not used).

| | Groups |
|---|---:|
| Closed `algo_auto` Go groups with fills and envelope (since 2026-09-25) | 624 |
| Purged strategy (the replay no longer has them) | 207 |
| No replay opportunity with the same key (the current Go code does not publish it, or the capture ended) | 212 |
| Replay opportunity with the same key but different geometry | 42 |
| **Exactly matched** | **163** (FX 92, XAU 71) |
| Primary: measured, closed from 10-15 | **102** (FX 62, XAU 40) |
| Other methods among the matched | `fill_record_risk` 121, `fills_v1` 8, `legacy_loss_unbooked` 17, `reconstructed_ladder_v1` 17 |

Matchability rises with recency (0% of groups closed before 10-05, 26%, 52%, 63% in the week of 10-19): earlier groups were produced by earlier engine code. Groups closed after the captures end (2026-10-22 16:10 UTC) have no replay.

**Entry and stop (all 163 matched).**

| | FX (92) | XAU (71) |
|---|---|---|
| Fill deeper than the proximal edge, pips (median / p10 / p90) | 0.7 / 0.0 / 7.2 | 16.4 / 4.7 / 26.9 |
| as % of the zone width (median) | 14.6% | 45.7% |
| Filled worse than the proximal edge | 0% | 1% |
| Executed stop / replay stop (median) | 0.99 | 0.85 |
| Floor binds in the replay | 88% | 30% |

**Result (primary, paired where the replay filled).**

| Group | Matched / paired | Production R (PF) | Replay R (PF) | Production minus replay | 95% interval | Within 0.25 R | Win/loss agree |
|---|---|---|---|---:|---|---:|---:|
| FX | 62 / 58 | -0.081 (0.85) | -0.066 (0.88) | **-0.016** | [-0.189, +0.154] | 81% | 90% |
| EURUSD | 23 / 22 | -0.271 | -0.091 | -0.180 | [-0.494, +0.288] | 77% | 82% |
| GBPUSD | 11 / 10 | +0.172 | +0.219 | -0.047 | [-0.277, +0.028] | 90% | 100% |
| USDJPY | 13 / 13 | +0.023 | -0.038 | +0.061 | [+0.015, +0.186] | 92% | 100% |
| GBPJPY | 15 / 13 | -0.060 | -0.269 | +0.209 | [+0.046, +0.405] | 69% | 85% |
| FX Key Level | 22 / 21 | -0.114 | +0.057 | -0.171 | [-0.572, +0.112] | 81% | 90% |
| FX Supply + Demand | 24 / 22 | -0.163 | -0.341 | +0.178 | [+0.028, +0.434] | 86% | 95% |
| XAU | 40 / 38 | +0.356 (1.68) | +0.558 (2.77) | -0.202 | [-0.439, +0.052] | 53% | 79% |
| XAU Key Level | 30 / 28 | +0.207 | +0.593 | **-0.386** | [-0.586, -0.196] | 54% | 75% |

Instrument and strategy rows are small (9 to 28 pairs); four intervals exclude zero (USDJPY, GBPJPY and Supply + Demand above, XAU Key Level below), about what chance gives for the twenty read. The FX aggregate is what the evidence supports.
Not in the primary figures and reported separately in the results file: measured groups closed before 10-15 (biased upward), `legacy_loss_unbooked` (an assumed -1), `reconstructed_ladder_v1` (a rebuilt ladder). Quality-model versions are not mixed: every matched group is judged on its own outcome, and no score is used here.

**Re-simulation (same bars, production fill and stop substituted).** The simulator is a port of the Go replay and reproduces it exactly on 12 committed cases (BUY and SELL, FX and XAU, stop, breakeven stop, target, unfilled, not executable).

| Mean R | FX (59) | XAU (38) |
|---|---:|---:|
| Production, measured | -0.097 | +0.356 |
| Replay (limit at the proximal edge) | -0.039 | +0.558 |
| Production fill, replay stop | -0.191 | +0.121 |
| Production fill and stop | -0.132 | +0.163 |
| the same, target checked before stop within a bar | -0.132 | +0.105 |
| Re-simulation minus production (standard error) | -0.035 (0.053) | -0.193 (0.148) |

FX: substituting the production fill and stop moves the replay by -0.09 R and lands within 0.7 standard errors of the measured result. XAU: the replay is +0.40 R above the same bars entered where production entered; the remaining -0.19 R to production is 1.3 standard errors, and the bar-ordering assumption is not its cause. Entry timing, price and stop are therefore the XAU replay's defect; the exits are consistent within the sample.

## 3. Why the FX replay is negative (decomposition)

Replay, 2,694 admitted FX observations, mean -0.120 R [-0.18, -0.06]. All strata are read against the matched random entry of the same technical risk, which shares the stop, the ladder and the cost model.

| Component | Effect |
|---|---|
| Replay cost model (assumed constant spreads, 0.8 to 2 pips) | -0.071 R [-0.091, -0.052] of the mean; with no cost the mean is -0.049 |
| Random entries under the same stop, ladder and cost | -0.051 R |
| Strategy entries against random | **-0.069 R** [-0.129, -0.014] |
| Fill selection | no separate effect on the mean: the random baseline is filled by the same rule; production's FX fills agree with the replay (section 2) |
| Proximal-edge fill against the actual fill | the actual fill is 0.7 pips deeper; re-simulating with it is -0.09 R lower, so the replay entry is *not* pessimistic for FX |
| Stop-floor widening | -0.04, -0.12, -0.16 R by widening bucket (1-1.5x, 1.5-2.5x, > 2.5x); above random -0.113 R [-0.209, -0.017] for > 2.5x |
| Session (UTC) | Asia -0.012, London -0.016, NY -0.130 [-0.235, -0.026], late -0.109 [-0.21, -0.004] above random |
| Higher-timeframe alignment | aligned (57%) -0.180 [-0.290, -0.073]; not aligned +0.082 [-0.028, +0.196] above random |
| Instrument | EURUSD -0.134 [-0.24, -0.02], GBPJPY -0.103, GBPUSD -0.051, USDJPY -0.002 |
| Strategy | Order Block -0.206 [-0.340, -0.068], Key Level -0.078, Demand -0.057, Supply -0.041, iFVG +0.128 |

Reading: most of the negative mean is the cost and the structure of the stop and ladder, common to every entry; the strategy entries are a further -0.07 R. The strata are exploratory, correlated with one another (alignment, session, strategy and instrument overlap) and about thirty were read: the alignment gradient is the largest and points *against* the doctrine that bias-aligned entries are better, but it is a hypothesis for forward testing, not a rule, and nothing was changed.

## 4. Replay candidates against what production executes

Among the replay-admitted, filled, thinned FX observations since 10-15, 40 of 941 (4%) were traded; XAU 21 of 153 (14%). The traded ones were not representative (FX replay R -0.23 against -0.14 for the rest; XAU +0.49 against +0.02), on very small counts.
The production funnel (Redis lifecycle of 21,129 opportunities, a retained window of about 25 days of rejections) ends, in order: `opposing_entry_overlap` 3,554 and `opposing_barrier_room_below_cost` 879 (replayed); `fx_opposite_position_not_allowed` 3,351, `same_direction_active_before_tp2` 2,995, `entry_zone_overlap_same_direction` 1,315 and `xau_opposite_position_too_close` 595 (**exposure rules the replay does not model**); `startup_reconciliation` 2,548 (opportunities dropped when the bot restarted); `setup_expired_before_execution` 2,063; `thesis_already_owned` 484; `confluence_below_minimum` 608 (replayed); stop-envelope rejections 444.

## 5. Classification

| Finding | Class |
|---|---|
| Plan-time evidence lived only in a six-day Redis list | **Confirmed implementation defect (evidence integrity), fixed** (section 1) |
| FX entry, stop, target and result agree between replay and production | no defect; replay validated for FX |
| XAU replay enters at the proximal edge; production enters 16 pips deeper by risk-targeted entry; replay +0.20 R optimistic | **Replay-model mismatch** (not changed here) |
| Replay admits what exposure, ownership, expiry and restarts stop in production (4% executed) | **Replay-model mismatch** (the replay is a measure of strategy edge under the barrier gates, not of the book's P&L) |
| Python plan provenance (`config_fingerprint`, `analysis_engine_version`) is empty for Go-origin plans | Data-quality limitation (Go's is on the envelope; joined) |
| No historical quote, commission or slippage; replay spreads are constants | Data-quality limitation (forward: `spot_bid/ask` in the snapshot) |
| FX strategy entries -0.07 R against random; HTF-aligned entries worse | Valid strategy behaviour without established expectancy |
| Production FX expectancy (n=58, interval includes zero); USDJPY, Key Level and FX Supply/Demand in production | Unresolved due to sample size |
| Execution defects (fills, stops, ladders, partial exits, breakeven) | none found |

## 6. Recommendation for Q5.6

1. **Continue forward collection (4)**, now with the snapshot: from the first deploy, every plan carries its barrier, room, widening, route and costs. About 100 groups per side resolve 0.5 R and about 390 per side 0.25 R (section 6 of Q5.4); FX closes roughly 10 groups a day.
2. **Repair a demonstrated replay defect (1):** model the XAU risk-targeted market entry in the replay (and optionally the exposure funnel), then rerun the Q5.3 and Q5.4 XAU figures *alongside* the old ones. No FX replay assumption needs repair.
3. **Not justified yet:** reworking a strategy's entry (2) (the FX evidence is a -0.07 R against random with a wide interval, and the alignment gradient is exploratory); revalidating quality model v1 on production outcomes (3) (still 13 scored groups at Q5.3; the snapshot records the version from now on).

## 7. Tests and reproduction

Python (full suite and the new files): `tests/test_plan_snapshot.py` (builder: technical and executable geometry, barrier and room in R, lineage never invented, unavailable is None, no barrier, JPY and XAU precision with the volume-weighted reference, JSON-safe; store: written once, redelivery changes nothing, NULL for unavailable, join to fill and result, restart and bootstrap, distinct plans), `tests/test_go_full_chain.py` (+3: a published plan leaves a durable snapshot that outlives the Redis lifecycle, a repeat publish writes none, a failing store never blocks the plan), `tests/test_q55_replay_parity.py` (the simulator against 12 Go results; matching, signed fill depth for BUY and SELL, volume-weighted fill, cutoff and method separation, decomposition, funnel). No Go or C# file changed.

```
python algo-bot/tools/q55_replay_parity.py --groups groups.jsonl --records records.jsonl --decisions decisions.jsonl --random random.jsonl \
    --captures analysis-engine/testdata --lifecycle lifecycle.jsonl --out q55.json
# groups.jsonl: read-only SELECT of auto_trade_results (trade_stream='algo_auto') with auto_trade_fills and the creation envelope (as in Q5.4);
# records/decisions/random: the Q5.4 replay steps; lifecycle.jsonl: LRANGE of auto_trade:lifecycle:go_opp_* kept to rejected/invalidated/expired/candidate_published
```

Results: [q55-replay-parity-results.json](q55-replay-parity-results.json). Limits: pairs are exact technical matches only (163 of 417 current-strategy groups); production fills are volume-weighted prices (XAU groups have one fill row); the re-simulation uses bid-only bars and the replay's constant spreads; quotes at the fill are unknown.
