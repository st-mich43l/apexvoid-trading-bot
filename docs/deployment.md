# Deployment Guide

Outbound-only Docker Compose stack: **no DNS, TLS cert, reverse proxy, or
inbound app ports**. SSH to the host is enough.

```text
postgres + redis + kafka → config-compiler + kafka-init
                         → ctrader-engine + analysis-engine → bot
```

| Service | Image / role |
|---|---|
| `postgres` | `postgres:17-alpine` — `signals` DB |
| `redis` | `redis:7-alpine` — bars, watches, plans, token mirror |
| `kafka` | `apache/kafka:4.0.0` — single-node KRaft market event bus, internal only |
| `kafka-init` | analysis-engine image — creates and verifies V3 topic contracts |
| `config-compiler` | algo-bot one-shot — validate YAML + emit manifest (exit 0) |
| `ctrader-engine` | .NET feed + TradePlan V8 executor |
| `analysis-engine` | Go closed-bar consumer and analysis runtime; `/health/ready` gates readiness |
| `bot` | Python Telegram + scanner + ZoneWatch + scalping |

Production cTrader authority (set on the engine; do not flip casually):

```text
CTRADER_CONFIGURATION_SOURCE=manifest
CTRADER_MANIFEST_PARITY_MODE=off
```

ENV holds **secrets + bootstrap only**. Non-secret tuning lives in
[`config/trading-bot.yml`](../config/trading-bot.yml). See
[configuration/manifest-authority-cutover.md](configuration/manifest-authority-cutover.md)
and [runtime/multi-symbol-routing.md](runtime/multi-symbol-routing.md).

**Live instruments (demo):** XAU, EURUSD, GBPUSD, GBPJPY, USDJPY.

---

## Two deploy shapes

| | Local / DIY | Production (Ansible) |
|---|---|---|
| Compose | [`docker-compose.yml`](../docker-compose.yml) | Rendered from [`deployment-template/docker-compose.yml.j2`](../deployment-template/docker-compose.yml.j2) |
| Images | `docker compose build` | Pre-built registry tags (`…/apexvoid-trading-bot:<sha>`, `…/apexvoid-ctrader-engine:<sha>`) |
| Secrets | root `.env` | `secrets/trading-bot.env` (`env_file`) |
| Config | `config/` V3 tree + legacy manifest file | Ansible ships the non-secret V3 tree and legacy manifest mirror |

Day-2 logs, backups, and troubleshooting: [operations.md](operations.md).

---

## Prerequisites

- Linux host with Docker Engine + Compose v2
- Telegram bot + VIP channel (optional public channel)
- cTrader Open API app + **demo** account tokens (for auto-trade)
- ~15 minutes for a first bring-up

---

## 1. Host & Docker

Official Docker packages (Compose v2 plugin):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $CODENAME stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                        docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER" && newgrp docker
```

Only inbound port needed: **SSH (22)**. Do not open 80/443 for the bot.

Host directories the compose file expects:

```bash
mkdir -p data/ctrader-token logs/algo-bot logs/ctrader-engine config secrets
chmod 700 secrets data/ctrader-token
```

---

## 2. Telegram credentials

1. **Bot token** — `@BotFather` → `/newbot`
2. **VIP channel** — private channel; bot admin with **Post Messages**; post once
3. **Chat ID** — `https://api.telegram.org/bot<TOKEN>/getUpdates` → `chat.id` (`-100…`)
4. **Owner ID** — `@userinfobot` (or `getUpdates`) — locks DM commands
5. **(Optional) Public channel** — same bot as admin; set public channel id

---

## 3. Configure

### Local compose (`.env`)

```bash
git clone <repo-url> apexvoid-trading-bot
cd apexvoid-trading-bot
cp .env.example .env
chmod 600 .env
```

Minimum secrets (full contract:
[environment-reference.generated.md](configuration/environment-reference.generated.md)):

```text
# Required
TELEGRAM_BOT_TOKEN=<from @BotFather>
SIGNAL_VIP_CHANNEL_ID=-100xxxxxxxxxx
TELEGRAM_OWNER_ID=<your numeric user id>   # strongly recommended
POSTGRES_PASSWORD=<secret>
DATABASE_URL=postgresql://apexvoid:<secret>@postgres:5432/signals
REDIS_URL=redis://redis:6379/0

CTRADER_CLIENT_ID=
CTRADER_CLIENT_SECRET=
CTRADER_ACCESS_TOKEN=
CTRADER_REFRESH_TOKEN=
CTRADER_ACCOUNT_ID=

# Compose defaults (usually leave as-is)
AUTO_TRADE_PROFILE=demo_eval
AUTO_TRADE_MAPPED_ZONE_ENABLED=false
AUTO_TRADE_MARKET_MAP_GUARD_ENABLED=false
LOG_DIR=/var/log/apexvoid
LOG_RETENTION_DAYS=14
LOG_FILE_ENABLED=true

# Optional
SIGNAL_PUBLIC_CHANNEL_ID=-100yyyyyyyyyy
ANTHROPIC_API_KEY=sk-ant-...               # Claude vision charts
```

Password in `DATABASE_URL` must match `POSTGRES_PASSWORD`. Hostnames must be
the compose service names (`postgres`, `redis`).

### Production Ansible

Vault renders `secrets/trading-bot.env` and the Jinja compose file. Do not
hand-edit the rendered compose on the VPS; change inventory/vault and
re-deploy.

**`config/trading-bot.yml` on the host is rendered from a mirror, not read
from this repo.** The deploy pipeline (`ansible-library`'s `deploy_image`
role, "Render trading-bot CONFIG_FILE YAML" task) writes the host's
`config/trading-bot.yml` verbatim from the Ansible variable
`apexvoid_trading_bot_config.yml`
(`inventory/group_vars/all/apexvoid_trading_bot_config.yml` in
`ansible-library`) — it never checks out or reads this repo's own
`config/trading-bot.yml` at deploy time. That variable is a **hand-maintained
copy** of this file, kept in sync manually.

Any PR that changes `config/trading-bot.yml` — new instrument fields, changed
policy values, a new `config_field` with a value that must differ from its
schema default — must update `apexvoid_trading_bot_config.yml` in
`ansible-library` in the same change (or a fast follow-up, merged and
deployed before/with this repo's change). Missing this does not fail the
build or the PR's own CI: it fails **every subsequent deploy**, silently,
until someone notices — `config-compiler` runs Pydantic validation against
the stale mirror on every container start, and a value mismatch (e.g. a
cross-field validator like "targeting.reward_risk must equal the final
target_r_multiple") makes it exit 1. Since `ctrader-engine` and `bot` both
`depends_on: config-compiler: condition: service_completed_successfully`,
that one exit code takes down the whole bot — not a partial/degraded start,
a full outage until the mirror is fixed and the stack is recreated. See
`docker compose logs config-compiler` first if a deploy "succeeds" in CI but
the bot doesn't come up — [operations.md § Troubleshooting](operations.md#troubleshooting)
has the exact signature.

### Non-secret YAML

Edit [`config/trading-bot.yml`](../config/trading-bot.yml) for instruments,
technique pack, activation, scalping mode, etc. After changes:

```bash
docker compose up -d --force-recreate config-compiler ctrader-engine bot
# or full: docker compose up -d --build
```

Current technique-pack shape (verify against the file before copying blindly):

```yaml
execution:
  technique:
    enforce: true
    include_late_ny: true
    reaction_require_killzone: false
    reaction_require_publish_window: false
    scalp_require_killzone: false
    require_sweep_body: false
    strict_premium_discount: true
  activation:
    mode: enforce
```

Operator detail: [technique-zonewatch-publish.md](technique-zonewatch-publish.md).

Autonomous demo profile contract: [demo-eval-autotrade.md](demo-eval-autotrade.md).

---

## 4. Launch (local)

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 config-compiler
docker compose logs -f ctrader-engine
docker compose logs -f bot
```

Expected:

| Check | Success signal |
|---|---|
| `config-compiler` | Exited **0**; wrote `/runtime/resolved-runtime.json` |
| `ctrader-engine` | Healthy after backfill (~2 min start period); bars for live symbols |
| `bot` | Up; Telegram polling + scanner / ZoneWatch / scalping loops |
| Redis | `bars:XAU:M5` (and FX keys) receiving closes |
| Host logs | `logs/algo-bot/algo-bot.log`, `logs/ctrader-engine/ctrader-engine.log` |

```bash
# Quick Redis peek (from host)
docker exec -it apexvoid-trading-redis redis-cli ZCARD bars:XAU:M5
docker exec -it apexvoid-trading-redis redis-cli ZCARD bars:EURUSD:M5
```

Engine healthcheck uses `/app/ctrader-feed --healthcheck` — first healthy can
take up to the **120s** `start_period` while historical bars backfill.

---

## 5. Smoke test

DM the bot (owner only):

```text
active
/trade
/trade XAU buy 4473-4470 / sl 4467 / tp 4476/4479/4482
```

Expect:

- `active` → empty book or open list
- `/trade` → live symbols + manual/algo capability
- A VIP (and public, if configured) signal card + `✅ Sent … (#N)`

Optional:

- `/trade XAU … / algo` — arms broker path; chart rows land in
  `manual_algo_charts` after issue/fill/close
- Chart screenshot DM — Claude analysis if `ANTHROPIC_API_KEY` is set

Technique / ZoneWatch smoke (after auto-trade is on):

- ZoneWatches retain across spot wicks; invalidate on closed-bar break
- Scalping discovery permits follow enabled archetypes in every session; optional `scalp_require_killzone` only blocks publish/activation when explicitly on
- Stops past furthest envelope log `stop_exceeds_envelope_furthest_leg`

---

## 6. Updates

```bash
cd ~/apexvoid-trading-bot   # or production deploy path
git pull                    # local DIY only
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 bot
docker compose logs --tail=100 ctrader-engine
```

Production image deploys pull the new tag via Ansible and recreate
`config-compiler` → engine → bot so the manifest rematerializes.

After YAML-only edits, recreate compiler + dependents (see §3). Day-2 ops:
[operations.md](operations.md).

---

## Deployment checklist

- [ ] Docker + Compose v2; `docker run hello-world` works
- [ ] `data/`, `logs/`, `config/`, `secrets/` present; secrets mode `600`/`700`
- [ ] `.env` or `secrets/trading-bot.env`: Telegram, Postgres, Redis, cTrader
- [ ] `TELEGRAM_OWNER_ID` set; bot admin on VIP (and public if used)
- [ ] `config/trading-bot.yml` matches intended instruments + technique pack
- [ ] `CTRADER_CONFIGURATION_SOURCE=manifest` / `MANIFEST_PARITY_MODE=off`
- [ ] `config-compiler` exit 0; engine healthy; bot Up
- [ ] `bars:XAU:M5` (and FX) non-empty; DM `active` replies
- [ ] (Optional) demo auto-trade verified via [demo-eval-autotrade.md](demo-eval-autotrade.md)
