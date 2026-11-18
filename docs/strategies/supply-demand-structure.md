# Supply and demand: major-swing structure and AE-owned entry (SD-1 to SD-7)

`supply` v12 and `demand` v10. The mirror of one another: everything below is written for supply (SELL, a zone above price) and holds for demand with every price
reflected (a swing LOW, an upward departure, the proximal edge is the zone's HIGH). The Analysis Engine owns every fact on this page; Algo Bot records a few of
them and decides nothing from them.

## What changed, in one paragraph

A zone used to be "the range of the three candles before a displacement leg", and the entry was always the zone's proximal slice (deepened only to meet the stop
cap). A zone is now anchored on a **classified swing** and the **actual base** it was made in, only a zone with a significant swing may publish, and the entry
is **chosen from candidate prices inside the zone** against the true invalidation, the stop cap and floor, the room and the minimum entry width. The stop is
never moved to make anything fit, and a zone with no feasible entry is refused. Everything is switchable back (see Rollback).

## SD-1: lifecycle defects, reproduced and fixed

Each was reproduced by a test that failed on the previous code (`structure_defects_test.go` in both strategies; the demand tests are the exact price mirror).

| # | Defect | Fix |
|---|---|---|
| 1 | A second invalidation test used the CLIPPED entry slice's far edge (supply: the clipped high, demand: the clipped low), so a zone was retired when price closed beyond its slice though still inside the structure. | Every life test uses the complete structural zone; the far edge is the structural high (supply) / low (demand). |
| 2 | `maximum_zone_atr` was judged on the clipped slice, so a 3-ATR region passed through a narrow slice. | Judged on the whole structure, before clipping. Still reported live (nothing is retired for being a region). |
| 3 | Every zone, including terminal, spent or in a breach, was handed to the frames below as higher-timeframe confluence. | Only a zone that may publish is structure for the frame below. |
| 4 | The 6-bar reclaim window returned "terminal" the moment a breach had no reclaim YET, so a zone retired at the breach bar could come back when a reclaim arrived (a terminal opportunity ID resurrected). | A breach is **pending** until its window has closed. Pending: not retired, not offered. Reclaimed within the window: forgiven (an episode). Whole window without a reclaim, or more than two forgiven episodes: **terminal**. The verdict only moves forward; a terminal zone is never undone by a later bar. |

A consequence of 4: a genuinely dead zone is now retired up to six structure bars (M15: 90 minutes, H1: 6 hours) after its breach instead of at it. That is the
price of not looking ahead; `Book.InvalidateByStructureClose` (a close beyond the far edge by the break buffer) still retires a published opportunity at once.

## SD-2: major swings (`structure.ClassifyMajor`)

Causal, ATR-normalised, from the frame's closed bars and ATR only. Pivots are the existing `DetectPivots` (a pivot exists only once its right window has closed;
its `ConfirmedAt` is the open time of the bar that completed it, so it is known when that bar closes). Per pivot:

* **prominence** (ATR): the swing's distance to the higher of its two saddles (the lowest low between it and the nearest earlier bar that beat it, and between it
  and the first later bar that beat it or the last bar);
* **departure** (ATR): how far price moved away within `swing_departure_bars` bars;
* **dominance**: bars on each side that nothing beat; **external** = at least `swing_external_bars` bars to its left (the boundary of the range, not an internal pullback);
* **shift**: a CLOSE through the most recent opposite pivot before the swing and before the swing was exceeded: **CHoCH** when the swing extended the prevailing
  move (a higher high / lower low than the previous swing of its kind, or the first), **BOS** when it was a lower high / higher low;
* **relevance**: no close beyond it since it formed and not older than `swing_relevance_bars`.

**MAJOR** = external, prominence >= `swing_major_prominence_atr`, departure >= `swing_major_departure_atr` and a shift. **INTERMEDIATE** = prominence >=
`swing_intermediate_prominence_atr` and departure >= `swing_intermediate_departure_atr`. Everything else is **MINOR**. A swing's identity never changes as bars are
added; its class is "as of the last bar given", and a decision published on it keeps the class it was published with.

## SD-3: the zone from the swing (`structure.FormSwingZones`)

* The **swing** is a high (supply) / low (demand) with a departure leg (the strategy's own displacement leg) that starts on the swing candle or within
  `base_departure_gap_bars` after it. A run that began inside the consolidation (the base candles happen to be the leg's colour) departs at its first impulse
  candle after the swing.
* The **base** is found from the candles: from the swing candle back while a candle is not an impulse (range <= `base_max_candle_atr` x ATR), overlaps the base so
  far and the base is under `base_max_candles` candles; to the last candle before the departure. Wicks included: the structural range is the whole base and no later
  entry decision narrows it.
* `proximal_price` is the edge price meets first, `distal_price` the far edge the structure is invalidated beyond, `swing_anchor_price` the swing's extreme.
  `swing_confirmed_at`, `base_formed_at` and `departure_confirmed_at` are open times of closed bars; the zone is touchable from the later of the swing and the
  departure.
* **Strong**: a MAJOR swing that still stands with a departure of at least `strong_departure_atr`. **Moderate**: at least INTERMEDIATE and still standing. **Weak**:
  anything else. A structure wider than `maximum_zone_atr` is a region and one narrower than `minimum_zone_atr` an incidental candle region: neither publishes.
* Only `minimum_zone_strength` or better publishes (shipped: moderate). A weak zone is context: it is counted when it would otherwise have traded
  (`zone_strength_below_minimum`) and never gives higher-timeframe confluence. FVG, order block or a sweep are not required; they are evidence, not a precondition.

## SD-4: quality

`supply.quality.v3` / `demand.quality.v3` (the legacy zone keeps v2). `Quality.Overall` is still the plain mean of the `score_*` dimensions. v3 changes `zone_departure`
to the swing's departure on the structure frame (the v2 M5 read was unknown once the origin left the M5 window) and adds three dimensions that each measure a different
fact: `swing_significance` (prominence), `displacement_efficiency` (how cleanly the departure travelled, not its size) and `structure_shift` (CHoCH 1, BOS 0.5, none 0).
The class and the strength are published as measurements (`swing_class`, `zone_strength`, `swing_prominence_atr`, `entry_depth_fraction`), never as scores of their own, so
nothing is counted twice. Premium/discount, freshness, precision, retest history, higher-timeframe alignment and room are the v2 dimensions, unchanged. Quality ranking stays
off and no quality gate was added.

## SD-5: the entry (`strategyutil.PlanEntry`)

Candidate prices inside the structural zone, shallowest first: the **proximal** edge, the proximal edge of the base's **bodies**, the **midpoint**, a **deep** price (0.75),
the price where the **stop cap** is exactly met and the price where the **minimum room** is exactly met. Candidates deeper than `entry_max_depth_fraction` (shipped 0.5: the
midpoint) are not offered. Each is judged on the same facts a trade is:

| Fact | Rule |
|---|---|
| inside the zone | the price is in the structure |
| band | the band from the price (`entry_max_width_price`, clipped to the zone) is at least the instrument's minimum entry zone (XAU non-scalp: 30 pips); a deep cut never bypasses it |
| stop | the structural invalidation (zone far edge or the reaction extreme, plus the buffer) is NEVER moved; the stop from the price must be within the instrument cap |
| risk | the effective risk is the larger of the structural stop and the instrument floor |
| room | the nearest opposing structure, limited by the canonical opposing barrier (the book the Algo Bot's target-room check and the quality room read), must be at least `minimum_target_r` x the effective risk; a target is never published beyond a blocking opposing zone (it becomes `opposing_barrier`) |

The **shallowest feasible** candidate is the entry, because a price nearer the market is the one the reaction the setup is about can fill. It is not "the smallest stop":
a deeper price always has a smaller stop and a larger reward:risk, which is exactly why neither is a reason to choose it. When nothing is feasible the setup is refused
(`stop_beyond_cap`, `no_target_room` or `no_feasible_entry`): the stop is not tightened, the zone is not widened and the band is not stretched.

The published `entry` band is the part of the zone from the chosen price; `entry_plan` (opportunity-v1, optional) carries the chosen kind and price, its depth, stop pips, effective
risk, room, target, and every candidate with the constraint it failed. Algo Bot still owns exposure, sizing and the ladder; the XAU 60/20/20 ladder, its minimum spacing,
distinct rounded prices and FX single entry are untouched.

## What did not change

`analysis.require_candle_confirmation` stays false (a touch of a valid zone is still enough); quality ranking stays off; XAU's published non-scalp entry zone stays >= 30 pips; the structural
stop is placed beyond the far edge and the reaction extreme with the configured buffer; a scalp keeps its single-entry exemption; zone expiry retires the opportunity; the Kafka event keeps
its identity rules (the strategy version changes the opportunity ID once at deploy).

## Rollback

Both leaves are per strategy (`strategies.supply`, `strategies.demand`) and need no redeploy of Algo Bot or cTrader: `zone_model: legacy` restores the three-candle zone and the earlier entry
(the SD-1 lifecycle fixes still apply); `entry_model: proximal` restores the earlier entry (proximal slice, deepened to the cap) on the swing zones. `minimum_zone_strength: weak` publishes
every swing zone.

## Evidence (hypotheses, not live performance)

Method: 9-12 days of production closed bars (XAU, EURUSD, GBPUSD, USDJPY, GBPJPY: M1/M5/M15/H1 read read-only from the bar store, 3 483 events replayed) through the previous master
and through this change, identical causal data. The simulator fills a limit at the band's near edge, puts the stop beyond the invalidation (widened to the instrument floor), takes the
first target, resolves a bar that touches both as a loss and expires at `expires_at`; no spread, slippage, exposure limit, ladder or position management. Results are per ZONE (the
first fill of each zone) because several opportunities on one zone are one trade idea; intervals are cluster bootstrap 95 %.

| | opportunities | zones | filled zones | mean R | 95 % interval | win rate |
|---|---|---|---|---|---|---|
| previous master | 1 095 | 116 | 109 | -0.24 | [-0.44, -0.04] | 36 % |
| legacy zones, SD-1 fixes only | 1 046 | 108 | 100 | -0.25 | [-0.44, -0.03] | 35 % |
| swing zones (moderate+), proximal entry | 764 | 76 | 70 | -0.05 | [-0.31, +0.21] | 44 % |
| **swing zones (moderate+), entry candidates (shipped)** | **410** | **67** | **53** | **+0.02** | **[-0.27, +0.30]** | **49 %** |
| swing zones (strong only), entry candidates | 47 | 12 | 9 | +0.20 | [-0.52, +0.91] | 56 % |

Reading it honestly: volume falls by 63 % and the zones that remain lost less in this window, but **none of the swing rows is distinguishable from zero** and the samples are
small (53 zones). XAU is 5 zones, all stopped out, and cannot be read either way. The shipped thresholds were fixed before this measurement (`swing_*` leaves are the displacement
baseline, `minimum_zone_strength: moderate` and `entry_max_depth_fraction: 0.5` were chosen on structure, not on the result) and were not tuned to it. Other strategies' events (3 073) are byte-identical to master.

What the shipped model publishes (410 opportunities): swing class INTERMEDIATE 360 / MAJOR 50; zone strength moderate 371 / strong 39; structure frame M15 209 / H1 201; entry kind
proximal 62, body_proximal 74, midpoint 34, stop_cap_fit 112, room_fit 128 (mean depth 0.32 of the zone); **median room 1.07R, 10th percentile 1.00R**: entries sit at the minimum
room the strategy has always required, they are feasible, not comfortable. Raising `minimum_target_r` is the lever if a wider margin is wanted.

## Compatibility

`opportunity-v1` gains two optional blocks (`structure.swing`, `entry_plan`); an event without them is valid and a consumer that ignores them trades as before. Algo Bot
records `structure_swing_class`, `structure_zone_strength`, `entry_plan_kind`, `entry_plan_price`, `entry_plan_room_r` and `entry_plan_effective_risk` on the match and the plan
`analysis` block; no execution module reads them (a test fails if one does). The cTrader executor ignores unknown plan fields. Supply and demand keep their own packages,
configuration, lifecycle, quality and candidates; the swing classification and zone formation live in `internal/structure` and the entry planner in `internal/strategyutil` as
side-parameterised mechanics so the two strategies cannot drift apart (mirror tests prove they are exact price mirrors).
