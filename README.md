# 📉 ApexVoid Trading Bot 🤖

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-2CA5E0?style=flat-square&logo=telegram&logoColor=white)
![Claude](https://img.shields.io/badge/Claude_Vision-D97757?style=flat-square&logo=anthropic&logoColor=white)

A self-hosted Telegram bot for posting and tracking XAUUSD (Gold) trading signals by hand, with optional **AI chart analysis**. It runs as a single long-polling process — no inbound webhook server, no public endpoint, no TLS required. 🚀

```text
┌────────────┐   DM    ┌──────────────────────────┐        ┌────────────┐
│  You (DM)  │ ──────▶ │  Telegram bot (aiogram)  │ ─────▶ │  Telegram  │
│  + charts  │         │  ├─ 📝 manual parse      │        │  channel   │
└────────────┘         │  ├─ 🔄 lifecycle track   │        └────────────┘
                       │  ├─ 🧮 pips calculator    │
                       │  └─ 👁️ Claude vision      │
                       └────────────┬─────────────┘
                                    ▼
                          ┌──────────────────────┐
                          │ 💾 SQLite            │
                          │ ├─ manual_signals    │
                          │ └─ pips_log          │
                          └──────────────────────┘
```

The bot talks to Telegram over **outbound long-polling only**, meaning it requires zero open ports, no domain names, and no reverse proxy to operate securely. 🔒

---

## ✨ Features

### 📡 Manual Signal Posting (DM interface)
- **Insta-Post Signals** ⚡ — DM `gold sell entry zone (4100-4105) / sl 4110 / tp 95/90/80` and the bot will instantly post a beautifully formatted signal to the channel.
- **TP Shorthand** 🎯 — Just write the last 2 digits (`35` → `3835`). You can list any number of TPs!
- **Scalp Metadata** ⚡ — Add `/ scalp` or `/ scalp nhanh` to mark a fast scalp internally for review/stats without exposing a channel tag.
- **Lifecycle Tracking** 🔄 — Every signal is tracked. DM `/trade_active` to see open positions, `/trade_close XAU #3 +80` to log a result, `/trade_uncclose XAU #3` to restore a mistaken close, or `/trade_cancel XAU #3` to drop it.
- **Cancel by Reply** 🗑️ — Simply reply `cancel` directly to a channel signal post; the bot removes the original and cleans up your reply.
- **Owner-Exclusive** 👑 — Set `TELEGRAM_OWNER_ID` to strictly lock all DM commands to you.

### 📣 VIP + Public Broadcast
- `SIGNAL_VIP_CHANNEL_ID` is the private control channel; bare lifecycle
  commands are accepted only there.
- `SIGNAL_PUBLIC_CHANNEL_ID` receives broadcast-only signal posts and updates
  without internal `#id` values.
- `SIGNAL_PUBLIC_SHOW_PIPS=true` shows per-signal results publicly; set it to
  `false` for event-only wording. Aggregate stats always stay in owner DM.
- Signals publish to both channels by default. Add `/ vip` to an entry to keep
  that signal and all later updates VIP-only.
- The bot must be an administrator with post/edit permissions in both channels.
- A restart-safe Sunday performance recap is delivered to VIP only. Configure
  it with `WEEKLY_REPORT_ENABLED`, `WEEKLY_REPORT_DOW`, and
  `WEEKLY_REPORT_HOUR`; public never receives aggregate performance.

### 🗓 Economic Calendar
- The daily ForexFactory brief includes only configured high-impact USD,
  gold, and oil events. The bot stays silent when none are scheduled.
- Calendar briefs are broadcast to both VIP and public channels.
- Upcoming events can tag or block a new signal through the local event guard.

### 🧮 Pips Calculator
- DM `calculate gold pips today` or `this week` for a win/loss pips summary drawn from the local `pips_log` (populated automatically whenever you close a result).
- **Auto-edit Pips** ✏️ — Post `+80 pips` or `-30 pips` in the channel and the bot replaces it with a clean formatted result (even works on photos!).

### 🧠 AI Chart Analysis
- DM one or more chart screenshots and the bot will run a structured **Smart Money Concepts (SMC)** analysis via **Claude Vision**, automatically drafting the setup to the channel.
- **Multi-Timeframe Aware** ⏱️ — Send several charts; it uses higher TFs for directional bias and lower TFs for entry precision.

### 🤖 Demo Auto-Scalper
- The cTrader executor accepts the existing M5 Range Edge Scalp gate and an
  independently enabled M1 momentum gate; the M1 lane requires a strong closed
  candle, a short-range breakout, and no explicit opposing M5 bias.
- Both lanes still fail closed on stale quotes, excessive spread, entry drift,
  guarded news, an existing XAU position, and the UTC daily trade cap.
- `/auto_status` exposes the latest M1 gate state for operator diagnostics.

---

## 🛠️ Tech Stack

| Layer | Choice |
|---|---|
| **Runtime** | Python 3.12 🐍 |
| **Telegram Bot** | aiogram 3 (long-polling) 🤖 |
| **AI Analysis** | Anthropic Claude (Vision) 👁️ |
| **Persistence** | SQLite (aiosqlite) 🗄️ |
| **Packaging** | Docker + Compose v2 🐳 |

---

## 📚 Documentation

Dive into the docs for full details on configuring and operating the bot:

- 📖 [Bot Commands](docs/bot-commands.md) — Manual posting, lifecycle (`active`/`close`/`cancel`), pips calculator, auto-edit, env setup.
- 🏗️ [Architecture](docs/architecture.md) — Process model, message flow, database schema, design decisions.
- 🚀 [Deployment Guide](docs/deployment.md) — From a fresh host to a running bot.
- ⚙️ [Operations](docs/operations.md) — Monitoring, backups, log rotation, updates, troubleshooting.
- 🔒 [Security](docs/security.md) — Threat model, secret management, hardening.
- 📊 [Redis Bar Contract](docs/redis-contract.md) — Closed OHLC window keys shared by the cTrader feed and scanners.
- 📝 [Changelog](CHANGELOG.md) — Notable behavior, configuration, and deployment changes.

---

## 🚀 Quick Start

```bash
git clone <this-repo> apexvoid-trading-bot
cd apexvoid-trading-bot

# Setup Environment
cp .env.example .env
# Edit .env: TELEGRAM_BOT_TOKEN, SIGNAL_VIP_CHANNEL_ID,
#            SIGNAL_PUBLIC_CHANNEL_ID, SIGNAL_PUBLIC_SHOW_PIPS,
#            TELEGRAM_OWNER_ID,
#            ANTHROPIC_API_KEY (chart analysis)

# Deploy
docker compose up -d --build
docker compose logs -f bot
```

**Expected startup lines:**
```log
bot: DB ready at /data/signals.db
bot: Starting Telegram polling
```

Then, just DM your bot: `active` should reply with `📋 No open signals.` 🎉

---

## 📁 Repository Layout

```tree
apexvoid-trading-bot/
├── docker-compose.yml        🐳 bot + postgres + redis + cTrader feed
├── .env.example              🔑 env template
├── README.md                 📖 this file
├── docs/                     📚 detailed documentation
├── ctrader-feed/             📊 .NET cTrader Open API → Redis bar producer
└── webhook/                  🤖 the bot application (dir name kept for history)
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── main.py           🏁 entrypoint: init DB, start long-polling
        ├── config.py         ⚙️ pydantic-settings (all env vars)
        ├── telegram.py       💬 aiogram bot: DM commands, channel handlers, formatting
        ├── chart_analysis.py 👁️ Claude vision chart analysis
        └── dedup.py          🗄️ SQLite: manual_signals + pips_log
```

---

## 📄 License

Private project. Not licensed for redistribution. ⛔
