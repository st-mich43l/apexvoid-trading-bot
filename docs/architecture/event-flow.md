# Event Flow

## Current (verified live, this session)

```mermaid
flowchart LR
    CT[cTrader] --> CE["ctrader-engine (.NET)"]
    CE -->|"bars:{SYMBOL}:{TF} ZSET + bars:new pub"| R[(Redis)]
    R --> AB["algo-bot: app/analysis (in-process)"]
    AB -->|"ZoneWatch + StrategyMatch"| R
    AB -->|"TradePlan V8"| R
    R --> CE
    CE -->|"execution events"| R
    R --> AB2["algo-bot: delivery / stats"]
    AB2 --> TG[Telegram]
```

Redis is the only cross-service boundary today (`ctrader-engine` "never
touches Postgres; Redis is the only cross-service boundary,"
`docs/redis-contract.md`). `algo-bot`'s `app/analysis` computes structure
in-process — there is no analysis-engine in this loop yet.

## Target

```mermaid
flowchart LR
    CT[cTrader] --> CE["ctrader-engine (.NET)"]
    CE -->|"market.bar.closed.v1 / market.tick.v1"| K[(Kafka)]
    K --> AE["analysis-engine (Go)"]
    AE -->|"analysis.opportunity.v1 / analysis.opportunity.invalidated.v1"| K
    K --> AB["algo-bot: analysis_client → auto_algo/manual_algo → risk"]
    AB -->|"execution.trade-plan.v1"| K
    K --> CE
    CE -->|"execution.trade-event.v1"| K
    K --> AB2["algo-bot: execution → journal/trade_records/telegram"]
```

The only structural difference from today: `algo-bot`'s in-process
computation is replaced by consuming `analysis.opportunity.v1` from
`analysis-engine`, and the transport moves from Redis to Kafka. Everything
downstream of "algo-bot has an opportunity" is unchanged in shape.

## Kafka topics (§33, frozen)

```text
market.bar.closed.v1
market.tick.v1

analysis.opportunity.v1
analysis.opportunity.invalidated.v1

execution.trade-plan.v1
execution.trade-event.v1
```

**Forbidden**: a topic per internal calculation (`analysis.atr`,
`analysis.swing`, `analysis.fvg`, `analysis.bos`, ...). Those stay internal
to `analysis-engine`'s own `SymbolState` — nothing outside the service ever
needs a partial computation, only the resulting `AnalysisOpportunity`.

**As of the Kafka transport task**: the Go side of the first four topics
is real — `analysis-engine/internal/transport/kafka` is a working
producer/consumer/codec, proven against a real broker
([ADR-008](../adr/008-go-kafka-client.md), [ADR-009](../adr/009-kafka-delivery-semantics.md),
[`../transport/kafka.md`](../transport/kafka.md)). This does **not**
mean live production traffic flows through Kafka yet — `ctrader-engine`
does not publish `market.bar.closed.v1` (no `.NET` Kafka work has been
done), `algo-bot` does not consume `analysis.opportunity.v1` (no Python
Kafka work has been done), and no strategy exists to produce a real
opportunity from (`internal/transport/kafka.Producer.PublishOpportunity`
has no caller in production code). `execution.trade-plan.v1` /
`execution.trade-event.v1` remain entirely unimplemented, explicitly out
of that task's scope. Schemas for all six event classes (plus the shared
`contracts/common/event-envelope-v1.schema.json` wrapper) live under
`contracts/` (see
[`../adr/007-shared-cross-service-contracts.md`](../adr/007-shared-cross-service-contracts.md)).

## Redis role, before and after Kafka cutover (§34)

Today: Redis is the durable cross-service bus (bars, ZoneWatch, TradePlan,
telemetry, execution events) — see `docs/redis-contract.md` for the full
key inventory. This does not change until Kafka cutover happens.

Target, once Kafka is live: Redis narrows to transient/cache/state support
only — latest quote, latest analysis snapshot, health, dedup, short-lived
caches, bootstrap candle cache. Redis stops being the primary durable
cross-service event bus. No date is set for this cutover; it is not part
of this architecture-definition task's scope.
