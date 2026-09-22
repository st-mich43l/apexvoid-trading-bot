# Dependency Rules

## Analysis Engine internal dependency direction

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
opportunity
  ↓
strategy / confluence / state
  ↓
transport (incl. transport/kafka, transport/redis)
  ↓
engine
```

Lower layers must not import higher layers:

```text
indicator MUST NOT import strategy
structure MUST NOT import opportunity
zone MUST NOT import strategy
market MUST NOT import anything under internal/ (it is the floor)
```

`visualization` is the sole true leaf consumer: it may import any core
type; nothing core imports it. `telemetry` is **not** a leaf (see the
amendment below) — it sits at rank 0 alongside `market`.

### Amendment: liquidity/context promoted, telemetry reclassified

Two corrections were made while actually *implementing* Analysis Engine
V2 (as opposed to the original architecture-freeze task, which only
scaffolded placeholders), both caught by
`analysis-engine/test/architecture/dependency_test.go` itself failing
rather than found by inspection — the same non-silent-correction
discipline as the strategy/opportunity ordering fix below:

1. **`liquidity` and `context` are no longer same-rank siblings of
   `structure`/`zone`.** The original freeze treated
   `structure / liquidity / zone` as one same-rank group. Once liquidity
   pools were actually built (`internal/liquidity`), they turned out to
   need `structure.Swing`/`structure.StructureLayer` directly
   (`PoolFromSwing(s structure.Swing, ...)`, `Pool.Layer`) — a real,
   necessary import that a same-rank classification forbids. `liquidity`
   was promoted to its own rank, strictly above `structure`/`zone` and
   strictly below `context` (which depends on both). The chain is now
   `structure/zone → liquidity → context`, not three siblings.
2. **`telemetry` moved from "leaf" to rank 0.** The original freeze
   classified `telemetry` as a leaf consumer (like `visualization`) on
   the assumption it would only ever be read by an external
   operator/dashboard. That assumption broke the moment `internal/engine`
   needed to actually *call into* `telemetry.Recorder.Time`/`.Count` to
   record its own phase timings (source task §57) — the thing being
   measured must import the recorder, which a true leaf (nothing may
   import it) cannot support. `telemetry` has zero `internal/*` imports
   of its own, so it sits at rank 0 alongside `market`: a foundational,
   dependency-free utility any higher rank may import.

Both changes are enforced, not just documented — see the `rank`/`leaf`
maps and their own inline comments in
`analysis-engine/test/architecture/dependency_test.go`.

### Amendment: transport moved below engine, not above it

A third correction, made implementing the Kafka transport task. The
original freeze placed `transport` as the outermost/highest rank (above
`engine`), reading the pipeline diagram's `engine → transport` arrow as
"transport comes after engine in the data-flow sequence." But that
task's own §1 states the required **Go import direction** explicitly:
"Allowed direction: engine ↓ transport/kafka" — i.e. `engine` imports
`transport`, to actually call a producer/consumer. Under this test's
"a package may only import a strictly lower rank" rule, that import is
only legal if `transport`'s rank is **lower** than `engine`'s — the
opposite of the original placement, exactly the same class of
arrow-direction-vs-import-direction confusion "the one correction, in
full" below already resolved once for `strategy`/`opportunity`.

`transport` (and `transport/kafka`, `transport/redis`) moved to sit
strictly **above** `strategy/confluence/state` and strictly **below**
`engine`. This single move satisfies two requirements from that task at
once, with no special-cased exception needed:

- `engine` (now the highest rank) can import `transport` — required, so
  the composition root can wire a Kafka consumer/producer into the
  engine.
- `strategy` (and everything at or below its rank — `structure`,
  `liquidity`, `zone`, `indicator`, `opportunity`) **cannot** import
  `transport` — which is exactly that task's own forbidden-edges list
  ("structure → kafka, liquidity → kafka, zone → kafka, strategy → kafka,
  indicator → kafka") and also matches this doc's own pre-existing
  Strategy dependency rule below ("Strategies must NOT depend on: Kafka
  · Redis...") — that rule is now enforced structurally by the rank
  table itself, not only asserted in prose.

Enforced in `analysis-engine/test/architecture/dependency_test.go`'s
`rank` map and its own inline comment on this exact reasoning.

### The one correction, in full

The source architecture task's §51 lists this order:

```text
market → indicator → structure/liquidity/zone → context → strategy/confluence → opportunity → engine → transport
```

— i.e. `strategy` below `opportunity`. But §21 of the same task writes the
`Strategy` interface as:

```go
type Strategy interface {
    Evaluate(ctx *context.MarketContext) []opportunity.Candidate
}
```

`Strategy.Evaluate` returning `opportunity.Candidate` means `strategy`
imports `opportunity`. If `opportunity` is "above" `strategy` in the
ordering (as §51 literally states), then per §51's own rule
("lower layers must not import higher layers") this would have to mean
`opportunity` is not allowed to be imported by the lower `strategy` package
— which is backwards from what the interface requires, or exactly the
inverse if you read "higher" as "later in the list," either reading
producing a contradiction with the other section.

**Resolution, adopted throughout this repo's docs and scaffolding:**
`opportunity` sits below `strategy`. It holds pure result/value types only
(`Candidate`, `Evidence`, `Target`, `StrategyQuality`, `StrategyID`) with
zero behavior and zero dependency on `strategy` — the same role
`market`/`context` play for everything above them. `strategy` is the only
one of the two packages that imports the other:

```text
market → indicator/marketdata → structure/liquidity/zone → context → opportunity → strategy/confluence → engine → transport
```

This is the only ordering under which every literal Go snippet in the
source task compiles without an import cycle. It is called out here rather
than silently applied so a future reader of the source task's original text
isn't confused by the discrepancy. See ADR-003 for the strategy-model
reasoning this sits inside.

## Strategy dependency rule (§52, unchanged)

Strategies may depend on:

```text
market · indicator outputs through context/state · structure · liquidity · zone · context · opportunity types
```

Strategies must NOT depend on:

```text
Kafka · Redis · Telegram · Postgres · cTrader · algo-bot
```

A strategy that needs persistence, transport, or notification is asking
for the wrong layer — that belongs in `engine` (orchestration) or
`algo-bot` (control plane), never inside a `Strategy.Evaluate`
implementation.

## Cross-service dependency rule

```text
analysis-engine  MUST NOT import/reference: Telegram, account risk, TradePlanBuilder, broker execution APIs
algo-bot         MUST NOT implement: ATR, swings, BOS/CHoCH, FVG, OB, liquidity sweep detection
ctrader-engine   MUST NOT implement: strategy detection
```

Confirmed compliant today: `ctrader-engine` (no violations found, see
`service-boundaries.md`). Confirmed non-compliant today: `algo-bot`'s
`app/analysis/*` and `app/scalping/*` (documented as V1–V6 in
`service-boundaries.md`, not fixed by this task).

## Enforcement

- **Go**: `analysis-engine/test/architecture/dependency_test.go` parses
  every `internal/*` package's import statements and fails the build if a
  forbidden edge from the graph above is present. This runs under the same
  `go test ./...` as every other test — a drifted boundary is a CI
  failure, not a doc that goes stale.
- **Python / .NET**: no automated static check added this task (the
  source task's own §53 says "where practical" — a Python import-linter
  rule against a target tree that doesn't exist yet, for a service
  (`analysis_client`) with no code, would check nothing real). Recorded as
  migration backlog: once `algo-bot`'s `app/analysis/*` is retired per the
  migration map, an import-linter rule forbidding `app/auto_algo/**` from
  importing anything under a to-be-deleted `app/analysis/**` becomes
  meaningful and should be added then.
