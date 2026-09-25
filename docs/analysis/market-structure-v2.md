# Market Structure V2 — Specification

Status: implemented (`analysis-engine/internal/structure`,
`internal/liquidity`, and the canonical Zone V1 domain in `internal/zone`).
This document and that code must always describe the same behavior (source
task §65) — where they'd disagree, the code and its tests are the actual
truth; file an issue against this doc, don't trust the doc over the code.

Every term below is defined precisely enough to be deterministic — source
task §66's own test: "what exactly makes a CHoCH? which swing must break?
wick or close? how far? does displacement matter? which layer changed?
when is the event confirmed?" Every one of those questions has a concrete
answer here, with the exact Go function that implements it.

Zone geometry is specified separately in [`zone-v2.md`](zone-v2.md).
Structure supplies the swings, breaks, and displacement evidence that the
Zone domain consumes; Zone owns the resulting technical bands and their
lifecycle/relevance state. Neither package imports strategy code.

## Pivot

A **raw candidate** turning point: a bar whose High (Low) is strictly the
greatest (least) among `left_bars` bars before it and `right_bars` bars
after it. Not yet structure — most pivots never become a Swing (see
Promotion below).

- **Where**: `structure.DetectPivots` (`pivot.go`).
- **Config**: `analysis.structure.pivot.{left_bars,right_bars}`.
- **Causal confirmation**: a pivot at bar `i` is only ever returned once
  `candles[i+right_bars]` exists in the given slice. `Pivot.Time` is the
  pivot bar's own timestamp; `Pivot.ConfirmedAt` is `candles[i+right_bars].Time`
  — strictly later. A consumer must never treat a pivot as known before
  `ConfirmedAt`. There is deliberately no separate "provisional, not-yet-
  confirmed" pivot record: a pivot simply does not appear in
  `DetectPivots`' output until its right window has closed. Proven, not
  just documented: `test/structure/causality_test.go`'s
  `TestDetectPivots_NeverConfirmsBeyondGivenData` and
  `TestDetectPivots_ConfirmedPivotsAreStableAsMoreDataArrives`.
- **Significance**: `Pivot.Strength` is the ATR-normalized excursion on
  the *weaker* side (`min(leftExcursion, rightExcursion) / atr`) — a
  pivot with a huge move away on only one side and a shallow one on the
  other scores low, not high. See `pivotStrength`'s doc comment in
  `pivot.go` for the exact excursion formula.

## Swing

A **confirmed, promoted** pivot — the market-structure fact a strategy can
actually read. Carries `ExcursionPrice`/`ExcursionATR` (its own
significance), `SourceTimeframe`, `BarsToConfirm`, and `ConfirmedAt`
inherited from its originating Pivot.

- **Where**: `structure.PromoteSwing` (`swing.go`).
- A pivot is promoted only if `Strength >= analysis.structure.swing.minimum_excursion_atr`
  (currently 0.5 ATR) — not every fractal is a swing.

## Structure Layer

An explicit hierarchy — `StructureMicro` < `StructureInternal` <
`StructureIntermediate` < `StructureMajor` — assigned **purely from a
swing's `ExcursionATR`**, never from its source timeframe. A large M5
swing can outrank a tiny M15 one; timeframe is evidence, never the sole
definition (source task §13).

| Layer | Threshold (config key) | Current value |
|---|---|---|
| (rejected) | `swing.minimum_excursion_atr` | 0.5 ATR |
| Micro | (the floor above, below `internal_atr`) | [0.5, 1.0) ATR |
| Internal | `swing.promotion.internal_atr` | [1.0, 2.0) ATR |
| Intermediate | `swing.promotion.intermediate_atr` | [2.0, 3.5) ATR |
| Major | `swing.promotion.major_atr` | >= 3.5 ATR |

Promotion is deterministic — the highest rung the pivot's `Strength`
clears, no tie-breaking beyond the thresholds (`PromoteSwing`).

## HH / HL / LH / LL / Equal High / Equal Low

Explicit classification of one swing against the **preceding same-kind
swing at the same layer** — never inferred, never direct floating-point
equality.

- **Where**: `structure.ClassifySwingRelation` (`classifier.go`).
- **Equality band**: `|current.Price - previous.Price| <= tolerance_atr * atr`
  (`analysis.structure.equal_level.tolerance_atr`, currently 0.05 ATR —
  matching `technique_geometry.py`'s already-live `epsilon()`). Outside
  the band: `HigherHigh`/`LowerHigh` for a high, `HigherLow`/`LowerLow`
  for a low, by sign of the (signed) price difference.
- Comparing a High against a Low returns `RelationUnknown` (a caller
  error, not a meaningful relation).

## Trend and Protected Levels

Per layer, folded from its own swing sequence (`structure.BuildLayerState`,
`hierarchy.go`):

- **TrendBullish**: the *most recently classified* high relation is
  `HigherHigh` **and** the most recently classified low relation is
  `HigherLow` (the two need not be the same swing — each side keeps its
  own latest reading). **TrendBearish** is the symmetric `LowerHigh` +
  `LowerLow`. Anything else — including a side with no second same-kind
  swing yet to classify — is **TrendRange**, never guessed as a
  continuation of whatever trend held before.
- **Protected low** (while Bullish): the layer's current `LastLow` — the
  swing whose failure would materially damage the bullish read.
  **Protected high** (while Bearish): symmetric.
- **Persistence**: a protected level is **not cleared** when the trend
  reads Range — it persists until a new Bullish or Bearish read
  explicitly supersedes it (a market pausing does not erase what was
  already established). Proven:
  `test/structure/hierarchy_test.go`'s
  `TestBuildLayerState_ProtectedLevelPersistsThroughARangeRead`.

## Displacement

A reusable, multi-candle expansion model — never "one big candle."

- **Where**: `structure.DetectDisplacement` (`displacement.go`).
- **Formula** (a direct, faithful port of the already-shipped, already-
  proven `app/analysis/zones.py::displacement()`, not a fresh invention):
  scanning the shortest qualifying prefix run of 1..`maxBars` candles,
  a run qualifies when (a) its net move `|close(end) - open(start)|`,
  ATR-normalized, clears `analysis.structure.break.displacement_range_atr`
  (currently 1.5 ATR — Python's own `k=1.5`), **and** (b) at least half its
  candles have `body/range >= analysis.structure.break.displacement_body_dominance`
  (currently 0.55 — Python's own `body_frac=0.55`).
- Prefers the **shortest** qualifying run — a single huge candle is
  recognized immediately, not diluted by waiting for `maxBars`.

## Break Types

Never "every penetration is a BOS" (source task §20). `structure.DetectBreak`
(`break.go`) classifies exactly one of:

| Type | Meaning | Confirmed when |
|---|---|---|
| `BreakWick` | Price wicked beyond the level (> `minimum_penetration_atr` tolerance) but never closed beyond, within the wait window | the wait window elapses with no close beyond |
| `BreakClose` | A bar closed beyond the level, held (not reclaimed) through the sweep-reclaim window, no qualifying displacement | the reclaim window elapses with no reclaim |
| `BreakDisplacement` | Same as `BreakClose`, but the closing bar (or a short run starting there) qualifies as Displacement in the break's own direction | same |
| `BreakSweep` | Price traded beyond the level (wick or close) and a **later close reclaimed** the origin side within `sweep_reclaim_bars` (currently 6 — matching this session's own shipped PR #574) | the reclaim bar closes |
| `BreakFailed` | A **held close** beyond the level that reclaimed within the tighter `failed_break_reclaim_bars` (currently 3) — an aggressive, close-confirmed move that reversed fast | the reclaim bar closes |

**Never automatically a structural event**: `BreakWick`/`BreakSweep`/
`BreakFailed` are never BOS or CHoCH — they did not hold.

**Causality**: `DetectBreak` waits out the configured reclaim window
before committing to a held classification. When the given candles run
out before that window fully elapses, it still returns its best
classification given the data available — `ConfirmedAt` equals the last
examined candle's time in that case, correctly signaling "not yet fully
resolved," not a fabricated certainty. The real, proven guarantee: once a
classification resolves **strictly before** the given data ends (the
window genuinely elapsed), it is byte-identical no matter how much more
data is appended afterward — `test/structure/causality_test.go`'s
`TestDetectBreak_ResolvedClassificationIsStableAsMoreDataArrives`.

## BOS vs. CHoCH

Answered by `structure.ClassifyEvent` (`event.go`), using the layer's
trend/protected-level context **as of just before** the break — only ever
applied to a `BreakClose` or `BreakDisplacement` (a break that held):

- **BOS**: the break's direction agrees with the already-established
  trend (closing up while Bullish, down while Bearish) — **or** there was
  no established trend yet (Range/Unknown), in which case the first
  directional close is the *initiating* BOS, not a CHoCH (there is
  nothing established yet to change from).
- **CHoCH**: the break direction **opposes** the established trend **and**
  the broken swing is specifically the layer's **current protected
  level**. A held close beyond some *other*, non-protected swing in the
  counter-trend direction (an internal pullback) is neither BOS nor CHoCH
  — real evidence on the break record, but not yet a regime-change
  signal.
- **Micro vs. major CHoCH**: not a separate enum value — a CHoCH's
  `Layer` field (already on `StructureBreak`) is the distinction. A
  consumer filters by `Layer` to ask for "major CHoCH only."

## Acceptance vs. Rejection

Represented directly on `StructureBreak`, not as a separate type:
`CloseBeyond` (did the bar actually close past the level, not just wick)
and `Displacement` (was it an explosive, multi-candle-qualifying move) are
the acceptance evidence a future Breakout Retest / Liquidity Sweep
strategy will read — the structural *facts* those strategies need are
built here; the strategies themselves are not (source task §25/§59, and
explicitly out of this task's Definition of Done).

## Liquidity

`internal/liquidity` (depends on `internal/structure` — see
[`../architecture/dependency-rules.md`](../architecture/dependency-rules.md)'s
amendment note):

- **Pool**: one liquidity concentration, either anchored at a single
  confirmed swing (`liquidity.PoolFromSwing`, `Source` = `swing_high`/
  `swing_low`) or an ATR-tolerance-clustered group of same-kind, same-layer
  swings (`liquidity.ClusterEqualLevels`, `Source` = `equal_high`/
  `equal_low`, requiring `pool_minimum_touches` — currently 2). Clustering
  uses the same ATR-normalized tolerance band as swing-relation equality
  (`analysis.liquidity.equal_level_tolerance_atr`, 0.05 ATR) — one
  canonical "same level" definition shared across structure and
  liquidity, not two independently-drifting ones (exactly the class of
  bug this session's PRs #574–#580 spent hours fixing in the legacy
  Python system).
- **Sweep**: the first candle that trades beyond a pool's outer edge —
  `liquidity.DetectSweep`. This alone **is** the completed "swept" event;
  no reclaim check is needed for a pool's own `SweptAt` (unlike a
  structural break, which needs one to distinguish `BreakSweep` from a
  held break).
- **Reclaim**: purely informational context on an already-swept pool
  (`liquidity.DetectReclaim`, `Pool.ReclaimedAt`) — never un-sets
  `SweptAt`.

## Threshold Provenance

Every Structure V2 / Liquidity V1 config value in `config/analysis.yml`'s
`analysis.{history,structure,liquidity}` sections carries a comment
tracing it to one of three sources:

1. **Direct port of an already-shipped, already-tested Python value** —
   e.g. `break.minimum_penetration_atr` = 0.5 (PR #574's
   `invalidation_tolerance_atr`), `break.sweep_reclaim_bars` = 6 (PR
   #574), `break.displacement_range_atr`/`displacement_body_dominance` =
   1.5/0.55 (`zones.py::displacement`'s own `k`/`body_frac`),
   `equal_level.tolerance_atr` = 0.05 (`technique_geometry.py`'s
   `epsilon()`).
2. **Matches an existing Python convention without a single canonical
   source** — `pivot.{left_bars,right_bars}` = 2, matching the common
   `swing_fractal_n=2` default seen across `swings.py`/`structure.py`
   call sites.
3. **No Python precedent — a genuine V2 concept, initial calibration**
   — `swing.minimum_excursion_atr` (0.5) and the three layer-promotion
   thresholds (1.0/2.0/3.5 ATR), `break.failed_break_reclaim_bars` (3),
   `liquidity.pool_minimum_touches` (2). These are **not** empirically
   tuned against extensive labelled data — flagged as a research item in
   [`../analysis-engine-v2-migration.md`](../analysis-engine-v2-migration.md),
   not presented as final (source task §16's own warning against
   overfitting to a handful of recent trades applies in reverse here too:
   these weren't tuned to any trades at all yet, recent or otherwise).

## What This Specification Does Not Cover

Technical zones (supply/demand, order blocks, FVG/iFVG, breaker, flip —
source task §32), regime classification (§38), session context (§39), and
per-layer differentiated calculation windows (§6's M5:100/250/500/800
example — every timeframe's structure/liquidity pass currently uses its
**full** stored window, not a narrower per-layer slice; a real, bounded
simplification, not a violation of "don't scan everything on every event"
— see [`../analysis-engine-v2-migration.md`](../analysis-engine-v2-migration.md)
for why). None of these are implemented. All are out of this task's own
Definition of Done.
