# ADR-004: Kafka business-event boundary

## Status

Superseded in part by [ADR-010](010-redis-market-data-kafka-events.md).
Kafka remains the durable event/command boundary; its proposed market-data role
is rejected.

## Decision

Redis owns operational market data: closed OHLC history, spot state, bar-close
wake-ups, bootstrap, and recovery. cTrader writes the existing Redis contract
and Analysis Engine reads it.

Kafka owns only durable business events and commands:

```text
analysis.opportunity.v1
analysis.opportunity.invalidated.v1
execution.trade-plan.v1
execution.trade-event.v1
```

PostgreSQL owns permanent plans, fills, journals, statistics, and audit
history.

## Consequences

- No Kafka topic, producer, contract, or consumer exists for market bars or
  ticks.
- Analysis Engine is currently a Kafka producer only. It maintains state from
  Redis even when Kafka is unavailable.
- The four business topics are provisioned now. Only the Analysis Engine
  producer implementation exists; Algo Bot and cTrader execution topic owners
  are future work.
- Existing Redis streams remain in the current execution path until their
  Kafka replacements are implemented and deliberately cut over.
