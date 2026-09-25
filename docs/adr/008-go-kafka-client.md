# ADR-008: Go Kafka client — `twmb/franz-go`

## Status

Accepted and implemented for Analysis Engine's producer-only Kafka transport.

## Decision

Use `github.com/twmb/franz-go` v1.19.5 as the single Go Kafka client. It is
pure Go, supports the required synchronous producer path, and is compatible
with the repository's Go 1.23 toolchain.

`kgo.Client` is constructed as a producer only. It uses Franz-go's
idempotent-producer defaults and `acks=all`; each API call uses
`ProduceSync` and returns broker delivery errors to the caller. Shutdown
flushes with a bounded context.

## Scope

The library preserves the common envelope, UUIDv7 identifiers, headers,
strict JSON codec, configuration provenance, producer health, metrics, and
topic administration. Analysis Engine has no Kafka consumer because Redis is
the market-data plane; see [ADR-010](010-redis-market-data-kafka-events.md).

Real-broker producer tests live under `analysis-engine/test/kafka` and skip
unless `KAFKA_TEST_BROKERS` is configured. No second Kafka client is used in
production or tests.
