# Range Scalp

`range_scalp` fades the probe of an edge inside an M5 range that is genuinely going sideways. It is a Go-native XAU strategy (`symbols: [XAU]`), live
(owner decision 2026-11-06, PR-S4; no observe-only, shadow or paper mode), and a member of the M1 scalp lane: stop 30 to 40 pips (SCALP-R40; wider is refused), 1R / break-even / 2R
exits, the 80-pip opposite separation. It shares nothing with the retired Range Box Scalp or Fade Scalp (no name, code or `range_lane` behaviour).

**Thesis.** Inside a range with no net direction, price that reaches the outer tenth of the range and closes back inside the inner 80 percent has been rejected
at the edge. The strategy sells the top edge probe and buys the bottom one at market.

## Rules (closed M5 bar `k`; only bars closed at or before `k`)

1. **Range.** `hi`, `lo` = highest high and lowest low of the `range_lookback_bars` (24) closed bars before `k`; `W = hi - lo`. `atr` = Wilder ATR(14) of M5 as known before `k`.
2. **Sideways.** All must hold: `efficiency = |C[k-1] - C[k-24]| / sum |C[i] - C[i-1]|` (the same bars) is below `maximum_efficiency_ratio` (0.30);
   `minimum_range_atr` (2.0) x `atr` <= `W` <= `maximum_range_atr` (6.0) x `atr`. The spread check belongs to the execution policy (`maximum_spread_pips`).
3. **Edge probe.** SELL: `H[k] >= hi - edge_fraction x W` and `lo + 0.10 W < C[k] < hi - 0.10 W`; BUY mirrors it with `L[k] <= lo + 0.10 W`. A bar that probes both edges has no side and is skipped.
   At most one signal per `cooldown_bars` (6) M5 bars and `maximum_signals_per_day` (8) per trading day (the session calendar's 17:00 New York rollover). Both are replayed forward from the start of the current trading day, so the answer does not depend on how many bars the engine holds.
4. **Entry.** Market at the signal close; entry band `[C[k] - w, C[k] + w]` with `w = entry_width_pips / 2` (3.0 pips, the XAU `deep_entry_minimum_width_pips`).
5. **Stop.** SELL `max(H[k], hi) + stop_buffer_atr (0.10) x atr`; BUY `min(L[k], lo) - 0.10 x atr`. Measured from the band's worst edge, then `ScalpStopPips(structural, 30, 70)`: narrower is widened to 30, **wider than 70 is refused**, never tightened. Invalidation `range_edge_lost`.
6. **Room and target (SQ-1, v2).** The room is finite and measured from the **worst planned entry** (the band's far edge), not the signal close: `room = min(objective, barrier)`.
   *Objective:* the far edge of the range (BUY `hi`, SELL `lo`). *Barrier:* the nearest live, side-correct wall ahead of the entry in the engine's canonical zone book (M5, M15 and H1 supply for a BUY, demand for a SELL), normalised by `internal/barrier`
   exactly as it is published to the Algo Bot, less the instrument's `barrier_buffer_atr` x the M5 ATR (XAU 0.15), once. A wall the entry sits inside has no room; no wall is skipped because it is "the same wall". Only zones that existed at the decision (created at or before the last closed bar) count, and a mitigated or invalidated zone does not.
   `ScalpTarget` then takes the first rung of `[2.0, 1.0]` that clears `minimum_net_target_pips` **and fits the room**; **if none fits the probe is refused** (`no_opposing_room` when the room is zero or behind the entry, `no_ladder_target_fits_room` otherwise). v1 passed `room = +Inf` and always offered the 2R price. The range midpoint is evidence only. The execution ladder books 50% at 1R, moves the stop to break-even and books 50% at 2R (1R only when Go's target offers only 1R).
7. **Expiry** 15 minutes (`expiry_minutes`).

Evidence `m5_range_sideways`, `m5_range_edge_probe`, `m5_range_close_inside`; measures `range_width_atr`, `efficiency_ratio`, `probe_depth_atr`, `entry_range_position`, `range_midpoint`, `range_high`, `range_low`, `available_room_pips`, `objective_room_pips`,
`barrier_room_pips` (when a wall lies ahead), `room_limited_by_barrier`, `room_reward_risk`, `target_pips`, `target_r`, `utc_hour`.

**Quality (v2, ranking only, never a gate; `quality_ranking` stays false).** The overall is the mean of the `score_*` dimensions, each in [0, 1], each a different fact:

| Dimension | Definition |
|---|---|
| `range_integrity` | `Fall(efficiency; 0, maximum_efficiency_ratio)`: a drifting range is a trend in a box |
| `edge_probe` | the excursion into the edge band in ATR, `Peak(d; 0, 0.10, 0.60, 1.50)`: a scrape is noise, a few tenths of an ATR is a healthy probe, beyond 1.5 ATR is a breakout with a wick and scores 0 (depth is not rewarded indefinitely) |
| `entry_location` | where the worst entry sits as a fraction of the range from the edge it fades, `Fall(p; 0.10, 0.50)` |
| `room_reward_risk` | the available room (above) against the stop, `Ramp(room / stop; 1, 3)`: 1R scores 0, 3R scores 1 |
| `structural_support` | 1 only when a live wall of the trade's own side overlaps the probe's span; otherwise the dimension is absent |

v1 scored `sideways` and `edge_probe` (the raw probe depth in ATR, unbounded and never penalised) only.

## Parameters (`analysis.strategies.range_scalp`)

`range_lookback_bars` 24, `maximum_efficiency_ratio` 0.30, `minimum_range_atr` 2.0, `maximum_range_atr` 6.0, `edge_fraction` 0.10, `stop_buffer_atr` 0.10, `entry_width_pips` 3.0,
`cooldown_bars` 6, `maximum_signals_per_day` 8, `expiry_minutes` 15, `atr_length` 14, `atr_window_bars` 140, `reward_risk_ladder` [2.0, 1.0]. The pip size and the stop and target book
(`stop_minimum_pips`, `stop_maximum_pips`, `minimum_net_target_pips`) come from `auto_algo.strategies.scalping.*`; the barrier policy (`barrier_buffer_atr`, `barrier_max_width_atr`, `barrier_max_width_pips`) from the leaves the Algo Bot's target-room check reads (`auto_algo.actionability.target_room.barrier_buffer_atr` with the instrument override, `execution.policy.execution_zone_max_width_*`); the rollover hour from the session calendar. A missing or invalid value fails startup.

## SQ-1 replay (real XAU capture, M5, 2-9 Nov 2026), v1 against v2

26 probes before, 24 after. The two refused are `no_opposing_room`: a wall the entry sat in or against. Of the 24, 22 keep the 2R price and 2 are limited to 1R: one by the far edge of its range (49.7 pips of room against a 30-pip stop), one by a barrier (34.5 pips after the buffer). Stops and entries are unchanged. The quality overall moves from 0.13 to 0.73 (median 0.45) to 0.51 to 0.99 (median 0.85) because the dimensions are now normalised; it ranks nothing. Counts only: no outcome was simulated.

## Replay (real XAU capture, M5 29 Oct - 6 Nov 2026), v1

42 candidates in 6 trading days (4 to 9 a day, never above the cap), stops 30.0 to 67.3 pips (median 43.9), 30 of the 42 between 00:00 and 08:59 UTC (Asia and early London), the rest in London, NY and the evening. All 42 adapt, pass admission and
the execution policy, with the 1R and 2R ladder. Counts only: no outcome was simulated.

## Expected performance (owner-supplied research, not measured here and not a gate)

XAUUSD M1 2020-01 to 2026-11, the same stop band and ladder, spread $0.30, in-sample 2020-23, out-of-sample 2024-26: about 10 trades a day before the cap, win rate about 50% at 1R,
gross E[R] OOS +0.01 +/- 0.02, **net E[R] -0.20 in-sample and -0.11 out-of-sample** (Asia hours only: gross +0.02, net -0.18 / -0.10). The strategy is a live demo experiment; measure it from
`auto_trade_results` with the volume-weighted R of `app/scalping/outcomes.py`.

Rollback: `analysis.strategies.range_scalp.enabled: false` and `auto_algo.strategies.scalping.mode` (shared with Impulse Pullback), then restart analysis-engine.
