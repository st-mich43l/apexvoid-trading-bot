# Configuration

The canonical source is `config/apexvoid.yml`. It includes the categorized
files in `config/` and the selected environment overlay under
`config/environments/`. The Python configuration compiler resolves that source
into the runtime manifest used by the services.

## Categories

- `runtime.yml`: service identity, timezone, logging, and runtime settings.
- `transport.yml`, `database.yml`, `telegram.yml`, `journal.yml`: service
  connections and delivery.
- `instruments.yml`: symbols, broker names, price geometry, and lookbacks.
- `analysis.yml`: Go analysis inputs and technical strategy settings.
- `auto-algo.yml`: Algo Bot execution policy, eligibility, lifecycle, and risk.
- `manual-algo.yml`: operator-controlled manual workflow.
- `execution.yml`: TradePlan and broker-execution contract settings.

## Authority boundaries

Analysis settings are consumed by Analysis Engine for technical facts and
strategy evaluation. Algo Bot settings govern execution-time eligibility,
account policy, sizing, and TradePlan creation. cTrader settings govern feed
and broker execution. Keep secrets in the deployment secret store; do not add
secrets to the categorized YAML.

## Validation

```bash
docker compose config -q
PYTHONPATH=algo-bot python -m app.configuration.generate --check
```

The generated files under `contracts/configuration/` are checked by the
configuration integrity and cross-language tests. Update source models and
regenerate them together when a configuration contract changes.
