# ADR-004: Kafka as the target inter-service event boundary

## Status
Accepted as target; not implemented (2026-09-22).

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
- This is **not implemented by this task**. No Kafka broker, topic, or
  client library exists in this repo yet. `internal/transport/kafka/`
  exists only as package-level skeleton (`consumer.go`, `producer.go`,
  `codec.go` are proposed-tree names, not yet written).
- Until cutover, Redis remains the real transport and
  `docs/redis-contract.md` remains the accurate reference for what's
  actually running — this ADR describes the target, not the present.
- Cutover sequencing (which of the six event classes moves first, whether
  a dual-write transition period is needed) is not decided by this ADR
  and is deferred to when `analysis-engine` has a real opportunity
  detector to publish from.
