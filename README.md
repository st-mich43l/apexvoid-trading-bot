# ApexVoid Trading Bot

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![.NET](https://img.shields.io/badge/.NET-8-512BD4?style=flat-square&logo=dotnet&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-DC382D?style=flat-square&logo=redis&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-2CA5E0?style=flat-square&logo=telegram&logoColor=white)

Self-hosted multi-symbol trading stack: Telegram signal delivery,
market-structure analysis, ZoneWatch activation, and broker execution on
cTrader. Production demo books are configured per instrument (currently XAU,
EURUSD, GBPUSD, GBPJPY, and USDJPY). All external connections are outbound
(Telegram long-polling, Anthropic optional, cTrader Open API). No inbound
webhook / public HTTP surface.

```text
┌──────────────┐   OHLC ZSET    ┌─────────────────────┐
│ ctrader-     │ ──────────────▶ │ Redis                │
│ engine (.NET)│ ◀──────────── │ bars / plans / events│
│ feed+exec    │  TradePlan V8  └──────────┬──────────┘
└──────────────┘                           │
                                           ▼
┌──────────────┐   SQL          ┌─────────────────────┐
│ Postgres     │ ◀──────────── │ algo-bot (Python)    │
│ signals DB   │                │ Telegram + scanner   │
└──────────────┘                │ ZoneWatch → publish  │
                                └──────────┬──────────┘
                                           │ long-poll
                                           ▼
                                    Telegram VIP/public
```

Compose boot order: **postgres + redis → config-compiler → ctrader-engine → bot**.

---

## What it does

### Manual signals (owner DM + VIP/public)

- Parse the owner command surface (`/trade XAU sell 4100-4105 / sl … / tp …`
  or `/trade EURUSD buy 1.15007 / algo`) while retaining the legacy free-text
  forms.
- Broadcast to VIP and optional public channels; lifecycle
  `active` / `close` / `cancel` / `/trade_*` commands.
- Economic calendar brief, weekly VIP recap, optional Claude Vision chart draft.
- Details: [docs/bot-commands.md](docs/bot-commands.md).

### Autonomous analysis → TradePlan V8

1. **Scanner** runs detectors on closed H1/M15/M5 bars from Redis OHLC.
2. **Techniques** (Supply Demand, Order Block, FVG, iFVG, CRT) and structural
   reactions publish candidates; **2+ overlapping techniques** become a
   **Confluence Zone**.
3. **ZoneWatch** retains the zone (no premature setup card). Spot / M1 cycles
   evaluate location + activation.
4. When executable, **direct publish** builds a **TradePlan V8** into Redis.
5. **ctrader-engine** claims the plan, sizes from the equity table, and manages
   the group (shared stop, TP ladder, BE/trail).

Authoritative path: ZoneWatch → direct publish (ready-stream is fallback only).
See [docs/technique-zonewatch-publish.md](docs/technique-zonewatch-publish.md)
and [docs/adr-trade-plan-v8-cutover.md](docs/adr-trade-plan-v8-cutover.md).

### High-frequency M1 scalping (separate lane)

Shadow/paper/live HFS on closed M1 with immutable M5 context. Does not share
the technique ZoneWatch publishers. See [docs/scalping/README.md](docs/scalping/README.md).

---

## Tech stack

| Layer | Choice |
|---|---|
| Algo / Telegram | Python 3.12, aiogram 3, asyncpg, Redis |
| Broker feed + execution | .NET 8, cTrader Open API |
| Config | `config/trading-bot.yml` + secrets in `.env` → `ResolvedRuntimeManifest` |
| Persistence | PostgreSQL (`signals`), Redis (bars, ZoneWatch, plans, events) |
| Packaging | Docker Compose v2 |

Canonical configuration docs live under
[docs/configuration/](docs/configuration/configuration-architecture.md).

---

## Documentation

| Doc | Contents |
|---|---|
| [Architecture](docs/architecture.md) | Services, loops, ZoneWatch publish path |
| [Technique / ZoneWatch publish](docs/technique-zonewatch-publish.md) | Five techniques, Confluence, chase, closed-bar invalidate, opposing room |
| [Bot commands](docs/bot-commands.md) | Manual posting and lifecycle |
| [Deployment](docs/deployment.md) | Host → running stack |
| [Operations](docs/operations.md) | Logs, backups, updates, troubleshooting |
| [Redis contract](docs/redis-contract.md) | Bars, plans, events boundary |
| [TradePlan V8 ADR](docs/adr-trade-plan-v8-cutover.md) | Plan identity cutover |
| [Security](docs/security.md) | Threat model and secrets |
| [Changelog](CHANGELOG.md) | Behavior / config / deploy notes |

---

## Quick start

```bash
git clone <this-repo> apexvoid-trading-bot
cd apexvoid-trading-bot
cp .env.example .env
# Fill secrets: TELEGRAM_BOT_TOKEN, SIGNAL_VIP_CHANNEL_ID, TELEGRAM_OWNER_ID,
# POSTGRES_PASSWORD, DATABASE_URL, REDIS_URL, CTRADER_* credentials.
# Non-secret tuning: config/trading-bot.yml

docker compose up -d --build
docker compose ps
docker compose logs -f bot
docker compose logs -f ctrader-engine
```

Expected shape:

- `config-compiler` exits 0 after writing `/runtime/resolved-runtime.json`
- `ctrader-engine` heartbeats and writes `bars:XAU:*`
- `bot` starts Telegram polling and scanner / ZoneWatch loops

Owner DM `active` should reply (empty book is fine). Demo auto-trade profile
runbook: [docs/demo-eval-autotrade.md](docs/demo-eval-autotrade.md).

---

## Repository layout

```text
apexvoid-trading-bot/
├── docker-compose.yml          # postgres, redis, config-compiler, engine, bot
├── config/trading-bot.yml      # non-secret Python/shared tuning
├── .env.example                # secrets + bootstrap (generated policy)
├── docs/                       # architecture, ops, config, ADRs
├── contracts/                  # shared JSON / schema contracts
├── ctrader-engine/             # .NET feed + TradePlan executor
│   └── src/                    # AutoTradeEngine, TradePlanExecutionEngine, …
└── algo-bot/                   # Python application
    ├── Dockerfile
    ├── requirements.txt / pyproject
    ├── scripts/
    └── app/
        ├── main.py             # composition root: install cutover, spawn loops
        ├── configuration/      # catalog, YAML loader, runtime manifest
        ├── core/               # runtime_config facade, symbols, logging
        ├── persistence/        # Postgres store + Redis client
        ├── bot/                # aiogram wiring + handlers
        ├── signals/            # manual signals, calendar, weekly report
        ├── analysis/           # detectors, techniques, scanner, market map
        ├── autotrade/          # ZoneWatch cutover, TradePlan V8, delivery
        ├── scalping/           # HFS M1 lane
        └── runtime/            # multi-symbol routing helpers
```

---

## License

Private project. Not licensed for redistribution.
