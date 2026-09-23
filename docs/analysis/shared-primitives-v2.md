# Shared Technical Primitives V2

Phase S4 (`apexvoid-bot-prompts/rebuild-strategies.md` §23–26) builds the
four remaining shared technical primitives several already-locked S2
strategies depend on: Session levels, Fibonacci/Dealing Range, Key Level,
and Trendline. Each domain lands as its own commit/PR, in the order the
phase's own plan set (simplest → most complex), so a partial S4 is always
a safe, complete, mergeable state — see
[`analysis-engine-v2-migration.md`](../analysis-engine-v2-migration.md)
for cross-domain status.

**Status: Session and Fib/Dealing Range done. Key Level and Trendline not
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

## Fibonacci / Dealing Range (`internal/fib`)

Ports `algo-bot/app/analysis/fibonacci.py` and `dealing_range.py`, kept
as one Go package since Python keeps them as directly-coupled siblings
(`fib_from_swings` calls `dealing_range.py::swing_range_pair` directly,
and `dealing_range()` calls back into `fib_zone_label`).

### Contract

```go
type Level struct {
	Ratio float64
	Price market.Price
	Kind  LevelKind // KindRetracement | KindExtension
}

func Ladder(low, high market.Price, includeExtensions bool) []Level
func NearestLevel(levels []Level, price, atr market.Price, epsilonATR float64, kinds ...LevelKind) (Level, bool)

type DealingRange struct {
	High, Low, Equilibrium market.Price
	Position                float64
	Zone                    string // "discount" | "eq" | "premium"
	FibZone                 string // "deep_discount" | "discount" | "eq" | "premium" | "deep_premium"
}

func Resolve(swings []structure.Swing, price market.Price, cfg Config) (DealingRange, bool)

type State struct {
	Ladder []Level
	Range  *DealingRange
}

func Update(candles []market.Candle, swings []structure.Swing, cfg Config) State
```

Pure and causal, the same contract every other domain package's
`Update` follows: only ever reads the `candles`/`swings` given to it.

### Ladder

Retracement ratios `0.236/0.382/0.5/0.618/0.786` measured back down from
`high` toward `low`; extension ratios `1.0/1.272/1.618` anchored at `low`
(so `1.0` reproduces `high` itself) — `fibonacci.py`'s exact
`RETRACEMENT_RATIOS`/`EXTENSION_RATIOS`, not a guess (an earlier session
note had wrongly guessed `0.705`/`0.718`, which actually belong to a
different, technique-specific fib usage in `technique_geometry.py`, out
of this package's scope).

### Bracketing swing pair

Both the ladder and the dealing range are built from the **same**
bracketing search (`swingRangePair`, called once by `Update`) — ports
`swing_range_pair`'s own two-step search exactly:

1. **Bracketing pair** (`_bracketing_pair`): scanning backward from the
   most recent swing, the first opposite-kind pair (checked in that same
   backward order) whose `[low,high]` actually contains price.
2. **Fallback — last opposing pair** (`_last_opposing_pair`): if no pair
   brackets price, the most recent swing paired with the nearest earlier
   swing of the opposite kind, regardless of whether price falls inside
   it — `Resolve`'s own `Position` then clamps to `[0,1]` at that pair's
   edge.

Verified directly in `test/fib/dealing_range_test.go`'s own case: an
older pair that genuinely brackets price wins over the most-recent
swing's own (non-bracketing) opposite, exactly matching
`_bracketing_pair`'s scan order rather than a naive "always use the last
two swings" shortcut.

### Dealing range zones

`Resolve` reports two labels at once: a coarse `Zone`
(`discount`/`eq`/`premium`, using `EqHalfBand` around the 0.5 midpoint)
and a finer `FibZone` (`deep_discount`/`discount`/`eq`/`premium`/
`deep_premium`, using `DeepDiscount`/`DeepPremium` — 0.382/0.618, the
same ratios as the 38.2%/61.8% retracement levels themselves) — ports
`dealing_range()`'s coarse zone and `fib_zone_label()`'s fine zone
exactly, including that both use the SAME half-band value (`dealing_range()`
halves its own `eq_band` once and passes the result to both).

### Config

`analysis.fibonacci.*` (`config/analysis.yml`), all four leaves newly
surfaced at their real Python function-default values — no
`trading-bot.yml` or Pydantic-schema precedent existed before this
phase:

| Leaf | Value | Source |
|---|---|---|
| `epsilon_atr` | 0.15 | `nearest_fib()`'s own default |
| `deep_discount` | 0.382 | `fib_zone_label()`'s own default |
| `deep_premium` | 0.618 | `fib_zone_label()`'s own default |
| `eq_half_band` | 0.05 | `dealing_range()`'s own `eq_band=0.10` default, pre-halved (see `Config.EqHalfBand`'s own comment) |

### Dependency rank

`fib` joins `zone`/`liquidity` at rank 3 — it needs `structure.Swing.Kind`/
`.Price` directly for the bracketing search, the same situation that
already promoted `zone`/`liquidity` above `structure`. See
[`architecture/dependency-rules.md`](../architecture/dependency-rules.md)'s
fifth amendment (`fib` joins `zone`/`liquidity` at rank 3).
