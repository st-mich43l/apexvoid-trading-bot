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
│   ├── replay/main.go              (not yet created)
│   ├── benchmark/main.go           (not yet created)
│   └── inspect/main.go             (not yet created)
├── internal/
│   ├── config/          (exists — Configuration V3 direct reader)
│   ├── market/           (exists — Candle, CandleWindow, Timeframe, Symbol, Geometry)
│   ├── marketdata/       (scaffolded this task — event normalization/history, doc.go only)
│   ├── indicator/        (exists — TrueRange, SimpleATR, WilderATR, AtrAt, AtrScalar)
│   ├── structure/        (scaffolded this task — pivot/swing/structure/break/hierarchy)
│   ├── liquidity/        (scaffolded this task — pool/equal-high-low/sweep/grab)
│   ├── zone/             (scaffolded this task — supply/demand/OB/FVG/breaker/flip/mitigation)
│   ├── context/          (scaffolded this task — MarketContext and its sub-contexts)
│   ├── opportunity/       (scaffolded this task — Candidate, Evidence, Target, StrategyQuality)
│   ├── strategy/          (scaffolded this task — Strategy interface; no strategy implementations)
│   ├── confluence/        (scaffolded this task — compositional evidence combining, doc.go + Score)
│   ├── state/             (scaffolded this task — SymbolState, wires every Book together)
│   ├── engine/            (scaffolded this task — orchestration skeleton, AnalysisSnapshot)
│   ├── transport/         (scaffolded this task — kafka/, redis/, doc.go only, no client libs added)
│   ├── visualization/     (scaffolded this task — doc.go only)
│   └── telemetry/         (scaffolded this task — doc.go only)
├── test/                  (exists — centralized, one subdir per domain; see below)
├── testdata/              (exists — golden-master fixtures)
├── go.mod / go.sum
└── README.md
```

"Scaffolded this task" means: a real, compiling Go package with type
declarations and doc comments that establish the package's ownership
boundary and prove the dependency direction against its neighbors — not a
strategy or indicator implementation. Per the task's own §58 ("avoid empty
architecture theater"), packages that don't need to prove a new dependency
edge (`visualization`, `telemetry`, `transport/kafka`, `transport/redis`)
got a `doc.go` only; packages central to the dependency graph
(`context`, `opportunity`, `strategy`, `state`) got real, minimal,
cross-importing types.

## Dependency graph (with one correction)

```text
market
  ↓
indicator / marketdata
  ↓
structure / liquidity / zone
  ↓
context
  ↓
opportunity            ← pure result/value types only (Candidate, Evidence, Target, StrategyQuality)
  ↓
strategy / confluence  ← behavior: Strategy.Evaluate(ctx) returns []opportunity.Candidate
  ↓
engine
  ↓
transport
```

`visualization` and `telemetry` sit outside this chain as leaf consumers:
they may import any core type; nothing core imports them.

**Correction to the source task's own §51 ordering, explained**: the source
task lists `strategy` before `opportunity`, which would forbid strategy
from importing opportunity — but its own §21 Go snippet has
`Strategy.Evaluate` return `[]opportunity.Candidate`, which requires exactly
that import. Taken literally the two sections contradict each other. This
is resolved by treating `opportunity` as a pure, behavior-free value-type
package with **no** dependency on `strategy` (or on `market` beyond basic
types), placing it below `strategy` in the graph. `strategy` is the only
one of the two that imports the other. Full reasoning: ADR-003.

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
§24, §26–27); they're now real, minimal Go in the repo, not just pseudocode.
Fields are placeholders sized to compile and prove the graph, not a final
schema — filling them in with real structure/liquidity/zone data is Stage
2+ work per the migration map, not this task.

```go
// internal/context/market.go
type MarketContext struct {
    Symbol     market.Symbol
    Timeframes map[market.Timeframe]*TimeframeContext
    Bias       BiasContext
    Regime     RegimeContext
    Liquidity  LiquidityContext
    Volatility VolatilityContext
    Session    SessionContext
}
```

```go
// internal/opportunity/candidate.go
type StrategyID string

type Candidate struct {
    ID        string
    Strategy  StrategyID
    Symbol    market.Symbol
    Direction market.Direction

    Entry        EntryZone     // deliberately not named "Zone" — see note below
    Invalidation market.PriceLevel
    Targets      []Target

    Evidence []Evidence
    Quality  StrategyQuality

    CreatedAt int64
    ExpiresAt int64
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
// internal/state/symbol_state.go
type SymbolState struct {
    Symbol market.Symbol

    Structure     *structure.Book
    Liquidity     *liquidity.Book
    Zones         *zone.Book
    Context       context.MarketContext
    Opportunities *opportunity.Book
}
```

`SymbolState` is the one analytical truth for one symbol (§27). Strategies
read it (via `context.MarketContext`); they do not rebuild it (§28 — no
`BreakoutRetest → recalculate ATR → recalculate swings → rebuild zones`).

```go
// internal/engine/engine.go
type AnalysisSnapshot struct {
    Symbol        market.Symbol
    Time          int64
    Context       context.MarketContext
    Opportunities []opportunity.Candidate
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
that survive the redesign (next task: the market-structure specification,
then Stage 6) get a real package.

## Market history sizing

Per §12: stored history and calculation window are distinct concepts. The
target minimums:

```text
M1   1,000-2,000 candles      M15  750-1,000 candles     H4   200-300 candles
M5   ~1,000 candles           H1   ~500 candles
```

`market.CandleWindow` (already implemented) is the bounded ring buffer this
sizing applies to. A detector's *view* into that window (e.g. "breakout
detector reads the last 100," "swing structure reads the last 300") is a
narrower slice taken at read time — no algorithm scans the full stored
window on every event. This is a sizing target for when `marketdata`'s
bootstrap/history logic is implemented (Stage 1+ continuation, not this
task); `CandleWindow.NewCandleWindow(capacity)` already supports an
arbitrary capacity per caller.

## Tick data separation (§13)

Ticks are a separate stream from candles, used for spread/velocity/
quote-acceleration/micro-rejection/execution-microstructure quality — never
as the primary source for major swing structure, BOS/CHoCH, order blocks,
or dealing ranges, unless a future strategy explicitly opts in. Not yet
implemented; `market/tick.go` and `marketdata/tick_event.go`/`tick_window.go`
are proposed-tree entries, not scaffolded this task (no live tick
consumer exists yet to prove the boundary against).

## Dependency-aware recomputation (§31) and per-symbol workers (§30)

Not implemented this task — `internal/engine` is a package-level skeleton
only. Frozen as the target model: one symbol worker owns that symbol's
mutable state, FIFO per symbol, parallel across symbols, and a closed bar
on timeframe X only recomputes X's own dependents (§31's table), never
every timeframe on every event. `internal/engine/dependency_graph.go` and
`scheduler.go` are proposed-tree entries for when real computation exists
to schedule.

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
