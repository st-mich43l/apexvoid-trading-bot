# Configuration architecture

Catalog V2 is the active identity model: every catalog entry is identified by
its canonical path (`actionability.contested_corridor.gap_atr`). Display IDs
are derived as `config:<path>`.

## Runtime flow

1. Source bundle (schema → profile → file secrets → **config file** → dotenv →
   process ENV → init)
2. Canonical resolver (compatibility rules + instrument registry projection)
3. `PythonRuntimeConfig` (includes typed `instruments` registry)
4. `app.core.config.runtime_config`
5. Typed production consumers

There is exactly one Python configuration authority. There is no Settings
singleton, flat facade, or runtime legacy selector.

## Active docs

| Doc | Purpose |
|---|---|
| [config-file-and-instrument-registry.md](config-file-and-instrument-registry.md) | YAML `CONFIG_FILE`, instruments, deprecated ENV aliases |
| [configuration-governance.md](configuration-governance.md) | Add/deprecate fields; integrity gate commands |
| [cross-service-runtime-manifest.md](cross-service-runtime-manifest.md) | `ResolvedRuntimeManifest` shared with cTrader |
| [manifest-authority-cutover.md](manifest-authority-cutover.md) | Production `CTRADER_CONFIGURATION_SOURCE=manifest` |
| [effective-instrument-context.md](effective-instrument-context.md) | Per-symbol typed runtime view |
| [config-authority-runbook.md](config-authority-runbook.md) | Operator authority checks |
| [config-catalog.generated.md](config-catalog.generated.md) | Generated catalog |
| [environment-reference.generated.md](environment-reference.generated.md) | Generated ENV reference |

## Fingerprints

- **configuration_contract_fingerprint** — behaviorally relevant metadata used
  by startup and config-health.
- **configuration_document_fingerprint** — contract fields plus descriptions
  and evidence notes for documentation freshness.

`catalog_fingerprint()` remains an alias of the contract fingerprint for
callers that still use the older helper name.

## Artifacts and commands

See [configuration-governance.md](configuration-governance.md).
