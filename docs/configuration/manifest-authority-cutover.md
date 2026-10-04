# Manifest authority cutover

PR4 makes the **ResolvedRuntimeManifest V2** the authoritative non-secret
trading configuration for Python and cTrader.

## Final authority model

```text
Ansible public structured YAML
+ Vault secrets / bootstrap credentials
        │
        ▼
config/trading-bot.yml
        │
        ▼
canonical Python resolver
        │
        ▼
ResolvedRuntimeManifest V2
        │
        ├── Python runtime
        └── cTrader runtime (CTRADER_CONFIGURATION_SOURCE=manifest)
```

Production Compose:

```text
CTRADER_CONFIGURATION_SOURCE=manifest
CTRADER_MANIFEST_PARITY_MODE=off
```

`off` disables only the legacy ENV-versus-manifest **parity comparison**.
Manifest schema, fingerprint, rollout, account, and runtime validation remain
**enforced** (`manifest_validation=enforced` in startup logs).

## ENV classification

| Classification | Source after cutover |
| --- | --- |
| `secret_environment` | Vault → secrets ENV |
| `bootstrap_environment` | public bootstrap ENV |
| `manifest` | ResolvedRuntimeManifest only |
| `derived_runtime` | calculated at runtime |
| `deprecated_compatibility` | must not affect behaviour |

Unclassified must remain zero. See
the generated runtime manifest and the environment contract.

## Startup

### Manifest source

1. Read source / parity mode (unknown values fail closed).
2. Load `CTraderAccountOptions.FromEnvironment()` (secrets + bootstrap only).
3. Load manifest; `ManifestRuntimeFactory.Create` builds feed, auto-trade, and
   instrument registry without any `AUTO_TRADE_*` / `CTRADER_SYMBOL` reads.
4. Validate: V2 instrument runtimes required; no XAU numeric fallbacks.

### Environment source (rollback-compatible application mode)

Still supports `FeedOptions.FromEnvironment()` /
`AutoTradeOptions.FromEnvironment()`. Production inventory after PR4B will
**not** ship full legacy trading ENV, so operational rollback requires
redeploying the previous Ansible inventory revision together with the previous
application image — not flipping a single variable.

No silent fallback from manifest → environment.

## Parity mode meaning

| Mode | When usable |
| --- | --- |
| `enforce` / `warn` | Environment source only (legacy trading ENV present) |
| `off` | Manifest authority (production) |

Setting parity ≠ `off` with `source=manifest` fails closed.

## Vault / bootstrap boundary

- Vault: credentials, tokens, channel IDs, database password — unchanged.
- Bootstrap ENV: host/port/timeouts/token paths/REDIS_URL/HEALTH_FILE/log/
  APEXVOID_* paths / source+parity selectors.
- Structured YAML: all trading policy.

## Deployment sequence

1. Merge application PR4A; build and publish images.
2. Merge Ansible PR4B.
3. Deploy new images and inventory together.
4. Never deploy either half independently.

## Rollback

1. Redeploy previous known-good images.
2. Redeploy previous Ansible inventory (full legacy ENV).
3. Confirm `source=environment` and `parity=enforce`.
4. Restart stack; verify XAU feed/execution.

## Production instrument boundary

Live instruments are declared in the resolved manifest / YAML registry
(currently XAU + EURUSD + GBPUSD + GBPJPY + USDJPY on demo). See
[`docs/runtime/multi-symbol-routing.md`](../runtime/multi-symbol-routing.md).
Do not assume XAU-only ENV shape after manifest authority.

## Future instrument onboarding

1. Observe manifest health under authority.
2. Add instrument as `feed_only` in structured YAML.
3. Promote through analysis_only → paper → live with explicit review.
