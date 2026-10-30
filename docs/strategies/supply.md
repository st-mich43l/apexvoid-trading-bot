# Supply (supply v7)

`supply`, `v7`. Implemented in `analysis-engine/internal/strategy/supply`, self-contained and SELL only: it reads the closed bars and the facts of the frames in the market context and nothing else (`strategyutil`'s shared confirmation spine, `internal/zone` and `internal/reaction` are not used, and it shares no code with `demand`; `test/architecture/strategy_ownership_test.go` enforces it). Algorithm versions: `structure=v2`, `liquidity=v1`, `zone=v1`, `config=3`. The strategy's whole configuration is `analysis.strategies.supply`.

## Thesis

A supply zone found on M15 or H1 is where a strong move down began. When M5 trades back up into it, rejects it, and there is room below to the next opposing structure, the zone is defended. The strategy refuses the other case and says why: an entry with no room in front of it.

## Structure (`structure.go`)

M5 never defines a zone. `structure_timeframes: [M15, H1]` names the frames a zone is found on; each is judged on its own bars and its own ATR.

- **Departure**: a run of consecutive down candles that moved at least `displacement_atr_mult` x ATR, at least half of them with a body of at least `momentum_body_fraction` of their range.
- **Zone**: the range, wicks included, of the 3 candles before the departure's first candle. It exists from the close of the candle that ended the departure; a touch before that is its formation, not a reaction.
- **Dead or spent**: a close above the zone's high is a break; the zone survives it only when a close returns inside within 6 bars (a sweep), and at most twice; a zone touched more than 30 times, or more than `maximum_zone_touches` (2), is spent. Wider than `maximum_zone_atr` of its frame's ATR it is a region, not a zone.
- The entry band is the zone's proximal (lower) slice, clipped to `entry_max_width_price`.

The zone is bound to the M5 frame by time: its first touchable M5 bar is the first one opening at or after the close of the structure candle that ended the departure.

## Confirmation (`confirm.go`)

Unchanged from v6, and owned here: after a touch of the entry band from below, within `reaction_lookback_bars` (3), one of `wick_rejection`, `sweep_reclaim`, `rejection_choch`, `strong_reclaim`, `engulfing` (`engulfing_minimum_range_atr` x the M5 ATR). The candidate's `Reaction` carries the touch and confirmation bar times, `ReactionType "rejection"` and the pattern.

## Entry, stop, room, target (`decision.go`, `candidate.go`, `geometry.go`)

- **Entry**: price at the band or within `maximum_entry_atr` x the M5 ATR below its proximal edge, not above it.
- **Stop**: above the whole zone and above the extreme of the M5 bars from the touch to the confirmation, plus `invalidation_buffer_atr` x the M5 ATR (`invalidation_beyond_reaction_extreme` when the reaction's extreme set it). Over the instrument stop cap it is refused, never tightened; `stop_budget_floor` below the floor ([legacy-stop-budget](legacy-stop-budget.md)).
- **Room**: the obstacles below the entry, nearest first, are (a) every confirmed fractal swing low (`obstacle_pivot_bars` bars on each side, causal: a pivot is known only once its right-hand bars have closed) on M5 and on the structure frame that no M5 close has gone below since it was known, and (b) the low of the departure, from its first candle to the decision bar. The nearest obstacle is the target (`nearest_opposing_structure`) when it is at least `minimum_target_r` x the risk away; closer, the entry has no room and is refused (`no_target_room`). With no obstacle the target is `fallback_target_r` x the risk (`fixed_reward_risk`).
- **No extension rule**: a zone sits at the origin of its departure.
- **Selection**: among this strategy's confirmed zones on one bar, the most stars, then the nearest entry. The competition is among SELL zones only.

## Qualification and evidence

The shared confluence rubric (stars, floor `confluence_floor`) with factors the confirmation measurably showed (the wick, the engulfing body, a reclaim, a change of character, the higher timeframe bias, the zone's touches, a fibonacci touch). Evidence codes (unchanged): `m5_supply_zone_confirmed`, `m5_supply_zone_rejection_confirmed`, `htf_zone_<tf>`, `invalidation_beyond_reaction_extreme`, the stop budget code. Quality (informational, never a gate): the zone-family dimensions plus the measure `room_r`.

## Refusals

`strategy_refused{symbol, strategy="supply", structure_timeframe, reason}` (a Go counter; `reason` is `no_target_room`). A structure is counted once per evaluation; the counter never blocks one.

## Configuration

Every key of `analysis.strategies.supply` has a consumer in the package and every key the package reads is there; the engine injects only the instrument scale (`pip_size`, `entry_max_width_price`, the stop envelope) and the shared confluence rubric. `minimum_strength` (read only by the deleted resting path) and `minimum_target_distance_atr` (replaced by `minimum_target_r`) of v6 are gone; `higher_timeframes` became `structure_timeframes`.

## Measured against v6

See the pull request that introduced v7. Commit A moved the code and was proven identical to master by replay. One thing master did is not kept: **the v6 supply publisher took the best decision of the bar across supply AND demand zones and published it only if it was a SELL**, so a better demand reaction on the same bar suppressed a supply one (and the reverse). That coupling existed only because both strategies were one publisher; v7 selects among SELL zones only. The PR reports how many setups that releases. The figures are publish counts, not profit: no capture here knows what a published setup went on to do.

## History

### Spent zones (v3)

A confirmed reaction on a zone already touched more than `maximum_zone_touches` (2) times no longer publishes; the frozen Python publishers had no such limit (the parameter is opt-in, default 0 = unlimited, and the parity golden still proves the frozen contract with it off). Replay of the 2026-09-14 to 10-14 captures, hypothetical causal fills, per side and half (dev before 2026-10-07 / out of sample after): zones touched 3 or more times -0.06 / -0.21R; touched at most once +0.04 / +0.12R. Hypothetical mean over all filled setups moved from -0.01R to +0.03R, 95% interval [-0.10, +0.17]. The rule was chosen after looking at both halves, so the out-of-sample figure is a consistency check, not an independent test.

What this does not fix: Demand stays negative in the replay in every slice (fresh zones, either timeframe, either half); Supply is flat. A one-month, one-regime sample cannot say whether that is the detector or the month, so no further tuning was made. The technical-RR-below-1 geometry finding was left alone: technical RR does not separate outcomes in the replay (setups with RR below 1 averaged -0.07R, 2 or more -0.12R), so a filter on it would be a logic change without evidence.

**Reaction after formation (Q2).** The zone exists once its displacement leg has ended (`Zone.BreakIndex`); a touch or confirmation on the leg's own bars is its formation, not a reaction to it (222 of 1,742 replayed confirmed Supply opportunities were of that kind; a later valid reaction replaced 34).


- v3: the spent-zone limit; v4: reaction-anchored stop; v5: instrument stop budget; v6: M15/H1 structure ([reaction-anchored-stop](reaction-anchored-stop.md), [legacy-stop-budget](legacy-stop-budget.md), [htf-zone-structure](htf-zone-structure.md)).
- v7: the package owns its mechanism; M15/H1 structure only; room refusal; no competition with demand.

## Release

Deploy the Python bot first (or together with Go). If `config/analysis.yml` is rendered from `st-mich43l/ansible-library`, the `strategies.supply` block changed shape (`higher_timeframes` -> `structure_timeframes`, new keys, two removed); that repository was not edited. Opportunity ids change once at deploy (the strategy version is part of the id).
