# Q5.2: Technical validity and market-structure correctness

Baseline: `master` at `ab0e824d` (#805). Production runs image `ab0e824` (all three services, started 2026-10-22 about 10:25 UTC).
Method: read the code path from market data to TradePlan, replay the 17 committed production captures (68,476 candidates, 21,032 of them admissible: a reaction-confirmed
or reaction-free adapter, not a scalp that bypasses the barrier book) through the real admission rules, and scan every opportunity Go has published since the Q2 purge.
Nothing about scores, thresholds, stop floors, sizing, ladders or enablement changed.

## 1. Result

| | |
|---|---|
| Confirmed defects fixed | 2 (both behaviour-neutral on admission): a counter-bias exception in `worker.py` that looked like a bypass and could never fire; the room in front of an admitted plan was never measured in R of the stop the executor really uses |
| Admission decisions before / after | **68,476 of 68,476 replayed decisions byte-identical** (`cmp` of the worker's own `evaluate_structural_target_room` over the captures) |
| Market structure | causal and covered: swings are confirmed only after their right bars, breaks are classified as of the data given and stable once their window has elapsed (`test/structure/causality_test.go`), no strategy and now **no barrier book** uses a future bar (`test/replaycapture/causality_test.go`, 1,252 books compared, 0 differ) |
| Opportunity geometry as published | 2,303 envelopes created 2026-10-20 08:10Z to 2026-10-22 11:40Z: **0** with an inverted band, no invalidation, a stop not beyond the entry, a target not beyond the entry, or no target |
| Barrier lifecycle | 0 live zones break the zone package's own `NotInvalidated` rule (`validity_test.go`: every live supply/demand zone at every 6th M5 bar of the 17 captures, 343,604 observations) |
| Not changed, owner decisions | four barrier set-aside rules and the strictness gap between Key Level and the zone strategies (section 6): each moves 7% to 25% of admissible decisions and none is a demonstrated mathematical error |

## 2. The pipeline: who decides what

| Stage | Where | Mandatory (hard) | Advisory / telemetry | Can be bypassed |
|---|---|---|---|---|
| Swings, breaks, BOS/CHoCH, layer trend | `internal/structure` (`DetectPivots`, `PromoteSwing`, `DetectBreak`, `ClassifyEvent`, `BuildLayerState`) | pivot confirmed only after `rightBars` closed bars; a break needs a held close (sweeps and failed breaks are named, never BOS) | swing layer by ATR excursion | none |
| Bias | `context.DeriveBias` (primary timeframe), `engine.ClosedHigherTimeframeBiases` (closed H1/H4/M15 only) | a higher bar must be closed no later than the observed bar; stale frames omitted | plan/card metadata and the `htf_aligned` confluence factor; no admission gate reads it | not applicable |
| Zones | `internal/zone` (`NotInvalidated`, `DeriveState`) | a zone is invalid after an unreclaimed close beyond its far edge by more than 0.5 ATR (or too many reclaimed breaks), spent after 30 retests, and (since F-7, `b5b94a94`) a zone price jumped over without trading in it is invalid | strength | none |
| Barrier book | `internal/barrier.Build`, `transport/redis.BuildZoneBook` | live supply/demand of M5/M15/H1, width gate (2 ATR, 100 pips) per member | tier is constant `zone`; members published as provenance | the book is read through `parse_zone_book`, strict: an unusable book holds a new plan (#793) |
| Strategy detection | `internal/strategy/*` | each strategy's own contract and confirmation | `quality.overall` (informational) | none |
| Adapter | `go_opportunity_policy.build_strategy_match` | technical context present, reaction confirmation where the profile requires one, recent, direction scope, evidence contract, not expired, positive band, targets beyond the entry, non-degenerate stop | | none |
| Admission | `worker.static_admission_failure` | global confluence floor (`gates.min_confluence: 2` on Go's selected stars), strategy enabled, symbol | | none |
| Target room | `worker._publish_trade_plan_v8` -> `structural_target_room.evaluate_structural_target_room` | entry inside an opposing zone (`opposing_entry_overlap/contained`), no positive raw room (`opposing_barrier_no_target`, `opposing_major_no_room`), buffered room below the execution cost (`opposing_barrier_room_below_cost`) | ladder fit, 15-pip minimum capped target, `opposing_evidence` | scalps that own their room (`bypasses_opposing_structure_gates`) |
| Stop | `protective_stop.plan_go_invalidation_stop` | stop beyond every planned entry on the right side, never closer than Go's invalidation, within the envelope maximum | widening to the instrument floor | none |
| Execution policy | `execution_policy.evaluate_execution_policy` | invalid geometry, stop plan, risk multiplier | per-strategy confluence, regime, zone width, target room, RR (`PREFERENCE_TELEMETRY_REASONS`) | `available_target_room_pips` is always `None` for a Go-origin plan |

Go and execution agree on technical evidence: Go computes it, the adapter refuses to approximate it, and the room check reads the same zones Go published.

## 3. Confirmed defects

**D1. A counter-bias exception that could never fire (fixed).** `worker.py` kept a counter-bias setup past `opposing_entry_overlap`, `opposing_entry_contained` and `opposing_major_no_room` when `usable_room_pips or room_pips` reached the 15-pip floor.
Those three codes are, by construction, entries inside or on the opposing zone: their room is never positive. Over a grid of every geometry the gate can produce (BUY and SELL, zone and major tier, barrier offsets -60..+60 pips, widths 1..90 pips, candidate bands 0..25 pips, executable price present or not, EURUSD / USDJPY / XAU conventions) the room is at most 0 for all three, and across the 7,268 replayed rejections with those codes the largest raw room is -1.0 pip.
So a counter-bias label never bypassed a structural impossibility, but the branch said it might, and a later change to the room figure would have made it real. It is removed; a structural rejection is final whatever the setup's relation to the bias. A valid counter-trend reversal needs no waiver: a zone that structure has invalidated is not in the book, so nothing opposes it (section 2, Zones).
Tests: `test_structural_target_room.py` (property over the grid, wrong-side zones, both directions) and `test_go_full_chain.py` (a Key Level BUY in the worker with an opposing zone holding or overlapping the entry, with and without counter-bias, for a zone and a major wall: rejected identically).

**D2. The room in R of the executable stop was never computed (fixed, diagnostic only).** `evaluate_structural_target_room` accepts `protective_stop_distance` and `first_target_r` for `opposing_evidence.room_r`, but the worker never passed them (the stop is planned later), so `room_r` is null in every production record, and the ladder-fit codes (`opposing_barrier_full_ladder_fits` / `..._room_ignored_full_ladder`) compare the barrier with **Go's technical targets**, not the ladder the executor places (fixed-R multiples of the executable stop, which is never tighter than Go's invalidation and is widened to the floor: FX 12 to 22 pips against technical 3 to 8; the published XAU test case 65 against 43).
`structural_target_room.assess_executable_room` now reads, from the same barrier and the same buffered room, the room in R of the executable stop, the technical stop and the widening ratio, how many of the plan's own targets lie within the room, and a classification (`no_opposing_barrier`, `ladder_within_room`, `ladder_exceeds_room`, `first_target_beyond_room`), recorded on the `candidate_published` lifecycle entry as `measured.executable_room`. It is advisory by construction (`enforcement: advisory`) and changes no decision, because the owner's rule stands that a barrier never shrinks the ladder.

**D3. A record error (documented).** `docs/architecture/q5-opportunity-quality-validation.md` says no quality value gates admission. The global confluence floor does: `worker.static_admission_failure` terminally rejects a match whose Go-selected stars are below `gates.min_confluence` (2), which is 62 of 2,301 post-purge opportunities (2.7%); the lifecycle lists read on 2026-10-16 held 539 such terminal rejections. The per-strategy `policy.min_confluence` is the advisory one. Corrected in that document.

## 4. Checked and correct

* **Direction.** Supply opposes a BUY and demand a SELL (`_nearest_opposing`, `exclude_entries_behind_the_entry`); a zone of the entry's own side never opposes (test). A barrier wholly behind both the proximal edge and the executable price is set aside before the nearest is chosen (F-7, `b5b94a94`), and a wall the price still has to pass or that holds the entry is kept.
* **Units.** Pip size comes from the instrument (`units.pip_size`): XAU 0.1, JPY pairs 0.01, other FX 0.0001. The grid test runs EURUSD, USDJPY and XAU; room is in pips of that size, the buffer in ATR of the observed timeframe.
* **Entry reference.** Execution requires the quote inside the executable zone, so the structural reference is the zone's proximal edge when the quote is in or near it, otherwise the executable bid/ask. The proximal edge is the edge nearest the barrier on both sides, so deeper scale-in legs only add room.
* **Zero and negative room, no barrier.** Raw room <= 0 is a hard reject (weak `level` tier excepted, which Go never produces); no barrier is `no_opposing_barrier`.
* **Stop side.** `plan_go_invalidation_stop` rejects a stop not beyond every planned entry (`go_invalidation_not_beyond_buy_entries` / `..._sell_entries`) and one beyond the envelope maximum; the adapter rejects a zero-distance stop.
* **No future data.** Pivots, breaks, zones and now the whole barrier book are prefix-invariant (cut the capture at a time and nothing decided before it changes).
* **Provenance of merged bands.** Direction, bounds, timeframes and member zones are carried (`members`, F-7); the replay tool checks the members tile their barrier.
* **Determinism.** `barrier.Build` sorts stably and has no clock or randomness; the cross-side pass is deterministic (rebuilt in Python it reproduces the published book on 93.4% of bars, the rest being bars where the dump window clips far zones).

## 5. Mandatory validity against advisory preference

| Condition | Current treatment | Technically required | Evidence |
|---|---|---|---|
| Invalid entry/stop geometry | hard reject (adapter, `plan_go_invalidation_stop`) | hard reject | `protective_stop.py` stop-side checks, adapter degenerate checks; 0 violations in 2,303 published envelopes |
| Invalid target direction | hard reject (`target_not_beyond_entry`) | hard reject | adapter; 0 violations |
| Entry inside an actionable opposing zone | hard reject, with three set-aside rules (section 6) and no counter-bias waiver | hard reject | geometry; D1 |
| No positive raw room | hard reject | hard reject | `evaluate_structural_target_room` |
| Buffered room below the execution cost | hard reject | hard reject (it cannot pay its own cost) | same; informative in the F-7 outcome race |
| Target room against the executed ladder | advisory; now measured as `executable_room` | contract-dependent: owner's rule is that the ladder is never shrunk | D2 |
| Global minimum confluence | **mandatory** (terminal) at 2 stars | strategy-specific decision | `static_admission_failure`; D3 |
| Per-strategy confluence, regime, zone width, min target room, min RR | advisory (`preference_allow`) | contract-dependent | `execution_policy.py` `PREFERENCE_TELEMETRY_REASONS` |
| Higher-timeframe bias | metadata and one confluence factor | not a gate for counter-trend techniques | `go_opportunity_policy` tags; 3.8% of higher-timeframe entries come from the momentum fallback, 1.8% of `htf_aligned` flags are momentum-only |

Nothing was promoted from advisory to hard. The proposed owner decisions are in section 7.

## 6. Set-aside rules that decide a large share of admissions (not changed)

Four rules remove an opposing barrier before the nearest is chosen. Each is documented design; each was measured by replaying the worker's own decision with only that rule varied (`setaside_sensitivity.py`, `crossside_sensitivity.py`).

| Rule | Where | Applies to | Effect of removing / narrowing it, on 21,032 admissible decisions (6,072 blocked today) |
|---|---|---|---|
| Same-wall overlap: an opposing barrier overlapping the candidate band by >= max(2 pips, 0.1 ATR) is dropped, **the whole merged band** | `filter_overlapping_opposing_entries` | technique and confluence strategies only | never applying it: 11,154 blocked, **24.5%** of decisions change (Supply 2,005 of 4,359, Demand 2,195 of 4,361, Order Block 328, Confluence 243); dropping only the overlapping member zones: 9,203 blocked, 17.8% change |
| Shared-boundary glue: an opposing barrier whose edge sits within max(1 pip, 0.05 ATR) of the proximal edge or the planned entry is dropped whole | `filter_shared_boundary_opposing_entries` | all strategies | never applying it: 7,606 blocked, 7.4% change (Key Level 535) |
| Cross-side reconciliation: of two opposite-side bands overlapping by half of either width, the weaker (strongest member) is dropped whole | `barrier.resolveCrossSide` | every barrier | not reconciling: 1,553 admitted decisions would be blocked, 8.5% of 20,607 comparable; 74% of those are Key Level; the dropped band has a median of 2 members, 17% of its members lie outside the winner |
| Behind the entry | `exclude_entries_behind_the_entry` | all | 344 events in the replay; a correction (F-7), not a policy |

The same-wall rule explains why Key Level is blocked on 54% of its admissible decisions (4,778 of 8,780) and Supply/Demand on 8% to 9%: the exemption was written when an opposing "entry" could be the candidate's own wall in the old map. In the Go book a zone of the candidate's own side is never opposing, so an overlapping opposite-side zone is a different zone, and the exemption lets a Demand BUY pass with live supply over its entry that the same rule blocks for a Key Level.
That is an inconsistency in policy. It is not a demonstrated invalidity: the F-7 outcome race found the blocks for an entry inside a live opposing zone indistinguishable from the executed trades (-0.03 R and +0.007 R against +0.015 R), and the merged chains they rest on are mostly stacked touched zones. Tightening it admits 24.5% fewer decisions on an unproven premise, so it is reported, not applied.

## 7. Owner decisions (current behaviour preserved)

1. **Same-wall exemption.** Keep it for the zone strategies, narrow it to the overlapping member zones, or apply the Key Level standard to every strategy. Effects: 9,203 or 11,154 blocked of 21,032 against 6,072 today. The evidence does not choose.
2. **Shared-boundary glue** for all strategies: keep or remove (+1,534 blocked).
3. **Cross-side reconciliation** at band level: keep, or reconcile per member (not measured; needs members on the drop decision).
4. **Executable room as a gate.** `executable_room` now shows how often TP1 or the ladder lies beyond the nearest barrier; making `first_target_beyond_room` a rejection would contradict the standing rule that a barrier never shrinks the ladder and needs an explicit decision, with its measured effect on the plans it would stop.
5. **Global confluence floor** (2 stars): confirm it is meant to be a mandatory gate (it is the only score-like value that is).

## 8. Before and after (causal replay)

| | Before | After |
|---|---|---|
| Replayed candidates (17 captures) | 68,476 | 68,476 |
| Admissible | 21,032 | 21,032 |
| Admitted / blocked | 14,960 / 6,072 | 14,960 / 6,072 |
| Reasons: `full_ladder_fits` 7,184, `room_ignored_full_ladder` 5,946, `entry_overlap` 4,122, `no_opposing_barrier` 1,830, `room_below_cost` 945, `entry_contained` 877, `no_target` 128 | | identical row by row |
| Valid opportunities rejected by mistake / invalid admitted by mistake | none proven | none proven |

The change moves no decision by design; the replay proves it. The sensitivity table in section 6 is the replay of what each open rule would do, not a claim that it would improve anything. As for profitability: this PR makes none, and the F-7 evidence (thin-room blocks informative, containment blocks not) is unchanged.

## 9. Tests

* Python: `test_structural_target_room.py` (+13 cases: the soft-code room property for EURUSD, USDJPY and XAU, wrong-side zones, entry inside the zone for both directions, `assess_executable_room` for no barrier, R of the executable stop against the technical one, three ladder classes, buffer, non-finite inputs), `test_go_full_chain.py` (+8 cases: counter-bias with and without, three barrier shapes; executable room recorded for a barrier ahead and for none). The full Python suite passes (2,006 passed).
* Go: `TestOpposingBarrierBookUsesNoFutureBars` (barrier-book prefix invariance), the existing structure, zone, strategy and replay suites; `go build`, `go vet` and `go test ./...` pass.
* Tools: `setaside_sensitivity.py`, `crossside_sensitivity.py`, `decide_dump.py --no-same-wall` (reproduce section 6 and 8; commands in `analysis-engine/test/f7barrier/README.md`).

## 10. Unproven concerns (documented, not treated as bugs)

* The higher-timeframe bias can come from price momentum when that frame's structure is undecided (3.8% of higher-timeframe entries) and from the micro layer (23% of higher-timeframe entries, 10.7% of primary bias); micro swings are noise by the [ground-truth](../strategies/ae-ground-truth.md) standard. No admission gate reads either.
* A break is classified as of the data available and can still be revised inside its reclaim window; strategies consume it causally.
* Execution cost is a flat 1 pip for every instrument (`execution.policy.execution_cost_pips`); XAU's real spread is larger. Measuring it needs quote history that is not in the captures.
* Technical risk is far below the executable stop on FX (Key Level median 4.7 to 5.7 pips against a 12 to 22 pip stop): realized R is measured against the widened stop (#805).

## 11. Deployment

No configuration, schema, Kafka or Redis-contract change. The `candidate_published` lifecycle entry gains an additive `measured.executable_room` object. Rollback is a revert. Behaviour on admission is unchanged by construction and by replay.
