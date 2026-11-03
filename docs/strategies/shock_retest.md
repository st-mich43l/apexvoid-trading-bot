# Shock Retest Strategy V2

`shock_retest` trades the **first pullback after a volatility shock**. It is a Go-native XAU strategy (`symbols: [XAU]`), enabled in the live Go opportunity stream;
Algo Bot applies execution policy before any TradePlan is published. It has no frozen Python predecessor.

## Thesis and evidence

Gold keeps adjusting for longer than five minutes after macro shocks: Awartani, Hussain and Virk (2024), *International Review of Financial Analysis*, and Cai,
Cheung and Wong (2001), *Journal of Futures Markets*. After a shock leg, the first pullback into the 50 to 61.8 percent retracement of the leg, rejected in the
shock direction, continues toward the shock extreme. The shock is **price-defined**: the engine has no economic calendar, and the strategy must behave identically
live and in replay. Calendar alignment is deferred (it needs a cross-service contract). A published hypothesis; nothing here claims the rule earns.

## Rules

All logic is written for an upward shock and a downward one is the same sequence on mirrored candles.

1. **Shock.** One closed M5 candle with range >= `shock_range_atr` (3) x ATR and body ratio >= `minimum_shock_body_ratio` (0.6), **or** two consecutive bullish closes
   whose combined leg (first open to second close) >= `shock_leg_atr` (4) x ATR. The ATR is measured on the window **before** the shock (the shock never inflates its
   own yardstick). Origin = the leg's first open; extreme = the leg's high.
2. **Settle.** Nothing for `settle_bars` (1) closed candles; the extreme may extend meanwhile. A further shock before the pullback continues the same leg (one move, one
   episode: the origin stays, the settle restarts).
3. **Retest band.** `[extreme - 0.618 x leg, extreme - 0.5 x leg]` (the retracement is the definition of the thesis, not a parameter). The **first** entry into the band within
   `retest_max_bars` (24) after the settle is the retest, else `no_retest`. A close beyond the 78.6 percent retracement first ends it (`retest_too_deep`).
4. **Confirmation.** The first candle in the band, or the next, that closes in the shock direction with a lower wick >= `minimum_wick_ratio` (0.4) or a bullish engulfing
   close; neither means `retest_not_rejected`. Not older than `confirmation_max_age_bars` (2).
5. **Invalidation (v2).** Behind the retest's own rejection extreme: the lowest low of the M5 candles from the first entry into the band to the confirming candle, minus
   `max(stop_buffer_pips x pip, stop_buffer_atr x ATR)`. It is never inside the band: when that extreme is above the band's far edge (61.8 percent) the far edge is the anchor.
   The leg origin is no longer used for the stop (v1 put it behind the origin, which made the reward:risk below 1 by construction). The stop budget refuses one above the
   instrument cap and widens one below the floor (`stop_budget_floor`), as for every strategy.
6. **Target (v2).** The shock extreme (`shock_extreme`) when it is at least `minimum_target_r` x risk away; otherwise the 1.272 extension of the leg
   (`shock_extension_1272`, `origin + 1.272 x leg`) when that is; otherwise `no_target_room`. The risk is the technical one (entry to that stop), before the stop budget.
7. **Counter shock.** A shock in the opposite direction before the confirmation ends the episode (`counter_shock`).
8. **Measured confluence factors:** `DisplacementGrade` (the leg in ATR >= `displacement_grade_atr`), `WickRejection`, `HTFAligned`. No structural-agreement or session fact is set.

Entry band = the retest band; reaction pattern `wick_rejection` or `engulfing`; evidence `m5_volatility_shock`, `m5_shock_first_retest`, `m5_shock_retest_rejection`;
identity `StructuralID = technique:shock_retest:<side>:<origin time>`. Session momentum, the stop budget and the confluence floor work as in [round_number](round_number.md).

## The v1 specification error, resolved

v1 entered at the 50 percent retracement with the stop behind the leg origin and the target at the extreme, so for a leg `L` the reward was `0.5 L` and the risk `0.5 L` plus the buffer:
the technical reward:risk was always below 1 and every confirmed retest was refused (`no_target_room`); the strategy was live and could not publish. v2 anchors the stop to the
retest's own rejection extreme and adds the extension as a second target. On the real XAU captures (below) v1 published **0** and refused 14 confirmed retests for room; v2 publishes
**5 distinct setups** (11 sightings across overlapping captures), all targeting the shock extreme. No test of the old geometry remains
(`TestTheShippedRoomRequirementCannotBeMet` is deleted); `TestWhenTheExtremeLeavesTooLittleRoomTheTargetIsTheExtension` and `TestWithNeitherTargetOfferingTheRoomTheSetupIsRefused` pin the new rule.

**Real-bar replay** (`analysis-engine/test/scalpstrategies`, seven XAU captures including the fresh `scalpwindow-xau-20261103`, publish counts and refusals, not P&L):

| | v1 | v2 |
|---|---|---|
| published (sightings) | 0 | 11 (5 distinct: 14 Sep SELL, 29 Sep BUY, 7 Oct SELL, 8 Oct BUY, 12 Oct BUY) |
| refused `no_target_room` | 14 | 0 |
| refused `confluence_below_floor` / `invalidated_after_confirmation` (new stop floor) | 0 / 0 | 3 / 2 |
| refused `retest_not_rejected`, `retest_too_deep`, `no_retest`, `counter_shock` | 33, 19, 7, 4 | 33, 19, 7, 4 (unchanged) |

The five distinct setups carry technical risks of 33 to 69 pips (four under the 50 pip floor, so published with `stop_budget_floor`; one `stop_budget_structural`) and every one targets the
shock extreme: the extension target does not occur on these captures (it is exercised by a unit fixture). Against the stop the executor really uses (the floor widens 33 to 39 pip stops to 50),
the first target still offers 2.4R to 3.8R. **None of the five is after 14 October**: the post-29 October window (`scalpwindow`) holds one confirmed retest that the confluence floor refused,
so v2 would also have been quiet live. A rare strategy (about one setup in a week of gold), not a silent one.

## Rejection reasons

`no_retest`, `retest_too_deep`, `retest_not_rejected`, `counter_shock`, `invalidated_after_confirmation`, `price_through_entry`, `invalid_stop_geometry`,
`no_target_room`, `target_already_reached`, `risk_exceeds_execution_envelope`, `confluence_below_floor`, `atr_unavailable`, `insufficient_m5_history`.
Target labels: `shock_extreme`, `shock_extension_1272`. Invalidation label: `retest_extreme_lost`.

## Release and rollback

New strategy id, so new opportunity ids; no existing id changes. Rollback: `analysis.strategies.shock_retest.enabled: false` (the entry must stay) and
`auto_algo.strategies.technique.shock_retest.enabled: false`, then restart analysis-engine and algo-bot.
