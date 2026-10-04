# Configuration

`config/apexvoid.yml` is the production root and `config/apexvoid.demo.yml` is
the optional demo root. Each root includes the same native YAML categories and
one environment overlay. Go, Python, and .NET load that tree directly.

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
PYTHONPATH=algo-bot python config/scripts/resolve_reference.py --all
```

The loader rejects malformed roots, include cycles, missing required sections,
and invalid instrument declarations. Update the owning YAML category and the
native schema together when adding a field.
