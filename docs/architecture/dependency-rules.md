# Dependency Rules

## Analysis Engine internal dependency direction

```text
market
  ↓
indicator / marketdata
  ↓
structure / liquidity / zone
  ↓
context
  ↓
opportunity
  ↓
strategy / confluence
  ↓
engine
  ↓
transport
```

Lower layers must not import higher layers:

```text
indicator MUST NOT import strategy
structure MUST NOT import opportunity
zone MUST NOT import strategy
market MUST NOT import anything under internal/ (it is the floor)
```

`visualization` and `telemetry` are leaf consumers outside this chain: they
may import any core type; nothing core imports them.

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
