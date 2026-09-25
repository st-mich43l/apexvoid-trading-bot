# Kafka transport

## Boundary

Kafka is ApexVoid's durable **trading-event and command plane**. It is not a
market-data feed. Closed bars, rolling OHLC history, latest spot, bootstrap,
and recovery remain on Redis; see [Redis market-data contract](../redis-contract.md)
and [ADR-010](../adr/010-redis-market-data-kafka-events.md).

```text
cTrader → Redis → Analysis Engine → Kafka → future Algo Bot
                                 ↑
                       AnalysisOpportunity events
```

The broker is deliberately available before every producer and consumer is
implemented. That does not mean every topic has a runtime owner yet.

## Topics

Configuration V3 explicitly provisions only these business topics:

| Topic | Intended producer | Intended consumer | Current status |
|---|---|---|---|
| `analysis.opportunity.v1` | Analysis Engine | Algo Bot | Analysis Engine lifecycle publisher is wired; no Algo Bot consumer yet |
| `analysis.opportunity.invalidated.v1` | Analysis Engine | Algo Bot | Analysis Engine lifecycle publisher is wired; no Algo Bot consumer yet |
| `execution.trade-plan.v1` | Algo Bot | cTrader Engine | topology only |
| `execution.trade-event.v1` | cTrader Engine | Algo Bot | topology only |

There is no `market.bar.closed.v1` or `market.tick.v1` topic. There is no
Kafka market producer in `ctrader-engine` and no Kafka consumer in
`analysis-engine`.

## Analysis Engine producer

`analysis-engine/internal/transport/kafka` is producer-only. It retains the
shared envelope, strict JSON codec, UUIDv7 IDs, correlation/causation IDs,
configuration provenance, headers, health, metrics, synchronous broker
acknowledgement, delivery error propagation, and bounded graceful shutdown.

`Producer.PublishOpportunity` and `Producer.PublishOpportunityInvalidated`
are neutral APIs. They carry a strategy identifier as data; Kafka has no
strategy-specific producer or topic.

Records are keyed by canonical symbol. This preserves partition ordering for
an opportunity and its invalidation for the same symbol while allowing other
symbols to use other partitions. Opportunity ID is the lifecycle identity.

`analysis.opportunity.invalidated.v1` contains exactly lifecycle data:
`opportunity_id`, `symbol`, `strategy`, a machine-readable `reason_code`, and
`invalidated_at`. It contains no account-risk or execution values.

## Delivery policy

The producer uses franz-go's idempotent producer defaults and `acks=all`.
Every publish is synchronous (`ProduceSync`), so the caller receives a broker
delivery failure rather than losing it in an asynchronous callback. A future
strategy must persist or retry an opportunity that cannot be acknowledged; it
must never silently drop it.

The producer is independent from Redis ingestion. A Kafka outage must not
stop the Analysis Engine from loading bars or advancing technical state. The
engine queues lifecycle transitions off its bar-ingestion path and retries
publication; see `internal/engine/publisher.go` and the documented in-memory
queue limitation in `docs/analysis-engine-v2-migration.md`.

ADR-009 defines at-least-once handling for future Kafka consumers. It does
not make Kafka a source of market candles.

## Configuration and operations

All broker addresses, client IDs, names, and topic specifications are in
`config/transport.yml`, resolved through Configuration V3. No topic or Redis
topology is supplied through environment variables.

`kafka-init` reads that configuration and creates/verifies topics
idempotently. The KRaft broker uses the persistent `kafkadata` volume and is
internal-only in both Compose definitions.

Broker-backed producer tests skip unless `KAFKA_TEST_BROKERS` is set. A local
Compose-network example is:

```bash
docker compose up -d kafka
docker compose run --rm kafka-init
docker run --rm --network apexvoid-trading-bot_default \
  -e KAFKA_TEST_BROKERS=kafka:9092 \
  -v "$PWD":/workspace -w /workspace/analysis-engine golang:1.23.12-alpine \
  go test ./test/kafka -run 'TestProducer_'
```

This validates the producer against a real broker; it does not claim that the
unimplemented Algo Bot or cTrader execution consumers are live.
