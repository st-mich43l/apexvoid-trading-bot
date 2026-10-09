# P3 execution audit: entry execution and trade management

Date 2026-10-09. Base `85a6c284` (master with #753-#762). Read-only: nothing was deployed or
restarted, and no historical record was rewritten or filled in.

**Verdict.** The path from a Go opportunity to a broker order is sound in the places that
carry the most risk: 0 of 305 filled plans produced more fills than the plan declared (no
hidden legs), BUY and SELL plans are exact mirror images across all 20 strategies that are
planned on gold, and every missed opportunity in the sample falls into a protective rule, not
a defect. Three execution defects were confirmed. Two were already fixed in #760-#762 (range
scalps were handed gold's structural stop, ladder and risk leg; Go scalps were not sized as
scalps). One is fixed here: the executor never checked the age of the quote it was acting on.

Reproduce (all inputs are read-only exports; they hold live trading records and are not
committed):

```bash
cd algo-bot && python -m tools.execution_audit --plans plans.jsonl --fills fills.csv \
  --results results.csv --opps opps.csv --algo-log algo.log.gz --out audit.json
```

| Input | Source | Window |
|---|---|---|
| `plans.jsonl` | Redis stream `execution:trade_plans` (full TradePlan payloads) | 2026-09-29 to 10-09, 311 plans |
| `fills.csv` | Postgres `auto_trade_fills` | 305 plan groups |
| `results.csv` | Postgres `auto_trade_results` | 299 closed algo_auto trades |
| `opps.csv` | Postgres `analysis_opportunities` | 2026-10-02 to 10-09, 13,380 |
| `algo.log.gz` | algo-bot log lines for confirmation, rejection, publication | 2026-10-02 to 10-09, 9,397 setups |

## Data limits (what could not be measured)

- `auto_trade_fills` is one volume-weighted row per plan group. Leg-level fill prices, partial
  fills of a ladder and the fill order of L1/L2/RISK are not recoverable. Fill-versus-plan
  slippage below uses the nearest declared price and is an approximation for ladders.
- The executor log has no timestamps and is rotated away between 10-02 and 10-08, so the time
  spent inside the executor (poll, sizing, order, fill) cannot be split; only plan-published to
  first-fill can be measured.
- The Redis `auto_trade:events` stream keeps about 1,000 entries and was emptied by the
  2026-10-09 deploy, so TP, break-even and close events cannot be replayed for the window.
  Post-TP1 outcomes are therefore not quantified.
- 39 of 299 closed trades carry `realized_pips`. Execution quality is not compared with P&L.

## 1. Findings

| # | Class | Finding | Evidence | Status |
|---|---|---|---|---|
| 1 | CONFIRMED_EXECUTION_BUG | Range scalps on gold were planned with the structural gold treatment: the 50-70 pip stop envelope, the 1R-4R ladder and, on the two-leg plans, the swing risk leg | Plan stream, XAU Range Edge Scalp: 6 of 6 plans risk 50.0 pips (the stop sits 20 pips beyond Go's invalidation on average), 6 of 6 carry 4 targets. XAU Fade Scalp: 4 of 4 plans 50.0 pips, 4 targets, 1 of 1 two-leg plan with a risk leg. Fill table: stop 48-51 pips on all 6 XAU Range Edge trades | Fixed in #760 (15-45 pip stop band), #761 (1R/2R, no scalp risk leg) |
| 2 | CONFIRMED_EXECUTION_BUG | Go scalps were not sized as scalps: the builder keyed on family `scalp` or mode `scalp_m1`, which none of the five Go scalps carry | `risk_percent` 1.0 on all five in the plan stream | Fixed in #762 |
| 3 | CONFIRMED_EXECUTION_BUG | The executor evaluated whatever tick it saw last. `SpotPrice` carries the broker tick time and Python refuses a spot older than 5 s, but `TradePlanRuntime.PollAsync` only tested for a null quote, and the last tick survives a feed stall or reconnect | Code: no age check anywhere in `TradePlanRuntime`/`AutoTradeEngine`; `_lastSpotBySymbol` is never cleared. The executor logs available (09-29 to 10-01 and 10-09) show 40 token refresh failures and 61 Redis session failures | **Fixed here** (below) |
| 4 | INTENDED_BEHAVIOR | Executed stops are wider than Go's invalidation on structural gold plans | `go_invalidation_widened` on 48 of 66 Key Level, 18 of 21 Confluence Zone, 19 of 21 Liquidity Sweep plans (all symbols); on gold the median distance beyond invalidation is 2.2 pips (Key Level), 12.3 (Confluence Zone), 25.8 (Liquidity Sweep). This is the owner's 50 pip gold floor | None |
| 5 | INTENDED_BEHAVIOR | No hidden or extra legs | 0 of 305 groups have more fills than declared legs plus the declared risk leg. `SubmitEntryAsync` places `BuildDeclaredLegs` plus `plan.Entry.RiskLeg` only; C# tests pin a plan without a risk leg to its declared ladder | None |
| 6 | INTENDED_BEHAVIOR | Entry outside the declared zone | Fills outside the zone: 0 pips on all 36 XAU market_with_limit_scale plans, 0.0-1.2 pips (p90 at most 0.8) on FX market_watch, XAU market_watch median 0, p90 3.1, max 7.0. The 7.0 is the 2026-09-30 catch-up chase already capped at 10% of the zone width; the 3.1 is a 3.3 pip zone entered at a 3 pip spread (the zone is entered when `[bid, ask]` overlaps it) | None |
| 7 | OWNER DECISION | `max_group_risk_percent` (2.0) is in every plan and enforced nowhere. Executor worst case at the declared stop, C# fixture XAU structural ladder: 3.3% of equity at $2,000 and 3.6% with the risk leg, 3.6% at $3,000, 2.6% at $5,000 | C# `WorstCaseGroupRiskIsMeasuredFromTheOrdersActuallySubmitted` (RISKTABLE output) and the pinned `MaxGroupRiskPercentIsDeclarativeTheExecutorNeverReadsIt` | No change (live risk) |
| 8 | EXECUTION_OPTIMIZATION (not implemented) | A chase entry outside the zone (Fade Scalp, within its small chase budget) still gets the manual shallow/deep ladder, with L2 on the wrong side of L1, so both legs are marketable and fill at the same price | Planner probe, Fade Scalp BUY quote below the zone: legs `[4111.17, 4111.47]`. Exposure is unchanged; only the card shows two prices | Proposed: declare one market leg when the quote is outside the zone |
| 9 | INSUFFICIENT_EVIDENCE | Close-reason lookup times out (2 s) | 50 `position_close_reason_lookup_failed` in the sample; the classifier falls back to the stop proximity heuristic. No wrong classification was demonstrated | Report only |
| 10 | INSUFFICIENT_EVIDENCE | Quote-driven management (target booking, close-reason heuristic) still reads the last tick when it is stale | Code only; no incident found | Residual risk, below |

### Fix for finding 3: no new entry on a stale quote

`AutoTradeOptions.MaximumQuoteAgeSeconds` (record default 0 = off, which keeps every existing
test as it was). The native runtime sets it to `max(15, 3 x analysis.spot.maximum_age_seconds)`,
so no new config key and no Python or Go loader is touched. `TradePlanRuntime.PollAsync` skips
`EvaluatePendingEntryPlansAsync` while the quote is older than that and logs `v8 quote stale`
once per stall. It still applies cancel intents first (unchanged), reconciles submitted legs and
manages open positions exactly as before, and leaves resting orders and broker-side stops alone.
The plan stays `Received` and enters on the first fresh tick, or expires with the existing
"never evaluated a live quote" message. The tick time is the same field Python's freshness
rule reads, so the two agree on what "fresh" means.

Tests: `TradePlanRuntimeTests.StaleQuote.cs` (stale tick opens nothing and keeps the plan,
fresh tick enters, first fresh tick after a stall enters, disabled check behaves as before).

## 2. Before and after: the Range Edge Scalp that started this

XAU SELL, zone 4176.60-4178.08, Go invalidation 4178.84, filled 03:48 on 2026-10-09 (plan
stream, then the planner re-run with Go's facts and the corrected envelope):

| | Before (#759 and earlier) | After (#760-#762) |
|---|---|---|
| Entry | market_watch, quote 4176.57 | unchanged |
| Stop | 4181.57 (50.0 pips, `go_invalidation_widened`) | 4178.84 (22.7 pips, Go's own invalidation) |
| Targets | 4171.57 / 4166.57 / 4161.57 / 4156.57, close 40/20/20/20 | 4174.30 / 4172.03, close 50/50 |
| Risk leg | none (single leg); a two-leg scalp got one at 15 pips inside the stop | none on any scalp |
| Risk at 0.23 lots, $2,591 | about $115 (4.4%) | about $53 (2.0%) |

## 3. Missed opportunities (2026-10-02 to 10-09)

9,397 setups reached the executor path. 6,697 reached their entry zone; 272 became plans; 270
of those filled. 2,700 never reached the zone before Go withdrew them.

| Outcome | Count | Verdict |
|---|---|---|
| Rejected `reaction_confirmation_unavailable` (admission) | 3,281 | Correctly rejected: no confirmed reaction yet (Go still publishes the observation) |
| Rejected `higher_timeframe_bias_unavailable` | 6 (was 544 before the bias rejection was removed) | Correctly rejected; nearly eliminated |
| `opposing_entry_overlap` | 1,354 | Correctly suppressed for exposure protection |
| `fx_opposite_position_not_allowed` | 1,314 | Correctly suppressed (FX never opposite) |
| `same_direction_active_before_tp2` | 854 | Correctly suppressed (stack rule) |
| `entry_zone_overlap_same_direction` | 524 | Correctly suppressed (45-minute reservation) |
| `opposing_barrier_room_below_cost` / `_no_target` | 348 / 115 | Correctly rejected: no room to a target |
| `opposing_entry_contained`, `xau_opposite_position_too_close`, `opposing_active_too_close` | 284 / 190 / 152 | Correctly suppressed (exposure) |
| `thesis_already_owned` | 170 | Correctly suppressed (one plan per thesis) |
| `stop_exceeds_go_invalidation_envelope` | 66 setups, all XAU | Correctly rejected; 3 at the new 70 pip cap, the rest were over 60 before #759 |
| `scalp_room_below_1r` | 12 (all Impulse Pullback) | Correctly rejected |
| `required_limit_side_unavailable` | 0 | Not occurring |
| Plans published but never filled | 4 auto plans in 11 days, all single_limit (EURUSD FVG, GBPJPY Supply Demand, GBPUSD Key Level, XAU Range Sweep); 2 of them in this window | Price never reached the limit before the plan expired or was withdrawn |
| Broker rejection | 10 `NOT_ENOUGH_MONEY` and 5,532 `Field comment is too long` on 2026-09-29, both gone by 09-30 | Fixed before the window |
| Kafka/event delay | `event_too_old` 21 | Invalidated before the bot could act |

Every rejection above is terminal for that opportunity (`terminal=invalidated`). Whether a
transient exposure rejection could instead wait for the blocking position to clear is a policy
question the data cannot answer without a counterfactual on exposure; it is left as is.

## 4. Latency and slippage (broker-confirmed records only)

| Interval | n | median | p90 | max |
|---|---|---|---|---|
| Go adaptation to entry trigger | 6,679 | 60 s | 3,882 s | 83,511 s (a retest that took a day) |
| Entry trigger to plan published | 272 | 0.21 s | 1.1 s | 783 s |
| Plan published to first fill, market | 39 | 2.9 s | 4.4 s | |
| market_watch / market_with_limit_scale | 93 / 36 | 3.2 s / 4.0 s | 53 s / 9.3 s | |
| single_limit (waits for price) | 112 | 9.0 s | 460 s | |

Slippage against the nearest declared price (pips, positive is worse): FX market and
market_watch at most +0.8 pips at p90; XAU market median -1.3 (better), market_watch median -2.3,
single_limit 0 or better, limit_ladder p90 +5.2 (fills are volume-weighted across legs, so this
is the approximation noted above).

## 5. Strategy-by-strategy scalp contract (gold)

| Strategy | Stop envelope | Targets | Risk leg | Entry |
|---|---|---|---|---|
| Range Edge Scalp | 15-45 | 1R, 2R (50/50) | none | retest-only in the bot; the executor's catch-up tolerates 10% of the zone width past the edge |
| Fade Scalp | 15-45 | 1R, 2R | none | chase within `min(40, 0.3 x stop)` |
| Range Sweep Scalp | 12-45 | 1R, 2R | single leg | M1 momentum chase within budget |
| Impulse Pullback Scalp | 12-45 (observe-only on XAU, EURUSD) | 1R, 2R | single leg | M1 momentum chase |
| Breakout Retest Scalp | 12-45 | 1R, 2R | single leg | retest-only inside the band |

Each row is pinned by `tests/test_scalp_strategies_contract.py` and
`stop_envelope_scalp_test.go`.

## 6. Tests added

- `tests/test_execution_invariants.py` (60): for 20 gold strategies, BUY and SELL plans are mirror
  images (stop, every leg, targets, route, R multiples; one broker tick of slack on a leg that
  lands on a half tick), and every allowed plan keeps its stop beyond every leg, targets beyond
  the entry and in order, the stop inside Go's declared envelope, scalps at or under 2R without
  a trail or band expansion, structural stops at or above 50 pips.
- `tests/test_execution_audit_tool.py` (6): slippage sign for BUY and SELL, extra fills, declared
  risk leg, stop widening, funnel and latency from logged events only, nothing invented for an
  unfilled plan.
- `TradePlanRuntimeTests.StaleQuote.cs` (5).
- Renamed `WithoutTheTagThePlanGetsTheInjectedRiskLegExactlyAsPythonOwnedPlansDoToday`, whose
  name claimed the opposite of what it asserts, to `ADeclaredRiskLegIsPlacedExactlyAsDeclaredAndNothingElseIsAdded`.

## 7. Remaining risks

- Quote-driven management on a stale quote (target booking, close-reason heuristic) is unchanged.
  A stall long enough to matter is bounded by broker-side stops and targets, but a TP could be
  booked off an old tick. Gating it needs the close-detection path split from the quote, which is
  larger than this change and was not made.
- Leg-level fills and partial-fill frequency need a per-leg fill ledger; the fills table cannot
  answer them. Executor log timestamps would make the opportunity-to-order latency measurable.
- Post-TP1 management (break-even, trail) is covered by the C# suite but cannot be quantified from
  production for this window (finding limits above).
- Finding 7 (unenforced group risk cap) and finding 8 (chase ladder) are decisions, not defects.

## 8. P3 completion matrix

| Invariant | Status | Evidence |
|---|---|---|
| Every strategy keeps its own technical authority | PASS | No detector, ranking or scoring change |
| Planner declares every leg; executor adds none | PASS | 0 of 305 groups over-filled; C# declared-ladder tests |
| Risk leg only when declared, never on a scalp | PASS | `test_scalp_strategies_contract.py`; plan stream shows none on scalps after #761 |
| BUY/SELL symmetry | PASS | `test_execution_invariants.py` mirror test, 20 strategies |
| XAU structural band 30-50 and stop 50-70 | PASS | invariants test (stop floor/cap per strategy), #759 tests |
| Range-scalp stop 15-45, targets at or under 2R | PASS | invariants and scalp contract tests, Go envelope test |
| No new entry on a stale quote | PASS | `TradePlanRuntimeTests.StaleQuote.cs` |
| Effective risk and exposure not increased by any change | PASS | No sizing, risk or exposure change in this PR; scalp risk fell (finding 1) |
| Stops never loosened, TP never declared before broker close | PASS (unchanged) | `never_worsen_stop`, deal-confirmed booking, C# suite |
| Post-TP1 outcomes quantified from production | NOT MEASURABLE | Events stream truncated; see data limits |
| Leg-level slippage and partial fills quantified | NOT MEASURABLE | Fills table is per group |
| Regression suites | see PR | Python, Go, .NET counts in the PR description |
