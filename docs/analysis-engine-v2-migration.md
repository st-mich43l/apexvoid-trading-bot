# Analysis Engine V2 — Migration Tracking

Tracks, capability by capability, where each piece of market-intelligence
logic lives: the remaining Python implementation, the Go Analysis Engine
owner, whether the behavior is parity or a redesign, and whether the live
automatic opportunity path uses it.

**Current production boundary:** the Go Analysis Engine owns automatic
opportunity detection, lifecycle, zones, invalidation, arbitration, thesis
correlation, quality and stop-envelope facts. Algo Bot remains the execution
policy owner: it applies freshness, broker/account, exposure and submission
checks to Go's published opportunity. Manual Algo and display-only market
maps remain Python-owned. The Python detector modules listed below are not a
second live automatic source; they remain only where an execution or
presentation consumer still imports them.

**Migration complete (2026-10-02):** all 19 configured strategy
factories and their technical inputs are Go-owned in production. The Python
worker no longer rebuilds HTF zones/levels, runs the M1 detector, or imports
legacy detector/confluence identity code; it consumes Go opportunity and
barrier facts and performs execution-time checks only. The historical
"Shadow only" labels below describe the original S-phase snapshots and are
superseded for the automatic path. The retired Python detector/scanner graph,
duplicate technical math and Python scalp technical publisher have been
deleted. Manual Algo, execution policy, Telegram, journals and position
management remain Python-owned.

> Status note: the matrix below is retained as the historical migration
> record, not a current status table. Any row labelled `Shadow only` or `Not
> removable` predates the live Go cutover. The authoritative current boundary
> is the completion statement above and
> `algo-bot/tests/test_go_execution_boundary.py`.

**Publication geometry invariant (2026-10-02):** before a Go opportunity is
stored in the lifecycle book or published to Kafka, its entry band, technical
invalidation, and targets are aligned to the resolved instrument
`price_digits`. Entry bands are widened rather than narrowed; directional
stop/target rounding preserves the candidate's protective ordering. This is
technical publication geometry only: Algo Bot still owns quote selection,
entry-ladder construction, account risk, and broker submission.

**Confluence ownership (2026-10-02):** the Python V1/V2 confluence arithmetic
is now computed by `internal/confluence` from canonical Go zone, structure,
session, Fibonacci, reaction, and MAD facts. `technical_context.confluence`
publishes the selected stars plus named factors and raw components. Algo Bot
consumes that value for policy telemetry and does not rerun the legacy
confluence detector. Older events without this additive block remain
backward-compatible and use their existing evidence-count compatibility path.
The source-zone quality inputs are intentionally documented as a remaining
parity item where Go does not yet retain Python-only Grade-A grab or exact
Fibonacci-level provenance.

| Capability | Legacy Python path (deleted) | V2 Go owner | Parity or redesign? | Historical status | Historical removal note |
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
| Regime classification | `app/analysis/regime.py` | `internal/regime` (`Classify`, `AcceptedBoxBreak`, `DisplacementGrade`) | **Parity port** — consumes canonical Go ATR, promoted swings and fib dealing range; config is resolved centrally and the result is carried by every timeframe context | Shadow only | Not removable |
| MAD / Asia phase | `app/analysis/mad_phase.py` | `internal/mad` (`UpdateAsiaRangeSeal`, `Classify`, `Features`) | **Parity port** — causal Asia seal, stale-day fail-closed behavior, single/double sweep-reclaim classification, accepted expansion and accumulation quality; carried in canonical timeframe context | Shadow only | Not removable |
| Candle Confirmation V2 | `app/analysis/candle_geometry.py`, `candle_rejection.py`, `candle_displacement.py`, `candle_sequences.py`, `candle_evidence.py` | `internal/candle/evidence.go`, `opportunity.TechnicalContext.CandleEvidence`, Kafka `technical_context.candle_evidence` | **Additive parity port** — shared OHLC geometry, rejection/displacement/sequence families, bounded score and presentation labels are computed from the same closed observation bars; descriptive only and never an eligibility/risk gate | Shadow/descriptive context | Not removable until the remaining Python detector import audit is closed |
| Technical zones (supply/demand, OB, FVG/iFVG, breaker, flip) | `app/analysis/zones.py`, `app/analysis/dealing_range.py` | `internal/zone` (`Zone`, `Book`, lifecycle/relevance) | **Explicit redesign** — per-origin geometry, hold-based invalidation, separate relevance | Shadow only | Not removable until strategy cutover |
| Trendline V2 (causal construction + live interaction) | `app/analysis/trendline_v2.py` (V1 in `trendlines.py` confirmed dead/shadow-metrics-only, not ported) | `internal/trendline` (`Build`, `Update`, `EvaluateInteraction`) | **Exact parity** — same immutable-anchor causal construction, validation-touch reaction-window deferral, health/lifecycle state machine, dedup, and live-interaction classifier; one faithfully-ported ATR median choice (unlike sibling S4 domains' simplified last-value ATR) documented in `internal/trendline/doc.go` | Shadow only | Not removable |
| Key level clustering + role | `app/analysis/levels.py`, `app/analysis/key_level_role.py` | `internal/keylevel` (`Cluster`, `Update`, `Role`) | **Exact parity** on clustering/round-levels/wick-touch re-enrichment/dedupe and role classification, with one documented ATR simplification (one canonical scalar ATR throughout, not Python's median-for-clustering vs. per-swing-for-round-levels split) | Shadow only | Not removable |
| Session/PDH-PDL/PWH-PWL levels, sweep detection, active session | `app/analysis/session_liquidity.py` (`session_levels`, `previous_week_levels`; no active-session classifier) | `internal/session` (`Update`, `State`, `Book`) | **Exact parity** on levels/sweep (same window/rollover/week-start rules, same cross-window sweep scan), plus **new**: the active-session (Asia/London/NY) classifier has no Python equivalent — added to finally populate `context.SessionContext`, previously an honest empty placeholder | Shadow only | Not removable |
| Fibonacci ladder, premium/discount dealing range | `app/analysis/fibonacci.py`, `app/analysis/dealing_range.py` | `internal/fib` (`Ladder`, `NearestLevel`, `Resolve`, `Update`) | **Exact parity** — same retracement/extension ratios, same bracketing/opposing swing-pair search, same premium/discount + fine fib-zone thresholds | Shadow only | Not removable |
| Technical opportunity lifecycle | Legacy candidate/delivery state mixes technical setup validity with execution policy | `internal/opportunity` (`DeterministicID`, `Book`) | **Explicit redesign** — one per-symbol runtime book owns Created → Active → Invalidated/Expired transitions, deduplicates semantic IDs, and permits only strategy-owned technical terminal reasons; it has no account, broker, or Kafka dependency | **Production live** through the Go Kafka lifecycle; Algo Bot consumes only Go-origin opportunities | Python lifecycle helpers remain only for execution/presentation consumers and can be retired after import inventory is clean |
| Strategy registry / evaluator | Legacy Python family registries and broad scanner passes | `internal/strategy` (`Config`, `Registry`, `Evaluate`) | **Explicit redesign** — the complete semantic V2 catalog is configuration-declared; only enabled concrete implementations instantiate; closed-bar evaluation runs only strategies that require that timeframe and defers until all declared timeframe context exists | Phase S6 done; Phase S8 wired `Registry.Evaluate` into `SymbolWorker.ApplyWithResult` via a new `internal/engine/strategies.go` composition root (the one place a strategy subpackage may be imported, per the architecture rank rule) | Legacy authority remains until strategy cutover |
| Strategy registry (19 entries; 18 independent theses) | Various legacy detector functions in `detectors.py` | `internal/strategy/*` (one package per registry entry) | **Explicit redesign** — each independent strategy is a Go package with its own `Evaluate`, configuration parsing/validation, specification under `docs/analysis/strategies/`, and deterministic qualifying/rejection/lifecycle coverage. `confluence_zone` is the one approved compositional exception: it consumes canonical facts and never invokes another strategy. | **Production live**: all 19 registry entries have concrete factories, are enabled in the resolved Go configuration, and publish eligible lifecycle transitions consumed by Algo Bot | Legacy detector definitions remain for import cleanup and manual/display consumers; they must not be reintroduced into automatic publication |
| Opportunity Kafka publication | Legacy has no equivalent event stream — trade-plan generation reads Python's own in-process state directly | `internal/engine/publisher.go` (`OpportunityPublisher`), `internal/transport/kafka` (`Producer.PublishOpportunity`/`PublishOpportunityInvalidated`, already built and real-broker-tested in the earlier Kafka transport task) | **New** — Phase S9 (source task §81-84). `SymbolWorker.observeTransition` enqueues every `ShouldPublish()` transition (Created/Invalidated/Expired); a single background goroutine per `Engine` (`OpportunityPublisher.Run`) drains that queue and calls the real Producer, off the ingestion hot path — `cmd/analysis-engine/main.go`'s own stated invariant ("a Kafka outage never becomes a candle-ingestion outage") would otherwise be violated by a synchronous publish inside `SymbolWorker`'s own mutex | Wired in `cmd/analysis-engine/main.go`; proven against a fake `OpportunityKafkaClient` (6 new tests: delivery, both/either payload shape, non-publishable transitions filtered, order preservation, retry-until-success, and the typed-nil-producer trap this wiring itself first hit) — **not** re-verified against a real broker in this session (no broker available in this sandbox); the underlying `Producer.PublishOpportunity`/`PublishOpportunityInvalidated` methods themselves were already real-broker-tested in the earlier Kafka transport task | N/A |
| Engine ↔ strategy wiring | N/A (no equivalent — the legacy scanner calls detector functions directly, no registry indirection) | `internal/engine/strategies.go` (composition root), `SymbolWorker.ApplyWithResult` (evaluation + lifecycle observation) | **New** — Phase S8. After every closed-bar context rebuild, `Registry.Evaluate` runs against the just-closed timeframe's dependent strategies; every returned `Candidate` is fed through `state.Opportunities.Observe`, then `Expire` applies each strategy's own technical deadline. Two real telemetry phases (`PhaseStrategy`, `PhaseOpportunity`) and five lifecycle-transition counters were added, all a documented amendment to the originally-frozen telemetry list | Working, verified against real data | N/A |
| Per-symbol event dispatch / worker | `app/analysis/worker.py` (one large sequential pass per symbol per bar, not clearly dependency-scoped) | `internal/engine/worker.go` (`SymbolWorker`), `engine.go` (`Engine`) | **Explicit redesign** — one mutex-guarded worker per symbol, concurrent across symbols, dependency-aware (an M1 close cannot trigger H1 recompute by construction, proven in `test/engine/worker_test.go`) | Shadow only (`cmd/replay` drives it directly; no live feed wired) | Not removable |
| Scanning / orchestration | `app/analysis/scanner.py` (one large file coordinating detection across all symbols/strategies) | *(deliberately not replicated — source task §60 explicitly forbids "another giant scanner file")* | N/A — architectural non-goal | N/A | N/A |
| Telemetry (analysis timing) | Ad hoc `time.time()` deltas scattered through `worker.py`/`engine.py`, not uniformly labeled | `internal/telemetry/metrics.go` (`Recorder`) | **New** — no Python equivalent structure; real per-phase timing (`marketdata_update_ms` through `event_total_ms`) added specifically for this task (source task §57) | Shadow only | N/A |
| Visualization | None in Python (charts are eyeballed from broker platforms / ad hoc notebook scripts, not a shipped module) | `internal/visualization/chart.go` (`RenderSnapshot`) | **New** — stdlib-only PNG renderer consuming only supplied `AnalysisSnapshot` facts: candles, swings, BOS/CHoCH, protected levels, liquidity pools, zones, and optional opportunity entry/invalidation/targets. Translucent overlays are alpha-composited for reviewability. | Shadow/research only (`cmd/replay -png` or `-setup-png-dir`) | N/A |
| Replay / backtest harness | Various one-off scripts, not a single canonical replay path sharing live code | `cmd/replay/main.go` | **New** — replays closed bars through the exact same `Engine.Dispatch` a live feed would use; Phase S10 captures a candidate's first-observed snapshot and writes focused PNGs around it. Verified against real production config + 300 real XAU M5 bars | Working, verified | N/A |
| Shadow-run event provenance | No equivalent: legacy scanner has no isolated technical-event plane | `marketdata.BarEvent.Origin`, Redis runtime bootstrap, `SymbolWorker` publication boundary | **New** — bootstrap and offline replay build exactly the same in-memory V2 state but suppress Kafka lifecycle publication. Only a newly observed/recovered live closed bar may publish `analysis.opportunity.v1` or its invalidation. This prevents every process restart from presenting retained historical setups as fresh opportunities. | Phase S11 ran in production shadow | No Algo Bot Kafka consumer exists, so this remains non-trading by construction |

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
- **Regime context** is now implemented in `internal/regime` and computed
  from the canonical per-timeframe inputs. The port includes the legacy
  chop/range classification, optional directional override, coiling state,
  accepted box-break detection and displacement-grade acceptance. It remains
  live technical context for the automatic Go opportunity path. Long-window
  Python comparison and threshold calibration remain evidence gaps below.
- **Technical zones** (Supply/Demand, Order Blocks, FVG/iFVG, Breaker,
  Flip) are now implemented in `internal/zone` as Phase S3. Strategy-level
  entry/quality decisions and higher-layer merging/reconciliation are wired
  through the 19 live Go strategy packages; Algo Bot still owns execution
  entry/risk policy after receiving the Go facts.
- **Session context** (source task §39) is now implemented in
  `internal/session` as Phase S4's first domain.
  `context.SessionContext` carries the real `session.State` for the
  primary timeframe rather than the prior empty placeholder.
- **Per-strategy packages**: all 19 registered strategies are implemented,
  enabled in the resolved Analysis Engine configuration, and are the live
  automatic opportunity source. The Go engine still does not own account
  sizing, exposure policy, broker submission, or Telegram delivery; those
  remain Algo Bot/executor responsibilities.
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
     itself already hashes together with `SetupKey`.
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
- **Phase S9 (Kafka opportunity publication) is real but has real,
  documented limitations of its own**:
  - `OpportunityPublisher`'s retry queue is a plain in-memory slice, not
    a durable outbox — Configuration V3/source task §45 explicitly does
    not require the Analysis Engine to own a PostgreSQL record for this.
    A process restart during a Kafka outage loses whatever is still
    queued, the same in-memory-only limitation `internal/opportunity.Book`
    itself already has. It retries a failed job indefinitely rather than
    dropping it (the "must never silently discard an opportunity"
    contract `cmd/analysis-engine/main.go` already stated before this
    phase existed), so the tradeoff is unbounded memory growth during an
    extended outage, not data loss while the process stays up.
  - Not re-verified against a real Kafka broker in this session — no
    broker is available in this sandbox. `OpportunityPublisher` is proven
    against a real, non-mocked `OpportunityKafkaClient` interface
    implementation (a fake, in `test/engine/publisher_test.go`), and the
    underlying `Producer.PublishOpportunity`/`PublishOpportunityInvalidated`
    methods it calls were already real-broker-tested in the earlier
    Kafka transport task — but the two have not been exercised together
    end to end against a live broker.
  - Technical invalidation is now evaluated by the lifecycle book on each
    closed bar for the candidate's own observed timeframe. A confirmed close
    through the strategy-owned invalidation threshold publishes exactly one
    `analysis.opportunity.invalidated.v1` event with the normalized strategy
    label as its machine-readable reason; time-based `Expire` remains the
    separate `SETUP_EXPIRED` path.
- **Threshold calibration**: several V2-only thresholds (swing
  promotion ATR cutoffs, `failed_break_reclaim_bars`, liquidity
  `pool_minimum_touches`) have no Python precedent and were set to
  reasonable initial values, not empirically tuned against a labelled
  dataset. A
  real calibration pass against the human-labelled research fixture
  library (source task §68–§71) is unstarted follow-up work, not
  attempted this task.
- **Human-labelled research fixture library** (source task §68–§71: 14
  structure categories × 5 instruments) is **not built**. This is
  flagged honestly as substantial follow-up research work requiring
  iterative human chart review — it was not silently skipped, it was
  never attempted, and should not be assumed done.

## Historical cutover gate and remaining evidence

The original S-phase gate required a meaningful Python-vs-Go shadow
comparison before cutover. Production has since been explicitly switched to
the Go technical authority (PR #713); the Python detector graph is no longer
reachable from automatic production entrypoints, and Algo Bot consumes Go
opportunities with execution policy still in Python. That operational cutover
is verified by S13 import classification and production Kafka/database data.

The long-window Python-vs-production comparison is still an evidence gap for
the migration audit: `cmd/replay` proves the Go pipeline runs on real data,
but does not by itself reproduce a historical Python production decision log.
It must be completed before claiming byte-for-byte historical parity or
retiring every offline Python compatibility module. This gap does not restore
Python as a live technical authority.
