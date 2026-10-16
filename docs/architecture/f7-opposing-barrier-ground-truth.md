# F-7 Opposing barrier and execution target room: ground-truth investigation (2026-10-16)

Baseline: `master` at `7da788ce` (PR #792 merge). Production runs images `7da788c` (analysis-engine, trading-bot, ctrader-engine), started 2026-10-16 13:08 UTC.
Frozen references: `a1c77584` (2026-09-19, last Python-authoritative scanner and worker) and `1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc` (2026-10-02, #719, last revision that still holds the Python analysis code).
Companion: [python-go-migration-forensic-audit.md](python-go-migration-forensic-audit.md) §7.5 (F-7) and [python-go-barrier-diff.json](python-go-barrier-diff.json).
Artifacts: [results](f7-opposing-barrier-results.json), [all 2,956 per-event rows](f7-opposing-barrier-disagreement.csv), [15 charts](f7-barrier-cases/), tooling in [analysis-engine/test/f7barrier](../../analysis-engine/test/f7barrier/README.md).

This document changes nothing in trading behavior. It is evidence, a failure-mode fix that lives in PR #793, and the reasons no barrier-source change is proposed yet.

## 0. Verdict

1. **The Go opposing barriers are real structure, not fabricated.** Every barrier is a union of live V2 supply/demand zones with their exact boundaries. What the barrier *says about* them is thin: tier is the constant `zone`, score is the strongest member's strength, touches is the *least* touched member's, and the zone ids, lifecycle state, formation time, last touch and member list are all discarded (§2).
2. **The Go-versus-Python difference in how often a barrier blocks is one rule: what counts as a spent zone.** The frozen Python marked a zone mitigated on its **first touch** and dropped it from the book (`mark_mitigation`: `mitigated = touches > 0`). Go keeps `fresh`, `touched` and `partially_mitigated` zones as live barriers, and the lifecycle only spends a zone after **30** touches (`retest_max_touches: 30`) or a close beyond its far edge. Giving Go's own zones Python's first-touch rule drops the block rate on the 2,852 production barrier rejections from **96.6% to 17.0%**; the frozen Python books block 12.9% (A) and 11.5% (B) (§5, §6). The two populations still disagree on *which* events they block (A and Go-fresh-only block the same event in 147 of 376 / 489 blocks), as #792 showed for the zones themselves.
3. **Neither book is "right" against independent labels.** With the independent, definition-based labels of [ae-ground-truth.md](../strategies/ae-ground-truth.md), a zone is credible in 56% of fresh, 46% of touched and 37% of partially mitigated cases. Python A and B agree with the labels about as often as chance (precision 45% and 51% against a base rate of about 46%); Go agrees almost always (recall 98%) because it blocks almost everything (§8). The labels cannot choose a lifecycle: the best Go-fact-only variant reaches 0.61 accuracy against a 0.47 base rate.
4. **What the blocks are worth, as a hypothetical stop / +1R / +2R race:** blocks for *thin room* (`opposing_barrier_room_below_cost`) are the only ones that look informative (expectancy -0.22 R, 95% CI -0.36 to -0.07, the same in the chronological dev and held-out halves). Blocks for *entry inside an opposing zone* (80% of all blocks) are statistically indistinguishable from the admitted trades (-0.03 R and +0.007 R against +0.015 R). Nothing here shows the barrier gate makes money or loses it (§9).
5. **Go's merged barriers are wide.** 87% of the barriers that decided a block are unions of more than one zone (median 8 members, median 39 pips = 6.3 ATR); 86% are wider than the 2-ATR limit the width gate applies to each *member* but never to the union. Three of the fifteen charts I labelled by eye show a 57-pip or 133-pip "wall" over a range price traded inside for the whole window (§7, §8).
6. **One defect is not a matter of judgement, and is fixed in PR #793:** a missing, stale, malformed or wrong-symbol zone book was read as "no opposing structure", so a new autonomous plan was admitted unchecked (§10).
7. **No Model D is implemented.** The evidence cannot tell a better lifecycle from a different one, and every candidate raises or lowers acceptance by tens of points. That is an owner decision about risk, not a correction (§11, §12).

## 1. Data, window and reproducibility

| | |
|---|---|
| Window | 2026-09-29 10:01 UTC to 2026-10-14 15:49 UTC (the oldest lifecycle entries still in Redis, cut where the committed captures end) |
| Production events | 3,853 admission events replayed: 3,744 production barrier rejections (lifecycle entries with `state=rejected`: `opposing_entry_overlap` 2,387, `opposing_barrier_room_below_cost` 595, `opposing_entry_contained` 587, `opposing_barrier_no_target` 175; 55 further events of strategies purged since are skipped) and 109 published TradePlans (122 in the window, 13 of them from purged strategies) |
| Complete A, B and C | 2,852 rejections and 104 published plans (the rest have a Go book but fewer than 700 retained M5 bars for the frozen Python, mostly 2026-09-29 to 2026-10-02) |
| Bars | 2,758 distinct (symbol, M5 bar) pairs, each evaluated on the capture that gives it the longest M5 history |
| Captures | the 16 committed production captures of 2026-10-05 to 2026-10-14 (XAU, EURUSD, GBPUSD, USDJPY, GBPJPY) |
| Production access | read-only: Redis `SCAN`/`LRANGE`/`GET` of `auto_trade:lifecycle:go_opp_*` and `analysis:zone_book:*`, Postgres inside `BEGIN READ ONLY` for `analysis_opportunity_events`, `auto_trade_fills`, `auto_trade_results`; the `docker ps` and `docker logs` of the services. No service touched. |

Deployed revisions and configuration for the window. The barrier code did not change inside it: `internal/barrier` and `transport/redis/zonebook.go` were last changed by `49f5ffdf` (2026-09-30 14:21 UTC, "Port barrier normalization"), `62f4af17` (09-30 12:27) fixed the EURUSD/GBPUSD book walling, `internal/zone` differs from `49f5ffdf` only by comments, the removal of the unused `IsSpent` and `IsDead`, and the `LegacyScore` field (no lifecycle semantics changed), and `structural_target_room.py` and `go_zone_book.py` are unchanged since `76e54bd1` (10-07, dead-branch removal). 126 successful deploys ran in the window; 59 replayed events precede the `49f5ffdf` port. The values that govern the barrier were identical at `49f5ffdf`, `21c7d4e0` and master: `execution_zone_max_width_atr: 2.0`, `execution_zone_max_width_pips: 100.0`, `retest_max_touches: 30`, `invalidation_tolerance_atr: 0.5`, instrument `barrier_buffer_atr` 0.15 (FX) / default 0.5 elsewhere, `execution_cost_pips: 1.0`. Replaying with master code is therefore representative; §4 measures how representative.

## 2. The pipeline and what each hop keeps

```
candles -> structure.Update -> zone.Update (V2 per timeframe) -> engine state.Zone[tf]
        -> redistransport.BuildZoneBook (supply/demand only, live = fresh|touched|partially_mitigated)
        -> barrier.Build (M5/M15/H1 scope, width gate per zone, same-side union, cross-side reconcile)
        -> Redis analysis:zone_book:{SYMBOL} (20 min TTL, republished on each bar)
        -> algo-bot go_zone_book.parse_zone_book -> worker (_publish_trade_plan_v8)
        -> filter_shared_boundary_opposing_entries -> evaluate_structural_target_room
```

| Field | V2 zone | Published barrier | Verdict |
|---|---|---|---|
| side | `Kind` (supply/demand) | `side` buy/sell | kept (derived) |
| boundaries | `Low`, `High` | `low`, `high` = min/max of members | **union**: a chain of touching zones becomes one band |
| tier | none | constant `zone` | **defaulted**: `level` (weak) and `major` are unreachable, so the rule that ignores weak-tier containment never applies |
| score | `Strength` 0..1 (formation quality x0.85 touched, x0.65 partially mitigated) | max of members | kept, **scale differs** from the frozen score; the decay is already inside it |
| touches | `TouchCount` | min of members | **understated** for a chain |
| state | fresh / touched / partially_mitigated | none | **discarded** (all treated the same) |
| zone id, origin time, created at, last touched, structure/displacement ref, relevance, layer | present | none | **discarded**: no barrier can be traced to the zone, bar or break that made it |
| members | one zone each | none | **discarded**: no count, no width of the widest member |
| timeframes | `Timeframe` | `source_timeframes` (union) | kept |
| freshness | | `generated_at` = wall clock at publish (the bar time is not published) | now read strictly (PR #793) |
| instrument | | in the key and document | kept |

Zone kinds the barrier ignores: order block, FVG, iFVG, breaker, flip, key levels, liquidity pools, trendlines (the frozen scanner's `zones` included order blocks and FVGs; the TradePlan-time book was supply/demand only, like Go).

## 3. The three models, reproduced independently

* **A, frozen scanner book.** `build_structural_barrier_book` over the `zones` of each timeframe from the frozen `build_context(causal_structure=False)`: supply/demand, order-block, flip and FVG zones merged first (overlap of at least half with a member, merged width capped at 3.0 ATR), stamped with touches as of the bar before the current one, scored and HTF-boosted; mitigated (touched) zones dropped; tiers by score; the barrier builder then unions same-side bands without a cap, as Go does.
* **B, frozen TradePlan-time book.** `_structural_barrier_zone_book` (displacement -> supply/demand -> `mark_mitigation`, per timeframe) through the same builder.
* **C, current Go book.** `redistransport.BuildZoneBook` with the instrument's barrier policy over the V2 zone state of M5, M15 and H1, exactly as `cmd/analysis-engine` publishes it.

A and B run the frozen code of a git worktree; C runs the engine on the committed captures. Neither side sees the other, and none of the reference algorithms was modified. The October revision (`1c9f323`) has no `_structural_barrier_zone_book`, so it gives A only: A′ below (same book rule; it adds order blocks caused by a CHoCH and a vectorised touch count, and decides like A on 2,806 of 2,852 events). For every event the **real** `filter_shared_boundary_opposing_entries` and `evaluate_structural_target_room` of this checkout decide, with the planned entry, candidate band, ATR, configured ladder, buffer, cost floor and same-wall flag of that event, so the three books are compared on identical state.

## 4. The reconstruction is faithful

| Check | Result |
|---|---|
| Real rules fed only the barrier the production log named reproduce the logged decision | **3,744 of 3,744 (100%)** |
| The barrier the production log named is in the replayed Go book | exact boundaries 2,897 (77.4%), overlapping by at least half 663 (17.7%), absent 158 (4.2%), no capture record 26 |
| Replayed Go book blocks the event production blocked | 93.3% (96.6% of the 2,852 with A and B), with the same reason code in 92.6% |

So the replay of C is a reproduction of production to within about 7%, and the replay of A and B is the same machinery fed the frozen books.

## 5. Candidate-level admission replay

The 2,852 production barrier rejections (every one blocked by Go in production):

| Book | Would have blocked | Would have admitted |
|---|---|---|
| A, frozen scanner | 368 (12.9%) | 2,484 |
| B, frozen TradePlan-time | 327 (11.5%) | 2,525 |
| C, Go (replay) | 2,754 (96.6%) | 98 |
| A′, October revision `1c9f323` | 374 (13.1%) | 2,478 |
| A and B both admit | | 2,274 (79.7%); 2,193 of them C blocks |

The 104 plans production published (every one admitted by Go): A would have blocked 8 (7.7%), A′ 8, B 6 (5.8%), C 1.

**Earliest divergence of A from C on all 2,956 events:** the barrier Go blocks on has no overlapping barrier at all in the Python book (1,684, 57%); the Python band overlaps but is narrower and leaves the entry outside it (530, 18%); both block (308); both allow (189); the rule gives a different outcome on the same barrier (177); both block on different barriers (56); Python blocks and Go allows (12). B: 1,612 / 646 / 303 / 182 / 183 / 11 / 19. The classification is `python_tier_level_weak_ignored` when the only difference is the weak-tier exemption; it never occurs: the frozen book produced only `zone` (4,594) and `major` (4,813) barriers in A and only `zone` in B over the window, never `level`, so the weak-tier exemption was dead in the frozen books too.

Excerpt of the requested table (all rows in the [CSV](f7-opposing-barrier-disagreement.csv), with the deciding barrier's band, width, touches and score):

| Opportunity | Symbol | Strategy | Python A | Python B | Go C | Actual admission | Earliest divergence (A) |
|---|---|---|---|---|---|---|---|
| `go_opp_5f4af47c1` | EURUSD | supply | allow: room ignored | allow: room ignored | block: entry contained | blocked: entry contained | barrier absent in Python book |
| `go_opp_2e32086a0` | EURUSD | key_level | allow: ladder fits | allow: ladder fits | block: no target | blocked: no target | barrier absent in Python book |
| `go_opp_6a82b7159` | USDJPY | key_level | allow: ladder fits | allow: ladder fits | block: entry overlap | blocked: entry overlap | barrier absent in Python book |
| `go_opp_dc5ac7494` | USDJPY | ifvg | allow: room ignored | allow: ladder fits | block: entry overlap | blocked: entry overlap | Python band narrower, entry outside |
| `go_opp_61af08852` | GBPJPY | confluence_zone | allow: ladder fits | allow: ladder fits | block: entry overlap | blocked: entry overlap | Python band narrower, entry outside |
| `go_opp_7dfb61956` | GBPUSD | key_level | block: room below cost | block: room below cost | block: entry overlap | blocked: entry overlap | agree blocked |
| `go_opp_d614ae554` | XAU | liquidity_sweep | allow: room ignored | block: room below cost | block: room below cost | blocked: room below cost | rule difference on the same barrier |
| `go_opp_394a787ac` | GBPUSD | supply | block: entry overlap | allow: no barrier | allow: ladder fits | blocked: entry overlap | Python blocks, Go allows (replay) |
| `go_opp_506b91b02` | EURUSD | range_edge | block: room below cost | allow: ladder fits | allow: room ignored | published: tp1_be | Python blocks, Go allows |
| `go_opp_c70cb3b54` | XAU | key_level | block: major no room | allow: ladder fits | block: no target | blocked: no target | both block, different barriers |

By symbol the Go block rate is 93% to 100% on every instrument and A's 8% to 20%; by reason, Go blocks 95% to 97% of each reason and A 8% to 15%. Of the strategies, key_level supplies 61% of the rejections (1,729 of 2,852).

## 6. Root cause: the lifecycle rule, not the zone source

The same Go zones (live, width-gated, M5/M15/H1) under progressively stricter lifecycle rules, block rate on the 2,852 rejected / 104 published events:

| Book | Rejected blocked | Published blocked |
|---|---|---|
| Python A / Python B | 12.9% / 11.5% | 7.7% / 5.8% |
| **Go zones, fresh only (the frozen first-touch rule)** | **17.0%** | 2.9% |
| Go zones, touches <= 1 | 61.5% | 9.6% |
| Go zones, touches <= 2 | 79.2% | 15.4% |
| Go zones, touches <= 3 | 86.9% | 20.2% |
| Go C (all live, up to 30 touches) | 96.6% | 1.0% |

Why the frozen book was so sparse: `zones.mark_mitigation` set `mitigated = final_touches > 0`, and `_barriers_from_timeframe` skipped every mitigated zone, so only a never-touched zone was a barrier. Why Go's is not: the V2 lifecycle (`internal/zone/lifecycle.go`) is the technique-zone rule of PR #574 ("touching alone never kills a zone"): a zone is invalidated only by a close beyond the far edge by more than 0.5 ATR that is not reclaimed within the sweep window (or by more than the allowed number of reclaimed breaks), or spent after more than 30 retests; `partially_mitigated` means "touched more than once, still holding". On the 81,868 zones labelled for §8, 44,952 (55%) are `partially_mitigated`.

The frozen system already ran two lifecycles side by side: the technique layer that fed the strategies (`technique_geometry.not_invalidated`) kept a touched zone tradeable, while the barrier book dropped the same zone on its first touch. Go has one lifecycle for both, so a touched zone that a strategy may still trade is also a zone that may block the opposite trade. That is an argument for consistency, not for correctness: it says why Go behaves as it does, not that the behavior is right.

Fresh-only Go blocks 17% and Python A 12.9%, but they block different events: of 2,956 events they agree on the decision for 80.7% and block together on 147 (A) / 212 (B). The rate follows the lifecycle; the choice of events still follows the zone population (#792: 2% to 4% overlap of M5 zones).

A property that is not a judgement call: **the book is not monotone in its inputs.** Removing zones adds blocks. With only fresh zones, 9 events (2 published) are blocked that the all-live book admits; with touches <= 1, 68 (9 published); with unmerged zones, 169 (37 published). A weaker or older zone chain-merges a fresh one into a band that the same-wall exclusion or the cross-side reconciliation then discards whole. The independent labels support 4 of those 37 published blocks; the 37 are not distinguishable in outcome from the other published plans (-0.01 R against +0.05 R, 95% intervals about 0.5 R wide).

## 7. What a Go barrier is made of

For the 3,468 barriers that decided a block (3,430 with every member recoverable from the dumped zones):

| | |
|---|---|
| Single zone | 12.9% |
| Union of two or more zones | **87.1%**, median 8 members |
| Width | median 39.4 pips = 6.3 ATR; **85.7% wider than the 2.0 ATR** per-zone limit |
| Part of the band covered by no member | median 0 (the unions are gap-free chains) |
| Entry inside the union but in no single member | 0% (stacking is dense enough that some member always holds the entry) |

The union is not what blocks the entry; the density of live opposing zones is. Even unmerged, 98.6% of the rejected events have the entry inside, or room-limited by, a live opposing zone. What the union changes is the *room*: the nearest edge of a chain starts at its lowest member, so Go's measured room is smaller than Python's on the same event by a median of 34 to 40 pips (25th to 75th percentile -92 to -12, n = 2,355 and 2,363).

## 8. Structural credibility against independent labels

**Labels.** For every live, width-gated zone of the 2,758 bars (81,868 labels), the definition-based oracle of `tools/ae_ground_truth.py` (no engine code or parameter) says: `reacted` (>= 1.5 ATR within 8 bars of the origin), `oracle_origin` (a live textbook impulse origin of the same side overlaps it), `broken` (an unreclaimed close beyond the far edge by > 0.5 ATR). `credible` = reacted and origin and not broken. These are one definition of "genuine structure", not the only one, and the marker is the same author as the tool: it is not a human check.

| Zone fact | Credible |
|---|---|
| state fresh / touched / partially_mitigated | 56.3% / 45.6% / 36.5% |
| touches 0 / 1 / 2 / 3 / 4 / 5 / 6+ | 56.3 / 45.6 / 43.1 / 38.8 / 37.8 / 28.7 / 28.7% |
| strength 0.4 / 0.6 / 0.8 / 1.0 | 33.8 / 39.7 / 46.8 / 58.8% |
| timeframe M5 / M15 / H1 | 44.1 / 40.8 / 44.3% |

Lifecycle facts do carry the label (touches and state, strength), which is the case for treating a barrier's states as information. A zone broken by the label's own rule is 0.2% (197 of 74,014 labelled): Go's invalidation already removes those.

**Agreement of each book's decision with the label book** (credible zones only, each its own barrier), on the 2,956 events, chronological dev (first 60%) and held-out (last 40%):

| Model | Blocks | Precision | Recall | Accuracy (held-out) |
|---|---|---|---|---|
| Python A | 376 | 0.449 | 0.125 | 0.519 |
| Python B | 333 | 0.508 | 0.125 | 0.545 |
| Go C | 2,755 | 0.479 | 0.977 | 0.552 |
| Go zones, fresh only | 489 | 0.46 / 0.62 (dev / held-out) | 0.19 | 0.561 |
| Go zones, touches <= 1, strength >= 0.8 | | 0.56 / 0.60 | 0.49 / 0.53 | 0.614 |

The reference blocks 46% of these events. A and B block 11% to 13% and agree with the label no better than chance; C blocks 93% and misses almost nothing, at 48% precision. No lifecycle or strength filter built from Go facts exceeds 0.62 accuracy (grid of 20 in the results file), so the labels cannot choose a design.

**Fifteen charts, labelled by eye** ([charts](f7-barrier-cases/), seeded sample of three per class, not representative). Marker: Claude, one pass. Orange outline: Python A band; purple dashed: B; red: the Go barrier that decided; grey: its member zones; blue: planned entry and candidate band.

| Class | Reading |
|---|---|
| K1: Go blocks, Python admits, label no (3) | GBPJPY range_edge BUY and GBPUSD key_level BUY: the "barrier" is a 57-pip chain over a range price had traded inside for the whole window, so it is a region of acceptance, not a wall. USDJPY SELL: a 9-pip demand band that price chopped through; undecidable. |
| K2: Go blocks, Python admits, label yes (3) | USDJPY BUY (sell band at the entry) and USDJPY BUY (top of an established range): genuine, Go is right. GBPJPY SELL just after a displacement through the stack: undecidable. |
| K3: Go admits, an unmerged book would block (3) | All three look like valid entries with no wall near; the unmerged zones are clutter. Go is right. |
| K4: room below cost (3) | Two USDJPY SELLs: the nearest edge is 3 to 4 pips under the entry, a near base under price: plausible, but the 103 to 122-pip chain misstates the room. **XAU range_edge SELL: a 133-pip chain that is the range the strategy trades through: a false block by construction.** |
| K5: all books block (3) | EURUSD x3: a genuine nearby wall in each. |

Tally: Go's decision defensible 10, barrier representation wrong 3 (K1 x2, K4 XAU), undecidable 2. Two failure modes recur: chain walls over ranges, and a barrier that contradicts the strategy's own thesis (`range_edge`: 105 of the rejections).

## 9. Consequences (hypothetical, labelled as such)

A causal stop / +1R / +2R race from the moment each check ran: entry at the logged executable price plus 1 pip of cost; stop = the Go invalidation clamped to the opportunity's own stop envelope; the bar containing the event is ignored; a bar that touches both stop and target counts as the stop. It is **not** a replay of the TradePlan ladder, partial closes or break-even. On the 113 executed trades with a known exit it agrees with the actual outcome (win at TP1 or beyond versus stop) in 98 (87%), and is 4 points optimistic (47.8% against 43.4%), so it is used only to compare groups with each other. Intervals are 95% bootstrap over episodes (symbol, direction, hour), not events: the 2,839 simulated rejections are 913 episodes, the 103 executed trades 94.

| Group | Events | Win at 1R | Expectancy | 95% CI (episodes) | Profit factor |
|---|---|---|---|---|---|
| Executed (published) | 103 | 50.5% | +0.015 R | -0.16 to +0.20 | 1.03 |
| All barrier-rejected | 2,839 | 47.0% | -0.060 R | -0.13 to +0.01 | 0.89 |
| rejected: room below cost | 425 | 39.0% | **-0.216 R** | **-0.36 to -0.07** (held-out -0.215) | 0.64 |
| rejected: no target | 153 | 41.3% | -0.165 R | -0.41 to +0.10 | 0.71 |
| rejected: entry overlap | 1,858 | 48.6% | -0.030 R | -0.11 to +0.06 | 0.94 |
| rejected: entry contained | 403 | 50.3% | +0.007 R | -0.12 to +0.13 | 1.02 |
| Go blocks, A and B admit | 2,183 | 47.4% | -0.049 R | -0.13 to +0.02 | 0.90 |
| Go blocks, A or B also blocks | 558 | 46.5% | -0.068 R | -0.19 to +0.06 | 0.87 |
| Go blocks, label supports | 1,314 | 46.8% | -0.057 R | -0.15 to +0.04 | 0.89 |
| Go blocks, label does not | 1,427 | 47.7% | -0.049 R | -0.14 to +0.05 | 0.90 |
| Go blocks, fresh-only also blocks | 654 | 44.0% | -0.109 R | -0.24 to +0.03 | 0.80 |
| Go blocks only because of touched zones | 2,814 | 48.1% | -0.039 R | -0.11 to +0.03 | 0.92 |

What this supports: thin-room blocks are worth keeping on this evidence. What it does not support: that any of the entry-inside-a-zone blocks avoids a loss or discards a win, or that the lifecycle rule matters for outcomes (fresh-only blocks are 0.07 R worse than touched-only blocks, well inside the intervals). A Python-style book would have admitted 2,183 of these at the barrier stage, 787 episodes against 94 executed in the same period; the downstream same-direction, opposite-direction and exposure gates that produced about 6,500 further rejections in the same lifecycle data are not simulated, so no drawdown or correlated-exposure figure is given and none should be inferred. Selection limits: the rejected set is exactly what Go blocked; a book that admits more would also admit events never observed here.

## 10. The missing-zone-book failure mode (PR #793)

`opposing_entries_for_go_match` returned `()` for a missing key, a Redis error, malformed JSON, a book generated for another symbol and a book of any age, and the worker read `()` as "no opposing structure". Existing tests enshrined it. A new autonomous plan was then admitted unchecked. The engine republishes on each bar; production books are 46 to 53 seconds old at any moment (sampled on 5 symbols), so the 600 s limit of PR #793 is a 10x margin. The exposure is real but not countable from history, because the old code logged nothing when it happened: 126 deploys restarted the analysis engine in the window (about 6% of wall-clock lies within 10 minutes of one), and 7 of the 122 plans published in the window were published within 20 minutes of a deploy. PR #793 gives the reader three states (`AVAILABLE_WITH_BARRIERS`, `AVAILABLE_EMPTY` as an explicit `barriers: []`, `UNAVAILABLE` with a reason), a strict parse that makes the whole book unavailable on one untrustworthy barrier, and a non-terminal hold (`stage=opposing_barrier`, `reason_code=zone_book_unavailable`, intent retained) for new autonomous plans only; existing positions are never touched.

## 11. Model D: candidates, and why none is implemented

The mission's invariants (Go sole technical authority, Algo Bot never rebuilds zones, deterministic and causal, original provenance, a missing book never an empty market, no score ranking, barrier quality separate from geometric validity) are met by every candidate. What is missing is evidence that a candidate is *more correct*, not merely different.

| Candidate | Change | Rejected events blocked | Published events blocked | Evidence for | Evidence against |
|---|---|---|---|---|---|
| D0 status quo | none | 96.6% | 1.0% | reproduces production; thin-room blocks informative | 53% of blocks unsupported by labels; chain walls over ranges |
| D1 first touch spends a barrier (frozen rule) | fresh zones only | 17.0% | 2.9% | credible 56% fresh vs 46% touched | rate falls 80 points; touched-only blocks 0.07 R better than fresh-only, inside the intervals |
| D2 spent after the first retest | touches <= 1 | 61.5% | 9.6% | credible 46% | best-case 0.60 accuracy against labels |
| D3 cap the union at the width gate | refuse a merge wider than 2 ATR / 100 pips | 98.0% | 30.8% | removes the 6-ATR chains | blocks more, not fewer; the union is not what blocks the entry |
| D4 barrier quality tier from lifecycle | `level` for decayed strength, soft on containment | not measured | not measured | restores the frozen weak-tier idea | needs a calibrated threshold; the labels cannot calibrate it |
| D5 provenance only | publish members (id, timeframe, state, touches, strength, origin and last-touch time) | unchanged | unchanged | closes the audit gap; lets the decisive constituent be logged and a lifecycle chosen on production data | none; additive |

None is justified as a correction. Every one is a statement about how many trades the system should take. D5 changes no decision and is the one step that makes the next round of evidence cheap.

Blockers to a Model D: (1) a labelled set that does not share the engine's premise: the labels here agree with a zone's own reaction, so they cannot tell a spent zone from a held one; (2) TradePlan-faithful outcomes for the blocked events (spreads, ladder, break-even) which the captures cannot give; (3) a window with provenance logged, so the decisive zone of each production decision is known rather than reconstructed; (4) the owner's statement of how much acceptance change is acceptable.

## 12. Owner decisions

1. **Merge PR #793** (fail-closed zone book). Behavior change by design: with no usable book, a new plan waits instead of being admitted.
2. Approve **D5 provenance** as a separate additive PR (no decision changes).
3. Decide whether the barrier should treat a touched zone as a barrier, and how many touches. This is a risk decision; the evidence in §6, §8 and §9 does not decide it.
4. Decide whether `range_edge` (and any strategy whose thesis is a rotation through a range) may be blocked by the range's own interior.
5. Nothing in this PR changes risk, sizing, enablement, arbitration or execution policy; none should change without an explicit approval.

## 13. Limits

* Replay of C reproduces production to about 93%; 4% of production barriers are absent from the replayed book (history depth: captures hold 1,500 to 2,223 M5 bars, production about 3,000).
* The 55 events of purged strategies are not replayed; 866 rejections have no A and B (history under 700 bars).
* The labels are one definition; the charts are one marker and fifteen cases. The race is hypothetical and calibrated on 113 trades.
* Rejections cluster (913 episodes among 2,839 events): the event counts are descriptive, the intervals are by episode.
* Production state after 2026-10-14 15:49 UTC is outside the committed captures.
