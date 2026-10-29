# Asia Range Strategy V1

`asia_range` trades **London's move on the finalized Asia range**. It is a Go-native XAU strategy (`symbols: [XAU]`), enabled in the live Go opportunity stream;
Algo Bot applies execution policy before any TradePlan is published. It has no frozen Python predecessor.

## Thesis and evidence

Gold consolidates in Asia; London either raids one side of that range and reverses (a stop run on the range edge) or breaks it and is accepted. The same order
clustering as [round_number](round_number.md) (Osler 2003, NY Fed SR 125 and SR 150) puts stop orders just beyond a range edge. The sequence (sweep, reclaim,
structure shift, retest) is the one CRT uses with a session anchor instead of an H1 anchor. These are published hypotheses; nothing here claims the rules earn.

**Not the purged Session Level / Range Edge strategy** ([shared-structure-evidence](shared-structure-evidence.md)). Those opened an opportunity on a level *touch*.
This is a complete thesis with its own structure (the finalized Asia range as of London open, width-qualified), its own manipulation or acceptance test, its own
confirmation and its own stop, the same shape as CRT.

## Anchor

The most recent **finalized** `ASIA_H` / `ASIA_L` pair of the session read, usable from its `AvailableAt` (London open on the session calendar, daylight-saving
correct). The range width in **M15 ATR** as of that instant must lie in `[minimum_range_atr, maximum_range_atr]` (1 to 6), else `range_too_narrow` /
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

## Release and rollback

New strategy id, so new opportunity ids; no existing id changes. Rollback: `analysis.strategies.asia_range.enabled: false` (the entry must stay) and
`auto_algo.strategies.technique.asia_range.enabled: false`, then restart analysis-engine and algo-bot.
