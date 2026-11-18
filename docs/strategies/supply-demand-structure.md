# Supply and demand: major-swing structure and AE-owned entry (SD-1 to SD-7)

`supply` v14 and `demand` v12. The mirror of one another: everything below is written for supply (SELL, a zone above price) and holds for demand with every price
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
* **shift**: a CLOSE through the **structure** the swing left: the most recent opposite pivot that is itself significant (prominence >= the intermediate threshold) and had not been
  closed through before the swing formed. A one-bar dip near a top is not structure and a level already broken is not broken again. A broken level is evidence ONCE: it is credited to the
  highest swing between the level and the break (the earliest when equal). **CHoCH** when the swing extended the move of the previous significant swing of its kind (or is the first),
  **BOS** when it did not. The swing records the level it broke (`ShiftLevel`, `ShiftLevelTime`);
* **relevance**: no close beyond it since it formed and not older than `swing_relevance_bars`.

**MAJOR** = external, prominence >= `swing_major_prominence_atr`, departure >= `swing_major_departure_atr` and a shift. **INTERMEDIATE** = prominence >=
`swing_intermediate_prominence_atr` and departure >= `swing_intermediate_departure_atr`. Everything else is **MINOR**. A swing's identity never changes as bars are
added; its class is "as of the last bar given", and a decision published on it keeps the class it was published with. **A zone's strength is read from the bars up to its formation bar only**
(the swing known and the departure closed), so it is the same at every later bar: a later shift or a later close is the lifecycle's business, never an upgrade.

## SD-3: the zone from the swing (`structure.FormSwingZones`)

The zone is the base the departure LEFT, built from the departure backwards, and anchored on the swing that is that base's extreme.

* **Departure.** The strategy's own displacement leg. It begins at the leg's first bar; for a run that began inside the consolidation (the base candles happen to be the leg's colour) the
  next attempt is its first impulse candle that is not itself a swing candle.
* **Base.** From the departure's first candle, which contributes only what it traded before leaving (a supply candle: from its body top to its high; its own travel is the departure),
  backwards: a candle joins while it is quiet (range <= `base_max_candle_atr` x ATR), overlaps the base so far, the base stays within `maximum_zone_atr` (a slow trend is not absorbed one
  candle at a time) and there are at most `base_max_candles` consolidation candles (counted over the complete base, the candles between the swing and the departure included). A confirmed
  swing candle may join even when impulsive, by its rejected part only. Wicks are included; no later entry decision narrows the structure.
* **Anchor.** The base's extreme candle must be a confirmed swing of the kind: the highest swing high of a supply base (equal extremes: the most significant, then the earliest),
  `AnchorPrice` is that swing's own extreme and equals the base's high. A base with no swing, or whose extreme candle is not a confirmed swing (equal adjacent highs), is rejected with its
  reason, never guessed; an extreme whose right window has not closed yet is "awaiting" and forms later; one swing anchors one zone; the swing must be within
  `base_departure_gap_bars` of the departure.
* `proximal_price` is the edge price meets first, `distal_price` the far edge the structure is invalidated beyond. `swing_confirmed_at`, `base_formed_at` and `departure_confirmed_at` are
  open times of closed bars; the zone is formed, and touchable, only from the close of the bar by which the swing AND the departure are both known (`ConfirmedAt`).
* **Strong**: a MAJOR swing that still stood at formation with a departure of at least `strong_departure_atr`. **Moderate**: at least INTERMEDIATE and standing at formation. **Weak**:
  anything else. Strength is read from the bars up to the formation bar and never changes. **Aged** (the swing older than `swing_relevance_bars` as of the last bar) is the zone's CURRENT
  validity, separate from its strength: an aged zone is not offered for new entries and gives no confluence (`zone_aged`). A structure wider than `maximum_zone_atr` is a region
  (`region_wider_than_a_zone`) and one narrower than `minimum_zone_atr` an incidental candle region: neither publishes.
* Only `minimum_zone_strength` or better publishes (shipped: moderate). A weak zone is context: it is counted when it would otherwise have traded (`zone_strength_below_minimum`). FVG,
  order block or a sweep are not required; they are evidence, not a precondition.

## SD-4: quality

`supply.quality.v4` / `demand.quality.v4` for swing zones (the legacy zone keeps v2, frozen; v3 was the first swing-zone model and is superseded by the SD-4 audit below).
`Quality.Overall` is still the plain mean of the ten `score_*` dimensions, informational, no gate, ranking off. The dimensions and their exact definitions are in the audit table
in "SD-4 audit: the technical quality model".

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

## SD-2 audit (v13 / v11)

Audit of `ClassifyMajor` as merged in #886, with tests that failed on it:

| Finding | Evidence | Fix |
|---|---|---|
| The shift reference was the most recent RAW opposite pivot. A one-bar dip (0.9 ATR of prominence) near a top was "the structure", so closing below it was a CHoCH that, with an external high and a deep fall, made the swing MAJOR. | `TestAnInternalBreakDoesNotMakeASwingMajor` | The reference must be significant. |
| A significant low already closed through before the swing formed stayed the reference whenever the newer lows were skipped, so a later close below it counted as a fresh break. | `TestALevelAlreadyBrokenBeforeTheSwingIsNotAFreshBreak` | The reference must be unbroken when the swing formed. |
| One close through one low was the shift of every swing above it. | `TestOneBrokenLevelIsCreditedToOneSwing` | Credited once (highest swing, earliest when equal). |
| CHoCH/BOS compared with the previous raw fractal. | same fixtures | Compared with the previous significant swing of the kind. |
| A zone formed as moderate/INTERMEDIATE was upgraded to strong/MAJOR when a shift appeared bars later (strength was read as of "now"). | `TestZoneStrengthIsFixedAtFormation` | Strength is read as of the formation bar. |

Correct and kept (tests added): a shift is never dated before its reference was confirmed (a close below a pivot low is impossible inside its right window); the reversal extreme of a
liquidity sweep and of a failed breakout are external swings; equal highs/lows are not both external; a swing's class never uses a bar after the prefix it is read from (every prefix
replayed; a read from a copy of the same bars is identical); truncating the window can only remove evidence and never raises a class; ATR initialisation (zero/NaN), a hole in the feed and
flat prices are handled; restart-safe (pure function); exact price mirror for the corrected logic; M15 and H1 evidence reaches M5 only from the close of its confirmation bar
(`TestStructureEvidenceIsTouchableFromTheCloseOfItsConfirmationBar`); `swing_relevance_bars + swing_external_bars` must fit the structure frames' windows (250 M15, 400 H1), or a class would
change because bars were evicted, not because the market did.

Real data (production closed bars, 9-12 days, M15 window 250 and H1 window 400, evaluated bar by bar; baseline = #886 as merged):

| | M15 (5 symbols) | H1 (5 symbols) |
|---|---|---|
| confirmed raw pivots (unique) | 172 to 186 per symbol | 523 to 567 per symbol |
| final read [MINOR / INTERMEDIATE / MAJOR] (XAU, EURUSD) | [72/104/10], [84/86/5] | [267/270/7], [258/257/8] |
| MAJOR when first seen, merged -> updated (all five symbols) | 33 -> 28 | 80 -> 57 |
| class changes while the swing is within 150 bars of the window end | 139 up, **0 down** | 503 up, **0 down** |
| disagreements on the last read | 1 | 5 |

Every class change inside the relevance horizon is an upgrade as the departure develops (evidence arriving causally); every downgrade seen in a long window happens to swings older than the
horizon, where the window start evicts their left side or their reference. The updated classifier demotes a quarter of the swings the merged one called MAJOR when first seen: all of them
used a raw pivot of under 1.5 ATR of prominence as their structure.

Zone funnel (supply highs / demand lows over the same windows): of 1 776 confirmed swing highs and 1 835 lows, 53 % and 58 % are INTERMEDIATE or better; **about two thirds of those
(68 % and 65 %) have no displacement leg beginning within 3 bars of them, so they form no zone**; zones formed 20 to 29 (M15) and 60 to 89 (H1) per symbol and side; at formation about 57 %
of the supply zones are moderate, 42 % weak and 2 % strong (demand similar); a structure wider than `maximum_zone_atr` is 0 to 6 per series. Of the zones that were tradable at some point, later
terminal (breach never reclaimed): supply H1 37 % to 73 %, demand H1 41 % to 81 %, M15 supply 6 % to 38 %, demand 60 % to 91 % in this window, which is why the lifecycle tests matter.

Published set, merged -> updated, same bars: **410 -> 410 opportunities on the same 67 zones** (EURUSD 67, GBPJPY 182, GBPUSD 49, USDJPY 70, XAU 42). Only labels move: MAJOR/strong 39 -> 20, MAJOR/moderate
0 -> 6, INTERMEDIATE/moderate 360 -> 384. At the shipped `minimum_zone_strength: moderate` nothing that publishes changes; with `strong` it would. Other strategies' 3 073 events are byte-identical.

Concrete examples (XAU H1; bar times are open times, a bar is known at its close):
* **MAJOR, kept.** High 4082.73 at 1793217600, ATR 17.04. Left dominance 98 bars (external). Prominence 6.28 ATR, departure 3.56 ATR. Reference: the low 4025.10 at 1793210400
  (prominence 3.63 ATR: significant, unbroken when the high formed). Bar 1793250000 closes at 4024.75, below it: **CHoCH** (higher high than the previous significant high). The swing is known when the
  bar opened 1793228400 closes (1793232000), the shift when the bar opened 1793250000 closes (1793253600). (The feed is missing the 1793221200 bar; identity is by time.)
* **MAJOR in #886, INTERMEDIATE now.** High 4070.91 at 1793145600, ATR 11.12, external (left 78), prominence 4.12, departure 3.85. #886's "CHoCH" was a close below the raw pivot low
  4054.37 whose prominence was 0.87 ATR. No significant low was closed through: INTERMEDIATE, `no structure shift`.
* **Same on EURUSD M15.** Low 1.10123 at 1794802500 (ATR 0.00039), prominence 3.31, external; #886's reference was the raw high 1.10203 (0.97 ATR).

## Rollback of the audit

Nothing new to switch: `zone_model: legacy` still restores the three-candle zone. The classifier fix itself is the previous behaviour's defect; to compare, check out #886.

## SD-3 audit (v14 / v12)

Audit of the zone formation as merged in #886/#887. Each defect has a test that failed on it (`swingzone_audit_test.go`):

| Finding | What it did | Fix |
|---|---|---|
| Forward candles (between the swing and the departure) joined the base unconditionally | a 2.8-ATR rebound candle unrelated to the base stretched the zone down to its low | the base is built from the departure backwards and must be contiguous; an unrelated impulse breaks it |
| `base_max_candles` bounded only the backward extension | a 4-candle base with a limit of 3 | counted over the complete base |
| The first swing in bar order claimed the departure; the base then ran forward to the departure and took a HIGHER extreme with it | `AnchorPrice` was not the anchoring swing's price (**226 of 1 043 zones, 22 %**) | the base's extreme swing anchors it; one swing, one zone |
| A swing candle that was itself the departure's first impulsive candle belonged to the base with its whole range | the zone swallowed the departure's own travel | only what it traded before leaving (open to extreme) |
| `standing()` read the length of the whole history | adding bars downgraded a formed zone to WEAK once the swing was 150 bars old | formation strength is fixed; age is the separate `Aged` state |
| Each candle only had to overlap the base so far | a slowly rising staircase was absorbed into a "base" that is a trend (7 candles, 3.1 ATR) | the base stays within `maximum_zone_atr` |
| The zone depended on whether the departure's first candle's swing had been confirmed yet | a different anchor and identity once it closed | that candle always contributes its pre-departure part; if it is the extreme and unconfirmed, the zone is "awaiting" |

Correct and kept: the departure legs, the swing classification and the strengths' definitions, the lifecycle of SD-1, every threshold (`base_departure_gap_bars: 3`, `base_max_candles: 6`,
`base_max_candle_atr: 1.5`, `minimum_zone_atr: 0.2`, `maximum_zone_atr: 3.0`, `strong_departure_atr: 2.0`, `minimum_zone_strength: moderate`: **no config value changed**).

Tests: the defects above; prefix replay (no zone before its swing and departure are confirmed, the base never moves, identity is the same at every later bar); a minor pivot before the major one,
several internal pivots, a departure on the swing candle and 1 to 3 bars after it (4 is rejected), a small first departure then a larger one, an ambiguous extreme; a property test over 60
pseudo-random markets checking every invariant (anchor = swing = base extreme, consolidation <= `base_max_candles`, no impulse in the base except a swing candle, no candle outside the structure,
swing inside the base and within the gap, one zone per swing, confirmed no earlier than swing and departure) and that **demand is the exact price mirror of supply** (same bases, anchors,
strengths, confirmation, mirrored prices); the entry band never rewrites the structural boundaries; an aged zone is refused as `zone_aged` with its strength intact.

Real data (production closed bars, 9-12 days, five symbols, supply and demand, M15 window 250 and H1 400, evaluated bar by bar; baseline = #887 as merged):

| | merged | corrected |
|---|---|---|
| confirmed swings / displacement legs | 3 611 / 1 874 | same |
| unique zones | 1 043 | 918 |
| `AnchorPrice` is not the swing's price | 226 | 0 |
| base with an unrelated impulse candle | 30 | 0 |
| wider than a zone (published as a region) | 104 | 0 (33 departures rejected as `region_wider_than_a_zone`) |
| strength [strong / moderate / weak] | 23 / 575 / 445 | 28 / 756 / 134 |
| median structure width | 1.92 ATR | 1.52 ATR |
| later terminal (breach never reclaimed) | 231 | 269 |
| median bars from swing to formation | 2 to 3 | 2 to 3 |
| zones in both / only merged / only corrected | 915 / 128 / 3 | |

Departures that made no zone (corrected): 1 426 had no confirmed swing in their base (a displacement from nowhere, not from a swing), 206 an extreme that is not a confirmed swing, 201 a swing
too far from the departure, 201 awaiting the swing's confirmation (formed later), 33 regions, 2 swings already anchoring a zone. Of the 128 merged-only zones: 74 were mis-anchored, 29 had no swing in
their genuine base, 16 are the same departure now anchored on its extreme swing, 6 too far, 3 other. The remaining 14 zones with two anchors for one departure occur only where the rolling window
evicts the swing's own left side (older than the relevance horizon).

Published (replay through the engine): **410 -> 380 opportunities, 67 -> 62 zones**; STRONG 20 -> 30, MAJOR 26 -> 36; XAU 42 -> 79 opportunities on 6 -> 10 zones (5 of the 10 have the same geometry as before
with their identity moved by one bar; the others are different bases). Other strategies' 3 073 events are byte-identical. Hypothetical outcomes, same simulator and assumptions as SD-1 to SD-5, first fill per
zone: merged +0.02R [-0.27, +0.30] (53 zones), corrected -0.03R [-0.32, +0.26] (53 zones); XAU 9 zones -0.14R [-0.74, +0.72]. **No performance claim**: nothing here is distinguishable from zero and the
correction was made for structural correctness, not for these numbers.

XAU examples (H1; open times; a bar is known at its close):
* **Supply, MAJOR, strong.** High 4219.29 at 1790769600 (ATR 14.42, class MAJOR: prominence 3.81, departure 3.81, external, CHoCH). That candle is a 2.5-ATR rally with a long upper wick; the next candle
  (1790773200, a 2.3-ATR bearish candle) is the departure. The base is the rejected part of each: from the rally candle's close 4208.54 up to 4219.29 (and the departure candle's open 4208.69 to its high
  4214.65, inside it): structure 4208.54..4219.29 (0.65 ATR), proximal 4208.54, distal 4219.29, departure 2.53 ATR (efficiency 0.75). The swing is known when its confirmation bar (opened 1790776800) closes; the zone is formed from the same close.
* **Demand, MAJOR, strong.** Low 3995.97 at 1785333600 (ATR 12.91; prominence 8.82, CHoCH) inside a 2-candle base (1785333600 and the departure's first candle 1785337200): structure 3995.97..4010.12
  (1.16 ATR), departure 5.58 ATR (efficiency 0.81). The swing is known when its confirmation bar (opened 1785340800) closes and the zone is formed once the departure has closed (bar opened 1785348000).
* **Supply, MAJOR, strong, six candles.** High 4435.06 at 1789020000, the departure's first candle itself: the base is the six candles 1789002000..1789016400 (4400.25..4431.29) plus that candle's
  open-to-high part: structure 4400.25..4435.06 (2.39 ATR, inside the 3.0 limit: a version of this change without the width bound absorbed a seventh candle, 1788998400, and reached 3.11 ATR, a region).
* **Rejected.** A displacement whose base holds no confirmed swing (1 426 departures in the window) makes no zone however large the move: its reason is `no_confirmed_swing_in_base`.

## SD-4 audit: the technical quality model (`quality.v4`)

Three mechanisms decide three different things and must not be confused. **Structural validity** (swing zone formation, `FormSwingZones`) decides that a zone exists.
**Confluence admission** (`scoreZone()` / `confluence.Evaluate()`, stars) decides whether it may be offered (`min_confluence`). **Technical quality** (`quality.v4`) describes a
published opportunity and gates nothing. This audit changes only the third; trade behavior is identical (see "Preservation").

### The ten dimensions (v4)

Structure-frame measures use the ATR of the zone's own structure frame (M15 or H1); execution measures use the M5 ATR. "Formation" is known when the departure closes and never
changes; "decision" is read at the M5 decision bar from closed bars only.

| # | dimension | formula (range [0,1]) | unit / timeframe | known at | variation (380 opps, 62 zones) |
|---|---|---|---|---|---|
| 1 | `zone_departure` | `Ramp(departure_atr, 1, 3)` | structure ATR | formation | 51 distinct, 0% zero, 14% saturated |
| 2 | `zone_precision` | `Peak(width_atr; 0.1, 0.4, 1.5, 3.5)` | **structure ATR** (v3: M5 ATR) | formation | 37 distinct, 0% zero, 36% saturated |
| 3 | `swing_significance` | `Ramp(approach_atr, 1.5, 6)` (intermediate .. 2x major prominence) | structure ATR | formation | 55 distinct, 3% saturated |
| 4 | `displacement_efficiency` | `Ramp(efficiency, 0.4, 0.8)` | ratio | formation | 55 distinct |
| 5 | `structure_shift` | 1 if the swing broke structure (CHoCH or BOS), else 0 | kind published | formation | 59% zero |
| 6 | `zone_freshness` | `Fall(touches, 0, maximum_zone_touches+1)` | count | decision | 3 distinct (0, 1, 2 touches) |
| 7 | `rejection` | `RejectionScore` only when a confirming pattern was recognised, else 0 | M5 bars | decision | 67% zero (touch-only) |
| 8 | `entry_location` | chase of the planned entry reference in M5 ATR; no credit without a valid M5 ATR | M5 ATR | decision | 29% zero, 38% saturated |
| 9 | `structure` | M5 structure for the trade, recent CHoCH, higher-timeframe alignment | M5 + HTF | decision | 7 distinct |
| 10 | `room` | `ScoreRoom` of the structural room over the **effective** risk, limited by the canonical opposing barrier | pips / R | decision | 171 distinct, 7% zero |

Measurements that are not scores (never counted twice): `swing_class`, `zone_strength`, `swing_prominence_atr`, `swing_approach_atr`, `zone_width_atr`, `zone_departure_atr`,
`structure_shift_kind` (CHoCH 1, BOS 2), `zone_touches`, `reaction_confirmed`, `entry_depth_fraction`, `room_r`, `pd_position`, `deep_entry_pips`, and the availability flags
`structure_atr_available` / `execution_atr_available`. `quality_model_version` is 4.

### Defects found (each reproduced by a test that failed on v3) and the correction

1. **`zone_precision` mixed timeframes.** The M15/H1 structural width was divided by the M5 execution ATR (observed ratio of the structure ATR to the M5 ATR: 1.5x for the XAU M15 zones above, 4.3x for the USDJPY H1 zone), so a normal zone read "too wide": zero on **52%** of
   published opportunities, mean 0.24, and on H1 zones 0.03 on average (M15 0.36). The same zone under two different M5 ATRs scored 0.05 and 1.0. Now the structure frame's ATR (`zone_width_atr`);
   H1 mean 0.03 -> 0.91, M15 0.36 -> 0.81. Zone geometry, stop and entry are untouched.
2. **A missing M5 ATR was fabricated as 1.0** in the view (an admission placeholder that leaked into quality), and freshness/touches were skipped when ATR was unknown. Now the view says
   `atrKnown`, a missing ATR gives no credit to the dimensions that need it (entry location, scalp room ATR) and takes nothing from the others; the reason is observable.
3. **`structure_shift` ranked CHoCH 1 / BOS 0.5** with no evidence that a reversal is a better supply/demand origin than a continuation. A break of structure is a fact the swing either has or has not; the
   kind is a measurement.
4. **`swing_significance` counted the departure twice.** `ProminenceATR = min(left, right)` is almost always the departure, which `zone_departure` already scores. It is replaced by the swing's
   **approach** (`MajorSwing.ApproachATR`: the distance price travelled into the swing over the `ExternalBars` window before it), a distinct fact that is known from the first bar after the swing.
   Their correlation on unique zones: precision~significance +0.47 in v3 is gone; the largest remaining pairs are departure~shift +0.46, precision~freshness +0.40, significance~shift -0.48
   (62 zones; low power, reported, not acted on).
5. **`rejection` scored the touch bar's shape when no pattern was recognised**, which invents reaction evidence for a touch-only trade. A touch-only trade stays eligible (candle confirmation is
   optional) and scores 0, with `reaction_confirmed = 0` (251 of 380 are touch-only).
6. **`zone_freshness` `Fall(touches, 1, 5)` could not tell fresh from once-retested** (two distinct values, 67% saturated). Now `Fall(touches, 0, maximum_zone_touches + 1)`: 1.00 / 0.67 / 0.33.

### Not changed (audited, found consistent)

* `zone_departure` (formation read, the departure cannot extend later), `displacement_efficiency` (a distinct property: how directly, not how far), `structure`, `entry_location` (execution measure,
  correctly on M5 ATR), `room`.
* **Room.** `entry_plan.room_r` equals `quality.effective_room_r` on all 380 opportunities (max difference 0.0): both use the same entry, the unmoved invalidation, the barrier-limited admission target and
  the effective risk. `quality.room_reward_risk` is the target's own reward over the **structural** (pre-floor) risk and differs on 149 of 380 (39%, up to 3.0R) exactly when the instrument stop floor widens the
  stop (e.g. EURUSD: structural 8.1 pips, effective 12 pips: 1.48 vs 1.00). It is published for reference; `score_room` uses the effective room. All 380 targets are structural, so no projected-target credit exists;
  a projected target earns no room credit (test).
* **Confluence (stars).** Audited with real candles, **nothing changed** (changing it changes admission). Findings: session levels at the same price are summed by name (ASIA_H, PDH, LONDON_H, NY_H at one price each earn
  2.0; 55 cases with two or more names at one price), a liquidity pool and a session level at the same price both score (296 cases), pool + session + key level all present on 107 of 277 scored zones, key level and round
  number overlap, base points are fixed, and the normalisation maximum (24.5) can be exceeded by up to five session names. A temporary patch that de-duplicated session levels by price changed the published set 380 -> 368
  (14 lost, 2 gained, 5 of 60 zones). That is a **separate behavioral change** and is not part of this PR; it is recorded here as the proposal and its measured impact.

### Real XAU examples (production closed bars, explicit arithmetic)

* **Demand M15 INTERMEDIATE** (`opp_07e6afa2`), zone 4084.63..4092.34, width 7.71 (77 pips). M5 ATR 4.793; M15 ATR 7.036. `zone_precision` v3 = Peak(7.71/4.793 = 1.61) = 0.971; v4 = Peak(7.71/7.036 = 1.10) = 1.000.
  `swing_significance` v3 (min(left,right) prominence 2.17) = 0.150; v4 (approach 6.73 over Ramp 1.5..6) = 1.000 while the departure 1.84 is scored once by `zone_departure` (0.421). `zone_freshness` (1 touch) v3 1.000, v4 0.667.
  `rejection` v3 0.292 (touch bar shape), v4 0 (`reaction_confirmed` 0). Room: plan 4.628R = effective 4.628R = reward/risk (stop 69.9 pips, no floor effect) -> `score_room` 1.000.
  Overall v3 0.555 -> v4 0.580 = mean of ten scores.
* **Supply M15 INTERMEDIATE** (`opp_06a86903`), zone 4046.56..4056.14, width 9.58. M5 ATR 2.868, M15 ATR 4.782. `zone_precision` v3 = Peak(9.58/2.868 = 3.34) = 0.139; v4 = Peak(2.00) = 0.748. Room 1.048R
  (the minimum): `score_room` 0.024. Overall v3 0.402 -> v4 0.439.
* **USDJPY H1** (no XAU H1 zone in this capture): width 0.252 (25 pips), M5 ATR 0.0623, H1 ATR 0.2655: `zone_precision` v3 = Peak(4.05) = 0.000; v4 = Peak(0.95) = 1.000.

### Before / after (380 opportunities on 62 zones, five symbols, 9-12 days of closed bars)

| dimension | v3 mean / zero / saturated | v4 mean / zero / saturated |
|---|---|---|
| zone_precision | 0.24 / 52% / 4% | 0.85 / 0% / 36% |
| zone_freshness | 0.92 / 0% / 67% (2 values) | 0.64 / 0% / 24% (3 values) |
| rejection | 0.34 / 27% / 2% | 0.19 / 67% / 2% |
| swing_significance | 0.24 / 0% / 1% | 0.43 / 0% / 3% |
| structure_shift | 0.29 / 59% / 17% | 0.41 / 59% / 41% |
| other six dimensions | unchanged | unchanged |
| **Overall** | 0.436 (sd .104, .20-.79) | 0.485 (sd .089, .26-.77) |

M15 vs H1 overall: v3 0.474 / 0.369, v4 0.502 / 0.456 (the H1 penalty was the M5-ATR defect). MAJOR vs INTERMEDIATE: v3 0.586 / 0.420, v4 0.589 / 0.474. Fresh vs retested: v4 0.554 / 0.464.
Contradictory measurements in v4: departure >= 0.8 with efficiency <= 0.2: 0; MAJOR with significance <= 0.2: 0; precision >= 0.9 with entry_location = 0: 66 (different facts: a good zone entered far from the reaction).

### Invariance and tests

* Price scale (x100), supply/demand price mirror (identical vector on both packages from one golden literal), M5 ATR change (identical zones), missing ATR, touch-only trade, projected vs structural target,
  `Overall = mean(score_*)`, every score in [0,1], approach known from the first bar and independent of the departure (`structure/major_audit_test.go`), quality versioning.
* **Preservation.** Replay of the five captures: the same 380 opportunity IDs, and no payload field except `quality` differs (entry, invalidation, targets, evidence, `entry_plan`, `structure`, stop envelope);
  3 073 events of other strategies are byte-identical. Strategy versions are not bumped (IDs are unchanged); `quality_model_version` goes 3 -> 4 for swing zones.

### Does quality predict outcomes? (hypothetical; not validated)

Spec frozen before reading outcomes: one first-filled opportunity per unique zone (53 filled of 62; 44 M15, 9 H1), Spearman(quality, hypothetical R) and top-half minus bottom-half mean R, 95% bootstrap over zones.
Simulator: limit fill at the band edge, stop beyond the invalidation widened to the floor, first target, stop-first when a bar touches both, no spread, no slippage. Mean R -0.03, win 45%.
Overall v3 rho -0.03 [-0.32, +0.25]; v4 rho -0.02 [-0.29, +0.26]; top-minus-bottom half -0.21R [-0.78, +0.43]. No dimension's interval excludes zero (precision +0.20 [-0.09, +0.45] is the largest positive,
rejection -0.19 [-0.40, +0.08] the largest negative). **Expectancy and the predictive value of quality are not validated**; a quality-only fix makes no profitability claim. **Ranking stays disabled.**

### Release and rollback (SD-4)

Analysis Engine only; no contract (the new quality measures are additive floats in `quality.components`), config or strategy-version change; opportunity IDs and every trading field are unchanged. Rollback: revert the change;
`zone_model: legacy` keeps v2.

## Release and rollback

Analysis Engine only; no contract or config change (the `swing` and `entry_plan` blocks are unchanged). Strategy versions change, so opportunity IDs change once at deploy; `structure.formed_at` is now the
open time of the departure's first candle. Rollback: revert the change, or `zone_model: legacy`.
