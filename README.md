# ApexVoid Trading Bot

A self-hosted multi-symbol trading stack for cTrader (XAU, EURUSD, GBPUSD,
GBPJPY, USDJPY). One service owns each decision:

```text
cTrader feed → Redis closed bars → Go Analysis Engine → Kafka opportunities
  → Python Algo Bot → TradePlan V8 (Redis) → cTrader Engine → broker
```

- **Go Analysis Engine** (`analysis-engine/`) computes market structure, liquidity,
  zones and context from closed bars, runs 21 independent strategies, and
  publishes the opportunity lifecycle on a Kafka bus. It is the sole technical
  authority.
- **Python Algo Bot** (`algo-bot/`) is the execution and control plane: it
  consumes opportunities, applies freshness, quote, exposure and risk policy,
  arbitrates same-thesis opportunities best-first, reserves the entry corridor
  atomically, builds **TradePlan V8**, and runs Telegram, the journal and manual
  `/algo`. It never scans markets or rebuilds zones.
- **cTrader Engine** (`ctrader-engine/`, .NET) owns the cTrader feed, the Redis
  bar sink and broker execution: it executes a TradePlan without recomputing it,
  enforces the final exposure fence, honours cancel and expiry, and manages
  stops and targets.
- **Redis** carries the market feed, TradePlans and execution state; **PostgreSQL**
  persists the opportunity ledger, plans, fills and journal.

`config/*.yml` is the one non-secret configuration authority; `.env` holds
secrets and bootstrap only. `contracts/` holds the cross-service schemas and
fixtures; `deployment-template/` the production Compose/Ansible templates.

## Documentation

- [Architecture](docs/architecture.md): ownership, planes, dependency rules
- [Execution](docs/execution.md): the decision cycle, same-thesis arbitration, exposure, expiry, TradePlan
- [Configuration](docs/configuration.md): files, secrets, reachability, containment
- [Operations](docs/operations.md): deployment, checks, backups, incidents
- [Strategies](docs/strategies/README.md): certification matrix and per-strategy specifications
- Reference: [Redis contract](docs/redis-contract.md), [Kafka transport](docs/transport/kafka.md),
  [bot commands](docs/bot-commands.md), [security](docs/security.md), [decisions](docs/adr/)

## Development

```bash
docker compose config -q
cd analysis-engine && go build ./... && go vet ./... && go test ./...
cd algo-bot && PYTHONPATH=. pytest -q $(grep -E '^tests/' tests/ci_autotrade_paths.txt)
cd ctrader-engine && dotnet test tests
```

The analysis engine has a deterministic replay command (`cmd/replay`) and
committed real-capture fixtures and parity goldens under
`analysis-engine/testdata/`. Capture fresh bars read-only with
`python -m tools.capture_bars` inside the bot container.
