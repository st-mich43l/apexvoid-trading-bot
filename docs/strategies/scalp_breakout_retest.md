# Breakout Retest Scalp

`scalp_breakout_retest`, `v3`, `analysis-engine/internal/strategy/scalpbreakoutretest`.
XAU only (`symbols: [XAU]`; the profitable Python scalp lane hosted gold only).
Evaluated on every closed M1 bar; the setup is M5 and the execution
confirmation is M1.

`v3` is the Breakout Retest V2 engine that ran at the end of the profitable XAU
week of 14–18 Sep 2026 (+150 pips on 19 trades, 6W/13L): Python
`app/scalping/strategies.py::discover_breakout_retest` and
`app/scalping/microstructure.py` at commit `a1c77584`. The previous Go version
was a 120-line compression-box detector that had produced no trade since the
migration. Do not tune this toward a higher win rate: a 32 % win rate was
profitable because of the setup selection and the 1:2 / 1:1 payoff.

## State machine

```
level source → true cross → acceptance → retest → role flip → ARMED
             → closed-M1 execution confirmation → entry zone around the level
```

Each source candidate runs the whole machine
(`WATCH_LEVEL → BREAK_DETECTED → ACCEPTED → WAIT_RETEST → RETESTED →
CONFIRMATION → ARMED`, or `FAILED_BREAK` / `EXPIRED` / `INVALID_RETEST` /
`NO_CONTINUATION`). A true cross needs the previous close at or inside the level
(plus `breakout_cross_tolerance_atr`) and the close beyond it by
`breakout_margin_atr`; the first true cross after the level's own source bar is
immutable. Acceptance needs `acceptance_required_closes` closes beyond the level
within `acceptance_bars`, an immediate reclaim fails it at once, and a close
through a compression box's far edge fails it as an opposite-structure break.
The retest is a bar reaching within `retest_front_run_atr` of the level, between
`min_retest_delay_bars` and `max_retest_delay_bars` after the break; a
penetration beyond `max_retest_penetration_atr` is `retest_too_deep`. The
retest bar must close back beyond the level (the role flip); `confirmation_mode:
retest_high_break` additionally needs the next bar to break its extreme.

## Level sources

- **Structure flip (setup window)**: the M5 window's own swing highs (BUY) /
  lows (SELL), 3 bars each side, 3–240 bars old, at least `0.3 · ATR` apart.
- **M5 key levels** with at least `m5_structure_min_touches` touches, on the
  breakable side of price.
- **Liquidity levels**: equal highs / lows of the window's swings.
- **Compression box**: a recent 8–20 bar window no wider than `1.5 · ATR` with
  both edges touched twice, run through the legacy break / hold / rejection /
  hold detector.

## Selection and ranking

Every armed episode is scored 0–100 — displacement 30 %, acceptance 15 %, retest
quality 25 %, retest speed 15 %, confirmation 15 % — and the single best
episode per direction wins, not the first match. (`min_quality_score` is 0, as
in production.)

## M5 setup, M1 execution

An M5 setup is only actionable once one of the last two closed M1 bars closes in
the trade direction and interacts with the level (a wick within the buffer of it,
a close back beyond it). Setup timeframe and execution timeframe are not
collapsed: the M5 window is the closed M5 bars at the M1 close, the M1 window the
last 60 bars.

## Geometry (analysis, not the XAU execution ladder)

- Entry zone: `level ± buffer`, `buffer = max(1.2 · M1 ATR, maximum_spread_pips ·
  1.5 · pip)`.
- Structural stop beyond the lower of the confirming M1 low and the level (BUY);
  widened to `stop_minimum_pips`, rejected beyond `stop_maximum_pips`.
- Target 1:2, else 1:1, whichever fits the corridor room (the M5 active range
  of the last 24 bars from the price the context was taken at) and the minimum net
  target. No room for either means no opportunity.
- The context price and M1 volatility are those of the first M1 bar after the
  latest M5 close while that is younger than 420 s, and of the current bar after,
  as the Python lane cached them.

Spread-dependent guards (the quote spread in the breakout margin, the
spread-multiple stop floor) need a live quote the analysis engine does not have
and remain execution-owned. The stop, target and spread parameters are the shared
`auto_algo.strategies.scalping.*` book, injected so they have one source.

## Evidence

- `test/scalpparity`: replays a committed real XAU capture with M1 (1939 closed
  M1 cycles) through the Go engine and requires the same opportunities as the
  Python engine on the same bars — 26 opportunities, 0 mismatches, zone, stop,
  target and quality included; the golden is produced by the Python.
- `internal/strategy/scalpbreakoutretest`: 38 hand-built episodes (both
  directions: armed, no cross, insufficient margin, immediate reclaim, failed
  acceptance, pending acceptance, retest early / late / too deep / front-run,
  no role flip, high-break confirmation armed / pending / missing, stale
  source, box-edge breach, level older than its source) and 22 seeded series for
  every level source and the compression box, all against the Python
  implementation.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: XAU only.
- **Execution**: Trades on XAU only, with the M1 scalp stop book and ladder. Proven against the frozen scalp lane on real and synthetic M1 captures.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
