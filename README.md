# ApexVoid Trading Bot

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![.NET](https://img.shields.io/badge/.NET-8-512BD4?style=flat-square&logo=dotnet&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-DC382D?style=flat-square&logo=redis&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-2CA5E0?style=flat-square&logo=telegram&logoColor=white)

Self-hosted multi-symbol trading stack: Telegram delivery, market-structure
analysis, ZoneWatch activation, and broker execution on cTrader.

**Live instruments today:** XAU, EURUSD, GBPUSD, GBPJPY, USDJPY — each with its
own pip geometry, session windows, and execution policy (XAU ladder vs FX
fixed-RR packs). All external I/O is outbound (Telegram long-poll, optional
Anthropic, cTrader Open API). No public HTTP or inbound webhooks.

```text
┌──────────────────┐   OHLC ZSET / spots    ┌─────────────────────┐
│ ctrader-engine   │ ─────────────────────▶ │ Redis               │
│ (.NET feed+exec) │ ◀── TradePlan V8 ───── │ bars · watches ·    │
└──────────────────┘                        │ plans · events      │
         ▲                                  └──────────┬──────────┘
         │ manifest                                     │
┌──────────────────┐   SQL                  ┌──────────▼──────────┐
│ config-compiler  │                        │ algo-bot (Python)   │
│ → resolved JSON  │                        │ Telegram · scanner  │
└──────────────────┘                        │ ZoneWatch → publish │
┌──────────────────┐                        │ manual /algo + HFS  │
│ PostgreSQL       │ ◀───────────────────── └──────────┬──────────┘
│ signals · stats  │                                    │ long-poll
└──────────────────┘                                    ▼
                                                 Telegram VIP/public
```

Compose boot order: **postgres + redis → config-compiler → ctrader-engine → bot**.

---

## Overview

Three execution lanes share one host and one Redis/Postgres spine, but keep
separate journals and publish paths:

| Lane | Who decides | Path | Book |
|------|-------------|------|------|
| **Manual /algo** | Owner DM | Parse → VIP/public → broker (optional) | `algo_manual` (+ OHLC chart snapshots for later XAU fitting) |
| **Autonomous reaction** | Scanner + ZoneWatch | Detectors → watch → activate → TradePlan V8 | `algo_auto` |
| **HFS scalping** | M1 lane | Closed M1 + immutable M5 context | Separate HFS publishers (not technique ZoneWatch) |

**Config authority:** non-secret tuning in [`config/trading-bot.yml`](config/trading-bot.yml);
secrets/bootstrap in `.env`. `config-compiler` emits a
`ResolvedRuntimeManifest` that both Python and the .NET engine consume.
Details: [docs/configuration/](docs/configuration/configuration-architecture.md)
and [docs/runtime/multi-symbol-routing.md](docs/runtime/multi-symbol-routing.md).

---

## What it does

### Manual signals

Owner commands (`/trade`, `/algo`, `/trade_modify`, lifecycle close/cancel)
post VIP/public cards, track fills and pips in Postgres, and can arm broker
execution. Chart windows around issued/filled/closed trades are stored for
offline XAU formula work later. Command surface:
[docs/bot-commands.md](docs/bot-commands.md).

### Autonomous analysis → TradePlan V8

1. **Scanner** runs detectors on closed H1/M15/M5 bars from Redis.
2. **Techniques** (Supply/Demand, OB, FVG, iFVG, CRT) and structural reactions
   become candidates; overlapping techniques form a **Confluence Zone**.
3. **ZoneWatch** retains the zone (no premature setup card) until location +
   activation (killzone, M1 trigger, chase rules) pass.
4. **Direct publish** writes a **TradePlan V8** to Redis; the ready-stream is
   fallback only.
5. **ctrader-engine** claims the plan, sizes from the equity table, and manages
   the group (shared stop, TP ladder, BE/trail).

See [docs/technique-zonewatch-publish.md](docs/technique-zonewatch-publish.md)
and [docs/adr-trade-plan-v8-cutover.md](docs/adr-trade-plan-v8-cutover.md).

### High-frequency M1 scalping

Shadow/paper/live HFS on closed M1 with immutable M5 context. Own publishers
and funnel counters — not the technique ZoneWatch path.
[docs/scalping/README.md](docs/scalping/README.md).

---

## Tech stack

| Layer | Choice |
|---|---|
| Algo / Telegram | Python 3.12, aiogram 3, asyncpg, Redis |
| Broker feed + execution | .NET 8, cTrader Open API |
| Config | `trading-bot.yml` + `.env` → `ResolvedRuntimeManifest` |
| Persistence | PostgreSQL (`signals`), Redis (bars, watches, plans, events) |
| Packaging | Docker Compose v2 (`postgres`, `redis`, `config-compiler`, `ctrader-engine`, `bot`) |

---

## Documentation

Index: [docs/README.md](docs/README.md).

| Doc | Contents |
|---|---|
| [Architecture](docs/architecture.md) | Services, loops, publish path |
| [Multi-symbol routing](docs/runtime/multi-symbol-routing.md) | Live instruments, policies, packs |
| [Technique / ZoneWatch](docs/technique-zonewatch-publish.md) | Techniques, confluence, activation |
| [Bot commands](docs/bot-commands.md) | Manual posting and lifecycle |
| [Scalping](docs/scalping/README.md) | HFS M1 lane |
| [Configuration](docs/configuration/configuration-architecture.md) | Catalog, YAML, manifest |
| [Deployment](docs/deployment.md) · [Operations](docs/operations.md) | Host → stack; logs, backups |
| [Redis contract](docs/redis-contract.md) | Bars / plans / events boundary |
| [TradePlan V8 ADR](docs/adr-trade-plan-v8-cutover.md) | Plan identity cutover |
| [Security](docs/security.md) · [Changelog](CHANGELOG.md) | Threat model; release notes |

---

## Quick start

```bash
git clone <this-repo> apexvoid-trading-bot
cd apexvoid-trading-bot
cp .env.example .env
# Secrets: TELEGRAM_*, POSTGRES_*, DATABASE_URL, REDIS_URL, CTRADER_*
# Non-secret tuning: config/trading-bot.yml

docker compose up -d --build
docker compose ps
docker compose logs -f bot
docker compose logs -f ctrader-engine
```

Expected shape:

- `config-compiler` exits 0 after writing `/runtime/resolved-runtime.json`
- `ctrader-engine` heartbeats and writes `bars:{SYMBOL}:*`
- `bot` starts Telegram polling plus scanner / ZoneWatch / HFS loops

Owner DM `active` should reply (empty book is fine). Demo auto-trade runbook:
[docs/demo-eval-autotrade.md](docs/demo-eval-autotrade.md).

---

## Repository layout

```text
apexvoid-trading-bot/
├── docker-compose.yml            # postgres, redis, config-compiler, engine, bot
├── config/trading-bot.yml        # non-secret Python / shared instrument tuning
├── .env.example                  # secrets + bootstrap
├── deployment-template/          # host deploy scaffolding
├── docs/                         # architecture, runtime, config, ops, ADRs
│   ├── runtime/                  # multi-symbol routing
│   ├── configuration/            # catalog + manifest authority
│   └── scalping/                 # HFS lane
├── contracts/                    # shared JSON schemas
│   ├── autotrade/                # TradePlan V8, …
│   └── configuration/            # catalog / env / manifest contracts
├── ctrader-engine/                # .NET feed + TradePlan executor
│   ├── src/
│   └── tests/
└── algo-bot/                     # Python application
    ├── Dockerfile
    ├── requirements.txt
    ├── tests/
    ├── scripts/
    └── app/
        ├── main.py               # composition root (cutover + loops)
        ├── configuration/        # catalog, YAML loader, runtime manifest
        ├── core/                 # runtime_config, symbols, logging
        ├── persistence/          # Postgres store + Redis client
        ├── bot/                  # aiogram wiring + handlers
        ├── signals/              # manual /algo, charts, calendar, recap
        ├── analysis/             # detectors, techniques, scanner, map
        ├── autotrade/            # ZoneWatch cutover, TradePlan V8, delivery
        ├── scalping/             # HFS M1 lane
        └── runtime/              # multi-symbol routing helpers
```

---

## License

Private project. Not licensed for redistribution.
