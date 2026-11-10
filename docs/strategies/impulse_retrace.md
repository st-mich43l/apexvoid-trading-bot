# Impulse Retrace Scalp

`impulse_retrace` buys (or sells) the first retrace to the midpoint of one forceful M5 displacement bar, in the direction of the impulse. It is a Go-native XAU strategy
(`symbols: [XAU]`), live (owner decision 2026-11-06, PR-S4; no observe-only, shadow or paper mode), and a member of the M1 scalp lane (stop 30 to 40 pips (SCALP-R40; wider is refused), 1R / break-even / 2R,
80-pip opposite separation). **It trades every session: there is no session, clock or MAD-phase condition anywhere in it.** The session is published as a label only.

Impulse Pullback (a 4 ATR displacement plus a structural reference plus M1 confirmation) is unchanged and is not gated by any clock either; it is rare because of its filters.

## Rules (closed M5 bar `k`; only bars closed at or before `k`)

1. **Impulse.** `|C[k] - O[k]| >= impulse_body_atr (2.0) x atr` (Wilder ATR(14) of M5 known before `k`); direction = the sign of the body. One impulse per `cooldown_bars` (12) bars: the impulses of the last day are replayed forward and the first of each cluster stands.
2. **Geometry.** BUY `origin = L[k]`, `extreme = H[k]`; SELL `origin = H[k]`, `extreme = L[k]`. Entry level = `extreme` back toward `origin` by `retrace_fraction` (0.50) of the bar's range. Entry band (limit side) BUY `[lim, lim + w]`, SELL `[lim - w, lim]`, `w = entry_width_pips` (3.0).
3. **Stop.** BUY `origin - stop_buffer_atr (0.10) x atr`, SELL `origin + 0.10 x atr`, measured from the band's worst edge, then `ScalpStopPips(structural, 30, 70)`: widened to 30, **refused above 70**. Invalidation `impulse_origin_lost`: a closed M5 bar through it before a fill invalidates the setup (the book's existing close-through rule).
4. **Room and target (SQ-1, v2).** The room is finite and measured from the **worst planned entry** (the limit band's far edge): `room = min(horizon, barrier)`.
   *Horizon:* the equal-leg projection of the impulse bar beyond its extreme, `extreme +/- room_projection (1.0) x range`. The impulse extreme itself is the first objective and is published as `extreme_room_pips`, but it cannot be the end of the room: with the entry at the retrace and the stop beyond the origin the extreme is always closer than the stop (room to the extreme is `0.5 x range - w`; the stop is at least `0.5 x range + w`), so no rung of the ladder could ever fit it.
   *Barrier:* the nearest live, side-correct wall ahead of the entry in the engine's canonical zone book (as for Range Scalp: `internal/barrier`, the instrument's `barrier_buffer_atr` once), excluding any zone born at or after the impulse bar (its own structure) or created after the decision. A wall the entry sits inside has no room.
   `ScalpTarget` takes the first rung of `[2.0, 1.0]` that clears `minimum_net_target_pips` and fits the room; **if none fits the impulse is refused** (`no_opposing_room`, `no_ladder_target_fits_room`). v1 passed `room = +Inf`. No rejection candle is needed or waited for (`analysis.require_candle_confirmation` is irrelevant: the entry is a pending limit).
5. **Expiry.** `entry_window_minutes` (60) after the impulse bar's close; unfilled it expires.

Evidence `m5_impulse_bar`, `m5_retrace_limit`, and `session_asia` / `session_london` / `session_ny` as a label; measures `impulse_body_atr`, `impulse_body_dominance`, `retrace_fraction`, `stop_atr`, `extreme_room_pips`, `available_room_pips`, `objective_room_pips`,
`barrier_room_pips` (when a wall lies ahead), `room_limited_by_barrier`, `room_reward_risk`, `target_pips`, `target_r`, `utc_hour`.

**Quality (v2, ranking only; `quality_ranking` stays false).** The overall is the mean of five `score_*` dimensions in [0, 1]:

| Dimension | Definition |
|---|---|
| `impulse_strength` | `Ramp(body / ATR known before the bar; impulse_body_atr, impulse_body_atr + 2)` |
| `impulse_efficiency` | `Ramp(body / range; 0.4, 0.9)`: a long wick is price that was refused on the way |
| `retrace_depth` | `Peak(retrace_fraction; 0.20, 0.38, 0.62, 0.80)`: the healthy pullback of an impulse |
| `invalidation_quality` | `Peak(stop / M5 ATR; 0.4, 0.8, 1.8, 3.0)`: outside the noise, not absurdly wide |
| `room_reward_risk` | the available room against the stop, `Ramp(room / stop; 1, 3)`: a strong impulse with little room cannot average up to an excellent score |

v1 scored `impulse_strength` only.

## Parameters (`analysis.strategies.impulse_retrace`)

`impulse_body_atr` 2.0, `cooldown_bars` 12, `retrace_fraction` 0.50, `stop_buffer_atr` 0.10, `entry_width_pips` 3.0, `entry_window_minutes` 60, `atr_length` 14, `atr_window_bars` 140,
`reward_risk_ladder` [2.0, 1.0], `room_projection` 1.0; the scalp book and the barrier policy are injected as for Range Scalp. A missing or invalid value fails startup.

## SQ-1 replay (real XAU capture, M5, 2-9 Nov 2026)

Every impulse in the window is refused by the stop cap (`stop_over_cap`, 23 of 23: the half-bar retrace entry puts the origin more than 40 pips behind the worst entry), so the room rules did not change what published there. They are covered by the unit tests only.

## Replay (real XAU capture, M5 29 Oct - 6 Nov 2026), v1

6 candidates (3 Nov: 2, 5 Nov: 4): 2 in Asia, 2 in London, 2 in NY (UTC hours 0, 12, 19, 21); stops 49.5 to 69.7 pips. All 6 adapt, pass admission and the execution policy with the 1R and 2R ladder.
Counts only: no outcome was simulated.

## Expected performance (owner-supplied research, not measured here and not a gate)

XAUUSD M1 2020-01 to 2026-11, same stop band and ladder, spread $0.30: about 1.7 trades a day, win rate about 50% at 1R, gross E[R] OOS -0.01 +/- 0.07, **net E[R] -0.17 in-sample and -0.12 out-of-sample**
(NY hours only: -0.17 / -0.02). A live demo experiment; measure it from `auto_trade_results`.

Rollback: `analysis.strategies.impulse_retrace.enabled: false`, then restart analysis-engine.
