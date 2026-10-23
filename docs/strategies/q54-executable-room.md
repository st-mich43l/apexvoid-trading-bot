# Q5.4: executable opportunity quality and target-room validation

Baseline: `master` at `5e555c1e` (#806). Production runs image `5e555c1` (all three services started 2026-10-22 14:35 UTC, checked 2026-10-23). The replay uses the 22 committed
production captures that #807 (Q5.3, open, CI green, mergeable) extends to 2026-10-22; this change adds new files only and touches nothing #807 touches.
**Nothing about admission, scores, stops, ladders, thresholds, enablement or sizing changed.**

## 0. Answer

*Are we admitting technically valid opportunities whose executable geometry makes them economically unattractive, and can we identify them reliably before placing a trade?*

* **They are identifiable, and there are many.** Of 2,694 replayed FX opportunities the worker would admit (current rules, two-star floor, one per symbol, strategy, direction and hour),
  the stop floor binds on 90% (median widening of the traded FX groups: 2.2x), Go's first technical target lies inside 1R of the executable stop on 65%, and the nearest buffered opposing
  barrier lies inside 1R on 37% (11% have no barrier at all). On the 228 traded FX groups of current strategies, 55% had the first technical target inside 1R of the stop that was used.
* **There is no evidence that they are worse, or that removing them would help.** Every candidate rule (fixed before it was evaluated, sections 4 and 8) removes trades that did *no worse*
  than the ones it keeps: removed minus kept = +0.04 R [-0.10, +0.17] (barrier room < 1R), +0.08 R [-0.09, +0.25] (first target < 1R), +0.02 R [-0.13, +0.16] (room < 0.5R), on a sample that could
  resolve about 0.2 R. The signs differ between instruments and windows. The only intervals that exclude zero (GBPJPY and XAU for the first-target rule) say the *removed* trades were better.
* **The weak FX expectancy has another explanation than room.** The admitted FX book is below the matched random entries (-0.07 R [-0.13, -0.01]) in the replay; the one gradient that points the
  same way in every cut is the stop-floor widening (section 3), and it is not resolvable either (-0.07 R [-0.18, +0.05]).
* **Executable-feasibility admission is not ready for enforcement.** No rule is applied. `executable_room` stays advisory, as #806 left it.

## 1. The decision path, as the code runs it

| Stage | Where | What it reads | Executable consequence |
|---|---|---|---|
| Go opportunity | `analysis-engine/internal/strategy/*` | closed bars only (prefix-invariance tests) | entry band, invalidation, first technical target (nearest opposing liquidity or level) |
| Adapter | `go_opportunity_policy.build_strategy_match` | the envelope | hard: geometry, expiry, reaction; no approximation |
| Admission | `worker.static_admission_failure` | Go's selected stars | hard: 2-star floor |
| Room | `worker._publish_trade_plan_v8` -> `evaluate_structural_target_room` | barrier book, the zone's **proximal edge** as the room reference when the quote is in or near the zone | hard: entry inside an opposing zone, no positive raw room, buffered room below the execution cost |
| Entry | `execution_policy` / `trade_plan_builder` | route (FX `single_limit` or zone-capped `market_watch`, XAU zone legs) | planned price(s), volumes |
| Stop | `protective_stop.plan_go_invalidation_stop` | Go's invalidation, the widest planned leg, the instrument envelope | never closer than Go; widened to the floor (`envelope_minimum`); rejected beyond the maximum |
| Ladder | instrument `targeting` | the executable stop | FX 1R / 2R at 50/50, breakeven after 1R; XAU 1R-4R at 40/20/20/20: **fixed multiples of the widened stop, not Go's objective** |
| Diagnostic | `assess_executable_room` (#806) | the same barrier and buffered room | advisory `measured.executable_room` |

What was checked against the code and the data, not assumed:

* **Room reference and fill.** The room is measured from the proximal edge; FX fills sit at or slightly *beyond* it (median 0.4 to 1.1 pips deeper, 0 to 3% worse, 229 groups), so the room is
  conservative, not overstated. Not a defect.
* **Room and stop are in different units until #806's diagnostic.** Room is checked against Go's technical targets; the ladder is built on the widened stop. That is the documented design (a barrier never
  shrinks the ladder) and the subject of this phase, not an error.
* **Dead branch.** `evaluate_execution_policy(..., available_target_room_pips=None)` is called with `None` for every Go plan, so `fixed_rr_room_insufficient` and the 1R fallback cannot fire. The data agrees:
  `target_room_fallback_used` is 0 in every closed group since 2026-10-14 (3 on 10-13, none after) and the planned reward:risk is 2 for all of them. Unchanged; reported in Q5.2.
* **Causality.** The room decision reads the barrier book as of the bar (`TestOpposingBarrierBookUsesNoFutureBars`, prefix-invariant); the replay entry is placed only after the evaluated bar closes.
* **Missing evidence in production.** Traded groups cannot be joined to the barrier book that admitted them: only 45 of 624 production opportunity ids exist in the replay (the code differed), and the
  `candidate_published` lifecycle (Redis, about 6 days) holds 132 plans, 20 of them with `executable_room` (since 2026-10-22 14:35Z). The realized evidence below therefore has the technical and executable
  geometry but not the barrier room; the barrier room is evaluated in the replay only.

## 2. Classification of the findings

| Finding | Class | Basis |
|---|---|---|
| Proximal-edge room reference, fills, units, causality | no defect | section 1, Q5.2 grid and replay (68,476 decisions byte-identical) |
| `fixed_rr_room_insufficient` unreachable | **B** (valid implementation, dead branch) | call site passes `None`; fallback count 0 since 10-14 |
| TP1 beyond the nearest barrier; very short executable room; fixed-R ladder against nearby structure | **B** | allowed by the owner's rule that a barrier never shrinks the ladder; 37% / 65% of FX admissions |
| Stop floor widening (FX 2.2x median) | **B**, deliberate noise protection | FX technical stops are 3 to 8 pips; floor 12 to 22 |
| Executable room as a rejection gate | **C** (unproven) | section 4: no separation, mde80 about 0.2 R |
| Stop widening as a cause of the weak FX expectancy | **C** | -0.07 R [-0.18, +0.05], mde80 0.16 R |
| Confirmed technical defect (A) | none found | |

## 3. H1, H2, H3

Replay, thinned and filled observations of the opportunities the worker would admit (hypothetical R with costs; above random = minus the matched random entry of the same technical risk):

| FX (n=2,694) | n | mean R | 95% interval | above random | 95% interval |
|---|---:|---:|---|---:|---|
| all admitted | 2,694 | -0.120 | [-0.176, -0.062] | -0.069 | [-0.126, -0.008] |
| stop widening 1.0x (floor not binding) | 272 | -0.108 | [-0.288, +0.089] | -0.034 | [-0.208, +0.155] |
| 1-1.5x | 508 | -0.037 | [-0.186, +0.099] | +0.031 | [-0.138, +0.179] |
| 1.5-2.5x | 934 | -0.123 | [-0.211, -0.040] | -0.086 | [-0.168, -0.004] |
| > 2.5x | 980 | -0.163 | [-0.261, -0.066] | -0.113 | [-0.209, -0.017] |
| barrier room < 0.5R | 598 | -0.107 | [-0.242, +0.022] | -0.050 | [-0.188, +0.082] |
| 0.5-1R | 409 | -0.083 | [-0.225, +0.082] | -0.008 | [-0.137, +0.140] |
| 1-1.5R | 293 | +0.020 | [-0.188, +0.205] | +0.125 | [-0.109, +0.329] |
| 1.5-2R | 277 | +0.032 | [-0.183, +0.265] | +0.053 | [-0.162, +0.286] |
| > 2R | 811 | -0.193 | [-0.302, -0.071] | -0.125 | [-0.246, +0.006] |
| no barrier | 306 | -0.273 | [-0.437, -0.111] | -0.333 | [-0.476, -0.189] |
| first technical target < 0.5R | 929 | -0.049 | [-0.162, +0.072] | -0.013 | [-0.115, +0.104] |
| 0.5-1R | 819 | -0.139 | [-0.236, -0.042] | -0.079 | [-0.181, +0.022] |
| 1-1.5R | 425 | -0.157 | [-0.337, +0.022] | -0.083 | [-0.251, +0.097] |
| 1.5-2R | 237 | -0.313 | [-0.505, -0.098] | -0.302 | [-0.528, -0.057] |
| > 2R | 284 | -0.082 | [-0.330, +0.183] | -0.005 | [-0.256, +0.263] |

* **H1, stop-floor distortion.** The floor binds on 90% of FX admissions (median widening of the traded groups 2.2x, quartiles 1.6 and 3.2; XAU 1.14x). R falls from -0.04 (1-1.5x) to -0.16 (> 2.5x), and the
  heaviest bucket is also below its matched random control (-0.11 R), which already has the same widening. That is a consistent gradient but not a resolvable one (> 2.5x against the rest: -0.07 R [-0.18, +0.05]).
  It cannot be separated from the strategies that sit on the floor (Key Level, 2.2x to 3.0x). Class C.
* **H2, insufficient room.** Room is not monotone: the worst FX buckets are *more* room (> 2R, -0.19 R) and *no barrier* (-0.27 R, -0.33 R below random). A near barrier does not predict a worse outcome, which is
  consistent with price running through nearby opposing zones to the 1R / 2R targets; the breakeven stop after 1R also caps the cost of a stall. Class B/C.
* **H3, score against feasibility.** `score_room` rises with the technical target (rank correlation 0.75 with the first target in executable R) and is unrelated to the barrier room (-0.08): they measure different things (Go's
  objective against the Python barrier book). 22% of the FX opportunities with `score_room` >= 0.5 have their first target inside 1R of the executable stop, 45% a buffered barrier inside 1R. Within the groups whose first target is
  inside and beyond 1R of the stop, `score_room` has no relation to outcome above random (rank correlation -0.05 and +0.02). So the executable-room mismatch does not explain Q5.3's null: `score_room` was already neutral. `score_room` is not replaced.

XAU (n=618, mean -0.088 R [-0.163, -0.008]): room 1-1.5R -0.50 R and no barrier -0.40 R are the worst buckets; > 2R is +0.08 R (above random +0.16 [+0.03, +0.31]); the floor rarely binds (432 of 618 at 1.0x).
Neither pattern is monotone; no inference.

## 4. Candidate rules (fixed before evaluation; none is an approved gate)

Replay, removed minus kept, mean hypothetical R. mde80 is the smallest effect the sample could resolve at 80% power (2.8 standard errors, symbol-day cluster bootstrap).

| Rule | Group | Admission rate | Removed (winners / losers, sum R) | Removed mean | Kept mean | Removed minus kept | 95% interval | mde80 | Cumulative-R drawdown, all / kept |
|---|---|---:|---|---:|---:|---:|---|---:|---|
| A: buffered barrier room < 1R | FX | 0.63 | 1,007 (450 / 557, -98.1) | -0.097 | -0.133 | +0.036 | [-0.095, +0.171] | 0.19 | -332 / -226 |
| B: first technical target < 1R | FX | 0.35 | 1,748 (789 / 959, -158.9) | -0.091 | -0.174 | +0.083 | [-0.087, +0.253] | 0.25 | -332 / -167 |
| C: buffered barrier room < 0.5R | FX | 0.78 | 598 (260 / 338, -64.1) | -0.107 | -0.124 | +0.016 | [-0.134, +0.159] | 0.21 | -332 / -260 |
| D: stop widened by more than 2.5x | FX | 0.64 | 980 (399 / 581, -159.9) | -0.163 | -0.095 | -0.068 | [-0.184, +0.045] | 0.16 | -332 / -166 |
| A | XAU | 0.57 | 264 (116 / 148, -18.6) | -0.070 | -0.101 | +0.030 | [-0.166, +0.200] | 0.26 | -82 / -51 |
| B | XAU | 0.75 | 155 (81 / 74, +14.6) | +0.094 | -0.149 | **+0.243** | [+0.024, +0.457] | 0.30 | -82 / -93 |
| C | XAU | 0.77 | 145 (62 / 83, -23.4) | -0.161 | -0.065 | -0.096 | [-0.293, +0.091] | 0.27 | -82 / -59 |

* The "kept" drawdown is smaller only because fewer trades are kept; removing trades whose mean is not worse than the rest does not raise the mean of what remains (A: -0.133 against -0.120 before).
* By window (development, out of sample 10-07 to 10-18, forward from 10-19; the forward window is already exposed through Q5.3 and is **not** a holdout): A is +0.12, -0.07, +0.13; B +0.07, +0.13, +0.06; no window excludes zero.
* By instrument (A): EURUSD +0.15, GBPUSD -0.23, USDJPY +0.17, GBPJPY +0.01; none excludes zero. By instrument (B): GBPJPY +0.41 [+0.12, +0.70] excludes zero, in the direction *against* the rule (the removed trades were
  better). Dozens of intervals were read; one or two excluding zero is what chance gives.
* Strategy cuts (Key Level, Supply, Demand, Order Block, Confluence Zone, FVG) are in the results file; none supports a rule, and none was pooled to manufacture significance.
* **Realized (traded groups, current strategies, n=228 FX).** The first technical target inside 0.5R of the stop used shows -0.31 R [-0.58, -0.04] (n=42), half of them USDJPY and GBPJPY; with measured methods only (`fills_v1`,
  `fill_record_risk`) it is -0.32 R [-0.71, +0.09] (n=25), and the replay bucket for the same condition (n=929) is -0.05 R [-0.16, +0.07]. Not corroborated. The realized sample is 24 `fills_v1`, 201 `fill_record_risk`, 142
  `reconstructed_ladder_v1`, 48 `legacy_loss_unbooked` (losers assumed -1), so the methods are reported separately in the results file and never read as broker results.

## 5. Decision table

| Rule | Technical justification | Evidence | Admission impact | Decision |
|---|---|---|---|---|
| Existing behaviour (room against Go's targets, ladder never shrunk, floor widening, 2-star floor) | the barrier is a structural fact; fixed-R targets are the owner's contract | no defect found; Q5.2 replay | baseline: 8,512 of 17,632 replayed records admitted | **Keep** |
| A: reject when TP1 lies beyond the nearest buffered barrier | a target past a wall is less likely | removed trades no worse (+0.04 R [-0.10, +0.17]); inconsistent by instrument and window | -37% FX | **Reject** (do not enforce) |
| B: reject when Go's first target is inside 1R of the executable stop | the objective is nearer than the stop | removed trades no worse (FX +0.08 R [-0.09, +0.25]); XAU removes better trades (+0.24 R [+0.02, +0.46]) | -65% FX | **Reject** |
| C: reject when the buffered room is under 0.5R | a wall almost on the entry | +0.02 R [-0.13, +0.16] | -22% FX | **Reject** |
| D: bound stop widening (floor / entry-band tightening) | a floor 2x to 3x the technical stop changes the setup's economics | -0.07 R [-0.18, +0.05], mde80 0.16; the floor is noise protection and the brief forbids loosening it | -36% FX | **Defer** (needs the forward evidence below) |
| Read `score_room` on the executable risk | removes the technical-versus-executable mismatch | `score_room` is already neutral; a new model version | none | **Defer** (owner; no outcome case) |
| Promote `executable_room` from advisory to a gate | consistent with a rejection | none of A to C | | **Keep advisory** |

## 6. What it would take to decide

* **Forward evidence, fixed now.** Rules A to D as written; the evaluation is production's own: closed `algo_auto` groups of current strategies from **2026-10-23 00:00 UTC** (after every exposed window), R from `fills_v1` and
  `fill_record_risk` only, `executable_room` read from the `candidate_published` lifecycle (harvest weekly: the Redis lists expire after about 6 days, see section 7). Decide rule A or C only if removed minus kept is negative with an interval
  that excludes zero.
* **Size.** With a standard deviation of R of about 1.2, a removed-against-kept difference of 0.25 R needs about 390 groups per side and 0.5 R about 100 per side; FX closes roughly 10 groups a day, so 0.5 R takes about
  three weeks of FX and 0.25 R two to three months. A smaller effect than 0.25 R cannot be settled with this book.
* **Selection.** Every outcome here is conditional on the current admission; the replay's single-leg limit fill at the proximal edge and flat costs are not production's market entry inside the zone.

## 7. Reproduce

```
# replay records, worker room decisions, matched random baseline (checkout of #807 for the Oct 22 captures, or master for 17 captures)
cd analysis-engine && AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies
                      AEQ_RANDOM_OUT=/tmp/aeq go test ./test/aequality -run TestMatchedRandomBaseline
                      F7_DECISIONS_DIR=/tmp/dec F7_CAPTURES=<capture names> go test ./test/f7barrier -run TestDumpDecisionInputs
cd ../algo-bot && python ../analysis-engine/test/f7barrier/decide_dump.py --books /tmp/dec --out /tmp/decisions.jsonl
# traded groups: read-only SELECT of auto_trade_results (trade_stream='algo_auto') with auto_trade_fills and the creation envelope, one JSON object per line
python tools/q54_executable_room.py --records /tmp/aeq/records.jsonl --decisions /tmp/decisions.jsonl --random /tmp/aeq/random.jsonl --groups groups.jsonl --out q54.json
# weekly harvest of the plan-time room (read-only): LRANGE of auto_trade:lifecycle:go_opp_* on the host, keep state == candidate_published
```

Results: [q54-executable-room-results.json](q54-executable-room-results.json). Tests: `tests/test_q54_executable_geometry.py` (FX stop below, at and above the floor for BUY and SELL, JPY rounding, XAU split entry, barrier before TP1, between TP1 and TP2, within room, technical against executable reading), `tests/test_q54_executable_room.py` (buckets, first decision, admission, rule evaluation, drawdown, group geometry, missing data).

## 8. Before and after, tests, next

* **Admission before and after: identical by construction** (no admission code changed). The replay dataset: 17,632 records, 17,003 with a room decision, 8,512 admitted, 3,312 filled and thinned observations (2,694 FX, 618 XAU). The 629 records without a room decision are scalps that own their room (`bypasses_opposing_structure_gates`) and are outside this evaluation.
* **Tests run:** `go build`, `go vet` and `go test ./...` are unaffected (no Go file changed; the replay was run on #807's tree); full Python suite 2,035 passed; C# unchanged.
* **Purge.** Reviewed for tools and documents that only supported a finished PR: none is eligible (the arbitration, calibration, ground-truth and F-7 tools and documents are cited from live code comments or other documents).
* **Q5.5.** (1) Run the forward evaluation of section 6 once `executable_room` has accumulated; (2) decide whether the plan-time room should be persisted with the closed group (a nullable column fed from the plan,
  an additive change) so the realized evaluation does not depend on a six-day Redis list; (3) investigate why the admitted FX book is below random entries (-0.07 R), starting with the strategies sitting on the floor, and whether the replay's
  limit fill at the proximal edge is the cause, using production quote history.
