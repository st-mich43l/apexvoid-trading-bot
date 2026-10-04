# Multi-symbol routing

Live symbol rollout is declared in `config/instruments.yml`. Each live
instrument owns its broker symbol, aliases, timeframes, price geometry,
execution envelope, and rollout state. The root selected by
`APEXVOID_CONFIG_FILE` is resolved identically by all three services.

`ctrader-engine` registers every non-disabled instrument and publishes bars to
the Redis keys `bars:{canonical_symbol}:{timeframe}`. Analysis Engine consumes
those keys and publishes Kafka opportunities with the canonical instrument
identity. Algo Bot applies execution policy using the same instrument
geometry, and cTrader resolves the broker symbol from its YAML-derived
registry.

To add an instrument:

1. Add or reuse an explicit pack in `config/instruments.yml`.
2. Add the instrument with `rollout: live` only after feed and analysis
   validation; use `analysis_only` or `paper` while testing.
3. Add representative bars and cross-language configuration tests.
4. Synchronize the complete config tree to the Ansible deployment and deploy.

The broker session credentials remain secret environment inputs. They do not
select symbols or override instrument policy.
