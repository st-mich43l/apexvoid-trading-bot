# Asia Range Strategy V1

`asia_range` trades **London's move on the finalized Asia range**. It is a Go-native XAU strategy (`symbols: [XAU]`), enabled in the live Go opportunity stream;
Algo Bot applies execution policy before any TradePlan is published. It has no frozen Python predecessor.

## Thesis and evidence

Gold consolidates in Asia; London either raids one side of that range and reverses (a stop run on the range edge) or breaks it and is accepted. The same order
clustering as [round_number](round_number.md) (Osler 2003, NY Fed SR 125 and SR 150) puts stop orders just beyond a range edge. The sequence (sweep, reclaim,
structure shift, retest) is the one CRT uses with a session anchor instead of an H1 anchor. These are published hypotheses; nothing here claims the rules earn.

**Not the purged Session Level / Range Edge strategy**. Those opened an opportunity on a level *touch*.
This is a complete thesis with its own structure (the finalized Asia range as of London open, width-qualified), its own manipulation or acceptance test, its own
confirmation and its own stop, the same shape as CRT.

## Anchor

The most recent **finalized** `ASIA_H` / `ASIA_L` pair of the session read, usable from its `AvailableAt` (London open on the session calendar, daylight-saving
correct). The range width in **M15 ATR** as of that instant must lie in `[minimum_range_atr, maximum_range_atr]` (1 to 9), else `range_too_narrow` /
`range_too_wide`. The thesis is live for `active_window_hours` (7) from `AvailableAt`; an episode must **start** inside that window. The window is the structure the
thesis is about, never a clock gate on plans or on any other strategy.

## Setup A: the London raid (`sweep_reclaim`)

CRT's episode machine with the Asia range as anchor (`strategyutil.FindLevelSweeps`): a sweep beyond an edge by at least `max(minimum_sweep_pips x pip,
minimum_sweep_atr x ATR)`; a close back inside by the reclaim buffer within `reclaim_max_bars` (6); an M5 structure shift within `mss_max_bars` (12).

| | |
|---|---|
| Entry band | centred on the reclaimed edge, `entry_band_pips` (20) wide |
| Invalidation | beyond the sweep extreme, by `max(stop_buffer_pips x pip, stop_buffer_atr x ATR)` |
| Target | the range mid if it is at least `minimum_target_r` x risk away, else the opposite edge, else refuse (`no_target_room`) |
| Evidence | `m5_asia_range_sweep`, `m5_asia_range_reclaim`, `m5_asia_range_structure_shift` |
| Reaction pattern | `sweep_reclaim` |
| Measured confluence factors | `WickRejection`, `StructuralAgreement`, `HTFAligned`, and `SessionContext`, true only because the swept edge is a finalized session level, which is exactly that factor's definition |

## Setup B: London acceptance (`break_retest`)

An **M15** close beyond an edge by at least `break_buffer_atr` (0.15) x M15 ATR from a prior close inside the range; `acceptance_closes` (2) M5 closes beyond it; the
first M5 retest of the edge band (`entry_band_pips`, centred) within `retest_max_bars` (24); a rejection close away from the edge (lower wick >= `minimum_wick_ratio`
or a bullish engulfing, in the break direction).

| | |
|---|---|
| Entry band | the retest band, centred on the edge |
| Invalidation | behind the retest candle or the edge, whichever is further, minus the buffer |
| Target | the nearest opposing liquidity at least `minimum_target_r` x risk away, else the measured move `edge +/- measured_move_fraction (0.5) x range width`, else refuse |
| Evidence | `m15_asia_range_break`, `m5_asia_range_acceptance`, `m5_asia_range_retest_rejection` |
| Reaction pattern | `wick_rejection`, or `engulfing` |
| Measured confluence factors | `DisplacementGrade` (the M15 break candle in M15 ATR >= `displacement_grade_atr`), `WickRejection`, `StructuralAgreement` (the break closed through the last M5 swing), `HTFAligned` |

## The lock

**One thesis per edge per trading day.** Episodes are resolved in confirmation-time order; the first *confirmed* episode on an edge (A or B) owns it and the other is
`edge_locked`. A raid of `ASIA_H` does not lock `ASIA_L`. The result is a function of the candles up to the evaluated one only, so replay and bar-by-bar
evaluation agree (both are tested). A thesis the stop budget later refuses is still that edge's thesis for the day.

## Rules shared with Round Number

Stop budget, session momentum evidence, the confluence floor, deterministic identity (`StructuralID = technique:asia_range:<edge>:<AvailableAt>`) and the
quality dimensions (`range_validity`, `force`, `rejection`, `sweep_depth`, `entry_location`, `structure`, `room`) work as described in [round_number](round_number.md).

## Rejection reasons

`range_too_narrow`, `range_too_wide`, `edge_locked`, `reclaim_window_expired`, `reclaim_lost`, `no_structure_reference`, `mss_window_expired`,
`mss_quality_insufficient`, `break_failed`, `no_retest`, `retest_not_rejected`, `stale_retest`, `invalidated_after_confirmation`, `price_through_entry`,
`invalid_stop_geometry`, `no_target_room`, `target_already_reached`, `risk_exceeds_execution_envelope`, `confluence_below_floor`, `insufficient_m5_history`,
`insufficient_m15_history`.

## Why it has published no live opportunity (finding of 3 Nov 2026)

The strategy was deployed on 29 October and no `asia_range` opportunity has been published since. Two suspects were checked on real bars; only one holds.

1. **Daylight saving: not the cause.** Every committed capture predates the London change of 25 October and the New York change of 1 November, so the live period was untested.
   `TestAsiaRangeIsAnchoredAndPublishesOnEachSideOfTheClockChanges` builds a morning on each side of both changes (Fri 23 Oct BST, Mon 26 Oct GMT, Fri 30 Oct, Mon 2 Nov EST) with
   the engine's real session calendar and asserts that `ASIA_H` / `ASIA_L` finalize at **that date's** London open (07:00 UTC before, 08:00 after), that the London levels finalize at that
   date's New York open (13:00 / 14:00 UTC), that the strategy anchors there, and that a London raid inside the window publishes. All four pass with no code change; the
   fresh capture also contains a real post-change publish (Tue 27 Oct, BUY 09:55 UTC).
2. **The market did not offer the setup, by the strategy's own range test.** `TestAsiaRangeDayByDay` replays `scalpwindow-xau-20261103` (every Asia range after 22 Oct) and
   prints each day against the `[1, 6]` M15 ATR window:

| London open | Asia range | in M15 ATR | outcome |
|---|---|---|---|
| Fri 23 Oct | 455 pips | 5.7 | `confluence_below_floor`, `target_already_reached` |
| Mon 26 Oct | 412 pips | 7.0 | `range_too_wide` |
| Tue 27 Oct | 284 pips | 4.5 | **published** BUY 09:55 UTC (and 10 refused episodes) |
| Wed 28 Oct | 428 pips | 9.0 | `range_too_wide` |
| **Thu 29 Oct (deployed)** | 468 pips | 6.7 | `range_too_wide` |
| **Fri 30 Oct** | 612 pips | 7.6 | `range_too_wide` |
| **Mon 2 Nov** | 373 pips | 5.6 | in window; `no_retest`, `reclaim_window_expired` |

Live days since the deploy: two Asia ranges wider than the strategy's maximum (6.7 and 7.6 M15 ATR) and one inside the window with no valid episode; 3 Nov's range finalizes at 08:00 UTC.
Across the seven XAU captures the strategy published 3 setups (2 raids, 1 acceptance), and `range_too_wide` is the commonest day-level refusal in the post-22 October gold regime, where a
$30 to $60 Asian session is not a consolidation. `strategy_refused{strategy="asia_range", reason}` now reports every such refusal live (once per distinct episode; `Evaluate` counts, `Analyze` does not).

**v2 (5 Nov 2026): `maximum_range_atr` 9 (was 6).** The width is measured in M15 ATR at London open, and that ATR is computed over the quiet Asia bars, so it is deflated: the live ranges since 29 Oct were 6.7 and 7.6 of it (5.6 on 2 Nov, without a valid episode) and 6 refused them as `range_too_wide`. Every other rule is unchanged (sweep and reclaim, structure shift, acceptance, retest, stop budget, room, edge lock). Rollback: `maximum_range_atr: 6.0`.

## Release and rollback

New strategy id, so new opportunity ids; no existing id changes. Rollback: `analysis.strategies.asia_range.enabled: false` (the entry must stay) and
`auto_algo.strategies.technique.asia_range.enabled: false`, then restart analysis-engine and algo-bot.
