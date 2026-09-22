# Analysis Engine V2 — Migration Tracking

Tracks, capability by capability, where each piece of market-intelligence
logic now lives: the legacy Python path (still production), the V2 Go
owner built this task, whether V2 is an exact-parity port or a deliberate
redesign (source task §3), whether V2 is live yet, and whether the
Python path can be removed.

**Nothing in this table has been cut over.** Python remains the sole
production path for every capability below. V2 Go runs only via
`cmd/replay` (research/shadow use) — it is not wired into live trade-plan
generation. See [`architecture/analysis-engine.md`](architecture/analysis-engine.md)
and [ADR-004](adr/) for the transport/cutover gating this depends on.

| Capability | Legacy Python path | V2 Go owner | Parity or redesign? | Live status | Python removal status |
|---|---|---|---|---|---|
| OHLC candle model / validation | `app/analysis/*` implicit dict shape, no central validator | `internal/market` (`price.go`, `window.go`), `internal/marketdata/validation.go` | **Exact parity** (numeric geometry) + **new**: explicit `ValidateCandle`/`InvalidReason` enum has no Python precedent — legacy never rejected malformed bars structurally | Shadow only (`cmd/replay`) | Not removable — still the only path live trading reads |
| True Range / ATR | `app/analysis/indicators.py` (Simple/Wilder, recomputed ad hoc per caller) | `internal/indicator/canonical.go` (`CanonicalATR`), `rolling_atr.go` (incremental) | **Exact parity** — same Simple/Wilder formulas, dispatched by `analysis.indicators.atr.{algorithm,length}` instead of each call site choosing independently | Shadow only | Not removable |
| Swing/pivot detection | `app/analysis/swings.py` (fixed-N fractal, no ATR normalization, no causal confirmation timestamp) | `internal/structure/pivot.go` (`DetectPivots`) | **Explicit redesign** — adds ATR-normalized weaker-side excursion strength, explicit `ConfirmedAt` separate from `Time`, ATR-based significance filter before anything becomes a Swing (`swing.go`'s `PromoteSwing`); Python's raw fractal-only pivots are baseline, not the target | Shadow only | Not removable |
| Structure hierarchy (layer) | `app/analysis/structure.py` — layer is implicit from which timeframe called it, no promotion rule | `internal/structure/swing.go` (`StructureLayer`, ATR-threshold promotion) | **Explicit redesign** — layer is now a function of `ExcursionATR`, not source timeframe (source task §13) | Shadow only | Not removable |
| HH/HL/LH/LL, equal high/low | `app/analysis/structure.py` + `app/analysis/technique_geometry.py::epsilon()` for equality | `internal/structure/classifier.go` (`ClassifySwingRelation`) | **Redesign, parity-adjacent** — same equality-tolerance concept as `epsilon()`, carried into `analysis.structure.equal_level.tolerance_atr` (0.05 ATR, same value), but relation classification itself (HH/HL/LH/LL) is a new explicit enum, not Python's implicit branching | Shadow only | Not removable |
| Trend state / protected high-low | `app/analysis/structure.py` (trend flips eagerly, no distinct "protected level" concept, no persistence-through-range rule) | `internal/structure/hierarchy.go` (`BuildLayerState`, `TrendState`) | **Explicit redesign** — protected-level persistence through a Range read (source task §17) has no Python equivalent; Python drops or recomputes trend on every call | Shadow only | Not removable |
| BOS / CHoCH classification | `app/analysis/structure.py::detect_bos_choch` (single boolean-ish break check, wick and close often conflated, no protected-level gate on CHoCH) | `internal/structure/break.go` (`DetectBreak`), `event.go` (`ClassifyEvent`) | **Explicit redesign** — five-way `BreakType` (Wick/Close/Displacement/Sweep/Failed) replaces Python's binary break check; CHoCH requires the broken swing to be the layer's *current protected level*, which Python does not check | Shadow only | Not removable |
| Displacement | `app/analysis/zones.py::displacement()` (`k=1.5` ATR, `body_frac=0.55`) | `internal/structure/displacement.go` (`DetectDisplacement`) | **Exact parity** — formula and both thresholds ported directly from the shipped Python (this session's own PR #574 provenance), shortest-qualifying-run selection preserved | Shadow only | Not removable |
| Liquidity pools (swing-anchored) | `app/analysis/liquidity.py` | `internal/liquidity/pool.go` (`PoolFromSwing`) | **Explicit redesign** — pool now carries `TouchCount`/`FirstTouchAt`/`LastTouchAt`/`Strength` fields with no direct Python equivalent (source task's own prose asked for richer metadata than its literal struct snippet) | Shadow only | Not removable |
| Equal-level liquidity clustering | `app/analysis/session_liquidity.py` (ad hoc clustering, session-scoped only) | `internal/liquidity/equal_high_low.go` (`ClusterEqualLevels`) | **Explicit redesign** — chain-based clustering over ANY layer's swings (not session-scoped), same ATR-tolerance concept as structure's equal-level check reused verbatim (one canonical tolerance, not two) | Shadow only | Not removable |
| Sweep / reclaim | `app/analysis/liquidity.py` (sweep detection present; reclaim tracked inconsistently, sometimes unsets the sweep flag) | `internal/liquidity/pool.go` (`DetectSweep`, `DetectReclaim`) | **Explicit redesign** — reclaim is explicitly informational-only and never un-sets `SweptAt` (a real, documented behavior change from Python's inconsistent handling) | Shadow only | Not removable |
| Market context / bias | `app/analysis/engine.py` (bias computed by a parallel HTF-swing pass, independent of the structure module's own read — the two can and do disagree in production) | `internal/context/market.go` (`Build`, `DeriveBias`) | **Explicit redesign** — bias is now strictly *derived* from the same canonical `StructureState` (Major→Intermediate→Internal→Micro precedence), never computed independently (source task §36 — this was an explicit, named bug class in the legacy system) | Shadow only | Not removable |
| Multi-timeframe disagreement | `app/analysis/engine.py` (flattens to a single bias value; per-timeframe disagreement is not preserved in the returned object) | `internal/context/market.go` (`MarketContext.Timeframes map`) | **Explicit redesign** — every timeframe's own structure/liquidity read is preserved in the context, not collapsed | Shadow only | Not removable |
| Regime classification | `app/analysis/regime.py` | *(not built this task — placeholder `RegimeContext{Kind string}` only)* | N/A — deferred | Not started | N/A |
| Session context | `app/analysis/session_liquidity.py` (session boundary logic only, embedded in liquidity) | *(not built this task — placeholder `SessionContext{Name string}` only)* | N/A — deferred | Not started | N/A |
| Technical zones (supply/demand, OB, FVG/iFVG, breaker, flip) | `app/analysis/zones.py`, `app/analysis/dealing_range.py` | *(not built — explicitly out of this task's Definition of Done)* | N/A — deferred | Not started | N/A |
| Per-symbol event dispatch / worker | `app/analysis/worker.py` (one large sequential pass per symbol per bar, not clearly dependency-scoped) | `internal/engine/worker.go` (`SymbolWorker`), `engine.go` (`Engine`) | **Explicit redesign** — one mutex-guarded worker per symbol, concurrent across symbols, dependency-aware (an M1 close cannot trigger H1 recompute by construction, proven in `test/engine/worker_test.go`) | Shadow only (`cmd/replay` drives it directly; no live feed wired) | Not removable |
| Scanning / orchestration | `app/analysis/scanner.py` (one large file coordinating detection across all symbols/strategies) | *(deliberately not replicated — source task §60 explicitly forbids "another giant scanner file")* | N/A — architectural non-goal | N/A | N/A |
| Telemetry (analysis timing) | Ad hoc `time.time()` deltas scattered through `worker.py`/`engine.py`, not uniformly labeled | `internal/telemetry/metrics.go` (`Recorder`) | **New** — no Python equivalent structure; real per-phase timing (`marketdata_update_ms` through `event_total_ms`) added specifically for this task (source task §57) | Shadow only | N/A |
| Visualization | None in Python (charts are eyeballed from broker platforms / ad hoc notebook scripts, not a shipped module) | `internal/visualization/chart.go` (`Render`) | **New** — stdlib-only PNG renderer (candles, swings, breaks, liquidity pools); no alpha-blended pool overlap (documented cosmetic limitation, see below) | Shadow only (`cmd/replay -png`) | N/A |
| Replay / backtest harness | Various one-off scripts, not a single canonical replay path sharing live code | `cmd/replay/main.go` | **New** — replays closed bars through the exact same `Engine.Dispatch` a live feed would use; verified end to end against real production config + 300 real XAU M5 bars (see report) | Working, verified | N/A |

## Known, Documented V2 Limitations (not silently omitted)

- **Per-layer differentiated calculation windows** (source task §6's own
  M5 micro=100/internal=250/intermediate=500/major=800 example) are
  **not implemented**. Every timeframe's structure/liquidity pass uses
  its **full stored window** (per `analysis.history.depth`) in one pass.
  This is a documented simplification, not an oversight — the
  dependency-aware recompute property (§42: an M1 close never
  recomputes H1 structure) is still fully proven, because each
  timeframe's computation is self-contained; only the *within-timeframe*
  narrower-lookback optimization is deferred.
- **Regime and Session context** (source task §38/§39) are left as
  bare placeholders (`RegimeContext{Kind string}`, `SessionContext{Name
  string}`) with no real classification logic — confirmed these are
  *not* part of the 50-item Definition of Done, so this is a legitimate,
  explicit deferral, not a gap in required scope.
- **Technical zones** (Supply/Demand, Order Blocks, FVG/iFVG, Breaker,
  Flip — source task §32) are entirely unimplemented, per the task's own
  explicit instruction to defer them until Structure V2 is stable, and
  confirmed absent from the 50-item Definition of Done.
- **Per-strategy packages** (Breakout Retest, Liquidity Sweep, etc.) are
  entirely unimplemented — explicitly the *next* task per the source
  spec, gated on this foundation passing review.
- **PNG pool-band rendering is not true alpha compositing** —
  `image.RGBA.Set()` overwrites pixels rather than blending, so
  overlapping liquidity pool bands render as solid overwritten
  horizontal strips, not a translucent gradient. Cosmetic only; does not
  affect any analytical output.
- **Threshold calibration**: several V2-only thresholds (swing
  promotion ATR cutoffs, `failed_break_reclaim_bars`, liquidity
  `pool_minimum_touches`) have no Python precedent and were set to
  reasonable initial values, not empirically tuned against a labelled
  dataset — see [`analysis/market-structure-v2.md`](analysis/market-structure-v2.md)'s
  "Threshold Provenance" section for the exact list and reasoning. A
  real calibration pass against the human-labelled research fixture
  library (source task §68–§71) is unstarted follow-up work, not
  attempted this task.
- **Human-labelled research fixture library** (source task §68–§71: 14
  structure categories × 5 instruments) is **not built**. This is
  flagged honestly as substantial follow-up research work requiring
  iterative human chart review — it was not silently skipped, it was
  never attempted, and should not be assumed done.

## Cutover Gate

Per source task §49/§61 and this repo's existing ADR-004 discipline: V2
Go does not take over live trade-plan generation until a real
replay/shadow comparison against Python's production output has run
over a meaningful stretch of real market data and the differences are
understood and accepted (not just "it compiled and one 300-bar replay
looked reasonable"). That comparison is unstarted — `cmd/replay`
proves the pipeline *runs* correctly end to end on real data, it does
not yet constitute a shadow-vs-production comparison.
