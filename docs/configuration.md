# Configuration

`config/apexvoid.yml` is the production root and
`config/apexvoid.demo-eval.yml` is the optional demo root. Each root resolves
the same categorized YAML files and one environment overlay. Go, Python, and
.NET load that tree directly; there is no generated runtime manifest or
second non-secret configuration file.

## Categories

- `runtime.yml`: service identity, infrastructure connections, feed settings,
  and logging.
- `instruments.yml`: symbols, broker names, price geometry, and lookbacks.
- `analysis.yml`: Go analysis inputs and technical strategy settings.
- `auto-algo.yml`: Algo Bot execution policy, eligibility, lifecycle, and risk.
- `execution.yml`: TradePlan, broker-execution, manual workflow, and journal
  policy settings.

## Authority boundaries

Analysis settings are consumed by Analysis Engine for technical facts and
strategy evaluation. Algo Bot settings govern execution-time eligibility,
account policy, sizing, and TradePlan creation. cTrader settings govern feed
and broker execution. Keep secrets in the deployment secret store; do not add
secrets to the categorized YAML. `APEXVOID_CONFIG_FILE` is the only
application configuration selector; secret credentials may remain in the
deployment environment.

## Validation

```bash
docker compose config -q
PYTHONPATH=algo-bot python -m app.configuration.validate config/apexvoid.yml
PYTHONPATH=algo-bot python -m app.configuration.validate config/apexvoid.demo-eval.yml
```

Validation must reject malformed roots, duplicate category ownership, missing
required values, unknown fields, and secrets in YAML. Update the owning
category and shared schema together when adding a field.
