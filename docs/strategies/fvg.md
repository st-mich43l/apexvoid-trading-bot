# Fair Value Gap (fvg v7)

## Strategy ID / version

`fvg`, `v7`. Implemented in `analysis-engine/internal/strategy/fvg`, self-contained: it reads the closed bars and the facts of the frames in the market context and nothing else (`strategyutil`'s shared confirmation spine, `internal/zone` and `internal/reaction` are not used; `test/architecture/strategy_ownership_test.go` enforces it). Algorithm versions: `structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`. The strategy's whole configuration is `analysis.strategies.fvg`.

## Thesis

A fair value gap found on M15 or H1 is a standing imbalance. When M5 trades back into it, rejects it with a measured candle, and there is room to the next opposing structure, the imbalance is defended. The strategy refuses the other cases and says why: a gap that was closed through, a rejection that never came or is stale, an entry with no room in front of it, and an entry at the extreme of a stretched leg (the gap a leg printed on its way up, entered at the top).

## Structure (`structure.go`)

M5 never defines a gap. `structure_timeframes: [M15, H1]` names the frames a gap is found on; each is judged on its own bars and its own ATR as of the candle that completed it (causal).

1. **Three-candle gap**: bullish when the first candle's high is below the third candle's low (mirror for bearish). The structural bounds are the whole gap; the **entry band** is its proximal slice, clipped to `entry_max_width_price` (the instrument's, injected).
2. **Definition**: width at least `minimum_gap_atr` of the frame's ATR (`gap_small`); the middle candle's body at least `minimum_displacement_body_atr` of that ATR and `minimum_displacement_body_ratio` of its own range (`displacement_weak`); and it closed in the gap's direction (`displacement_direction`). Same values for every instrument (the contract iFVG v3 uses).
3. **Dead once closed through** (`gap_closed_through`): any close of the gap's own frame after its third candle beyond the gap's far edge by more than `close_through_buffer_atr` of that bar's ATR. No reclaim forgiveness: a wick that closes back inside is the sweep, a close is the break. Judged before the fill test, which a close beyond the far edge always satisfies.
4. **Lifecycle** (unchanged from v6): a gap is not tradable when it is under one pip, wider than `maximum_zone_atr`, fully filled by a wick (a later low at or below a bullish band's low), or has taken more than 30 touches. A gap only exists from its third candle's close: a touch or rejection on that candle or earlier is its formation, not a reaction.

The M15/H1 gap is bound to the M5 frame by time: its first touchable M5 bar is the first one opening at or after the close of the candle that completed it.

## Confirmation (`confirm.go`)

On closed M5 bars after the gap exists:

- **Touch**: a bar that trades into the entry band and does not close beyond the gap's far edge.
- **Rejection**, within `confirmation_window_bars` (3) of the touch, the touch bar included, one bar that either
  - `wick_rejection`: closes beyond the band's proximal edge (a BUY above the band high) with a wick, in the trade direction, of at least `minimum_wick_ratio` of its range; or
  - `engulfing`: is an engulfing close of the prior bar with a range of at least `engulfing_minimum_range_atr` x the M5 ATR.
- The rejection must not be older than `confirmation_max_age_bars` (2) at the decision bar.

A touch without a rejection is counted `no_rejection`; a rejection that is too old, `stale_confirmation`. Sweep-reclaim, strong-reclaim and rejection-CHoCH are not confirmations of a gap. The candidate's `Reaction` carries the touch and rejection bar times, `ReactionType "rejection"` and the pattern.

## Entry, stop, room, target (`decision.go`, `candidate.go`, `geometry.go`)

- **Entry**: the entry band must be valid at the latest close (price at the band or within `maximum_entry_atr` x the M5 ATR of its proximal edge, not through it).
- **Stop**: beyond the whole gap and beyond the extreme of the M5 bars from the touch to the rejection, plus `invalidation_buffer_atr` x the M5 ATR (`invalidation_beyond_reaction_extreme` when the reaction's extreme set it). Over the instrument stop cap it is refused, never tightened; `stop_budget_*` evidence otherwise.
- **Room**: the obstacles in front of the entry, nearest first, are (a) every confirmed fractal swing (`obstacle_pivot_bars` bars on each side, causal: a pivot is known only once its right-hand bars have closed) on M5 and on the structure frame that no M5 close has gone through since it was known, and (b) the impulse extreme, the highest high (lowest low) since the gap's first candle. The nearest obstacle is the target (`nearest_opposing_structure`) when it is at least `minimum_target_r` x the risk away; closer, the entry has no room and is refused (`no_target_room`). With no obstacle the target is `fallback_target_r` x the risk (`fixed_reward_risk`).
- **Leg extension** (`entry_at_leg_extreme`): the leg runs from the lowest low of the structure frame from `leg_lookback_bars` before the gap's first candle to the impulse extreme. A leg of at least `extension_minimum_leg_atr` of the structure frame's ATR whose extreme the entry has retraced by less than `minimum_leg_retracement` is refused. It is not a premium/discount gate: it applies only to a stretched leg and only to the gap that leg printed.
- When both room and extension apply, both are counted.

## Qualification and evidence

The shared confluence rubric (stars, floor `confluence_floor`) with factors that are measured, not assumed: `WickRejection` (the wick branch), `DisplacementGrade` (the engulfing branch, or the gap's middle candle with a body of at least one structure ATR), `HTFAligned`, `Touches`, `FibTouch` (`fibonacci_enabled`) and `StructuralAgreement` only when the rejecting bar closed through the most recent M5 swing in the trade direction that was already confirmed before it.

Evidence codes (unchanged): `m5_fvg_zone_confirmed`, `m5_fvg_rejection_confirmed`, `htf_zone_<tf>`, `invalidation_beyond_reaction_extreme`, the stop budget code. Quality (informational, never a gate): the zone-family dimensions as before plus the measures `room_r`, `leg_retracement` and `formation_steepness_atr_per_bar` (|third close - first open| / 3 / the structure frame's ATR).

## Refusals

`strategy_refused{symbol, strategy="fvg", structure_timeframe, reason}` (a Go counter; `reason` is `gap_small`, `displacement_weak`, `displacement_direction`, `gap_closed_through`, `no_rejection`, `stale_confirmation`, `no_target_room` or `entry_at_leg_extreme`). A structure is counted once per evaluation; the counter never blocks one. It replaces `fvg_definition_refused`.

## Configuration

Every key of `analysis.strategies.fvg` has a consumer in the package and every key the package reads is there; the engine injects only the instrument scale (`pip_size`, `entry_max_width_price`, the stop envelope) and the shared confluence rubric. `minimum_strength` and `epsilon_atr_fraction` of v6 were dead keys on this path and are gone.

## Measured against v6

See the pull request that introduced v7: commit A moved the code and was proven identical to master by replay; commit B changed behaviour. The 29 Oct 2026 GBPJPY BUY (M15 gap 210.856-211.006, engulfing at 15:40, stop 210.803, target 211.176) is a regression test (`test/fvgv7`): v6 publishes it, v7 refuses it (`no_target_room` and `entry_at_leg_extreme`). The figures are publish counts, not profit: no capture here knows what a published setup went on to do.

## History

- v3-v5: reaction-anchored stop, instrument stop budget, M15/H1 structure ([reaction-anchored-stop](reaction-anchored-stop.md), [legacy-stop-budget](legacy-stop-budget.md), [htf-zone-structure](htf-zone-structure.md)).
- v6 (owner report 2026-10-28): the gap definition. Trades were labelled FVG where the chart shows no gap; measured on XAU, 68% of M15 gaps and all H1 gaps were under 0.5 ATR of their own frame. The thresholds (0.5 / 0.5 / 0.5) are the iFVG v3 contract, nothing was fitted to results. Of 1,266 replayed v5 candidates over 613 distinct gaps, 453 (36%) were refused.
- v7: the package owns its mechanism; M15/H1 structure only; close-through death; measured confirmation; room and leg-extension refusals.

## Contract and card (display only)

Go publishes an optional `structure: {timeframe, low, high, formed_at}` on the candidate and on `analysis.opportunity.v1` (`contracts/analysis/opportunity-v1.schema.json`, `kafka.StructurePayload`) for the zone family (`supply`, `demand`, `order_block`, `fvg`), on every timeframe including M5: the whole structure on its own timeframe (the whole gap, not the clipped entry band) and `formed_at`, the open time of the bar that completed it (the third candle of a gap; for a higher-timeframe structure that frame's bar time, carried by `techniquezone.Instance.OriginTime`). `opportunity.Candidate.Validate` rejects an invalid timeframe or an inverted range. Documented in `docs/redis-contract.md`.

Python (`app/analysis_client/models.py` `StructureZone`, optional so old events still parse) copies it to `StrategyMatch` (`structure_timeframe/low/high/formed_at`, optional) and the root card (`app/autotrade/setup_card.structure_lines`) prints one line under the setup name:

```
📈 BUY · FVG · ⭐⭐
Structure: FVG M15 · 4,152.30–4,154.40 · formed 14:15 UTC
Order zone: 4,153.60–4,154.40
```

The name is the card's own setup label (`FVG`, `Supply` for a SELL, `Demand` for a BUY, `Order Block`), prices use the instrument's card digits, and `Order zone` appears only when the zone the plan trades differs from the structure. An event, cached match or card without the block renders exactly as before. Display only: strategy identity, stats keys, policy and results do not read it, and an already-published card still matches its setup (`forming_card_matches_strategy`, #814).


## Release

Deploy the Python bot first (or together with Go): its envelope models forbid unknown fields and already parse the optional `structure` block. If `config/analysis.yml` is rendered from `st-mich43l/ansible-library`, the `strategies.fvg` block changed shape (keys renamed `confirmed_minimum_*` -> `minimum_*`, `higher_timeframes` -> `structure_timeframes`, new keys); that repository was not edited. Opportunity ids change once at deploy (the strategy version is part of the id).
