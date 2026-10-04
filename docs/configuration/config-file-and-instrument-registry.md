# Config file source and instrument registry

PR A introduces a structured YAML `CONFIG_FILE` layer and a typed
`instruments` registry while keeping production XAU readers on existing flat
catalog leaves.

## Precedence

```text
schema_default → profile → file_secret → config_file → dotenv →
process_environment → init_value → derived_compatibility_rule
```

`APEXVOID_CONFIG_FILE` (or CLI `--config-file`) selects the YAML path. When
unset, the CONFIG_FILE layer is empty so ENV-only deploys keep working.
Missing/unreadable/malformed YAML fails closed with a path-based error.

## Instrument registry

Dynamic `instruments.<SYMBOL>.*` values cannot be expressed as static Catalog
V2 ENV leaves (mappings need unique `canonical_env`). The registry is therefore
validated outside the leaf catalog as `InstrumentsConfig` and attached to
`runtime_config.instruments`.

For PR A, `instruments.XAU` is projected into:

- `contract.instrument.*`
- `market_data.lookbacks.*`
- `analysis.zones.symbol_contract.*`

Trading consumers and the cTrader shared ENV handshake therefore stay
unchanged. A second instrument may be declared (for example disabled `EUR`)
without introducing new Python field names for that symbol.

For the typed per-instrument composition boundary
(`runtime_config.for_instrument(...)`, rollout stages, policy references, and
XAU parity rules), see
[effective-instrument-context.md](./effective-instrument-context.md).

## Alias policy

Existing XAU ENV names remain as deprecated aliases in PR A. Conflicting YAML
registry values versus ENV/init follow normal precedence; the resolver emits
`instrument_registry_leaf_conflict` when ENV/init wins over the registry.

## Deployment contract

`contracts/configuration/deployment-contract.generated.json` lists bootstrap
ENV, secret ENV, YAML schema paths, deprecated XAU aliases, and the instrument
schema for Ansible (PR C). It is secret-safe and unused by production runtime.

## Deployment (Compose)

Production compose (`deployment-template/docker-compose.yml.j2`) no longer
embeds Jinja trading defaults. Ansible renders:

- `config/trading-bot.yml` — structured CONFIG_FILE (`APEXVOID_CONFIG_FILE`)
- `secrets/trading-bot.env` — vault secrets + cleartext shared ENV
- `docker-compose.yml` — mounts YAML; bot/cTrader consume `env_file`

Local `docker-compose.yml` follows the same pattern with `.env` +
`config/trading-bot.yml`.

## Commands

```bash
python -m app.configuration.diagnostic_cli --check \
  --config-file ./config/trading-bot.yml --show-sources

python -m app.configuration.generate --check
```
