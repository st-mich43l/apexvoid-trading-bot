# Arbitration and strategy-quality audit (P1)

Date 2026-10-08, base `6b03d5f9` (master with PRs #753-#756). Evidence is the six committed
production captures (about five days each), read-only production records, and the
reproducible tool `algo-bot/tools/arbitration_eval.py`.

**Verdict.** No alternative ranking model is justified, so the production ranking is
unchanged. Three correctness defects were confirmed and fixed. The unproven proposals stay
offline.

Reproduce:

```bash
cd analysis-engine && ARBITRATION_DUMP_DIR=/tmp/arb-dump go test ./test/replaycapture -run TestDumpCandidates
cd ../algo-bot && PYTHONPATH=. python -m tools.arbitration_eval /tmp/arb-dump --out /tmp/arbitration-eval.json
```

Both steps are deterministic: two runs give identical dump hashes and a byte-identical report.
The committed result is [arbitration-eval-results.json](arbitration-eval-results.json).

## 1. The path from a Go candidate to a TradePlan

1. Each of the 21 Go strategies emits independent candidates with its own `quality.overall`
   (`analysis-engine/internal/strategy/*`). The engine attaches one shared confluence block to
   every candidate (`Technical.Confluence`: selected stars, a continuous `v2_raw`, the factors).
   Go's own `internal/arbitration` is provenance telemetry; it cannot veto an intent.
2. The adapter (`go_opportunity_policy.build_strategy_match`) writes a `StrategyMatch`.
   `confluence` is Go's selected stars, `quality_overall` is Go's score, `structural_quality` is
   `v2_raw`. Resting zones whose strategy requires a reaction stay observations.
3. Each cycle, admission removes what cannot compete (existing terminal state, disabled
   strategy or mode, symbol mismatch, confluence floor). Everything else gets
   `executable_now` from the live quote against the entry contract.
4. `arbitrate_execution_intents` ranks by: executable now, `quality_overall`, confluence stars,
   `v2_raw`, freshness, intent id. The direction is decided among executable intents; a
   quality gap under 0.15 holds both directions unless exactly one agrees with the HTF bias.
   Then one winner per same-thesis group (shared Go thesis, structural id, or entry corridors
   within one ATR); the rest are `same_thesis_suppressed`, named after their winner.
5. `publish_ranked_cycle` takes the ordered winners under a per-symbol, per-cycle lock. Only a
   terminal reject exposes the next intent; any ownership or global block stops the chain.
   Execution policy (reward/risk, target room, stop envelope), exposure, the news guard and
   the atomic 45-minute corridor reservation run **after** selection and fall back through
   terminal rejects.

Ranking is therefore execution-aware only through `executable_now`.

## 2. Findings

| Class | Finding | Evidence | Action |
|---|---|---|---|
| CONFIRMED_BUG | A Go invalidation or expiry (`on_terminal`) transitions the setup but never updates its route outcome, and the setup record keeps no reason. The outcome stays at its last waiting state until a restart, when startup reconciliation rewrites it to a generic `startup_reconciliation`. | 4,705 of 6,499 outcomes in four days, 9,522 of 11,176 stored now; the true reason survives only in Go's ledger | Fixed: `go_<reason>` is recorded unless the outcome is already terminal (`go_opportunity_policy._record_terminal_outcome`) |
| CONFIRMED_BUG (latent) | Ranking is not a total order for a non-finite score: four intents in 24 delivery orders gave 8 different results with one NaN. `quality_overall` is rejected upstream, but `confluence_v2_raw` and freshness were not. | `tests/test_arbitration_determinism.py` (4 of its 23 tests fail on the old code) | Fixed: None and non-finite scores rank as unavailable (`arbitration._score`) |
| CONFIRMED_BUG (latent) | `required_limit_side_unavailable` is a per-intent waiting state, but the publication result treated it as a global block, so a waiting top intent stopped every lower-ranked executable one. | `worker._strategy_publication_result`; reproduced in `tests/test_publication_fallback.py`; **0 occurrences in 96 production hours** | Fixed: it now falls back like `waiting_retest_entry_zone` |
| No defect | Same-thesis grouping depends only on the ranked set. | 4,534 cycles with two or more executable intents, three shuffled delivery orders each (13,602): 0 mismatches | Regression test added |
| No defect | Overlap chaining. Grouping is winner-anchored, not transitive (A~B, B~C, A not C gives {A,B}, {C}). | Differs from the transitive closure in 2 of 4,622 same-direction contested sets | Documented and tested |
| No defect | One-ATR corridor pad. 98.6% of merges are zones that intersect; only 1.4% (143 of 10,231) depend on the pad. | `thesis_audit` | Keep |
| No defect | No technical opportunity is lost silently. Every `match_written` opportunity in four days has a route outcome or a lifecycle state; the 152 without an outcome are published plans or admission rejects whose 23-hour outcome key has expired. | Production Redis | Report only |
| INSUFFICIENT_EVIDENCE | Reservation non-displacement. 1,220 better-ranked candidates were blocked by an earlier winner's 45-minute reservation (1,089 are the same strategy re-confirming). | If each had displaced its holder: mean hypothetical R +0.010, CI [-0.065, +0.091] | Keep the reservation unchanged |
| INSUFFICIENT_EVIDENCE | 544 opportunities rejected for `higher_timeframe_bias_unavailable`. | Recorded in the ledger; no outcome data | Next stage |

`event_too_old` (2,052) is one backfill at the 2026-10-01 cutover (1,867 in a single hour),
recorded in the ledger, not a live defect.

## 3. Strategy quality audit (all 21)

Common to all: the shared confluence block (HTF alignment, touches, wick rejection,
displacement, session, structural agreement, Fibonacci, CHoCH) is computed the same way for
every candidate. Observed over the captures (20 strategies emitted; `impulse_pullback` is
contained and produced nothing).

**The central fact: for 11 strategies the executable candidate's `quality.overall` is
`confluence stars / 3`**, one number that the ranking then compares again as "confluence".
It carries no information beyond the stars, and has three values at most.

| Strategy | Executable `quality.overall` | Observed values (n candidates) | Independent signal beyond stars | Limits |
|---|---|---|---|---|
| key_level | stars/3 | {0.67, 1.0} (3,521) | touches (component only) | quantised |
| supply, demand | stars/3 on a confirmed reaction; resting zone: mean(strength, relevance, freshness) | 0.67-1.0, 8 values (6,959 / 6,881) | zone strength is only in the resting score | 75% of rows are non-tradable observations |
| order_block, fvg | stars/3; resting: strength, relevance, fill | 8 / 7 values | as above | FVG's touch count is a fill signal |
| ifvg | stars/3; resting: 0.7 strength + 0.3 | {0.67, 1.0} | none | contained on XAU |
| crt | stars/3 | {0.67, 1.0} (210) | none | |
| flip_zone, session_level, trendline, box_breakout | stars/3 | 2 values (trendline 1: 8 rows) | none | session_level contained on XAU |
| confluence_zone | independent facts / 4 | 0.5-1.0, 4 values (4,050) | the count of distinct overlapping techniques | does not suppress other strategies except as a ranked same-thesis competitor |
| break_retest | constant 0.75 | 1 value (63) | none | no resolution; zone is a level |
| momentum_ride | 0.5 impulse + 0.3 continuity + 0.2 | 0.48-1.0, 23 values (34) | displacement, overlap | small n |
| snap_back | 0.45 + 0.1 touches + 0.15 grade A + 0.1 extension | 0.46-0.96, 25 values (50) | liquidity grade, extension | small n |
| fade_scalp | 0.55 + 0.15 grade + 0.05 touches | 0.65-0.85, 4 values (181) | liquidity grade | |
| range_edge | 0.25 + 0.06 barrier score + 0.15 grade A | 0.68-1.0, 25 values (646) | barrier score | |
| liquidity_sweep | reclaim bar body / ATR | 0.33-1.0, 13 values (17) | one bar's displacement | tiny n |
| range_sweep | reclaim depth / ATR | 0.13-0.31 (5) | sweep depth | **max 0.31, so it can never win a quality comparison against a 0.67 / 1.0 strategy** |
| scalp_breakout_retest | score / 100 | 0.65-0.85, 8 values (26) | displacement, acceptance, retest quality | small n |
| impulse_pullback | displacement / (2 x threshold) | none observed | displacement | observe-only everywhere |

Within the capture window a higher score has not meant a better outcome (section 4): rank
correlation of hypothetical R with `quality` is -0.054 pooled, with stars -0.061, with `v2_raw`
-0.078. A within-strategy percentile would not change that: it is a rank inside one
strategy's own history, not a probability of winning, and this report never reads it as one.

## 4. Comparative evaluation

Identical eligible sets: 30,541 candidates, 7,506 closed-bar cycles, 20,536 eligible
intents after reaction, containment, expiry and invalidation filters. Model A is production:
the generic implementation reproduces `arbitrate_execution_intents` with **0 differences** in
all 6,882 non-empty cycles. Model B is within-strategy percentile of quality, fitted on the
first 60% of each capture and applied to the last 40%. Model C ranks shared confluence stars
and `v2_raw` ahead of quality. Model D (outcome-calibrated) is **not run**: only 19 trades have
`realized_pips` (the new executor contract started 2026-10-08 07:13; 273 trades have only the
legacy highest-target `result_pips`, which is not P&L). It needs a few hundred clean trades.

| | A incumbent | B percentile | C confluence-first |
|---|---|---|---|
| Cross-strategy groups tied on the top score | 478 of 2,092 (23%) | 663 of 2,021 | 1,152 of 2,290 |
| Direction conflicts (of 6,882 cycles) | 128 | 115 | 112 |
| Winner differs from A (of 4,406 contested cycles) | | 2,179 (dev 1,667, OOS 512) | 1,777 (dev 1,431, OOS 346) |
| Unique decision pairs that differ | | 1,485 | 1,268 |
| Played forward with the 45-minute reservation: published | 1,375 | 1,385 | 1,384 |
| Same published set as A (Jaccard) | | 0.683 | 0.684 |
| Hypothetical fill rate | 0.733 | 0.723 | 0.731 |
| Hypothetical mean R (all / dev / OOS) | 0.014 / 0.044 / -0.081 | 0.022 / 0.031 / -0.005 | 0.007 / 0.051 / -0.130 |
| Same, spread doubled | -0.058 | -0.043 | -0.072 |
| **Paired mean R, alternative minus A (all)** | | +0.008 [-0.072, 0.085] | -0.065 [-0.172, 0.038] |
| Paired, out of sample | | +0.029 [-0.094, 0.160] | **-0.206 [-0.480, 0.001]** |

The 23% tie rate is the production grouping on eligible, per-bar intents; the previous
audit's 47% used transitive corridor groups over reaction-only candidates, so the two are not
the same quantity. Winner changes in B and C concentrate on the strategies whose score is
stars/3 (key_level, supply, demand, confluence_zone, ifvg) in every symbol. Neither alternative
beats the incumbent: B's difference is indistinguishable from zero, and C is worse out of
sample. A mean R that flips sign when the spread is doubled says the simulator, not the
ranking, dominates these numbers.

**What the outcome numbers are.** A hypothetical opportunity outcome, not broker P&L. One
single-leg entry per candidate (limit at the near edge, market when the quote is inside the
zone), bid-only bars with assumed constant spreads, stop at Go's invalidation held to the
instrument's floor and cap, targets 1R-4R closing 40/20/20/20 with break-even after 1R, the
stop acting before targets inside a bar, nothing before the next bar, 24 hours maximum.
It does not model the XAU ladders, partial fills, risk legs, opposing exposure or the live
quote. In it 12.9% of key_level and 42% of XAU candidates exceed the stop cap, which
overstates production (it measures one leg, not the ladder). Comparisons are paired on
the same candidates and chronologically split; no claim of improved expectancy is made.

By dimension (hypothetical R of 7,228 unique executable candidates; strategy, instrument,
timeframe, session, volatility tercile, quality bin and stars are in the JSON):
3-star candidates average -0.115 R against +0.059 for 2-star, and quality 1.0 averages
-0.109 against +0.046 for 0.5-0.67. That is a hypothesis about late, over-confirmed entries,
not a finding: the candidates overlap in time and no clustered interval was computed.

## 5. Execution-aware ranking

Selection happens before reward/risk, target room, stop envelope, exposure and spread are
checked on the real plan, and falls back through terminal rejects (publication blocks seen:
`fx_opposite_position_not_allowed` 282, `same_direction_active_before_tp2` 264,
`opposing_entry_overlap` 253, `entry_zone_overlap_same_direction` 121,
`opposing_barrier_room_below_cost` 72, `stop_exceeds_go_invalidation_envelope` 26). Geometry
comes from the TradePlan, not card rounding. PR #755 (Range Edge never chases, the planner
declares the risk leg, the card matches the plan), #756 and PR #754's original-stop R are
unchanged and their suites run green. Entry execution is not redesigned here.

## 6. Code changes

Fixed with regression tests: the terminal route outcome, the non-finite ranking, the
per-intent waiting fallback. Added: `tools/arbitration_eval.py`, its tests,
`analysis-engine/test/replaycapture/dump_candidates_test.go` (does nothing without
`ARBITRATION_DUMP_DIR`), and permutation and fallback tests. Production ranking, risk,
containment, strategy enablement, stops and exposure are untouched.

## 7. Recommendations for the next stages

- **Analysis Engine tuning.** 11 strategies report `stars/3` as their quality, so the shared
  confluence score decides. Give those strategies a quality that measures something of their
  own (zone strength and freshness, level touches), keep it independent per strategy, and
  decide the cross-strategy comparison in Python once outcome data exists. Give `range_sweep`
  (max 0.31) and `break_retest` (constant) a usable scale. Continue to publish the shared
  confluence block unchanged.
- **Execution.** The rank is blind to stop-envelope feasibility (XAU is the worst case in the
  simulation); the retained waiting states, the 23-hour outcome TTL and the `reaction_confirmation_unavailable` / HTF-unavailable rejections deserve their own pass.
- **Observability.** Persist the final arbitration and route outcome in Postgres (an additive
  table, no change to `/trade_stats` or journal accounting) so audits do not depend on a
  23-hour Redis key.
- **Evidence.** Re-run `arbitration_eval` after about 300 clean `realized_pips` trades, add
  a clustered interval, and consider model D then. Until then keep quality as is and do not
  read any percentile as a win probability.
