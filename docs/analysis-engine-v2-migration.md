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
| Technical zones (supply/demand, OB, FVG/iFVG, breaker, flip) | `app/analysis/zones.py`, `app/analysis/dealing_range.py` | `internal/zone` (`Zone`, `Book`, lifecycle/relevance) | **Explicit redesign** — per-origin geometry, hold-based invalidation, separate relevance | Shadow only | Not removable until strategy cutover |
| Trendline V2 (causal construction + live interaction) | `app/analysis/trendline_v2.py` (V1 in `trendlines.py` confirmed dead/shadow-metrics-only, not ported) | `internal/trendline` (`Build`, `Update`, `EvaluateInteraction`) | **Exact parity** — same immutable-anchor causal construction, validation-touch reaction-window deferral, health/lifecycle state machine, dedup, and live-interaction classifier; one faithfully-ported ATR median choice (unlike sibling S4 domains' simplified last-value ATR) documented in `internal/trendline/doc.go` | Shadow only | Not removable |
| Key level clustering + role | `app/analysis/levels.py`, `app/analysis/key_level_role.py` | `internal/keylevel` (`Cluster`, `Update`, `Role`) | **Exact parity** on clustering/round-levels/wick-touch re-enrichment/dedupe and role classification, with one documented ATR simplification (one canonical scalar ATR throughout, not Python's median-for-clustering vs. per-swing-for-round-levels split) | Shadow only | Not removable |
| Session/PDH-PDL/PWH-PWL levels, sweep detection, active session | `app/analysis/session_liquidity.py` (`session_levels`, `previous_week_levels`; no active-session classifier) | `internal/session` (`Update`, `State`, `Book`) | **Exact parity** on levels/sweep (same window/rollover/week-start rules, same cross-window sweep scan), plus **new**: the active-session (Asia/London/NY) classifier has no Python equivalent — added to finally populate `context.SessionContext`, previously an honest empty placeholder | Shadow only | Not removable |
| Fibonacci ladder, premium/discount dealing range | `app/analysis/fibonacci.py`, `app/analysis/dealing_range.py` | `internal/fib` (`Ladder`, `NearestLevel`, `Resolve`, `Update`) | **Exact parity** — same retracement/extension ratios, same bracketing/opposing swing-pair search, same premium/discount + fine fib-zone thresholds | Shadow only | Not removable |
| Technical opportunity lifecycle | Legacy candidate/delivery state mixes technical setup validity with execution policy | `internal/opportunity` (`DeterministicID`, `Book`) | **Explicit redesign** — one per-symbol runtime book owns Created → Active → Invalidated/Expired transitions, deduplicates semantic IDs, and permits only strategy-owned technical terminal reasons; it has no account, broker, or Kafka dependency | Phase S8 wired it into the live per-symbol engine loop; real candidates now flow through it against real data (see next two rows) | Legacy authority remains until strategy cutover |
| Strategy registry / evaluator | Legacy Python family registries and broad scanner passes | `internal/strategy` (`Config`, `Registry`, `Evaluate`) | **Explicit redesign** — the complete semantic V2 catalog is configuration-declared; only enabled concrete implementations instantiate; closed-bar evaluation runs only strategies that require that timeframe and defers until all declared timeframe context exists | Phase S6 done; Phase S8 wired `Registry.Evaluate` into `SymbolWorker.ApplyWithResult` via a new `internal/engine/strategies.go` composition root (the one place a strategy subpackage may be imported, per the architecture rank rule) | Legacy authority remains until strategy cutover |
| Independent strategy theses (`key_level`, `supply`, `demand`, `order_block`, `fvg`, `flip_zone`, `session_level`) | Various legacy detector functions in `detectors.py` (`docs/analysis/strategy-v2-catalog.md`'s per-row mapping) | `internal/strategy/{keylevel,supply,demand,orderblock,fvg,flipzone,sessionlevel}` | **Explicit redesign** — each strategy is a fully independent Go package (no shared strategy base class; only canonical market-fact primitives — `zone.Relevance`, `structure.Swing`, `liquidity.Pool` — are shared), each with its own `Evaluate`, own quality-scoring reasoning, own config parsing/validation, own spec doc under `docs/analysis/strategies/`, own real tests under `test/strategy/<name>` | Phase S7: 7 of 19 real and `enabled: true`; Phase S8: proven live against real XAU M5 data via `cmd/replay` (147 real opportunities across 4 of the 7 strategies for that dataset) and a real `test/engine` integration test using the same data | Shadow only — Phase S9 (Kafka publication) has not run; a live opportunity reaches `OpportunityBook`/`AnalysisSnapshot.Opportunities` but nothing publishes it anywhere yet |
| Engine ↔ strategy wiring | N/A (no equivalent — the legacy scanner calls detector functions directly, no registry indirection) | `internal/engine/strategies.go` (composition root), `SymbolWorker.ApplyWithResult` (evaluation + lifecycle observation) | **New** — Phase S8. After every closed-bar context rebuild, `Registry.Evaluate` runs against the just-closed timeframe's dependent strategies; every returned `Candidate` is fed through `state.Opportunities.Observe`, then `Expire` applies each strategy's own technical deadline. Two real telemetry phases (`PhaseStrategy`, `PhaseOpportunity`) and five lifecycle-transition counters were added, all a documented amendment to the originally-frozen telemetry list | Working, verified against real data | N/A |
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
- **Regime context** (source task §38) is left as a bare placeholder
  (`RegimeContext{Kind string}`) with no real classification logic —
  confirmed this is *not* part of the 50-item Definition of Done, so this
  is a legitimate, explicit deferral, not a gap in required scope.
- **Technical zones** (Supply/Demand, Order Blocks, FVG/iFVG, Breaker,
  Flip) are now implemented in `internal/zone` as Phase S3. Strategy-level
  entry/quality decisions and higher-layer merging/reconciliation remain
  deferred to the independent strategy phases.
- **Session context** (source task §39) is now implemented in
  `internal/session` as Phase S4's first domain — see
  [`analysis/shared-primitives-v2.md`](analysis/shared-primitives-v2.md).
  `context.SessionContext` carries the real `session.State` for the
  primary timeframe rather than the prior empty placeholder.
- **Per-strategy packages**: 7 of 19 are now implemented and enabled
  (`key_level`, `supply`, `demand`, `order_block`, `fvg`, `flip_zone`,
  `session_level` — Phase S7, this task). The remaining 12
  (`confluence_zone`, `ifvg`, `crt`, `trendline`, `range_edge`,
  `box_breakout`, `momentum_ride`, `snap_back`, `liquidity_sweep`,
  `range_sweep`, `impulse_pullback`, `scalp_breakout_retest`) remain
  entirely unimplemented — each has a specific, individually-documented
  reason (a contingent spec write-up still needed, a primitive this phase
  did not build, or the compositional strategy waiting on its
  non-compositional siblings), not a silent omission; see
  `docs/analysis/strategy-v2-catalog.md`'s "Phase S7 status" for the
  itemized list. No S7 strategy candidate has reached the engine or Kafka
  — Phase S8 (engine wiring) and Phase S9 (Kafka publication) are
  separate, unstarted phases.
- **Strategy-level config provenance**: each S7 strategy still hardcodes
  its own known-compatible algorithm versions (`structure=v2`,
  `liquidity=v1`, `zone=v1`) and computes its own narrow
  `ConfigFingerprint` from only its OWN `strategy.Config.Parameters` (a
  strategy has no access to `*config.Document` — it sits below
  `internal/config`'s rank) — this part is unchanged and remains a real,
  documented limitation. **Phase S8 did the enrichment this doc
  previously flagged as expected**: `engine.Settings.ConfigVersion`/
  `ConfigFingerprint` (the SAME whole-resolved-document provenance
  `ConfigProvenanceFromConfig` already computes for Kafka envelopes) now
  overwrite `Candidate.Provenance.ConfigVersion`/`ConfigFingerprint` in
  `SymbolWorker.ApplyWithResult`, right before a Candidate reaches
  `OpportunityBook.Observe` — so the value actually stored in the Book
  (and later published by S9) is the real whole-document fingerprint, not
  the strategy's own narrower placeholder. `StructureVersion`/
  `LiquidityVersion`/`ZoneVersion` remain each strategy's own
  hardcoded, version-pinned constants — engine does not overwrite those.
- **Two real Phase S8 bugs were found and fixed by running real strategy
  evaluation against real XAU M5 data** (`cmd/replay`, `test/engine`'s new
  `TestEngine_RealS7StrategiesProduceRealOpportunitiesAgainstRealXAUData`),
  not caught by any hand-built fixture or unit test beforehand:
  1. `opportunity.Book`'s identity-collision check originally compared
     Entry/Invalidation/CreatedAt too, but every Phase S7 strategy's
     Invalidation is ATR-relative (and several derive CreatedAt from a
     touch/swing-anchored reference) — both legitimately drift between
     re-evaluations of the same still-valid setup, so the very first real
     replay run rejected a real, valid re-observation as a false
     collision. Fixed by narrowing the check to the four true identity
     fields (Strategy/Version/Symbol/Direction) that `DeterministicID`
     itself already hashes together with `SetupKey` — see
     `docs/analysis/opportunity-lifecycle-v2.md`'s own amendment note.
  2. `key_level`'s `SetupKey` used its cluster centroid's raw 6-decimal
     price directly; `internal/keylevel` re-clusters every closed bar, so
     that price jitters slightly for the same real level, producing a new
     `SetupKey` (and therefore a "new" opportunity) almost every
     evaluation — 147 "live" opportunities for one strategy across a
     300-bar window that likely represented far fewer real levels. Fixed
     by bucketing the price to a fixed fraction of itself before hashing
     — see `docs/analysis/strategies/key_level.md`'s own "Real bug found"
     section for the two other bucketing approaches that were tried and
     empirically rejected (both made the flooding worse, not better).
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
