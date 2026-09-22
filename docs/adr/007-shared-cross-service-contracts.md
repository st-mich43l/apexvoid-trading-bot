# ADR-007: Shared cross-service contracts live at the repo root

## Status
Accepted; `contracts/configuration/` already implements this pattern.
`contracts/market/`, `contracts/analysis/`, `contracts/execution/` created
this task as skeletons.

## Context
`contracts/configuration/` already proves the pattern this ADR generalizes:
one JSON Schema, read/validated by Python (`v3_root.py`), Go
(`internal/config`), and referenced by `.NET`'s config path, with
cross-language parity tests (`docs/configuration-v3-migration-audit.md`
Stage C6) proving no language silently drifted from the schema. Without
this discipline, a cross-service event contract (bar-closed, opportunity,
trade-plan, trade-event) risks becoming three independently-maintained
structs that happen to agree today and silently diverge tomorrow — exactly
the ATR-divergence failure mode (V1 in `service-boundaries.md`) but at the
service boundary instead of inside one process.

## Decision
Cross-service contracts live outside every individual service, at
`contracts/`:

```text
contracts/
├── configuration/   (exists)
├── market/           market/bar-closed-v1.schema.json, tick-v1.schema.json
├── analysis/          analysis/opportunity-v1.schema.json, opportunity-invalidated-v1.schema.json
└── execution/          execution/trade-plan-v1.schema.json, trade-event-v1.schema.json
```

No service's source tree contains its own private copy of a schema another
service also reads. `contracts/autotrade/*` (the current TradePlan V8
Python/C# fixture set) is not deleted by this task — it remains the real,
live contract until `execution/trade-plan-v1.schema.json` is adopted at
Kafka cutover (ADR-004), at which point it is superseded.

## Consequences
- A schema change is a PR against `contracts/`, reviewed by every service
  that reads or writes it — never a unilateral change inside one service's
  tree that the others discover at runtime.
- The four new schema files created this task are skeletons (field names
  and types sketched from the real Go/Python/C# shapes already in the
  codebase — `TimeframeAnalysis`, `opportunity.Candidate`, TradePlan V8's
  own `contracts/autotrade/trade-plan-v8.json` fixture shape) — not yet
  wired into any service's validation path. Wiring them in is Stage 4+
  work (once analysis-engine has a real opportunity to publish), not this
  task.
- This does not create a second configuration authority
  (`contracts/configuration/` stays exactly as-is) — it extends the same
  root-level-contracts pattern to the three new event classes, per the
  source task's own §6 instruction that configuration values remain owned
  by root YAML and no service package should own business defaults.
