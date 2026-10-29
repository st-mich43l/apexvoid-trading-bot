# Round Number Strategy V1

`round_number` trades the **stop orders that cluster at gold's round numbers** (handles). It is a Go-native XAU strategy (`symbols: [XAU]`), enabled in the live
Go opportunity stream; Algo Bot applies execution policy before any TradePlan is published. It has no frozen Python predecessor.

## Thesis and evidence

Take-profit orders cluster *at* round numbers and stop orders just *beyond* them (buy stops above, sell stops below): Osler, *Currency Orders and Exchange Rate
Dynamics*, Journal of Finance 58 (2003), and the New York Fed's [Staff Report 125](https://www.newyorkfed.org/research/staff_reports/sr125.html) and
[150](https://www.newyorkfed.org/research/staff_reports/sr150.html). Two consequences, two setups on the same handle:

- A trade through the stop band that is immediately taken back is a **stop run**: fade it (setup A).
- A trade through the band that is **accepted** triggers a stop cascade: trade the retest in the break direction (setup B).

These are published hypotheses about order clustering. Nothing here, and nothing in the replay counts below, claims the rules earn.

**Not a duplicate of `key_level`.** `key_level` keeps a round level (`round_step` 5.0) only when at least `minimum_touches` swings already reacted there and
trades the reaction in the level's role. `round_number` trades the **handle itself**, whether or not price has reacted there before, because the order cluster
exists regardless. When both fire on one price the overlap is resolved downstream by thesis arbitration; neither is suppressed.

## Handles

`price = k x handle_step` (10) within `max_handle_distance_atr` (3) x ATR of the last close. A handle that is a multiple of `major_step` (50) is **major**
(evidence `round_number_major_handle`, quality dimension `major_handle`); it changes no threshold. ATR is the Wilder ATR(14) of the M5 candles over a fixed
trailing window of 140, so it does not depend on loaded history. All logic is written for a BUY and a SELL is the same sequence on mirrored candles.

## Setup A: sweep, reclaim, structure shift (`sweep_reclaim`)

BUY at a handle (SELL mirrored): a candle trades below the handle by at least `minimum_sweep_pips` (5); the candle before it closed above the handle (price came
from the origin side); the whole excursion is at most `maximum_sweep_pips` (40) deep, deeper being acceptance and not a stop run; a close back above the handle by
`minimum_reclaim_pips` (2) within `reclaim_max_bars` (3); then a later M5 close above the most recent confirmed swing high that precedes the manipulation extreme
(inside `structure_lookback_bars`, 24), with body ratio >= 0.5, close strength >= 0.6 and displacement >= 0.5 ATR, within `mss_max_bars` (6) of the reclaim and not
older than `confirmation_max_age_bars` (2). `acceptance_closes` (2) closes beyond the handle before a reclaim make the excursion acceptance, not a sweep.
This is CRT's structure-shift logic (`strategyutil.FindLevelSweeps`); the swing is the one preceding the extreme, as in CRT, because a swing *between* the sweep and
the reclaim cannot exist when the sweep candle reclaims itself.

| | |
|---|---|
| Entry band | `[handle, handle + entry_band_pips]` (15 pips) on the origin side: the retest of the reclaimed handle |
| Invalidation | the manipulation extreme minus `max(stop_buffer_pips x pip, stop_buffer_atr x ATR)` |
| Target | the nearest unswept opposing liquidity at least `minimum_target_r` x risk away, else the next handle that far |
| Evidence | `m5_round_number_sweep`, `m5_round_number_reclaim`, `m5_round_number_structure_shift` |
| Reaction pattern | `sweep_reclaim` |
| Measured confluence factors | `WickRejection` (a candle from the sweep to the reclaim closed up with a lower wick >= `minimum_wick_ratio`), `StructuralAgreement` (the shift happened), `HTFAligned`, `SessionContext` (a finalized session, previous-day or previous-week level swept and reclaimed in the window) |

## Setup B: accepted break, first retest (`break_retest`)

BUY (SELL mirrored): an M5 close above the handle by `break_buffer_pips` (10) from a prior close at or below it, body ratio >= 0.6 and range >= 1.2 ATR (measured
before the break); `acceptance_closes` (2) further closes above the handle; the **first** candle that trades into the band `[handle - retest_tolerance_pips,
handle + entry_band_pips]` within `retest_max_bars` (12) is the retest; it, or the next candle, must reject: close above the band top with a lower wick >=
`minimum_wick_ratio` (0.4), or a bullish engulfing close above the handle. A later touch after a retest that did not reject is a second retest and is refused
(`stale_retest`).

| | |
|---|---|
| Entry band | the retest band |
| Invalidation | `min(retest candle low, handle - stop_buffer_pips)` minus the buffer |
| Target | as setup A |
| Evidence | `m5_round_number_break`, `m5_round_number_acceptance`, `m5_round_number_retest_rejection` |
| Reaction pattern | `wick_rejection`, or `engulfing` for the engulfing branch |
| Measured confluence factors | `DisplacementGrade` (the break candle's displacement >= `displacement_grade_atr`), `WickRejection`, `StructuralAgreement` (the break closed through the most recent confirmed M5 swing), `HTFAligned`, `SessionContext` |

## Rules that apply to both

- **One episode per handle per side per trading day.** The first *confirmed* episode (A or B) owns it; a later confirmation is `handle_locked`. The trading day rolls
  over where the session levels do (`analysis.sessions.daily_rollover_utc_hour`, injected as `trading_day_rollover_utc_hour`). A thesis the stop budget later
  refuses is still that handle's thesis for the day.
- **Siblings on one bar:** more stars, then the entry nearest the last close (`superseded_by_sibling`).
- **Stop budget:** an honest stop above the instrument cap is refused (`risk_exceeds_execution_envelope`), never tightened; one below the floor is published with
  `stop_budget_floor` and widened by execution ([legacy-stop-budget](legacy-stop-budget.md)).
- **Session momentum** (evidence only, [shared-structure-evidence](shared-structure-evidence.md)): `session_momentum_aligned` / `session_momentum_opposed` when
  confirmed during New York.
- **Confluence floor:** the shared rubric (`confluence.Evaluate`), refused below `confluence_floor` (`confluence_below_floor`). Quality dimensions: `force`,
  `rejection`, `sweep_depth`, `entry_location`, `structure`, `room`, `major_handle`, plus the measurements `technical_rr`, `technical_risk_pips`,
  `session_momentum_atr`.
- **Identity:** `StructuralID = technique:round_number:<side>:<handle>:<trading day>`; the candidate id comes from the kind, side, handle and the sweep (or break)
  and confirmation times, so re-observing the same thesis on a later bar is the same opportunity.

## Rejection reasons

`sweep_too_deep`, `accepted_not_swept`, `reclaim_window_expired`, `reclaim_lost`, `no_structure_reference`, `mss_window_expired`, `mss_quality_insufficient`,
`break_failed`, `no_retest`, `retest_not_rejected`, `stale_retest`, `handle_locked`, `invalidated_after_confirmation`, `price_through_entry`,
`invalid_stop_geometry`, `no_target_room` (unreachable by construction: the next handle always exists), `target_already_reached`, `risk_exceeds_execution_envelope`,
`confluence_below_floor`, `superseded_by_sibling`, `atr_unavailable`, `insufficient_m5_history`.

## Parameters

`analysis.strategies.round_number` in `config/analysis.yml`, each with a live consumer in `roundnumber/config.go`. They are tunable parameters validated against the
committed captures, not claimed constants. Real-capture counts are in the pull request that introduced the strategy.

## Release and rollback

New strategy id, so new opportunity ids; no existing id changes. Rollback: `analysis.strategies.round_number.enabled: false` (the entry must stay: the registry
requires every catalog id) and `auto_algo.strategies.technique.round_number.enabled: false`, then restart analysis-engine and algo-bot.
