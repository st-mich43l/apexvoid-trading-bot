# Shared structure evidence

Session structure, range geometry and liquidity sweeps are market observations, not strategies. Range Edge, Session Level and the standalone Liquidity Sweep
were purged (Q2): no code path generates an opportunity from them. What they were built on stays, in one implementation each, as evidence any strategy may cite.

| Capability | Where | Used by |
|---|---|---|
| Session, previous-day and previous-week highs and lows, finalized and developing | `internal/session` | the frame's session references, zone scoring, the measured session context |
| Equal highs/lows, swing liquidity, graded sweeps (grabs) | `internal/liquidity`, `internal/techniquezone/liquidity.go` | the sweep evidence |
| Established-range geometry (barriers, equilibrium, state) | `internal/techniquezone/scalp.go`, `analysis.legacy_read.range_structure` | the opposing-barrier book's native room |
| Confirmed sweeps of finalized liquidity | `strategyutil.LegacyDetector.SweepsSupporting` | Key Level, Flip Zone, Trendline, iFVG (the session-context factor) |

## Session calendar (daylight saving)

Sessions are anchored on real market clocks, not fixed UTC hours (`analysis.sessions.calendar`): Asia opens at 07:00 Tokyo (22:00 UTC, no DST), London at 08:00 London
(07:00 UTC in BST, 08:00 in GMT), New York at 09:00 New York (13:00 UTC in EDT, 14:00 in EST) and the FX trading day rolls over at 17:00 New York (21:00 UTC in EDT, 22:00 in EST).
Each boundary is placed on its own zone's local calendar date, so a clock change moves it in UTC exactly when the zone does (`session.NewCalendar`; the tz database is embedded). In northern summer the
boundaries equal the integers still kept in `analysis.sessions` for the scanner and Algo Bot (22/7/13/21), so summer results are unchanged; the first UTC difference is London's session on
25 Oct 2026. With no calendar a `session.Config` falls back to the fixed UTC hours.

## Finalized versus developing

`session.State.Levels` holds only finalized levels: a session occurrence whose window has closed, the previous trading day, the previous week. Each carries `Final` and `AvailableAt`, the close of
the period that defined it, the first instant it may be used. `session.State.Developing` holds the running extremes of the occurrences and the trading day that have not closed; they have no
`AvailableAt`, never appear in `Levels`, and are never swept liquidity.

## Sweep evidence

A *liquidity sweep* is an observation: finalized liquidity was traded through and a candle closed back (`strategyutil.LiquiditySweep`). A sell-side sweep (below a low) can support a BUY, a buy-side sweep a SELL;
it never opens an opportunity by itself, and a strategy cites it only where its own thesis needs it.

* A level counts only from its `AvailableAt` (the close of its period; for a pool, the candle after its newest swing); the sweep must come after it; nothing after the deciding candle is read.
* The first breach decides: reclaimed within the reaction window it is a sweep, otherwise the market accepted beyond the level and the liquidity is consumed (a later wick and reclaim is not a sweep of it).
* One event is one sweep: sweeps of the same side whose levels lie within the proximal band (`proximal_band_atr` x ATR) and whose candles are within two bars are merged, the most authoritative kind
  (session, then equal pool, then swing) kept. A session low that is also an equal-low pool, swept by the same candle, is one fact, never two bonuses.

## Session context (confluence factor)

`session_context` used to be set on every decision (the three sessions cover all 24 hours, so it was true by construction and added two points to every raw score). It is now
`LegacyDetector.SessionSweepContext` / `SessionSweepBetween`: a finalized session, previous-day or previous-week level supporting the trade was swept and reclaimed in the setup's own window
(the reaction window for Key Level, Flip Zone and Trendline; the move from the gap's first candle to the rejection for iFVG). The frozen detectors' behaviour stays reproducible for the oracle goldens through the optional strategy parameter
`frozen_session_context` (only the parity tests set it); technique and CRT never carried it.
