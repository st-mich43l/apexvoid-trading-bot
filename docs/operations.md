# Operations

Day-to-day operation of the running bot: monitoring, backups, logs, updates,
and troubleshooting. There is no TLS certificate or nginx to manage.

## Routine Checks

A weekly sanity pass takes under a minute:

```bash
cd ~/apexvoid-trading-bot
docker compose ps                          # 'bot' is Up
docker compose logs --tail=50 bot          # any ERROR lines?
tail -n 50 logs/algo-bot/algo-bot.log      # host-mounted daily log
df -h /                                     # free space
free -h                                     # RAM not pinned
```

Then DM the bot `active` — a reply confirms the poll loop is alive.

Compose also runs a one-shot `config-compiler` that writes
`/runtime/resolved-runtime.json`. Production uses
`CTRADER_CONFIGURATION_SOURCE=manifest` with
`CTRADER_MANIFEST_PARITY_MODE=off` (legacy ENV parity off; manifest validation
enforced). See `docs/configuration/manifest-authority-cutover.md`.

Multi-symbol: production **live** instruments are XAU, EURUSD, GBPUSD,
GBPJPY, and USDJPY (demo account). See `docs/runtime/multi-symbol-routing.md`.

## Log Access

Each service writes and rotates its own daily log files on the host. Stdout
still feeds `docker compose logs`.

```bash
# Live docker stream (unchanged)
docker compose logs -f bot
docker compose logs -f ctrader-engine

# Host-mounted rotated files (service-managed)
tail -f logs/algo-bot/algo-bot.log
tail -f logs/ctrader-engine/ctrader-engine.log
ls logs/algo-bot/          # algo-bot.log, algo-bot.log.YYYY-MM-DD, …
ls logs/ctrader-engine/    # ctrader-engine.log, ctrader-engine.log.YYYY-MM-DD, …
```

Rotation is done **inside the process** at local midnight (Python
`TimedRotatingFileHandler`, C# `DailyFileLog`). Default retention is 14 days
(`LOG_RETENTION_DAYS`). No host `logrotate` job is required.

## Backups

### What to back up

- The `postgres` container's `signals` database — signal lifecycle + pips
  history. Dumped via `pg_dump`, not a raw volume/file copy.
- `~/apexvoid-trading-bot/.env` — secrets. Store in a password manager, **not**
  on the same host.

### Daily local snapshot

```bash
# crontab -e
0 2 * * * docker exec apexvoid-trading-postgres pg_dump -U apexvoid signals \
          > ~/backup-$(date +\%F).sql && \
          find ~ -maxdepth 1 -name 'backup-*.sql' -mtime +14 -delete
```

### Restore

```bash
docker exec -i apexvoid-trading-postgres psql -U apexvoid signals \
  < ~/backup-YYYY-MM-DD.sql
```

## Database Maintenance

PostgreSQL `signals` holds manual lifecycle rows and ingested auto-trade
stats. Growth is modest; prefer `pg_dump` backups over ad-hoc deletes.

Trim closed/cancelled **manual** signals older than 180 days only when needed
(adjust table/column names to the current store schema if they differ):

```bash
docker exec -i apexvoid-trading-postgres psql -U apexvoid -d signals -c "
  DELETE FROM manual_signals
  WHERE status <> 'open'
    AND closed_at < EXTRACT(EPOCH FROM NOW() - INTERVAL '180 days');
"
```

## Updating

### Code changes

```bash
cd ~/apexvoid-trading-bot
git pull
docker compose up -d --build
docker compose logs -f bot
```

### Docker / OS updates

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo systemctl restart docker      # or: sudo reboot if kernel/libc updated
docker compose up -d
```

With `restart: unless-stopped`, the container resumes after a reboot.

## Monitoring

Because there is no HTTP health endpoint, monitor liveness by either:

- Watching for a startup line / absence of crashes in `docker compose logs bot`.
- A cron heartbeat that pings a dead-man's-switch service (Healthchecks.io)
  only while the container is running:
  ```bash
  */5 * * * * docker inspect -f '{{.State.Running}}' xau-bot | grep -q true && \
              curl -fsS --retry 3 https://hc-ping.com/<uuid> > /dev/null
  ```

## Troubleshooting

### Container is not starting

```bash
docker compose logs bot
docker compose logs config-compiler
docker compose logs ctrader-engine
```

- Config / pydantic validation errors — required secrets missing from `.env`
  (`TELEGRAM_BOT_TOKEN`, `POSTGRES_PASSWORD`, `CTRADER_*`, …) or invalid
  `config/trading-bot.yml`.
- Manifest verify failure — `config-compiler` did not write
  `/runtime/resolved-runtime.json`; fix YAML and recreate.
- Postgres / Redis unhealthy — wait for healthchecks; check
  `DATABASE_URL` / `REDIS_URL`.

### Telegram messages are not arriving

- Bot removed from the channel — re-add as admin with Post Messages.
- Token revoked/regenerated — update `TELEGRAM_BOT_TOKEN` and
  `docker compose up -d --force-recreate bot`.
- `SIGNAL_VIP_CHANNEL_ID` (or public id) wrong — re-derive from
  `https://api.telegram.org/bot<TOKEN>/getUpdates`.

### DM commands are ignored

- DM commands are disabled unless `TELEGRAM_OWNER_ID` is set. Confirm your
  numeric ID is configured and matches the sender.

### Chart analysis fails

- `ANTHROPIC_API_KEY not configured` — set it in `.env` and recreate the
  container. Otherwise check `docker compose logs bot` for the API error.

### Host disk fills up

```bash
df -h /
docker system prune -a --volumes   # removes unused images and layers
```
