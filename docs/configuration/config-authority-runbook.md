# Python configuration startup and recovery runbook

Python configuration is **canonical-only**. There is no authority selector and
no legacy Settings rollback path.

See also: [configuration architecture](configuration-architecture.md).

## Preconditions

- Apply the intended environment through the normal deployment inventory.
- Keep real secrets in the deployment vault or environment, never in this
  runbook or Git.
- Confirm required values: Telegram bot token, VIP channel id (or compatible
  alias), PostgreSQL password / `DATABASE_URL`, plus optional knobs used by the
  deployment.

## Startup

1. Deploy/restart the bot process/container.
2. Verify one startup record contains:
   `configuration_authority=canonical`,
   `configuration_profile=<profile>`,
   `configuration_catalog_fingerprint=<sha256>`.
3. Verify basic bot health: process remains running, Telegram polling starts,
   Redis/PostgreSQL health is normal, and configuration health publishes.

Canonical selection is unconditional. Invalid configuration fails closed.

## Resolved runtime manifest (PR2 / PR3 / PR4)

Compose compiles `/runtime/resolved-runtime.json` before cTrader and the bot
start.

**PR4 production authority:**

```text
CTRADER_CONFIGURATION_SOURCE=manifest
CTRADER_MANIFEST_PARITY_MODE=off
```

See `docs/configuration/manifest-authority-cutover.md` and
`docs/configuration/cross-service-runtime-manifest.md`.

Environment source remains available as an application rollback mode, but
operational rollback after inventory cleanup requires redeploying the previous
Ansible inventory revision that still contains legacy trading ENV.

## Leftover `APEXVOID_CONFIG_AUTHORITY`

If present in a host environment, it is an unmanaged unknown variable. It is
not read and does not alter runtime behavior.

## Recovery from invalid configuration

1. Correct the reported invalid input.
2. Restart the service.
3. Confirm the startup record and config health.

## Recovery from a bad deployment

1. Revert the Phase 2I deployment commit (or redeploy the previous known-good
   image).
2. Rebuild/redeploy if required.
3. Restart the bot.
4. Verify configuration fingerprint and health.

Do **not** set `APEXVOID_CONFIG_AUTHORITY=legacy` — that selector no longer
exists.

## Diagnostics

```
python -m app.configuration.diagnostic_cli --check
python -m app.configuration.generate --check
```

## Deprecated ENV aliases

Catalog-backed deprecated aliases remain supported (equal duplicates warn;
conflicts fail closed).


## Evergreen integrity

Use `python -m app.configuration.configuration_integrity_gate --check`.
