# Shared Technical Primitives V2

Phase S4 (`apexvoid-bot-prompts/rebuild-strategies.md` §23–26) builds the
four remaining shared technical primitives several already-locked S2
strategies depend on: Session levels, Fibonacci/Dealing Range, Key Level,
and Trendline. Each domain lands as its own commit/PR, in the order the
phase's own plan set (simplest → most complex), so a partial S4 is always
a safe, complete, mergeable state — see
[`analysis-engine-v2-migration.md`](../analysis-engine-v2-migration.md)
for cross-domain status.

**Status: Key Level done on this branch. Session (`feat/session-domain-v2`,
PR #605) and Fib/Dealing Range (`feat/fib-domain-v2`, PR #606) are done on
their own branches; Trendline not started yet.** Each domain's own PR
creates/extends this file starting from a fresh `master` checkout, so
expect a straightforward merge conflict combining sections wherever two
of these branches land close together — same pattern as S0/S1's
`CHANGELOG.md` conflict earlier in this rebuild. This doc grows one
section per domain as each lands, mirroring
[`zone-v2.md`](zone-v2.md)'s own contract-documentation shape rather than
duplicating it into four separate large documents.

## Key Level (`internal/keylevel`)

Ports `algo-bot/app/analysis/levels.py` (price-clustered key levels) and
`key_level_role.py` (pure closed-bar role classification), kept as one
Go package since Python keeps them logically paired even though
`Role`'s real production call sites source their `kind` argument from
several different vocabularies, not only `levels.py`'s own `Level.kind`.

### Contract

```go
type Level struct {
	Price    market.Price
	Kind     Kind // KindReaction | KindRound
	Touches  int
	Band     float64
	Strength float64
}

func Cluster(candles []market.Candle, atr float64, swings []structure.Swing, cfg Config) []Level
func Update(candles []market.Candle, atrSeries []float64, swings []structure.Swing, cfg Config) State

type RoleKind uint8 // Support | Resistance | Ambiguous | BrokenSupport | BrokenResistance

func Role(kind string, bandLow, bandHigh market.Price, closes []float64, breakoutAcceptBars int) RoleKind
```

`Cluster`/`Update` are pure and causal, the same contract every other
domain package's `Update` follows. `Role` is a standalone pure function
— not wired into the per-timeframe engine pipeline, since it needs a
live per-signal band and `breakoutAcceptBars` a future strategy supplies
(S7), the same reasoning the phase's own plan gives for leaving
`breakoutAcceptBars` uncached.

### Clustering

`Cluster` ports `key_levels()` in full: price-sorted greedy clustering
of swings (`_price_clusters`/`_can_join_cluster`'s dual gate — a
candidate must be within `tolerance` of **every** existing cluster
member, AND the cluster's total span stays within
`tolerance*MaximumClusterSpanMultiple`, even when every individual
pairwise gap would otherwise qualify — verified directly in
`test/keylevel/cluster_test.go` with a constructed chain that clears
every pairwise gap but not the span cap), a round-number price scan
(`_round_levels`), a sorted-adjacent dedupe merge, and optional
wick-touch re-enrichment (`_with_wick_touches`/`wick_touch_episodes`) —
real OHLC overlap counted as touch **episodes** (a multi-bar dwell in
the band counts once), not a raw per-bar count, when `Update`/`Cluster`
are given candles.

### One documented ATR simplification

`key_levels()` reads ATR two different ways in the same function:
clustering tolerance uses `atr_scalar()` (the **median** of the whole
ATR series), while `_round_levels()`'s per-swing touch check uses
`atr_at()` (that swing's **own bar's** ATR value). This package uses
**one** canonical scalar ATR — the same "current/last value" convention
`structure.Update`/`zone.Update`/`liquidity.Update`/`fib.Update` already
use — for both. `Update` passes `atrSeries`'s last value into `Cluster`.
Documented here and in `internal/keylevel/doc.go` rather than silently
diverging from Python's own two-different-ATR-reads behavior.

### Role

`Role` ports `key_level_role.py::classify_key_level_role` exactly,
including one real omission: Python's own `level_price` parameter is
declared but never read anywhere in the function body (confirmed by
reading the full source) — not ported, rather than carrying dead surface
area forward. Explicit support/resistance semantics (substring-matched
from `kind` — "support"/"low" or "resist"/"high", case-insensitive)
remain authoritative until `breakoutAcceptBars` **consecutive** closes
(counted from the most recent, backward) accept beyond the opposite
edge; an accepted break is always reported as a `Broken*` role, never
silently reinterpreted as the plain opposite role in place — "Break &
Retest owns any later flip; Key Level must not reinterpret it," still
true after the S2 catalog's Break & Retest → Trendline merge.

### Config

`analysis.key_levels.*` (`config/analysis.yml`), all four leaves newly
surfaced at their real Python function-default values — no
`trading-bot.yml` or Pydantic-schema precedent existed before this
phase:

| Leaf | Value | Source |
|---|---|---|
| `cluster_atr` | 0.5 | `key_levels()`'s own `level_cluster_atr` default |
| `round_step` | 5.0 | `key_levels()`'s own `round_step` default |
| `minimum_touches` | 2 | `key_levels()`'s own `min_touches` default |
| `maximum_cluster_span_multiple` | 2.0 | `key_levels()`'s own `max_cluster_span_multiple` default |

`breakout_accept_bars` for `Role` stays a caller-supplied parameter, no
default to carry forward — it already was in Python.

### Dependency rank

`keylevel` joins `zone`/`liquidity` (and, once that branch merges,
`fib`) at rank 3 — it needs `structure.Swing.Kind`/`.Price` directly for
the clustering search. See
[`architecture/dependency-rules.md`](../architecture/dependency-rules.md)'s
fifth amendment (`keylevel` joins `zone`/`liquidity` at rank 3).
