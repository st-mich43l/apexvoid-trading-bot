# Operations

Deployment, day-to-day checks, backups, updates and incident response for the
running stack. The deployment is outbound-only; there is no inbound HTTP surface
to secure.

## Stack

```text
postgres + redis + kafka → kafka-init
                         → ctrader-engine + analysis-engine → bot (algo-bot)
```

- `postgres`: trade history, opportunity ledger and audit data.
- `redis`: market bars, spot, TradePlans, plan state, locks and reservations.
- `kafka` / `kafka-init`: the durable opportunity lifecycle and its topic provisioning.
- `analysis-engine`: Go analysis and opportunity publication.
- `bot`: Python admission, arbitration, TradePlan construction, Telegram, persistence.
- `ctrader-engine`: .NET feed, Redis bar sink and broker execution.

`config/apexvoid.yml` is the production root. The Ansible template mounts the
whole `config/` directory read-only into `analysis-engine`, `ctrader-engine` and
`bot`; there is no compiler container or generated runtime state. Set
`APEXVOID_CONFIG_FILE` to `/config/apexvoid.yml` (or `/config/apexvoid.demo.yml`
for a deliberate demo deployment). Secrets stay in `.env` locally or the Ansible
secret store ([configuration](configuration.md)).

## Rollout

1. Update the YAML and the synchronized Ansible-library config.
2. Validate and run the service suites:

   ```bash
   docker compose config -q
   PYTHONPATH=algo-bot python config/scripts/resolve_reference.py --all
   ```

3. Deploy through Ansible; do not hand-edit rendered Compose.
4. Confirm `docker compose ps` shows `analysis-engine`, `ctrader-engine` and `bot`
   healthy, that Redis receives `bars:XAU:M5` and the FX bar keys, and that the
   Kafka consumer group and broker session are ready in the logs.

The cTrader healthcheck can stay pending during historical backfill. A healthy
feed does not prove the Python consumer is receiving opportunities; check both
the analysis Kafka logs and the Algo Bot policy logs.

## Routine checks

```bash
docker compose ps
docker compose logs --tail=50 bot analysis-engine ctrader-engine
tail -n 50 logs/algo-bot/algo-bot.log
docker exec apexvoid-trading-redis redis-cli ZCARD bars:XAU:M5
df -h /
```

DM the bot `active`: a reply confirms the poll loop is alive.

## Logs

Each service writes and rotates its own daily file (`logs/<service>/`); stdout
still feeds `docker compose logs`. Rotation happens inside the process at local
midnight (Python `TimedRotatingFileHandler`, C# `DailyFileLog`); retention is 14
days (`LOG_RETENTION_DAYS`). No host `logrotate` is needed.

## Backups

Back up the `signals` database with `pg_dump` (never a raw volume copy) and keep
`.env` in a password manager off the host.

```bash
# crontab -e
0 2 * * * docker exec apexvoid-trading-postgres pg_dump -U apexvoid signals \
          > ~/backup-$(date +\%F).sql && \
          find ~ -maxdepth 1 -name 'backup-*.sql' -mtime +14 -delete

docker exec -i apexvoid-trading-postgres psql -U apexvoid signals < ~/backup-YYYY-MM-DD.sql
```

Growth is modest. Trim closed or cancelled manual signals older than 180 days
only when needed:

```bash
docker exec -i apexvoid-trading-postgres psql -U apexvoid -d signals -c "
  DELETE FROM manual_signals
  WHERE status <> 'open'
    AND closed_at < EXTRACT(EPOCH FROM NOW() - INTERVAL '180 days');
"
```

## Updating

```bash
git pull && docker compose up -d --build && docker compose logs -f bot
sudo apt-get update && sudo apt-get -y upgrade   # then restart docker or reboot
```

`restart: unless-stopped` resumes everything after a reboot; executor state
recovers from Redis on every start.

## Monitoring

The analysis engine exposes liveness and readiness inside the Compose network:

```bash
docker compose ps kafka kafka-init analysis-engine ctrader-engine
docker inspect --format '{{.State.Health.Status}}' analysis-engine
```

The bot has no inbound endpoint: watch for crashes in `docker compose logs bot`
or ping a dead-man's-switch while the container runs.

## Owner DM daily wipe

`telegram.owner_dm_daily_wipe_enabled` (on in `config/telegram.yml`): at each
trade-day rollover the ApexVoid bot deletes the messages in its own private DM
with the owner sent since the previous wipe, both its sends and the owner's typed
commands. Irreversible. The scanner/algo bot's DM (autonomous root cards, the
personal-trade card and its lifecycle) is a separate Telegram conversation and is
never touched: that is the trade record. Outgoing and incoming messages are
journaled in Redis for the current trade date and swept at rollover (best effort
per message); a missed sweep self-expires after three days.

## Kafka and Go analysis incidents

Go is the sole automatic technical producer and Kafka delivery is the live
boundary. If Kafka or the consumer fails, automatic plan creation stops and the
startup and health checks must be repaired before automatic trading restarts;
there is no Python fallback.

- **Kafka down or lagging**: no new events arrive. On recovery the freshness gate
  refuses to replay a stale backlog as live opportunities.
- **Kafka data loss**: topics and committed offsets vanish together, so nothing is
  replayed inconsistently, but a terminal event may never arrive. Orphaned matches
  expire through their own `expires_at`. A persistent Kafka log directory
  (`KAFKA_LOG_DIRS`) and the analysis-engine ledger volume
  (`analysis-engine-state`) are a hard precondition for trusting any live window.
- **PostgreSQL down**: lifecycle persistence and the plan pipeline fail closed.
- **Redis down**: the executor and the plan stream have no fallback; execution stops.
- Disabling the consumer flag is an outage switch, not a strategy handover; the
  startup gate refuses automatic trading until the durable consumer is back.

Freshness-gate reason codes (an event failing any is applied to the ledger for
history but never becomes a live plan): `opportunity_expired`, `event_too_old`,
`delivery_lag_exceeded`.

### Broker-position recovery

Withdrawal never closes an open position (see [execution](execution.md#expiry-and-cancellation)):

| Stage | On withdrawal |
|---|---|
| match, no plan yet | match removed, setup invalidated; nothing reached the broker |
| plan submitted, unfilled | every resting order cancelled; plan cancelled |
| partially filled | unfilled legs withdrawn; filled legs stay open and managed |
| open position | untouched; stop and target management continues |

**Known operational gap**: a frozen `ctrader-engine` stops TP/BE/trailing
management while its broker-side stops remain in force, and `restart:
unless-stopped` does not restart a stuck-but-running process. A watchdog on the
heartbeat is not implemented; a frozen executor needs a manual restart.

## Troubleshooting

- **Container not starting**: read the service log. Missing secrets (`TELEGRAM_BOT_TOKEN`,
  `POSTGRES_PASSWORD`, `CTRADER_*`) or invalid YAML are the usual cause; validate
  the exact root mounted in the container.
- **Telegram messages not arriving**: the bot was removed from the channel, the
  token was revoked (update `TELEGRAM_BOT_TOKEN` and recreate the container), or
  `SIGNAL_VIP_CHANNEL_ID` is wrong (re-derive it from `getUpdates`).
- **DM commands ignored**: they require `TELEGRAM_OWNER_ID`.
- **Host disk full**: `df -h /`, then `docker system prune -a --volumes` for unused
  images and layers.
