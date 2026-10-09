# Arbitration and strategy-quality audit (P1)

Date 2026-10-09. First written 2026-10-08 on master with PRs #753-#756; corrected on master
with #753-#759 (the P1 finalization PR). Evidence is the six committed production captures
(about five days each), a read-only export of production's own admission records, and the
reproducible tool `algo-bot/tools/arbitration_eval.py`.

**Verdict.** Keep the production ranking (Model A). The first evaluation was wrong in two ways
that mattered (it dropped the wrong candidates, and its simulator read the future), so its
numbers are superseded: [arbitration-eval-results.p1-initial.json](arbitration-eval-results.p1-initial.json)
is kept as the previous baseline, [arbitration-eval-results.p1-logic-on-current-data.json](arbitration-eval-results.p1-logic-on-current-data.json)
is the old logic on today's dump, and [arbitration-eval-results.json](arbitration-eval-results.json)
is the corrected result. After the correction no alternative is supported by independent
evidence. Model B shows a paired signal that is a **hypothesis**, not a finding (section 4).

Reproduce:

```bash
cd analysis-engine && ARBITRATION_DUMP_DIR=/tmp/arb-dump go test ./test/replaycapture -run TestDumpCandidates
cd ../algo-bot && PYTHONPATH=. python -m tools.arbitration_eval /tmp/arb-dump --out /tmp/arbitration-eval.json \
  [--production-export prod_export.jsonl --production-plans plans.csv --production-results results.csv \
   --code-since 1791433800]
```

The dump now carries the production creation envelope for every candidate, so the tool runs
production's own admission code on it. The three `--production-*` inputs are a read-only
export from the production Postgres (`analysis_opportunities.creation_envelope`,
`analysis_shadow_decisions`, filled plans, closed `auto_trade_results`); they hold live trading
records and are **not committed**. Without them the report omits Levels B and C and says so.
Both steps are deterministic: two runs give a byte-identical report.
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

### 2a. Corrective findings (P1 finalization)

Each was confirmed by reproducing it before the fix. Counts are over the six captures.

| # | Finding | Root cause | Actual effect | Fix | Regression coverage |
|---|---|---|---|---|---|
| 1 | The offline tool treated supply and demand resting zones as tradable | `requires_reaction()` parsed `ScopeProfile(...)` source with a regex that matches `requires_reaction=True` but not the positional `True` supply/demand use | 10,413 candidate-bar rows (supply 5,222, demand 5,172, other 19; EURUSD 1,546, GBPJPY 1,846, GBPUSD 2,233, USDJPY 2,646, XAU 2,142) competed that production keeps as observations; 688 unique resting observations. Eligible intent rows fell from 20,536 to 10,123 | No parsing: the tool imports `REVIEWED_SCOPES`, `build_strategy_match`, `containment_reason`, `evaluate_freshness` and the worker's `static_admission_failure`, and builds intents with the worker's own `execution_intent_for_match` (extracted so both share one builder) | `test_arbitration_eval_tool.py`: registry-driven eligibility for all 21 strategies, positional-`True` case, mutation-checked |
| 2 | `simulate()` read the future | When the loop ended with a position open it settled the rest at `bars[-1][4]` and called it a `timeout`, whether the 24-hour expiry had passed or the capture had simply run out; an entry that never filled before the data ended was scored `unfilled_until_expiry` for the same reason | Positions the data never resolved were scored at the last close in the file (a price after the decision horizon), and a truncated capture looked like a real timeout | Strictly causal walk: a bar counts only once complete and only up to the decision's own expiry; the stop acts before targets inside a bar; a position still open at the end is `censored` (not a timeout, not scored); no entry means `not_filled`; no price means `unavailable`. Outcome consumers use only `usable` results | `test_arbitration_eval_tool.py`: prefix invariance (appending future bars never changes an earlier result), stop-before-target, censoring, timeout vs censored, determinism |
| 3 | A Go invalidation or expiry could lose or overwrite its attribution | `_record_terminal_outcome()` built a thin `SimpleNamespace` with no strategy or direction, so a projected outcome could overwrite them; a redelivered terminal event skipped projection when the setup was already terminal; startup reconciliation replaced `go_*` with `startup_reconciliation` | Outcomes lost strategy/direction, a failed projection was never retried, and a restart rewrote the true Go reason | `SetupRecord` carries `strategy`, `strategy_family`, `direction`, `structural_source`, `terminal_reason` (optional, old records still load); one `attribution_shim` fills attribution from the match, the record and the event; `record_route_outcome` keeps earlier attribution and never lets an analysis-terminal stage (`scanner`, `entry_invalidation`) overwrite an executor/broker status; the retry runs whenever the setup is terminal with the Go reason (or none); reconciliation keeps `record.terminal_reason` and counts `terminal_reason_unrecoverable` | `test_terminal_attribution.py` (14): attribution kept, redelivery idempotent, retry after a failed projection, broker outcome never overwritten, old record format, reconciliation keeps the reason |
| 4 | "Baseline reproduced" overstated what was compared | One number stood for three different claims | Level B and C were never measured | Three explicit levels with denominators and unavailable counts (section 4) | `level_a`, `level_b`, `level_c` tests with synthetic ledgers |

### 2b. Earlier findings (P1, still valid)

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

## 4. Comparative evaluation (corrected)

Inputs: 6 captures, 30,541 candidate-bar rows, 11,113 unique opportunities, 7,506 closed-bar
cycles. Production's own admission code decides eligibility.

**Admission of the 11,113 unique opportunities:** 7,024 admitted; excluded:
`reaction_confirmation_unavailable` 2,907, `execution_contained` 477,
`execution_contained_structure_timeframe` 698, `reaction_not_current_observation` 6,
`confluence_below_minimum` 1. The per-strategy and per-symbol table is in the JSON
(`admission`, `registry`); all 21 strategies are audited from the registry
(reaction-required, 13: confluence_zone, crt, demand, fade_scalp, flip_zone, fvg, ifvg,
order_block, range_edge, session_level, snap_back, supply, trendline; immediate-entry, 8:
box_breakout, break_retest, impulse_pullback, key_level, liquidity_sweep, momentum_ride,
range_sweep, scalp_breakout_retest). That leaves **10,123 eligible intent rows** in 5,322 cycles (the first
cut had 20,536).

### Reproduction claims, by level

| Level | Claim | Denominator | Result | Unavailable |
|---|---|---|---|---|
| A. Function equivalence | offline Model A == `arbitrate_execution_intents` on the same intents | 5,322 cycles with at least one admitted intent | **0 disagreements** | 0 |
| B. Pipeline eligibility | offline admission == production admission on production's own envelopes (exported from Postgres) | same code version (since 2026-10-08 04:30Z, after the #743 containment deploy): 2,081 compared | **2,081 / 2,081 agree** | 171 recovery events need the live Redis book |
| B (all history) | the same, over everything exported | 16,470 compared of 19,568 exported | 13,950 agree (84.7%); the 2,520 disagreements are code and config drift (reaction requirement, containment lists, evidence codes, a removed HTF-bias rejection, a degenerate-band check) and cannot be attributed to the tool | 3,098 recovery events |
| C. Historical decision equivalence | reconstructed candidates and selection == what production recorded and published | only 645 of the 11,113 replay ids exist in the production ledger | field-identical on all five fields 240 / 645 (entry 607, quality 459, invalidation 261); admission verdict agrees 636 / 645; of 296 filled production plans 6 are in the replay and Model A offline would have published 0 of those 6 | 10,468 replay-only ids |

**Level C is not established.** The replay is today's engine build on captured bars; production
decided on the live bars of the time with the engine of the time. The tool says so in its
output rather than claiming a baseline reproduction. Nothing in this report is a statement
about what production would have earned.

### Models

Model A is production. Model B ranks a within-strategy percentile of quality (fitted on the
first 60% of each capture, applied to the last 40%). Model C ranks shared confluence stars and
`v2_raw` ahead of quality. Model D (outcome-calibrated) is **not evaluated**: 288 closed trades,
39 with `realized_pips` (the executor began publishing it 2026-10-08); the strategy with the
most has 10, and the threshold is 30 per strategy. `result_pips` is the highest target reached,
not a P&L, so it is not used.

| | A incumbent | B percentile | C confluence-first |
|---|---|---|---|
| Cross-strategy groups (tied on the top score) | 2,109 (890) | 2,023 (2,023) | 2,101 (1,129) |
| Direction conflicts | 38 | 102 | 41 |
| Winner differs from A (of ~2,200 contested) | | 232 (dev 191, OOS 41) | 83 (dev 64, OOS 19) |
| Published, 45-minute reservation played forward | 1,275 | 1,273 | 1,278 |
| Same published set as A (Jaccard) | | 0.856 | 0.968 |
| Hypothetical mean R (all / dev / OOS) | 0.008 / 0.057 / -0.171 | 0.006 / 0.052 / -0.164 | 0.011 / 0.062 / -0.175 |
| Same, spread doubled | -0.056 | -0.053 | -0.053 |
| Paired mean R, alternative minus A (pairs) | | +0.473 (214) | +0.109 (74) |
| Pair interval (treats pairs as independent) | | [0.184, 0.775] | [-0.133, 0.368] |
| Cluster interval (resamples symbol-days) | | [0.065, 0.872], 27 episodes | [-0.206, 0.395], 19 episodes |
| Paired, out of sample | | +0.531 (28 pairs) | +0.059 (12 pairs) |

In Model A's contested cycles the rank is decided by `quality_overall` in 1,170, by
`structural_quality` in 1,026, by confluence in 3, by freshness in 6 and by intent id in 20.
Model B's percentile collapses to 0.5 for single-valued strategies, so every cross-strategy
group ties on it and its own tie-breaks decide: 965 by confluence and 1,166 by structural quality.

**Reading.** The corrected evaluation removes the first run's headline (B +0.008, C -0.065
out of sample): those came from comparing against intents production never ranks. What is left:

- **C** is not distinguishable from A (cluster interval spans zero), and its published set is
  97% the same.
- **B** has a positive paired difference whose lower bound stays above zero even when whole
  symbol-days are resampled. It is nevertheless a **hypothesis**, for four reasons: the 214
  diverging pairs come from 27 symbol-day episodes; the effect is a strategy-mix effect (B
  prefers other strategies in cross-strategy ties, it does not rank within a strategy better);
  playing the whole selection forward gives no portfolio improvement (mean R 0.006 against
  0.008, OOS -0.164 against -0.171, doubled spread -0.053 against -0.056); and the simulator
  is a single-leg model of an XAU/FX system with ladders. A hypothesis is worth a forward
  test once realized outcomes exist (Model D's data requirement), not a production change.
- Rank correlation of hypothetical R with `quality` is -0.062 pooled (dev -0.051, OOS -0.106),
  with stars -0.066, with `v2_raw` -0.052; strategy mean R is about 0. A higher score has not
  meant a better outcome in this window.

### Before and after

| | P1 initial (committed 2026-10-08) | P1 logic on current data | Corrected |
|---|---|---|---|
| Eligible intent rows | 20,536 | 20,536 | 10,123 |
| Unique executable candidates with a usable outcome | 7,228 | 7,228 | 6,481 (complete 5,114, censored 173, not filled 1,194) |
| A published / mean R / OOS | 1,375 / 0.0135 / -0.081 | 1,375 / 0.0142 / -0.086 | 1,275 / 0.0079 / -0.171 |
| B published / mean R / OOS | 1,385 / 0.0219 / -0.005 | 1,385 / 0.0287 / +0.014 | 1,273 / 0.0055 / -0.164 |
| C published / mean R / OOS | 1,384 / 0.0073 / -0.130 | 1,384 / 0.0145 / -0.123 | 1,278 / 0.0107 / -0.175 |
| Rank correlation, quality vs R | -0.054 | -0.055 | -0.062 |

The first run's flattering Model B (OOS about zero or positive) was produced by ineligible
supply/demand rows plus a simulator that settled open positions at the end of the data.

**What the outcome numbers are.** A hypothetical opportunity outcome, not broker P&L. One
single-leg entry per candidate (limit at the near edge, market when the quote is inside the
zone), bid-only bars with assumed constant spreads, stop at Go's invalidation held to the
instrument's floor and cap, targets 1R-4R closing 40/20/20/20 with break-even after 1R, the
stop acting before targets inside a bar, nothing before the next completed bar, 24 hours
maximum, anything still open when the data ends is censored. It does not model the XAU
ladders, partial fills, risk legs, opposing exposure or the live quote.

**Limitations.** Replay is today's engine on captured bars (Level C); the captures cover about
five days per symbol; candidates overlap in time, so pair intervals are optimistic and the
cluster interval has few episodes; spreads are assumed; Model D has no data.

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

Corrective PR (P1 finalization): `execution_intent.py` (new single builder of `ExecutionIntent`
from a `StrategyMatch`, used by the worker and the tool); `worker.static_admission_failure`
(the admission gates as a pure function); `setup_lifecycle`, `route_outcome`,
`terminal_cleanup`, `go_opportunity_policy`, `startup_reconciliation` (terminal attribution,
idempotent retry, no overwrite of executor outcomes); `tools/arbitration_eval.py` (production
admission code, causal simulator, Levels A/B/C, clustered intervals, Model D assessment);
`TestDumpCandidates` writes the production creation envelope. From the first P1 PR, still
present: the three #757 fixes (non-finite scores rank as unavailable, terminal route reasons,
the per-intent waiting fallback). Production ranking, risk, containment, strategy enablement,
stops, exposure, the 45-minute reservation, the 1-ATR corridor and XAU/FX entry, stop and
target geometry are untouched, as are #754, #755, #756 and #758.

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

## 8. Final arbitration recommendation

**Keep Model A (production).** Confirmed: it is a deterministic total order (0 order-dependent
results in 4,000 randomized trials including non-finite scores), the offline copy equals the
production function (Level A, 5,322 cycles), and no alternative has independent evidence of a
better result. Hypotheses, not findings: Model B's paired gain (+0.473 R over 214 pairs, 27
episodes, no portfolio gain when played forward); that quality scores of the 11 `stars/3`
strategies carry no information beyond the stars; that late 3-star entries are worse. Model D
needs at least 30 verified `realized_pips` outcomes per strategy (now 0 strategies of 12 with
any, the best has 10); re-run `arbitration_eval` then.

## 9. P1 completion matrix

| Item | Status | Evidence |
|---|---|---|
| Independent strategies | PASS | 21 strategies, no families; `test_strategy_independence.py`, `execution_intent.py` registered as a label carrier |
| Correct offline eligibility | PASS | registry-driven; Level B same-code 2,081 / 2,081; the regex defect removed (10,413 rows) |
| No simulation future leakage | PASS | causal simulator; prefix-invariance test; censored/unavailable explicit |
| Terminal outcome attribution | PASS | `test_terminal_attribution.py` 14 tests |
| Idempotent terminal recovery | PASS | redelivery and failed-projection retry tests; broker outcome never overwritten |
| Deterministic ranking | PASS | `test_arbitration_determinism.py` incl. randomized non-finite property |
| Safe ranked fallback | PASS | `test_publication_fallback.py` |
| Production behavior preserved | PASS | no change to ranking, risk, containment, enablement, reservation, corridor, geometry; Python/Go/.NET suites below |
| Evaluation reproducibility | PASS | two runs byte-identical to each other and to the committed result (sha256 `e224c809...aada69`) |
| Historical decision equivalence (Level C) | NOT ESTABLISHED | 645 of 11,113 ids in the ledger; stated, not claimed |
| Model D | NOT EVALUATED | insufficient verified realized outcomes |
| Regression suites | PASS | Python 1,686 passed / 1 skipped / 0 failed (real Redis); Go `build`, `vet`, `gofmt` clean and `test ./...` 56 packages ok, 0 failed (44 without tests); .NET 418 passed. CI itself now gates only startup/contract checks (see CHANGELOG), so these ran locally |
