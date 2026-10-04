# ApexVoid Trading Bot

A self-hosted multi-symbol trading stack for cTrader.

## Current automatic flow

```text
cTrader market feed
  → ctrader-engine writes closed bars to Redis
  → analysis-engine (Go) computes technical facts, strategies, and opportunities
  → Kafka opportunity lifecycle events
  → algo-bot (Python) applies freshness, quote, session, exposure, and risk policy
  → Redis TradePlan
  → ctrader-engine validates and executes with cTrader
```

The Go analysis engine is the sole automatic technical authority. Python does
not scan markets or rebuild zones; it owns execution policy, sizing, Telegram,
journal, and persistence. Manual signals remain an operator-controlled path.

## Services

- `analysis-engine/`: Go market state, indicators, structure, zones, strategies,
  opportunity lifecycle, arbitration, Kafka publication, and replay.
- `algo-bot/`: Python Kafka consumer, execution policy, risk/exposure checks,
  TradePlan construction, Telegram, journal, and manual algo.
- `ctrader-engine/`: .NET cTrader feed, Redis bar sink, TradePlan execution,
  and position lifecycle.
- `config/`: categorized YAML source used by the configuration compiler.
- `contracts/`: active cross-service schemas and generated configuration
  contracts.
- `deployment-template/`: production Compose/Ansible templates.

## Quick start

```bash
docker compose config -q
docker compose up -d
```

Run service tests from each service directory. The analysis engine also has a
deterministic replay command and committed fixtures under `analysis-engine/testdata/`.

## Configuration and documentation

The canonical entrypoint is `config/apexvoid.yml`, with the included files in
`config/` and environment overlays in `config/environments/`. Start with
[`docs/architecture.md`](docs/architecture.md), [`docs/configuration.md`](docs/configuration.md),
and [`docs/deployment.md`](docs/deployment.md).
