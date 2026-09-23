# Shared Technical Primitives V2

Phase S4 (`apexvoid-bot-prompts/rebuild-strategies.md` §23–26) builds the
four remaining shared technical primitives several already-locked S2
strategies depend on: Session levels, Fibonacci/Dealing Range, Key Level,
and Trendline. Each domain lands as its own commit/PR, in the order the
phase's own plan set (simplest → most complex), so a partial S4 is always
a safe, complete, mergeable state — see
[`analysis-engine-v2-migration.md`](../analysis-engine-v2-migration.md)
for cross-domain status.

**Status: Session done. Fib/Dealing Range, Key Level, and Trendline not
started yet** — this doc grows one section per domain as each lands,
mirroring [`zone-v2.md`](zone-v2.md)'s own contract-documentation shape
rather than duplicating it into four separate large documents.

## Session (`internal/session`)

Ports `algo-bot/app/analysis/session_liquidity.py`: UTC session windows
(Asia/London/NY), prior-day (PDH/PDL) and prior-week (PWH/PWL) extreme
levels, and their sweep status. Adds one piece with no Python
equivalent: classifying which session the latest closed bar falls in —
this is what finally populates `context.SessionContext`, an honest empty
placeholder (`SessionContext{Name string}`) since Phase S3's own
foundation task.

### Contract

```go
type Level struct {
	Name    string        // ASIA_H/ASIA_L/LONDON_H/LONDON_L/NY_H/NY_L/PDH/PDL/PWH/PWL
	Price   market.Price
	Time    int64         // the extreme candle's own Time
	Swept   bool
	SweptAt *int64
}

type State struct {
	Levels []Level
	Active string // ASIA/LONDON/NY — the latest closed bar's session
}

func Update(candles []market.Candle, cfg Config) State
```

Pure and causal, the same contract every other domain package's
`Update` follows: only ever reads the `candles` given to it.

### Levels

- **Session extremes** (`ASIA_H`/`ASIA_L`/`LONDON_H`/`LONDON_L`/`NY_H`/
  `NY_L`): the two most recently **closed** occurrences of each UTC
  window, walking backward through that window's calendar dates. A
  window whose close time is still after the latest candle is still
  forming and is never reported — ports `_session_extremes`'s
  `if close_ts > last_ts: continue` guard exactly.
- **PDH/PDL**: the trading day immediately before the current one,
  rolled over at `analysis.sessions.daily_rollover_utc_hour` (21 UTC,
  Python's real default). Requires at least two distinct trading days to
  exist — the still-open current trading day is never reported as
  "previous."
- **PWH/PWL**: the calendar week (Monday 00:00 UTC boundaries)
  immediately before the week the latest candle falls in. Ports
  `previous_week_levels`'s own conservative guard exactly: the very
  *first* candle in the given window must reach back to (or before) that
  prior week's own start, or PWH/PWL are withheld entirely — even when
  some in-window data exists, a dataset that doesn't reach the week's
  true start is not trusted to report its extreme.

### Sweep detection

A level is swept by the first later candle (`Time` strictly after the
level's own close time) whose high exceeds it (a high level) or low
undercuts it (a low level) — ports `_swept_ts` exactly, including its
most easily-missed property: the scan runs over **every** later candle
in the given window, not just the same session's own future occurrences.
A later session's own extreme can and does sweep an earlier, unrelated
level — verified directly in `test/session/session_test.go`'s cross-
session sweep case.

### One difference from production, by design

Production computes PWH/PWL once per symbol from a single representative
timeframe's frame (`engine.py::_weekly_session_levels`), not once per
timeframe, before folding it into the same flat `session_levels` list
`session_levels(df, cfg)` produces. This package's `Update` computes
every level — including PWH/PWL — per timeframe instead, consistent with
every other field this package returns and with how every other Go
domain package (`structure.Update`, `zone.Update`, `liquidity.Update`)
is architected: one pure function of one timeframe's own candle window.
A higher timeframe with less stored history simply produces fewer (or
no) PWH/PWL levels — the same honest behavior Python's own single-frame
call would have if that frame lacked two weeks of history.

### Config

`analysis.sessions.*` (`config/analysis.yml`):

| Leaf | Value | Source |
|---|---|---|
| `asia_start` | 22 | pre-existing |
| `london_start` | 7 | pre-existing |
| `ny_start` | 13 | pre-existing |
| `daily_rollover_utc_hour` | 21 | Phase S4 — `MarketDataSessionsConfig`'s real Pydantic schema default |

### Dependency rank

`session` sits at rank 1, alongside `indicator`/`marketdata`/`config` —
**not** rank 3 with `zone`/`liquidity` (and Fib/Key Level/Trendline,
S4's remaining domains). It needs only candles and timestamps; its
Python source never imports `swings.py`/`structure.py` either. See
[`architecture/dependency-rules.md`](../architecture/dependency-rules.md)'s
fifth amendment.
