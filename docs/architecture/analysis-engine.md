# Analysis Engine (Go)

"What is happening in the market?" — deterministic technical interpretation
only. See [`service-boundaries.md`](service-boundaries.md) for the full
owns/must-not-own list.

This document defines the **target** package tree and the type shapes the
rest of the engine builds on. For what is actually implemented today, read
`docs/go-analysis-migration-audit.md` first — it is the binding record of
the Python computation graph this Go code ports, every duplicate/divergent
calculation found, and why each package boundary below is drawn where it
is. This document does not re-derive that audit; it freezes the boundary
the audit's own package names already imply, and extends it to the layers
the audit's Stage 0–2 scope didn't need yet (context, strategy, opportunity,
state, engine, transport, visualization, telemetry).

## Directory tree

```text
analysis-engine/
├── cmd/
│   ├── analysis-engine/main.go     (exists)
│   ├── replay/main.go              (implemented — Analysis Engine V2 task; drives Engine.Dispatch over real historical bars, optional PNG output)
│   ├── benchmark/main.go           (not yet created — benchmarks live under test/benchmark/ instead)
│   └── inspect/main.go             (not yet created)
├── internal/
│   ├── config/          (exists — Configuration V3 direct reader)
│   ├── market/           (exists — Candle, CandleWindow, Timeframe, Symbol, Geometry, Price)
│   ├── marketdata/       (implemented — Analysis Engine V2 task; BarEvent, validation, TimeframeHistory, MarketHistory, bootstrap)
│   ├── indicator/        (exists — TrueRange, SimpleATR, WilderATR; extended this task with CanonicalATR dispatch + RollingSimpleATR)
│   ├── structure/        (implemented — Analysis Engine V2 task; pivot/swing/classifier/displacement/break/event/hierarchy/state)
│   ├── liquidity/        (implemented — Analysis Engine V2 task; pool/equal-high-low/sweep/reclaim)
│   ├── zone/             (implemented canonical technical-zone domain — lifecycle and relevance)
│   ├── context/          (implemented — Analysis Engine V2 task; MarketContext, Build, DeriveBias)
│   ├── opportunity/       (implemented lifecycle domain — stable identity, dedup, technical terminal transitions)
│   ├── strategy/          (registry + dependency-aware evaluator implemented; no strategy thesis yet)
│   ├── confluence/        (still scaffolded — compositional evidence combining, doc.go + Score)
│   ├── state/             (implemented — Analysis Engine V2 task; SymbolState wires History/Structure/Liquidity/Context/Opportunities together)
│   ├── engine/            (implemented — Analysis Engine V2 task; SymbolWorker, Engine, Settings, AnalysisSnapshot)
│   ├── transport/         (Redis market runtime and Kafka producer implemented — ADR-008/009/010)
│   ├── visualization/     (implemented — Analysis Engine V2 task; stdlib PNG renderer, consumes AnalysisSnapshot data only)
│   └── telemetry/         (implemented — Analysis Engine V2 task; Recorder, per-phase timing + counters)
├── test/                  (exists — centralized, one subdir per domain; see below)
├── testdata/              (exists — golden-master fixtures, incl. real XAU M5 production data)
├── go.mod / go.sum
└── README.md
```

"Scaffolded" (still applies to `strategy` and `confluence`) means: a real,
compiling Go package with
type declarations and doc comments that establish the package's
ownership boundary and prove the dependency direction against its
neighbors — not a strategy or indicator implementation. "Implemented"
(the rest, as of the Analysis Engine V2 task and the Redis/Kafka transport
correction) means real, tested, benchmarked logic — see
[`../analysis-engine-v2-migration.md`](../analysis-engine-v2-migration.md)
for the Analysis Engine V2 capability table and
[`../transport/kafka.md`](../transport/kafka.md) for the Kafka transport
one.

## Dependency graph (with three corrections)

```text
market / telemetry
  ↓
indicator / marketdata / config
  ↓
structure / zone
  ↓
liquidity
  ↓
context
  ↓
opportunity            ← Candidate identity and technical lifecycle
  ↓
strategy / confluence / state  ← behavior: Strategy.Evaluate(ctx) returns []opportunity.Candidate
  ↓
transport (incl. transport/kafka, transport/redis)
  ↓
engine
```

`visualization` is the sole leaf consumer outside this chain: it may
import any core type; nothing core imports it.

**Second correction, made during Analysis Engine V2 implementation**:
this doc originally placed `structure / liquidity / zone` as same-rank
siblings and classified `telemetry` as a leaf alongside `visualization`.
Actually building Analysis Engine V2 broke both assumptions —
`liquidity.PoolFromSwing` needs `structure.Swing` directly, and
`engine`/`worker.go` needs to call `telemetry.Recorder.Time` to record
its own phase timings, which a true "nothing imports this" leaf cannot
support. `liquidity` was promoted to its own rank (above `structure`/
`zone`, below `context`), and `telemetry` was moved to rank 0 alongside
`market`. Full detail and the enforcing test:
[`dependency-rules.md`](dependency-rules.md)'s own amendment section and
`analysis-engine/test/architecture/dependency_test.go`.

**Third correction, made implementing the Kafka transport task**:
`transport` originally sat above `engine` (reading the "engine → transport"
pipeline-diagram arrow as sequence order). But that task's own §1 states
the actual required **Go import** direction as "engine ↓ transport/kafka"
— `engine` must import `transport` to wire a real Redis runtime and Kafka
producer —
which is only legal under this graph's own rule if `transport` outranks
nothing above it and `engine` sits strictly above `transport`. Moving
`transport` to sit between `strategy/confluence/state` and `engine`
satisfies that AND keeps `strategy` unable to import `transport` at all
— exactly that task's own forbidden edge ("strategy → kafka") and this
doc's pre-existing "Strategies must NOT depend on Kafka/Redis" rule
(§115 below), now enforced by the rank table itself. Full detail:
[`dependency-rules.md`](dependency-rules.md)'s own amendment section and
[ADR-008](../adr/008-go-kafka-client.md).

**Correction to the source task's own §51 ordering, explained**: the source
task lists `strategy` before `opportunity`, which would forbid strategy
from importing opportunity — but its own §21 Go snippet has
`Strategy.Evaluate` return `[]opportunity.Candidate`, which requires exactly
that import. Taken literally the two sections contradict each other. This
is resolved by treating `opportunity` as a strategy-independent technical
lifecycle domain with **no** dependency on `strategy`, placing it below
`strategy` in the graph. `strategy` is the only one of the two that imports
the other. Full reasoning: ADR-003.

Rules (§51/§52), unchanged from the source task:

```text
indicator MUST NOT import strategy
structure MUST NOT import opportunity
zone MUST NOT import strategy
strategy MUST NOT import: Kafka, Redis, Telegram, Postgres, cTrader, algo-bot
```

Enforced by `test/architecture/dependency_test.go` (see below) — not just
documented.

## Canonical type shapes

These are the shapes this task's own spec writes out explicitly (§19, §21,
§24, §26–27). As of the Analysis Engine V2 implementation task,
`MarketContext`/`SymbolState`/`AnalysisSnapshot` below are real, populated
with actual structure/liquidity data (not placeholder fields sized only to
compile). Opportunity lifecycle and the S6 strategy registry/evaluator are now
implemented. Individual strategy theses remain Phase S7 work.

```go
// internal/context/market.go — real as of Analysis Engine V2
type MarketContext struct {
    Symbol     market.Symbol
    Timeframes map[market.Timeframe]*TimeframeContext
    Structure  StructureContext   // the primary timeframe's structure.StructureState
    Liquidity  LiquidityContext   // the primary timeframe's liquidity.LiquidityState
    Zones      ZoneContext        // primary timeframe's canonical zone state
    Bias       BiasContext        // DERIVED from Structure via DeriveBias, never computed independently
    Regime     RegimeContext      // placeholder — not in this task's DoD
    Volatility VolatilityContext
    Session    SessionContext     // placeholder — not in this task's DoD
}
```

`Timeframes` is the multi-timeframe-disagreement-preserving map: each
timeframe keeps its own `StructureContext`/`LiquidityContext`, never
flattened into one value (source task §36/§40).

```go
// internal/opportunity/candidate.go
type StrategyID string

type Candidate struct {
    ID              string
    Strategy        StrategyID
    StrategyVersion string
    Symbol          market.Symbol
    Direction       market.Direction

    Entry        EntryZone     // deliberately not named "Zone" — see note below
    Invalidation market.PriceLevel
    Targets      []Target

    Evidence []Evidence
    Quality  StrategyQuality

    CreatedAt  int64
    ExpiresAt  int64   // strategy-owned technical deadline
    Provenance AnalysisProvenance
}
```

**Naming deviation from the source spec, explained**: §24's snippet names
the entry field's type `Zone`. `internal/zone` (§18) already owns a much
richer `Zone` domain type (supply/demand/OB/FVG geometry with lifecycle and
mitigation state). Reusing that name for `opportunity.Candidate.Entry`
would either create a same-named type in two packages (confusing at every
call site) or force `opportunity` to import `internal/zone` for a two-field
price range, which is not a real dependency `opportunity` needs. The entry
field type is named `EntryZone` and defined locally in `opportunity` as a
plain `{Low, High float64}` range — a deliberate simplification of the
source spec's illustrative pseudocode, not a scope change.

```go
// internal/strategy/strategy.go
type StrategyID = opportunity.StrategyID // one shared identity, opportunity owns it

type Strategy interface {
    ID() StrategyID
    RequiredTimeframes() []market.Timeframe
    Evaluate(ctx *context.MarketContext) []opportunity.Candidate
}
```

```go
// internal/opportunity/candidate.go (continued)
type StrategyQuality struct {
    Overall    float64
    Components map[string]float64 // strategy-specific — breakout_quality, retest_quality, ... per §26
}
```

```go
// internal/state/symbol_state.go — real as of Analysis Engine V2
type SymbolState struct {
    Symbol market.Symbol

    History       *marketdata.MarketHistory // real per-timeframe bounded candle storage
    Measurements  *MeasurementBook          // canonical ATR series per timeframe
    Structure     *structure.Book
    Zones         *zone.Book
    Liquidity     *liquidity.Book
    Context       context.MarketContext
    Opportunities *opportunity.Book
}
```

`internal/zone` is now the canonical Phase S3 domain. It owns technical
geometry and lifecycle/relevance state; strategies must consume it through
`SymbolState`/`MarketContext` rather than rebuilding zones.

`SymbolState` is the one analytical truth for one symbol (§27). Strategies
read it (via `context.MarketContext`); they do not rebuild it (§28 — no
`BreakoutRetest → recalculate ATR → recalculate swings → rebuild zones`).

```go
// internal/engine/snapshot.go — real as of Analysis Engine V2
type AnalysisSnapshot struct {
    Symbol        market.Symbol
    Time          int64
    Context       context.MarketContext
    Structure     map[market.Timeframe]structure.StructureState
    Liquidity     map[market.Timeframe]liquidity.LiquidityState
    Opportunities []opportunity.Candidate // created/active lifecycle records only
    Version       SnapshotVersion         // {StructureVersion, LiquidityVersion} — unknown version fails closed at load, never silently assumed
}
```

`AnalysisSnapshot` (§37) is the one canonical, network-safe analysis result
— what visualization, research, journal correlation, debugging, and replay
all consume. Internal mutable `SymbolState` is never exposed directly as a
contract (§37).

## Independent strategy architecture (§20–22, frozen)

The legacy generic `reaction` family (`supply`/`demand`/`key_level`/
`trendline`/`liquidity` all inheriting shared entry/confirmation/
invalidation/quality/targeting/expiry logic) is **rejected** as the V2
model. Every strategy under `internal/strategy/<name>/` owns its own:

```text
market condition · setup thesis · required structure · required location
trigger · confirmation · entry geometry · technical invalidation
technical target thesis · expiry · strategy-specific quality model
```

The shared contract is the `Strategy` interface above — an engineering
contract, not shared trading behavior. `internal/confluence` is the
deliberate, documented exception: it may compose independent evidence
(demand + order block + FVG + Fibonacci + liquidity + flip zone), but no
other strategy inherits from a generic confluence base.

Strategy package slots this task's own §20 names (`breakoutretest`,
`liquiditysweep`, `orderblock`, `fvg`, `keylevel`, `supply`, `demand`,
`trendline`, `sessionlevel`, `rangeedge`, `boxbreakout`, `impulsepullback`,
`rangesweep`, `snapback`) are **architectural slots, not directories
created by this task** — per §58, creating 14 near-empty strategy
subpackages proves no dependency boundary that the `Strategy` interface
above doesn't already prove once, so none were scaffolded. Only strategies
that survive the redesign (Phase S7: approved independent strategies) get a
real package.

## Market history sizing

Per §12: stored history and calculation window are distinct concepts.
Actual, config-driven depths as of Analysis Engine V2
(`analysis.history.depth` in `config/analysis.yml`, read via
`engine.HistoryDepthsFromConfig` — never hardcoded in Go):

```text
M1   2,000 candles      M15  1,000 candles     H4   250 candles
M5   1,000 candles      H1     500 candles     D1   150 candles
```

`marketdata.TimeframeHistory` (wrapping `market.CandleWindow`) is the
bounded storage this sizing applies to, built via
`marketdata.NewMarketHistory(symbol, depths, allowReplace)`.

**Known simplification, documented not silent**: true **per-layer**
differentiated calculation windows (§6's own example: M5 stored=1000,
micro-lookback=100, internal=250, intermediate=500, major=800) are **not**
implemented — every timeframe's structure/liquidity pass runs over its
*full* stored window in one pass, not a narrower per-layer slice. The
dependency-aware recomputation property below (an M1 close never
recomputes H1) is still fully true regardless, because it's a
*cross-timeframe* guarantee, not a within-timeframe one. See
[`../analysis-engine-v2-migration.md`](../analysis-engine-v2-migration.md).

## Tick data separation (§13)

Ticks are a separate stream from candles, used for spread/velocity/
quote-acceleration/micro-rejection/execution-microstructure quality — never
as the primary source for major swing structure, BOS/CHoCH, order blocks,
or dealing ranges, unless a future strategy explicitly opts in. Not yet
implemented; `market/tick.go` and `marketdata/tick_event.go`/`tick_window.go`
are proposed-tree entries, not scaffolded this task (no live tick
consumer exists yet to prove the boundary against).

## Dependency-aware recomputation (§31) and per-symbol workers (§30)

Implemented as of Analysis Engine V2: `internal/engine.SymbolWorker` wraps
one `sync.Mutex`-guarded `state.SymbolState`; `internal/engine.Engine`
holds a `sync.RWMutex`-guarded `map[Symbol]*SymbolWorker` and dispatches
concurrently across symbols. Dependency-aware recomputation is achieved by
construction rather than an explicit dependency-graph/scheduler file: each
timeframe's structure/liquidity computation reads only that timeframe's
own `TimeframeHistory`, so a closed M1 bar structurally cannot trigger H1
recomputation — proven directly in
`analysis-engine/test/engine/worker_test.go`'s
`TestEngine_ClosingOneTimeframeNeverRecomputesAnother`, plus two
concurrency proofs (`TestEngine_ConcurrentDispatchAcrossDifferentSymbolsNeverLosesAnEvent`,
`TestEngine_ConcurrentDispatchToTheSameSymbolAccountsForEveryEventExactlyOnce`)
run under `go test -race`. No separate `dependency_graph.go`/`scheduler.go`
files exist — the guarantee doesn't need one, since it falls out of "each
timeframe only reads its own history."

## Testing architecture

See ADR-006 for the full rationale. Summary: every Go test lives under
`analysis-engine/test/`, one subdirectory per domain
(`test/structure/`, `test/indicator/`, ...), each a black-box `<pkg>_test`
package testing only its package's exported API. No `internal/**/*_test.go`
exists or is added. Test categories:

| Category | Directory | Validates |
|---|---|---|
| Unit/domain | `test/indicator/`, `test/structure/`, `test/liquidity/`, `test/zone/` | Deterministic domain behavior |
| Strategy | `test/strategy/` | Individual strategy theses, independently |
| Integration | `test/integration/` | Multiple layers together (bars → structure → context → strategy → opportunity) |
| Parity | `test/parity/` | Retained mathematical behavior against legacy Python fixtures — already the pattern `test/indicator/atr_fixture_test.go` and `test/config/v3_fixture_parity_test.go` use |
| Replay/research | `test/replay/` | Old vs. new strategy outcomes across historical data — no signal parity required |
| Fixtures | `test/fixtures/` (small) / `testdata/` (large, exists) | Reusable test data |
| Architecture | `test/architecture/` (added this task) | Import-boundary enforcement — see below |

`test/architecture/dependency_test.go` walks every `internal/*` package's
Go source, collects its import list, and fails if any import crosses a
forbidden edge from the dependency graph above (e.g. `indicator` importing
`strategy`). This is the enforcement mechanism for §53's "add architectural
tests/static checks where practical" — a static assertion, not a
convention that can silently drift.

## What stays Python (§4 of the migration audit, unchanged here)

`app/scalping/lab_event_builder.py` and other offline/research tooling;
everything under `app/bot/`, Telegram formatting, owner DM, weekly reports,
calendar sync, manual signal parsing; `app/autotrade/{structural_barriers,
structural_target_room,range_context,range_lifecycle,entry_activation,
execution_confirmation}.py` (consumers of analysis output, not computation
sources — will get repointed at Go-produced state at cutover, but their
decision logic is not itself a migration target).
