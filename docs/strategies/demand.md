# Demand (demand v7)

`demand`, `v7`. Implemented in `analysis-engine/internal/strategy/demand`, self-contained and BUY only: it reads the closed bars and the facts of the frames in the market context and nothing else (`strategyutil`'s shared confirmation spine, `internal/zone` and `internal/reaction` are not used, and it shares no code with `supply`; `test/architecture` enforces both). Algorithm versions: `structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`. The strategy's whole configuration is `analysis.strategies.demand`.

## Thesis

A demand zone found on M15 or H1 is where a strong move up began. When M5 trades back down into it, rejects it, and there is room above to the next opposing structure, the zone is defended. The strategy refuses the other case and says why: an entry with no room in front of it.

## Structure (`structure.go`)

M5 never defines a zone. `structure_timeframes: [M15, H1]` names the frames a zone is found on; each is judged on its own bars and its own ATR.

- **Departure**: a run of consecutive up candles that moved at least `displacement_atr_mult` x ATR, at least half of them with a body of at least `momentum_body_fraction` of their range.
- **Zone**: the range, wicks included, of the 3 candles before the departure's first candle. It exists from the close of the candle that ended the departure; a touch before that is its formation, not a reaction.
- **Dead or spent**: a close below the zone's low is a break; the zone survives it only when a close returns inside within 6 bars (a sweep), and at most twice; a zone touched more than 30 times, or more than `maximum_zone_touches` (2), is spent. Wider than `maximum_zone_atr` of its frame's ATR it is a region, not a zone.
- The entry band is the zone's proximal (upper) slice, clipped to `entry_max_width_price`.

The zone is bound to the M5 frame by time: its first touchable M5 bar is the first one opening at or after the close of the structure candle that ended the departure. A zone whose origin has left the 150-bar M5 window stays tradable for as long as its own frame holds it ([analysis-engine-windows](../analysis-engine-windows.md)); its identity and `formed_at` come from the origin bar's time.

## Confirmation (`confirm.go`)

Owned here: after a touch of the entry band from above, within `reaction_lookback_bars` (3), one of `wick_rejection`, `sweep_reclaim`, `rejection_choch`, `strong_reclaim`, `engulfing` (`engulfing_minimum_range_atr` x the M5 ATR). The candidate's `Reaction` carries the touch and confirmation bar times, `ReactionType "rejection"` and the pattern.

## Entry, stop, room, target (`decision.go`, `candidate.go`, `geometry.go`)

- **Entry**: price at the band or within `maximum_entry_atr` x the M5 ATR above its proximal edge, not below it.
- **Stop**: below the whole zone and below the extreme of the M5 bars from the touch to the confirmation, minus `invalidation_buffer_atr` x the M5 ATR (`invalidation_beyond_reaction_extreme` when the reaction's extreme set it). Over the instrument stop cap it is refused, never tightened; `stop_budget_floor` below the floor ([legacy-stop-budget](legacy-stop-budget.md)).
- **Room**: the obstacles above the entry, nearest first, are (a) every confirmed fractal swing high (`obstacle_pivot_bars` bars on each side, causal: a pivot is known only once its right-hand bars have closed) on M5 and on the structure frame that no M5 close has gone above since it was known, and (b) the high of the departure, from its first candle to the decision bar. The nearest obstacle is the target (`nearest_opposing_structure`) when it is at least `minimum_target_r` x the risk away; closer, the entry has no room and is refused (`no_target_room`). With no obstacle the target is `fallback_target_r` x the risk (`fixed_reward_risk`).
- **No extension rule**: a zone sits at the origin of its departure.
- **Selection**: among this strategy's confirmed zones on one bar, the most stars, then the nearest entry. The competition is among BUY zones only.

## Zone score and premium/discount (`zonescore.go`, `dealing.go`)

Points per fact: fresh (never touched) 3 · touched once 1 · a demand base 1.5 · a key level inside 2 · a round number inside 1 · a sell-side pool just below 2 · each distinct unswept session low in or just below 2 · the zone wholly in the discount half of the dealing range `pd_position_score` (2) · a grade-A bullish sweep into the zone 2 · inside a demand zone of this strategy on a higher frame 3 · an unbroken trendline through it 1.5. The facts (key levels, pools, session levels, sweeps, trendlines, `round_step`) are read from the frames. The zone's frame's dealing range is built from its last confirmed swing high and low (`structure_pivot_bars`) and extended by every high and low since: it always contains price ([premium-discount](premium-discount.md)). Premium/discount is a score input, never a gate.

## Qualification and evidence

The shared confluence rubric (stars, floor `confluence_floor`) with factors the confirmation measurably showed (the wick, the engulfing body, a reclaim, a change of character, the higher timeframe bias, the zone's touches, a fibonacci touch). Evidence codes: `m5_demand_zone_confirmed`, `m5_demand_zone_rejection_confirmed`, `htf_zone_<tf>`, `m5_demand_zone_discount` (the points were awarded), `invalidation_beyond_reaction_extreme`, the stop budget code. Quality (informational, never a gate): the zone-family dimensions plus the measures `room_r` and `pd_position`.

## Refusals

`strategy_refused{symbol, strategy="demand", structure_timeframe, reason}` (a Go counter; `reason` is `no_target_room`). A structure is counted once per evaluation; the counter never blocks one.

## Configuration

Every key of `analysis.strategies.demand` has a consumer in the package and every key the package reads is there; the engine injects only the instrument scale (`pip_size`, `round_step`, `entry_max_width_price`, the stop envelope) and the shared confluence rubric. `minimum_strength` (read only by the deleted resting path) and `minimum_target_distance_atr` (replaced by `minimum_target_r`) of v6 are gone; `higher_timeframes` became `structure_timeframes`.

## Measured against v6

See the pull request that introduced v7. Commit A moved the code and was proven identical to master by replay. One thing master did is not kept: **the v6 demand publisher took the best decision of the bar across supply AND demand zones and published it only if it was a BUY**, so a stronger supply reaction on the same bar suppressed a demand setup (supply v7 removed the mirror of this). v7 selects among BUY zones only. The figures are publish counts, not profit: no capture here knows what a published setup went on to do.

## History

### Spent zones (v3)

A confirmed reaction on a zone already touched more than `maximum_zone_touches` (2) times no longer publishes; the frozen Python publishers had no such limit (the parameter is opt-in, default 0 = unlimited, and the parity golden still proves the frozen contract with it off). Replay of the 2026-09-14 to 10-14 captures, hypothetical causal fills, per side and half (dev before 2026-10-07 / out of sample after): zones touched 3 or more times -0.31 / -0.39R; touched at most once -0.08 / -0.22R. Hypothetical mean over all filled setups moved from -0.18R to -0.17R, 95% interval [-0.29, -0.04]. The rule was chosen after looking at both halves, so the out-of-sample figure is a consistency check, not an independent test.

What this does not fix: Demand stays negative in the replay in every slice (fresh zones, either timeframe, either half); Supply is flat. A one-month, one-regime sample cannot say whether that is the detector or the month, so no further tuning was made. The technical-RR-below-1 geometry finding was left alone: technical RR does not separate outcomes in the replay (setups with RR below 1 averaged -0.07R, 2 or more -0.12R), so a filter on it would be a logic change without evidence.

**Reaction after formation (Q2).** The zone exists once its displacement leg has ended (`Zone.BreakIndex`); a touch or confirmation on the leg's own bars is its formation, not a reaction to it (252 of 1,894 replayed confirmed Demand opportunities were of that kind; a later valid reaction replaced 55).


- v3: the spent-zone limit; v4: reaction-anchored stop; v5: instrument stop budget; v6: M15/H1 structure ([reaction-anchored-stop](reaction-anchored-stop.md), [legacy-stop-budget](legacy-stop-budget.md), [htf-zone-structure](htf-zone-structure.md)).
- v7: the package owns its mechanism and its zone score; M15/H1 structure only; room refusal; no competition with supply; the corrected dealing range.

## Release

Deploy the Python bot first (or together with Go). If `config/analysis.yml` is rendered from `st-mich43l/ansible-library`, the `strategies.demand` block changed shape (`higher_timeframes` -> `structure_timeframes`, new keys, two removed); that repository was not edited. Opportunity ids change once at deploy (the strategy version is part of the id).
