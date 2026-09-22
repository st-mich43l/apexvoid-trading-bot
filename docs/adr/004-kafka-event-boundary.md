# ADR-004: Kafka as the target inter-service event boundary

## Status
Accepted as target. **Go transport implemented** (Kafka transport task,
2026-09-22 — `internal/transport/kafka/`, ADR-008, ADR-009,
`docs/transport/kafka.md`) for `market.bar.closed.v1`, `market.tick.v1`
(consumer, disabled by default), `analysis.opportunity.v1`,
`analysis.opportunity.invalidated.v1` (producer — not yet called by a
real strategy; no strategy exists yet). `execution.trade-plan.v1` /
`execution.trade-event.v1` remain unimplemented by design (explicitly
out of that task's scope). `.NET` (ctrader-engine) and Python (algo-bot)
Kafka integration remain unimplemented — see the Consequences update
below.

## Context
Today, Redis is the only cross-service transport
(`docs/redis-contract.md`): closed bars, ZoneWatch, TradePlan V8 payloads,
executor events, and telemetry all move through Redis keys/streams/ZSETs.
This works for a single-host, two-runtime-process deployment, but conflates
"durable cross-service event bus" with "transient cache/state store" in one
system, and gives no schema enforcement or replay guarantee beyond
whatever TTL a given key happens to carry.

## Decision
Kafka becomes the durable, schema-enforced inter-service transport for
four event classes only:

```text
market.bar.closed.v1
market.tick.v1
analysis.opportunity.v1
analysis.opportunity.invalidated.v1
execution.trade-plan.v1
execution.trade-event.v1
```

No topic is created for an internal calculation (`analysis.atr`,
`analysis.swing`, `analysis.fvg`, `analysis.bos`, ...) — those never leave
`analysis-engine`'s own `SymbolState`. Redis's role narrows to
transient/cache/state support (latest quote, latest snapshot, health,
dedup, short-lived caches) once cutover completes — it is never the
primary durable cross-service bus again after that point.

## Consequences
- Every cross-service message has one schema, versioned (`v1` suffix),
  owned under `contracts/` (ADR-007) — not three independently-drifting
  per-language structs.
- **As of the Kafka transport task**: the Go side of four of the six
  event classes is real — `internal/transport/kafka/` is a working
  producer/consumer/codec against a real client library (ADR-008),
  proven against a real ephemeral Redpanda broker in integration tests.
  `analysis-engine`'s `cmd/analysis-engine` composition root can consume
  `market.bar.closed.v1` and drive the real engine pipeline end to end —
  but nothing else in the system produces to or consumes from Kafka yet:
  **no `.NET` producer exists** (ctrader-engine does not publish
  `market.bar.closed.v1`/`market.tick.v1` — this ADR's Consequences
  section explicitly does not claim otherwise), **no Python consumer
  exists** (algo-bot's `analysis_client` is not built), and
  **`execution.trade-plan.v1`/`execution.trade-event.v1` are
  unimplemented** (out of the Kafka transport task's scope by its own
  §62/§63). Until those exist, Kafka carries no live production traffic.
- Until cutover, Redis remains the real transport and
  `docs/redis-contract.md` remains the accurate reference for what's
  actually running — this ADR still describes the target for
  cross-service production traffic, not the present, even though the Go
  side is now real.
- Cutover sequencing (which of the six event classes moves first, whether
  a dual-write transition period is needed) is not decided by this ADR
  and is deferred to when a real cTrader producer and algo-bot consumer
  exist (the two tasks this ADR names as following this one).
