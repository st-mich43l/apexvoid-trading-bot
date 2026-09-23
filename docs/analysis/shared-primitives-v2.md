# Shared Technical Primitives V2

Phase S4 (`apexvoid-bot-prompts/rebuild-strategies.md` §23–26) builds the
four remaining shared technical primitives several already-locked S2
strategies depend on: Session levels, Fibonacci/Dealing Range, Key Level,
and Trendline. Each domain lands as its own commit/PR, in the order the
phase's own plan set (simplest → most complex), so a partial S4 is always
a safe, complete, mergeable state — see
[`analysis-engine-v2-migration.md`](../analysis-engine-v2-migration.md)
for cross-domain status.

**Status: Trendline done on this branch. Session (`feat/session-domain-v2`,
PR #605), Fib/Dealing Range (`feat/fib-domain-v2`, PR #606), and Key Level
(`feat/keylevel-domain-v2`, PR #607) are done on their own branches — S4
is now complete across its four domains, each awaiting review on its own
PR.** Each domain's own PR creates/extends this file starting from a
fresh `master` checkout, so expect a straightforward merge conflict
combining sections wherever two of these branches land close together —
same pattern as S0/S1's `CHANGELOG.md` conflict earlier in this rebuild.
This doc grows one section per domain as each lands, mirroring
[`zone-v2.md`](zone-v2.md)'s own contract-documentation shape rather than
duplicating it into four separate large documents.

## Trendline (`internal/trendline`)

Ports `algo-bot/app/analysis/trendline_v2.py`'s causal construction
(`build_causal_trendlines`) and live-interaction classifier
(`evaluate_live_interaction`) — the largest and most complex of S4's four
domains. V1 (`trendlines.py::_trendlines_v1`, an all-pairs brute-force
fit) is confirmed dead/shadow-metrics-only in production
(`analysis.trendlines.version: v2` is the live setting) and is **not**
ported.

### The central invariant: immutable anchors

A line's two anchor pivots (A/B) never change once chosen. A later pivot
(C) can **validate** the A/B projection — becoming a `ValidationTouch` —
but can never be promoted into a new anchor that redraws the line around
B. `Build` only ever pairs **chronologically adjacent** confirmed pivots
as anchor candidates, never a later, non-adjacent re-pairing.
`test/trendline/build_test.go`'s
`TestBuildOnlyPairsChronologicallyAdjacentPivots` proves this directly:
three swings whose direct first-to-last slope would be accepted, but
whose two individual adjacent slopes are each rejected, must yield zero
lines — if `Build` ever tried the non-adjacent pair, this would produce
one.

### Contract

```go
type Trendline struct {
	Kind Kind // KindSupport | KindResistance
	AnchorA, AnchorB string // structure.Swing.ID references
	AnchorAIndex, AnchorBIndex int // candle index within the window Build was given
	Slope, Intercept, SlopeATRPerBar float64
	State State // Candidate | Tentative | Confirmed | Exhausted | Degraded | Broken
	ValidationTouches []ValidationTouch
	WickViolations, CloseViolations int
	BrokenAt *int64
	ViolationReclaimed bool
	SpanBars int
	Exhausted bool
}

func ValueAt(tl Trendline, index int) market.Price
func Build(candles []market.Candle, atrSeries []float64, swings []structure.Swing, cfg Config) []Trendline
func Update(candles []market.Candle, atrSeries []float64, swings []structure.Swing, cfg Config) TrendlineState

func EvaluateInteraction(candles []market.Candle, tl Trendline, atr float64, cfg Config) Interaction
```

`Build`/`Update` are pure and causal, the same contract every other
domain package follows. `EvaluateInteraction` — like `keylevel.Role` —
stays a standalone pure function, not part of the per-timeframe engine
pipeline: it needs a live per-signal ATR reading a future strategy
supplies at S7, and is explicitly a read-only interaction classifier,
never an entry signal ("a reclaimed test still needs a new trigger" —
Python's own docstring).

### Coordinates are candle index, not wall-clock time

A line's `Slope`/`Intercept` are in price-per-**bar**, not price-per-
second — matching Python's own DataFrame integer-position coordinate
exactly. A weekend gap between two candles does not distort the line,
because bar count, not calendar time, is what's measured. This means a
`Trendline`'s geometry is only meaningful against the SAME candles window
(same index-0 origin) it was built from — which holds automatically
here, since `Build` recomputes every line from scratch on every closed
bar from the full stored candle window, the same "no incremental state"
contract every other domain package's `Update` already follows.

### Health, lifecycle, and one real state-machine quirk

`computeHealth` scans **every** bar (not just pivot points) from just
after anchor B to the end of the given candles for wick penetration and
close violations. "Broken" is defined by an **unresolved close
violation** — never by a wick alone, however deep; a wick violation the
same bar's close recovers from marks `ViolationReclaimed` but does not
break the line (`test/trendline/build_test.go`'s
`TestHealthWickViolationAloneDoesNotBreakTheLine`/
`TestHealthUnresolvedCloseViolationBreaksTheLine` prove both halves of
this directly). `State` carries `StateCandidate` as a named value, but —
confirmed by reading the real Python control flow — `candidateFromAnchors`
always overrides it with `Exhausted`/`Confirmed`/`Tentative` whenever
health's own state is not `Broken`/`Degraded`. A `Trendline` this package
returns therefore never actually carries `StateCandidate`; the enum
names the complete conceptual state space Python's `_Health`/`Trendline`
pair describes, not a gap in this port.

### Validation touches defer until their reaction window fully closes

`measureValidation` only counts a touch once `pivot.confirmed_index +
validation_reaction_bars` worth of bars have actually closed — a partial
reaction window is deferred, never counted early. This is the core
causality guard for validations specifically (on top of `Build`'s own
immutable-anchor guarantee); proven directly in
`test/trendline/build_test.go`'s
`TestMeasureValidationDefersUntilReactionWindowFullyCloses` and, at the
whole-domain level, in `test/trendline/causality_test.go`.

### One faithfully-ported ATR choice, not simplified

Unlike `internal/keylevel`'s deliberate ATR simplification (documented
there), `Build` ports `atr_scalar`'s real behavior exactly: the
**median** of the whole ATR series, not its last value. Computing a
median in Go is cheap and mechanically simple (unlike `keylevel`'s
per-swing `atr_at()` lookup, real added plumbing complexity) — and a
long-lived structural object like a trendline benefits from the same
stability against one volatile bar that Python's own choice already
buys. See `internal/trendline/build.go`'s `medianATR`, which also ports
`atr_scalar`'s real two-stage validation exactly: only NaN is dropped
per-element (matching pandas' own `dropna()`), and the finite/positive
check applies to the **final** median only, not each element.

### Config

`analysis.trendlines.*` (`config/analysis.yml`) — twelve leaves already
existed (this phase adds no new meaning to them); six more
(`minimum_slope_atr` through `dedup_slope_percent`) existed only as
Python `getattr()` fallback-default names in `_settings()`, never real
YAML keys, added here at those same real fallback values:

| Leaf | Value | Source |
|---|---|---|
| `minimum_slope_atr` | 0.02 | `_settings()`'s own fallback default |
| `maximum_slope_atr` | 0.15 | `_settings()`'s own fallback default |
| `minimum_touch_spacing_bars` | 3 | `_settings()`'s own fallback default |
| `minimum_span_bars` | 20 | `_settings()`'s own fallback default |
| `dedup_value_atr` | 0.5 | `_settings()`'s own fallback default |
| `dedup_slope_percent` | 0.2 | `_settings()`'s own fallback default |

`chop_minimum_validation_touches`/`chop_require_htf_aligned` already
exist in YAML but are confirmed dead (zero reads in `trendline_v2.py`) —
left alone, out of scope for this phase.

### Dependency rank

`trendline` joins `zone`/`liquidity` (and, once those branches merge,
`fib`/`keylevel`) at rank 3 — `Build`'s causal anchor construction needs
`structure.Swing.Kind`/`.Price`/`.Time`/`.ConfirmedAt` directly. See
[`architecture/dependency-rules.md`](../architecture/dependency-rules.md)'s
fifth amendment (`trendline` joins `zone`/`liquidity` at rank 3).
