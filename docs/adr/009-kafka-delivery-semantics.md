# ADR-009: Kafka delivery semantics — at-least-once + application idempotency

## Status

Accepted for current and future ApexVoid business-event producers and
consumers.

## Decision

Use at-least-once delivery with application-level idempotency. Do not introduce
Kafka transactions until there is a demonstrated atomic read-process-write
workflow spanning business topics.

The current Analysis Engine producer uses idempotence and `acks=all`.
`ProduceSync` errors are returned to the strategy boundary. A future
strategy must retain or retry an unacknowledged opportunity; it must not
silently discard one.

Future consumers must make their own lifecycle operation idempotent before
committing an offset. Opportunity events use their explicit opportunity ID for
lifecycle identity and use canonical symbol as the Kafka record key so one
symbol's created/invalidated lifecycle remains partition ordered.

## Boundary

This ADR is about Kafka business events only. Redis candle re-reads may produce
duplicate or conflicting bars; `marketdata.TimeframeHistory` reports
`AppendDuplicate` or `AppendConflict` and retains the original analyzed
candle. That market-data policy is independent of Kafka and is defined by
[ADR-010](010-redis-market-data-kafka-events.md).
