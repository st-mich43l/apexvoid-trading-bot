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

## Autonomous opposite-direction exposure

Each instrument (usually through its pack) owns one rule in `instruments.yml`
under `exposure.opposite_position`; strategy names, families and scalp status
are never consulted.

| Instrument class | Config | Behavior |
| --- | --- | --- |
| FX (EURUSD, GBPUSD, GBPJPY, USDJPY, any new pair) | `allowed: false` | Any opposite exposure on the symbol blocks the plan at any distance (`fx_opposite_position_not_allowed`). |
| XAU (`XAU`, `XAUUSD`, `GOLD`) | `allowed: true`, `minimum_separation_pips: 150` | Entry must be at least 150 pips (inclusive; `pip_size` 0.1, so 15.0 price) from EVERY existing opposite group, else `xau_opposite_position_too_close`. |

A missing or invalid policy fails closed: Algo Bot rejects with
`opposite_exposure_policy_unavailable` and cTrader Engine refuses to start (or
refuses the order if exposure is present). Algo Bot (`evaluate_opposite_exposure`)
and cTrader Engine (`OppositeExposureFence`, run before the first broker
mutation of a plan against tracked plan state plus real broker positions and
pending orders) enforce the same rule independently. Same-direction stacking is
a separate rule and is unchanged. Manual `/algo` plans are exempt (owner
instruction).

## Validation

```bash
docker compose config -q
PYTHONPATH=algo-bot python config/scripts/resolve_reference.py --all
```

The loader rejects malformed roots, include cycles, missing required sections,
and invalid instrument declarations. Update the owning YAML category and the
native schema together when adding a field.
