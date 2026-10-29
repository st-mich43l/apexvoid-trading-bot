# Shock Retest Strategy V1

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
5. **Invalidation.** The lower of the retest swing low and the leg origin, minus `max(stop_buffer_pips x pip, stop_buffer_atr x ATR)`. The stop budget refuses one above the
   instrument cap.
6. **Target.** The shock extreme, which must give at least `minimum_target_r` x risk, else `no_target_room`.
7. **Counter shock.** A shock in the opposite direction before the confirmation ends the episode (`counter_shock`).
8. **Measured confluence factors:** `DisplacementGrade` (the leg in ATR >= `displacement_grade_atr`), `WickRejection`, `HTFAligned`. No structural-agreement or session fact is set.

Entry band = the retest band; reaction pattern `wick_rejection` or `engulfing`; evidence `m5_volatility_shock`, `m5_shock_first_retest`, `m5_shock_retest_rejection`;
identity `StructuralID = technique:shock_retest:<side>:<origin time>`. Session momentum, the stop budget and the confluence floor work as in [round_number](round_number.md).

## Open issue: the shipped room requirement cannot be met

As specified, the entry is the proximal edge of the band (the 50 percent retracement), the stop is behind the leg origin and the target is the shock extreme. For a leg
`L` the reward is `0.5 L` and the risk `0.5 L` plus the buffer, so the technical reward:risk is **always below 1** (about 0.9 for a 100 to 140 pip leg). With
`minimum_target_r: 1.0` every confirmed retest is refused with `no_target_room`: the strategy is enabled and live but cannot publish
(`TestTheShippedRoomRequirementCannotBeMet`). Lowering the requirement is not enough: on the committed captures the confirmed retests that remain are legs of 100 to 200
pips whose honest stop exceeds the 70 pip cap, so the stop budget refuses them, and the Algo Bot's fixed 1R to 4R ladder needs at least 1R of room regardless. Making it
tradeable is a geometry decision for the owner (a stop at the retest swing instead of the origin, a target beyond the extreme, or a deeper entry); none of them is made here.

## Rejection reasons

`no_retest`, `retest_too_deep`, `retest_not_rejected`, `counter_shock`, `invalidated_after_confirmation`, `price_through_entry`, `invalid_stop_geometry`,
`no_target_room`, `target_already_reached`, `risk_exceeds_execution_envelope`, `confluence_below_floor`, `atr_unavailable`, `insufficient_m5_history`.

## Release and rollback

New strategy id, so new opportunity ids; no existing id changes. Rollback: `analysis.strategies.shock_retest.enabled: false` (the entry must stay) and
`auto_algo.strategies.technique.shock_retest.enabled: false`, then restart analysis-engine and algo-bot.
