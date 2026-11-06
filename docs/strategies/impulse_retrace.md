# Impulse Retrace Scalp

`impulse_retrace` buys (or sells) the first retrace to the midpoint of one forceful M5 displacement bar, in the direction of the impulse. It is a Go-native XAU strategy
(`symbols: [XAU]`), live (owner decision 2026-11-06, PR-S4; no observe-only, shadow or paper mode), and a member of the M1 scalp lane (stop 30 to 70 pips, 1R / break-even / 2R,
80-pip opposite separation). **It trades every session: there is no session, clock or MAD-phase condition anywhere in it.** The session is published as a label only.

Impulse Pullback (a 4 ATR displacement plus a structural reference plus M1 confirmation) is unchanged and is not gated by any clock either; it is rare because of its filters.

## Rules (closed M5 bar `k`; only bars closed at or before `k`)

1. **Impulse.** `|C[k] - O[k]| >= impulse_body_atr (2.0) x atr` (Wilder ATR(14) of M5 known before `k`); direction = the sign of the body. One impulse per `cooldown_bars` (12) bars: the impulses of the last day are replayed forward and the first of each cluster stands.
2. **Geometry.** BUY `origin = L[k]`, `extreme = H[k]`; SELL `origin = H[k]`, `extreme = L[k]`. Entry level = `extreme` back toward `origin` by `retrace_fraction` (0.50) of the bar's range. Entry band (limit side) BUY `[lim, lim + w]`, SELL `[lim - w, lim]`, `w = entry_width_pips` (3.0).
3. **Stop.** BUY `origin - stop_buffer_atr (0.10) x atr`, SELL `origin + 0.10 x atr`, measured from the band's worst edge, then `ScalpStopPips(structural, 30, 70)`: widened to 30, **refused above 70**. Invalidation `impulse_origin_lost`: a closed M5 bar through it before a fill invalidates the setup (the book's existing close-through rule).
4. **Target.** `ScalpTarget`, ladder `[2.0, 1.0]`, no room cap: the 2R price.
5. **Expiry.** `entry_window_minutes` (60) after the impulse bar's close; unfilled it expires.

Evidence `m5_impulse_bar`, `m5_retrace_limit`, and `session_asia` / `session_london` / `session_ny` as a label; measures `impulse_body_atr`, `retrace_fraction`, `utc_hour`.
Quality (ranking only): `clamp01((|body| / atr - 2.0) / 2.0)`.

## Parameters (`analysis.strategies.impulse_retrace`)

`impulse_body_atr` 2.0, `cooldown_bars` 12, `retrace_fraction` 0.50, `stop_buffer_atr` 0.10, `entry_width_pips` 3.0, `entry_window_minutes` 60, `atr_length` 14, `atr_window_bars` 140,
`reward_risk_ladder` [2.0, 1.0]; the scalp book is injected as for Range Scalp. A missing or invalid value fails startup.

## Replay (real XAU capture, M5 29 Oct - 6 Nov 2026)

6 candidates (3 Nov: 2, 5 Nov: 4): 2 in Asia, 2 in London, 2 in NY (UTC hours 0, 12, 19, 21); stops 49.5 to 69.7 pips. All 6 adapt, pass admission and the execution policy with the 1R and 2R ladder.
Counts only: no outcome was simulated.

## Expected performance (owner-supplied research, not measured here and not a gate)

XAUUSD M1 2020-01 to 2026-11, same stop band and ladder, spread $0.30: about 1.7 trades a day, win rate about 50% at 1R, gross E[R] OOS -0.01 +/- 0.07, **net E[R] -0.17 in-sample and -0.12 out-of-sample**
(NY hours only: -0.17 / -0.02). A live demo experiment; measure it from `auto_trade_results`.

Rollback: `analysis.strategies.impulse_retrace.enabled: false`, then restart analysis-engine.
