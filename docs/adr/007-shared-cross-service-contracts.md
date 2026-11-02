# ADR-007: Shared cross-service contracts live at the repo root

## Status
Accepted. `contracts/configuration/` implements the pattern; `contracts/market/`,
`contracts/analysis/` and `contracts/common/` (the shared event envelope) are the
implemented transport contracts (see [ADR-008](008-go-kafka-client.md) and
[`../transport/kafka.md`](../transport/kafka.md)). `contracts/execution/` is still a skeleton.

## Context
`contracts/configuration/` already proves the pattern: one JSON Schema, read and validated by
Python, Go and referenced by .NET, with cross-language tests proving no language drifted from the
schema. Without this discipline a cross-service event contract (bar-closed, opportunity,
trade-plan, trade-event) becomes three independently maintained structs that agree today and
diverge tomorrow, the same failure mode as an indicator computed differently in two processes.

## Decision
Cross-service contracts live outside every individual service, under `contracts/`:

```text
contracts/
├── configuration/   (implemented)
├── common/           shared event envelope
├── market/           bar-closed-v1, tick-v1
├── analysis/         opportunity-v1, opportunity-invalidated-v1
└── execution/        trade-plan-v1, trade-event-v1 (skeleton)
```

No service's source tree contains a private copy of a schema another service also reads.
`contracts/autotrade/*` (the TradePlan V8 fixture set) stays the live contract until
`execution/trade-plan-v1.schema.json` is adopted ([ADR-010](010-redis-market-data-kafka-events.md)),
at which point it is superseded.

## Consequences
- A schema change is a PR against `contracts/`, reviewed by every service that reads or writes
  it, never a unilateral change inside one service's tree.
- Skeleton schemas are sketches of the real Go, Python and C# shapes and are not yet wired into a
  validation path; wiring them in belongs with the first real publisher.
- Configuration stays owned by the root YAML (`contracts/configuration/`); no service package owns
  business defaults.
