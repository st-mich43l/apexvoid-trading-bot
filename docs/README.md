# Documentation index

Active operator and design docs for ApexVoid. Completed migrations and
phase ledgers live under [`history/`](history/) and
[`configuration/history/`](configuration/history/).

## Start here

| Doc | Purpose |
|---|---|
| [../README.md](../README.md) | Product overview and repo map |
| [architecture.md](architecture.md) | Services, loops, data flows |
| [runtime/multi-symbol-routing.md](runtime/multi-symbol-routing.md) | Live instruments and per-symbol policies |
| [configuration/configuration-architecture.md](configuration/configuration-architecture.md) | Catalog V2 + runtime manifest |

## Trading lanes

| Doc | Purpose |
|---|---|
| [bot-commands.md](bot-commands.md) | Manual `/trade`, `/algo`, lifecycle, pips |
| [technique-zonewatch-publish.md](technique-zonewatch-publish.md) | Techniques → ZoneWatch → TradePlan V8 |
| [scalping/README.md](scalping/README.md) | HFS M1 lane |
| [demo-eval-autotrade.md](demo-eval-autotrade.md) | Demo auto-trade runbook |

## Contracts and integrity

| Doc | Purpose |
|---|---|
| [redis-contract.md](redis-contract.md) | Bars / plans / events boundary |
| [adr-trade-plan-v8-cutover.md](adr-trade-plan-v8-cutover.md) | Live TradePlan identity |
| [adr-trade-plan-v7-boundary.md](adr-trade-plan-v7-boundary.md) | V7 boundary (still referenced by code) |
| [trade-plan-v7-migration.md](trade-plan-v7-migration.md) | V7 migration notes |
| [autotrade-config-contract.md](autotrade-config-contract.md) | Auto-trade config surface |
| [autotrade-execution-integrity.md](autotrade-execution-integrity.md) | Execution integrity checks |
| [p0-simple-zone-m1-baseline-map.md](p0-simple-zone-m1-baseline-map.md) | Zone / M1 baseline map (test-cited) |
| [schema.sql](schema.sql) | Reference DDL (store.py is authoritative) |

## Configuration (active)

| Doc | Purpose |
|---|---|
| [configuration/configuration-architecture.md](configuration/configuration-architecture.md) | Authority model |
| [configuration/configuration-governance.md](configuration/configuration-governance.md) | Add/deprecate fields, integrity gate |
| [configuration/config-file-and-instrument-registry.md](configuration/config-file-and-instrument-registry.md) | YAML + instruments |
| [configuration/cross-service-runtime-manifest.md](configuration/cross-service-runtime-manifest.md) | ResolvedRuntimeManifest |
| [configuration/manifest-authority-cutover.md](configuration/manifest-authority-cutover.md) | cTrader manifest authority |
| [configuration/effective-instrument-context.md](configuration/effective-instrument-context.md) | Per-symbol typed context |
| [configuration/adr-canonical-only-python-configuration.md](configuration/adr-canonical-only-python-configuration.md) | Canonical-only Python ADR |
| [configuration/config-authority-runbook.md](configuration/config-authority-runbook.md) | Operator authority checks |
| [configuration/config-catalog.generated.md](configuration/config-catalog.generated.md) | Generated catalog |
| [configuration/environment-reference.generated.md](configuration/environment-reference.generated.md) | Generated ENV reference |

## Ops

| Doc | Purpose |
|---|---|
| [deployment.md](deployment.md) | Host → running stack |
| [operations.md](operations.md) | Logs, backups, troubleshooting |
| [security.md](security.md) | Threat model and secrets |

## History

- [`history/`](history/) — completed P0 / migration / one-shot regression notes
- [`configuration/history/`](configuration/history/) — Catalog V2 phase ledgers
