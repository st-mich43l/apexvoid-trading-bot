# Operations

Day-to-day operation of the running bot: monitoring, backups, logs, updates,
and troubleshooting. There is no TLS certificate or nginx to manage.

## Routine Checks

A weekly sanity pass takes under a minute:

```bash
cd ~/apexvoid-trading-bot
docker compose ps                          # 'bot' is Up
docker compose logs --tail=50 bot          # any ERROR lines?
docker compose logs --tail=50 kafka-init analysis-engine ctrader-engine
tail -n 50 logs/algo-bot/algo-bot.log      # host-mounted daily log
df -h /                                     # free space
free -h                                     # RAM not pinned
```

Then DM the bot `active` — a reply confirms the poll loop is alive.

All services resolve the mounted categorized YAML directly from
`APEXVOID_CONFIG_FILE`. There is no generated runtime manifest or compiler
container. Validate the selected root with `python -m app.configuration.validate`
before deployment.

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

The analysis engine has HTTP liveness/readiness endpoints inside the Compose
network. Check the initializer and engine health state first:

```bash
docker compose ps kafka kafka-init analysis-engine ctrader-engine
docker compose logs --tail=100 kafka-init
docker compose inspect --format '{{.State.Health.Status}}' analysis-engine
```

The bot itself still has no inbound HTTP endpoint, so monitor its liveness by either:

- Watching for a startup line / absence of crashes in `docker compose logs bot`.
- A cron heartbeat that pings a dead-man's-switch service (Healthchecks.io)
  only while the container is running:
  ```bash
  */5 * * * * docker inspect -f '{{.State.Running}}' xau-bot | grep -q true && \
              curl -fsS --retry 3 https://hc-ping.com/<uuid> > /dev/null
  ```

## Owner DM daily wipe

Opt-in (`DELIVERY_OWNER_DM_DAILY_WIPE_ENABLED`, default off): at each local
trade-day rollover (same `SEQ_RESET_TZ` midnight boundary as the daily
`#seq` reset), the **ApexVoid bot** deletes every message in its own
private DM with the owner sent since the previous wipe — both the bot's
own sends and the owner's own typed commands. Irreversible; enable only
once satisfied with the behavior.

Scope is deliberately narrow: only the ApexVoid bot's own DM. The
scanner/algo bot's DM (autonomous root cards, and the owner's `/1r`
personal-trade root card + its lifecycle) is a **separate** Telegram
conversation and is never touched — that is the actual trade record.

Mechanism: every outgoing message the ApexVoid bot sends and every
incoming message the owner sends it are journaled in Redis for the current
trade date; at rollover the just-completed day's journal is swept
(best-effort per message — an already-gone message does not block the
rest) and cleared. A missed sweep (restart, Redis hiccup) self-expires
after 3 days rather than accumulating forever.

## Kafka & Go Analysis Incident Response

Go is the sole automatic technical-opportunity producer. Kafka delivery is
the live boundary; there is no per-symbol, per-strategy grant, acceptance row,
or Python fallback to roll back to. If Kafka or the consumer fails, automatic
plan creation stops and the startup/health checks must be repaired before
restarting automatic trading.

### Kafka failure modes

- **Kafka down or lagging** — no new Go events reach the consumer. On
  recovery, the freshness gate refuses to replay a stale backlog as live
  opportunities (see reason codes below). Rollback and withdrawal need no
  Kafka at all.
- **Kafka data loss** (broker logging to a non-persistent log dir) — topics
  and committed offsets vanish together, so nothing is replayed
  inconsistently, but a terminal event for an earlier creation may never
  arrive. Orphaned matches are not stuck: they self-expire via their own
  `expires_at` TTL. This is why a persistent Kafka log directory
  (`KAFKA_LOG_DIRS`) and the analysis-engine's own ledger volume
  (`analysis-engine-state`) are a hard precondition for trusting any
  live analysis window — without them, a container recreate silently
  resets both the topic history and the publication ledger.
- **PostgreSQL down** — lifecycle persistence and the normal plan pipeline fail
  closed; no technical fallback is enabled.
- **Redis down** — the executor and the trade-plan stream have no fallback;
  execution stops.
- Disabling the Kafka consumer flag is an outage switch, not a strategy
  handover. The bot startup gate refuses automatic trading until Go mode and
  the durable consumer are enabled again.

### Freshness-gate reason codes

An event failing any of these is applied to the durable ledger for history,
but never becomes a live plan:

| reason code | meaning |
| --- | --- |
| `opportunity_expired` | the opportunity's own `expires_at` passed before consumption |
| `event_too_old` | observation older than the configured max event age |
| `delivery_lag_exceeded` | Kafka publish-to-consume lag exceeded the configured maximum |

### Broker-position recovery

Rollback/withdraw behavior is defined at every lifecycle stage, and none of
them close an open position:

| stage | on rollback/withdrawal |
| --- | --- |
| pending match, no plan yet | match removed, setup invalidated — nothing was ever at the broker |
| plan submitted, unfilled | every resting order is cancelled at the broker; plan cancelled |
| partially filled | unfilled legs are withdrawn; filled legs remain open, still managed to their own TP/BE |
| open position | untouched — stop and target management continues exactly as before |

Executor state recovers from Redis on every process start, so a restart of
the executor does not lose track of open work.

**Known operational gap:** an unhealthy or frozen `ctrader-engine` container
stops TP/BE/trailing management for everything it holds, while the
broker-side stops already placed remain in force (nothing closes, but
nothing actively manages either). `restart: unless-stopped` does **not**
restart the container on a stale internal heartbeat by itself — Docker only
restarts on process exit, not on a healthy-looking-but-stuck process. A
heartbeat fix for the auto-trade session loop specifically shipped in PR
#651 (2026-09-28), but a watchdog that auto-restarts `ctrader-engine` itself
on a stale heartbeat is still not implemented. Until it is, a frozen executor
needs a manual restart.

### Live Go activation

The Go consumer is configured by `config/analysis.yml` and the selected root.
After a deploy, verify `analysis_opportunity_consumer_loop` health and the
Kafka group.

## Troubleshooting

### Container is not starting

```bash
docker compose logs bot
docker compose logs analysis-engine
docker compose logs ctrader-engine
```

- Config validation errors — required secrets missing from `.env`
  (`TELEGRAM_BOT_TOKEN`, `POSTGRES_PASSWORD`, `CTRADER_*`, …), or invalid
  categorized YAML. Run the validator against the exact root mounted in the
  container and inspect the service that reports the path.
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
