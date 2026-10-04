# Deployment Guide

The production stack is an outbound-only Compose deployment:

```text
postgres + redis + kafka → kafka-init
                         → ctrader-engine + analysis-engine → algo-bot
```

`config/apexvoid.yml` is the production non-secret configuration root. The
Ansible template mounts the complete `config/` directory read-only into
`analysis-engine`, `ctrader-engine`, and `algo-bot`. There is no compiler
container, generated manifest, or runtime configuration volume.

## Services

- `postgres`: trade history and audit database.
- `redis`: market-data bars, execution plans, and token mirror.
- `kafka` / `kafka-init`: durable analysis event plane and topic provisioning.
- `analysis-engine`: Go technical analysis and opportunity publication.
- `algo-bot`: Python execution policy, TradePlan construction, Telegram, and
  persistence.
- `ctrader-engine`: .NET feed, Redis bar sink, and broker execution.

## Configuration and secrets

Keep non-secret behavior in `config/*.yml`. Set `APEXVOID_CONFIG_FILE` to
`/config/apexvoid.yml` in production or `/config/apexvoid.demo.yml` for a
deliberate demo deployment. Secrets remain in `.env` locally or the Ansible
secret store: Telegram token, cTrader credentials/tokens, and database
password/DSN.

Validate before deployment:

```bash
docker compose config -q
PYTHONPATH=algo-bot python config/scripts/config_check.py
```

## Production rollout

1. Update the categorized YAML and the synchronized Ansible-library config.
2. Run the validation commands above and the service CI suites.
3. Deploy the image/config through Ansible; do not hand-edit rendered Compose.
4. Confirm `docker compose ps` shows `analysis-engine`, `ctrader-engine`, and
   `bot` healthy.
5. Confirm Redis receives `bars:XAU:M5` and the enabled FX bar keys, then
   inspect the service logs for Kafka consumer and broker readiness.

Useful checks:

```bash
docker compose ps
docker compose logs --tail=100 analysis-engine
docker compose logs --tail=100 ctrader-engine
docker compose logs --tail=100 bot
docker exec apexvoid-trading-redis redis-cli ZCARD bars:XAU:M5
```

The cTrader healthcheck can remain pending during historical backfill. A
healthy feed does not by itself prove that the Python consumer is receiving
opportunities; check both the analysis Kafka logs and Algo Bot policy logs.
