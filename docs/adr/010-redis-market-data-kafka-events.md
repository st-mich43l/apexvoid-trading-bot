# ADR-010: Redis market-data plane; Kafka trading-event plane

## Status

Accepted and implemented.

## Context

cTrader already writes a recoverable rolling market store in Redis. Analysis
Engine requires deep per-timeframe bootstrap and must recover from transient
notification loss. Treating a pub/sub message or Kafka market record as the
market-data authority would duplicate that store and create a second recovery
model without operational benefit.

Business workflows are different: opportunities, invalidations, plans, and
execution events need durable, versioned cross-service contracts.

## Decision

Redis owns operational market data:

```text
bars:{SYMBOL}:{TIMEFRAME}   closed OHLC ZSET
price:{SYMBOL}:spot         latest bid/ask
bars:new                    bar-close wake-up
spots:new                   spot wake-up
```

Analysis Engine subscribes before bootstrap, reads the ZSET as authority,
tracks a cursor per symbol/timeframe, coalesces wake-ups, re-reads every bar
newer than its cursor, and reconciles periodically. Pub/sub improves latency;
it is not a correctness dependency. A changed already-processed candle is
reported as a conflict and never silently rewrites analyzed history.

Kafka owns durable business events and commands only:

```text
analysis.opportunity.v1
analysis.opportunity.invalidated.v1
execution.trade-plan.v1
execution.trade-event.v1
```

PostgreSQL owns permanent business history.

## Consequences

- cTrader remains Redis-only for market feed publication.
- Analysis Engine's live input is Redis and its Kafka role is producer-only.
  Kafka unavailability cannot corrupt or stop Redis market ingestion.
- Redis unavailability prevents live-analysis readiness but not process
  liveness.
- Redis retention must meet V3 history depth. The current .NET sink has a
  global cap, configured at 2,000 bars as the V3 high-water mark while a
  per-timeframe manifest capability is introduced separately.
- The Kafka broker and all four business topics are deployed now. The Analysis
  Engine producer is ready; strategies, Algo Bot Kafka consumption, TradePlan
  Kafka publication, and cTrader execution Kafka consumption/publication are
  not yet live.
