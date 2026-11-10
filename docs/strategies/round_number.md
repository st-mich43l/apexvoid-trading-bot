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
  `stop_budget_floor` and widened by execution.
- **Session momentum** (evidence only): `session_momentum_aligned` / `session_momentum_opposed` when
  confirmed during New York.
- **Confluence floor:** the shared rubric (`confluence.Evaluate`), refused below `confluence_floor` (`confluence_below_floor`). Quality dimensions: `force`,
  `rejection`, `sweep_depth`, `entry_location`, `structure`, `room`, `major_handle`, plus the measurements `technical_rr`, `technical_risk_pips`,
  `session_momentum_atr` and `choch_in_sequence` (quality model `round_number.quality.v3`, RN-1).
- **Candle confirmation** (`analysis.require_candle_confirmation`, committed `false`, `docs/strategies/candle-confirmation.md`): applies to setup B only. With `true` the
  retest candle (or the next) must reject, as written above. With `false` the first candle that trades into the band is the trigger even when it is a plain touch; it
  then carries the evidence `candle_confirmation_optional`, no `m5_round_number_retest_rejection` and no `Reaction`, and nothing about the candle is fabricated
  (`WickRejection` and `Engulfing` stay false). Setup A's confirmation is its structure shift and is not affected.
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

## RN-1 validation (10 Nov 2026): what was measured, and what is only a hypothesis

RN-1 asked whether Round Number is technically sound, independent of the strategies it resembles, and profitable. **The sample cannot answer the third question**
and the first two are answered only where the code or a direct measurement decides them. Data: 11 days of production XAU M5 (30 Oct to 10 Nov, 2,000 closed
candles through `tools.capture_bars`, one regime), the five production opportunities since the strategy went live, and the two trades they produced. Outcomes below
are a historical simulation (`analysis-engine/cmd/rnstudy`), never P&L; they are not the executed results, which are reported separately.

**Units.** XAU `pip_size` is 0.1 (`config/instruments.yml`): one pip is $0.10, a $10 handle is 100 pips, the sweep window 5-40 pips is $0.50-$4, the entry band 15 pips is
$1.50, the stop envelope 50-70 pips is $5-$7. Round Number is a technique-lane strategy (`is_scalp=False` in the Algo Bot catalog, `stopEnvelopeTrend` in the engine),
so the 40-pip M1 scalp limit does not apply to it; the 70-pip cap is its refusal limit.

### Documented against executed

| | Documented | Executed |
|---|---|---|
| Setup A under the floor | live, floor 2 | 0 of 28 sweep/reclaim/structure-shift setups pass: A is worth 1 star on structure alone, and 2 only with a wick rejection **and** higher-timeframe alignment (7 and 6 raw points alone, 10 together); 6 had the wick, 10 the alignment, none both. One A was ever published (29 Oct, before the capture) |
| Setup B rejection | the retest must reject | with `require_candle_confirmation: false` (since 10 Nov) 33 of 37 B setups are plain touches; all 13 published in the 11 days are |
| Setup B gate | HTF, displacement, structure | what reaches 2 stars is exactly: higher-timeframe aligned + break candle displacement + the break through a confirmed swing (13 of 13) |
| Stop | beyond the sweep/retest extreme | the structural stop is $2.5-3.1 for 12 of 13 (median 0.48 ATR, one is $5.7); it is below the 50-pip floor for those 12, so **execution takes the 50-pip ($5) floor stop** (0.79 ATR) |
| Entry | retest band | A would be published a median 1.45 ATR above its band (a structure shift runs first), so it fills only on a pullback (8 of 28 never did in the simulation); published B is 0.06 ATR from its band |
| "Scalp" strategy | catalog title "XAU scalp strategies" | technique lane, 50-70 pip envelope |

### Is the level the information?

- **A round level is not rarer or richer in setups than any other level.** The same detector on grids shifted by $2, $3, $7 and $8 finds 22-36 B setups per grid
  against 31 at the round numbers, and 11-16 A setups against 14: price breaks, retests and sweeps arbitrary levels as often. A major handle (x50) is 26% of B setups
  (8 of 31) against 20% by count.
- **Outcome, round against matched levels** (historical, bid/ask candles, $0.30 spread, executed 50-pip stop, stop before target in a candle): B at round
  numbers +0.13R to the published target (n=31) against -0.24R at matched levels (n=103), difference +0.37R, day-block bootstrap 95% CI [-0.20, +0.90], the round grid
  ranks first of five (1 in 5 by chance). A: -0.19R (n=10) against -0.14R (n=47). **Inconclusive.**
- **Does round alignment help the other strategies?** Entry zones of other strategies within $1.50 of a $10 handle (44 of 154 distinct ideas) did better than the rest
  to their own target (+0.91R, day-block CI [+0.34, +1.43]) but not on the R ladder (+0.23R, [-0.02, +0.64]); the same rule on shifted grids gives +0.00, -0.51, -0.43,
  -0.08. First of five grids, one exit model of two, 11 days: **a hypothesis worth a forward test, not a finding.**
- **Overlap.** Of the 13 published B setups, 8 have a same-direction opportunity of another strategy within $1 of the entry while both are alive, but only 1 shares
  entry, stop and target (Key Level), 5 share Key Level's target (the same liquidity pool), none coincides with CRT (no XAU CRT at any floor in the window) or Break & Retest
  (4 at 1 star, none within $2). Round Number's stop is $1 under the handle, the neighbours' wider: a different invalidation, so not duplicates.

### Setup quality (what is measured)

- Published B, simulated: 13 trades, +0.50R to the target (CI [-0.38, +1.45], win 46%) or +0.08R on the ladder ([-0.42, +0.60]); before AE-CF1 (confirmation required) the same
  rule published 3 of them (+1.34R / +1.00R). Neither interval excludes zero or a loss. Per direction, session, volatility, M5 structure, handle and retest candle every
  cell has 1 to 22 setups: **INCONCLUSIVE**. 190 to 850 setups per arm are needed to see a 0.25R difference (standard deviation 0.9-1.8R).
- Executed: 2 trades (4 Nov BUY, 52-pip stop, stopped 5 minutes after the fill, -1R; 10 Nov SELL, TP2, +1.85R). The 3 other published opportunities were not traded: one never
  returned to its band, two were refused by exposure policy (`same_direction_stack_requires_tier_a`, `xau_opposite_position_too_close`). The Algo Bot found all five outside
  their band when it first saw them (5, 23, 34, 100 and 300 pips).
- Opposing room: 5 of 13 published B have a zone barrier ahead (`room_limited_by_barrier`) and 4 have under 1R of effective room; the detector does not check it (the quality
  and the Algo Bot do). The executed reward:risk (target over the 50-pip stop) is 1.06-5.9 (median 2.44), 1 of 13 below the Algo Bot's 1.15; at the 1-star floor 3 of 37 B are
  under 1.0 because `minimum_target_r` tests the structural stop, not the executed one. Not changed here (a change of admission).

### Defect fixed: quality v3

`structure` read any change of character of the last bars. For a stop run the confirming structure-shift candle is that change of character in 10 of the 11 setups that
have one, and `force` (its displacement) and the confluence `StructuralAgreement` already credit it. v3 counts a change of character only on a bar of the setup's own
sequence that `force` does not credit (stop run: from the sweep candle up to, not including, the structure shift; accepted break: after the break candle up to and
including the retest), and publishes `choch_in_sequence`. Effect on the replay: the 13 published setups keep their score (no change of character in their window); the
unfiltered population changes for 11 of 65 setups, ten stop runs fall by 0.08 to 0.09 and one break rises. Entry, stop, target, ids, evidence, admission and the other
strategies' envelopes are byte-identical.

### Not defects, but unproven (left alone)

`major_handle` adds up to 1/7 of `Overall` on the hypothesis that x50 handles matter (26% of setups; no outcome difference measurable). `sweep_depth` is a constant 0.5 for
every setup B. Both are weights, not errors; an unproven weight is not changed without evidence. `Overall` does not rank setups (Q5.3).

### Recommendation: keep as it is (option C), measure forward

No correctness defect that changes a published trade was found, the sample cannot separate an independent edge (H1), a context value (H2) and no value (H3), and a thesis
change needs approval. The two hypotheses worth the data: (1) setup B at round numbers against matched levels, (2) round alignment as evidence for other strategies. Both need the
Q1.1 outcome labeller or a longer capture; neither needs a new strategy. Rolling back, if ever decided, is the existing `enabled: false` pair listed above.
