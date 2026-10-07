# Configuration

`config/*.yml` is the one non-secret configuration authority. `.env` carries
secrets and process bootstrap only. There is no second catalog, resolver,
profile system or generated manifest: each service loads the YAML tree directly
into its own small typed view.

## Files

`config/apexvoid.yml` is the production root and `config/apexvoid.demo.yml` the
optional demo root. `APEXVOID_CONFIG_FILE` selects one; Go, Python and .NET
resolve the same includes and the same environment overlay
(`config/environments/production.yml` or `demo.yml`).

| File | Holds |
|---|---|
| `runtime.yml` | service identity, Redis, Kafka topics and specs, feed, logging |
| `instruments.yml` | instrument packs and instruments: broker names, geometry, stop envelope, targeting, exposure policy, execution containment |
| `analysis.yml` | Go analysis inputs and every strategy's thresholds |
| `auto-algo.yml` | Algo Bot eligibility, lifecycle and risk policy |
| `execution.yml` | TradePlan and broker execution, manual workflow |
| `telegram.yml` | Telegram presentation, lifecycle and reports |

Secrets and bootstrap inputs (see `.env.example`): the Telegram, cTrader and
database credentials, channel and owner ids, `APEXVOID_CONFIG_FILE`, and the
`LOG_*` switches. A secret is never added to the YAML.

## Reachability

Every key must have a live consumer in Python, Go or .NET. A key nothing reads is
deleted, not left as a harmless default: dead keys read as policy and mislead
operators. Do not add a compatibility alias, a deprecated path or a translation
layer; rename the key everywhere in one change. When adding a field, update the
owning YAML file and its consumer together.

## Autonomous opposite-direction exposure

Each instrument (usually through its pack) owns one rule under
`exposure.opposite_position`; strategy names, families and scalp status are never
consulted.

| Instrument class | Config | Behavior |
|---|---|---|
| FX (EURUSD, GBPUSD, GBPJPY, USDJPY, any new pair) | `allowed: false` | any opposite exposure on the symbol blocks the plan at any distance (`fx_opposite_position_not_allowed`) |
| XAU (`XAU`, `XAUUSD`, `GOLD`) | `allowed: true`, `minimum_separation_pips: 150` | the entry must be at least 150 pips (inclusive; `pip_size` 0.1, so 15.0 price) from EVERY existing opposite group, else `xau_opposite_position_too_close` |

A missing or invalid policy fails closed in both Algo Bot and cTrader Engine.
Manual `/algo` plans are exempt (owner instruction).

## Execution containment

An instrument can observe a strategy without trading it:
`instruments.<SYMBOL>.overrides.execution.go_opportunity.observe_only_strategies`.
Analysis stays on, so the opportunity is produced, stored and decided with reason
`execution_contained`, but no TradePlan is built from it. Removing a name
re-enables execution; nothing else changes. Current containment is documented
with its evidence in [strategies](strategies/README.md).

`analysis.strategies.supply` and `demand` carry `higher_timeframes: [M15]`: the M15
frame's own supply/demand zones are also evaluated against the M5 closed bars
(see [supply](strategies/supply.md#higher-timeframe-zones)). Removing the key
restores M5-only evaluation.

## Sessions

Session is analysis context and a quality input, never a clock gate: no
London/NY or kill-zone window blocks a plan on any instrument. XAU earned most of
its profitable week outside the London/NY opens (Asia +1240 pips, London +296,
NY +1569), so a hard session gate would remove edge.

## Validation

```bash
docker compose config -q
PYTHONPATH=algo-bot python config/scripts/resolve_reference.py --all
```

The loaders reject malformed roots, include cycles, missing required sections and
invalid instrument declarations.
