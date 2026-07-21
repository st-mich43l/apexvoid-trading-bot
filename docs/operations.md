# Operations

Day-to-day operation of the running bot: monitoring, backups, logs, updates,
and troubleshooting. There is no TLS certificate or nginx to manage.

## Routine Checks

A weekly sanity pass takes under a minute:

```bash
cd ~/apexvoid-trading-bot
docker compose ps                          # 'bot' is Up
docker compose logs --tail=50 bot          # any ERROR lines?
df -h /                                     # free space
free -h                                     # RAM not pinned
```

Then DM the bot `active` — a reply confirms the poll loop is alive.

## Log Access

Docker's `json-file` driver (configure `max-size`/`max-file` in
`/etc/docker/daemon.json` to bound volume) captures stdout:

```bash
docker compose logs -f bot            # live tail
docker compose logs --tail=200 bot    # last N lines
docker compose logs --since 2h bot    # since a time
```

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

`signals.db` holds `manual_signals` and `pips_log`. Both grow slowly (a handful
of rows per day) and rarely need pruning. To trim closed/cancelled signals older
than 180 days:

```bash
docker compose exec bot python3 -c "
import sqlite3, time
conn = sqlite3.connect('/data/signals.db')
cur = conn.cursor()
cutoff = int(time.time()) - 180 * 86400
cur.execute(\"DELETE FROM manual_signals WHERE status != 'open' AND closed_at < ?\", (cutoff,))
n = cur.rowcount
conn.commit(); cur.execute('VACUUM')
print(f'Deleted {n} closed signals')
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
```

- `pydantic ... ValidationError` on `Settings` — a required env var
  (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) is missing from `.env`.
- `sqlite3.OperationalError: unable to open database file` — the `./data`
  bind-mount is missing. `mkdir -p data` and retry.

### Telegram messages are not arriving

- Bot removed from the channel — re-add as admin with Post Messages.
- Token revoked/regenerated — update `TELEGRAM_BOT_TOKEN` and
  `docker compose up -d --force-recreate bot`.
- `TELEGRAM_CHAT_ID` wrong — re-derive from
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
