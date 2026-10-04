# ApexVoid architecture

## Services

| Service | Responsibility |
|---|---|
| `ctrader-engine` | cTrader market feed, Redis closed-bar writes, TradePlan validation/execution, position lifecycle |
| `analysis-engine` | Go technical analysis, strategies, opportunity lifecycle, arbitration, Kafka events |
| `algo-bot` | Kafka consumption, execution policy, quote/session/news checks, risk/exposure, TradePlans, Telegram, journal |
| Redis | Closed bars, current market state, execution streams and TradePlans |
| Kafka | Durable analysis opportunity lifecycle events |
| PostgreSQL | Algo Bot lifecycle, plans, fills, journal, statistics, and audit data |

## Automatic path

```text
cTrader → ctrader-engine → Redis closed bars
  → analysis-engine (Go)
  → Kafka analysis opportunities
  → algo-bot execution policy
  → Redis TradePlan
  → ctrader-engine → broker
```

The analysis engine is the sole automatic technical authority. Algo Bot must
consume technical facts and apply current execution conditions and account
policy; it must not recreate technical detectors. cTrader remains the only
broker-facing service.

## Manual path

Manual signals originate from operator commands and retain their explicit
setup, entry zone, stop, targets, and strategy label. Algo Bot formats and
persists them, applies shared execution/risk checks, and publishes a TradePlan
only when the operator request is eligible.

## Source and deployment

`config/apexvoid.yml` and its includes define the categorized configuration.
Each service resolves the selected categorized YAML root directly into its
own typed runtime options.
Compose and production deployment templates are in `docker-compose.yml` and
`deployment-template/`.

See [`configuration.md`](configuration.md), [`deployment.md`](deployment.md),
[`operations.md`](operations.md), and
[`architecture/dependency-rules.md`](architecture/dependency-rules.md).
