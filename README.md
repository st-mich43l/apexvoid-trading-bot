# Signal Bot

A self-hosted Telegram bot for posting and tracking XAUUSD (Gold) trading
signals by hand, with optional AI chart analysis. It runs as a single
long-polling process — no inbound webhook server, no public endpoint, no TLS.

```
┌────────────┐   DM    ┌──────────────────────────┐        ┌────────────┐
│  You (DM)  │ ──────▶ │  Telegram bot (aiogram)  │ ─────▶ │  Telegram  │
│  + charts  │         │  ├─ manual signal parse  │        │  channel   │
└────────────┘         │  ├─ lifecycle tracking   │        └────────────┘
                       │  ├─ pips calculator      │
                       │  └─ Claude chart vision  │
                       └────────────┬─────────────┘
                                    ▼
                          ┌──────────────────────┐
                          │ SQLite               │
                          │ ├─ manual_signals    │
                          │ └─ pips_log          │
                          └──────────────────────┘
```

The bot talks to Telegram over **outbound long-polling only**, so it needs no
open ports, no domain, and no reverse proxy.

## Features

### Manual signal posting (DM interface)
- **Post a signal** — DM `gold sell entry zone (4100-4105) / sl 4110 / tp 95/90/80`
  and the bot posts a clean, formatted signal to the channel instantly.
- **TP shorthand** — write the last 2 digits only (`35` → `3835`); any number of TPs.
- **Signal lifecycle** — every manual signal is tracked. DM `active` to see open
  positions, `close 3 +80` to record a result, `cancel 3` to invalidate.
- **Cancel by reply** — reply `cancel` directly to a channel signal post; the bot
  cancels it and cleans up the reply.
- **Owner-only** — set `TELEGRAM_OWNER_ID` to lock all DM commands to you.

### Pips calculator
- DM `calculate gold pips today` / `this week` for a win/loss pips summary drawn
  from channel history (via a Pyrogram MTProto client).
- **Auto-edit pips** — post `+80 pips` or `-30 pips` in the channel and the bot
  instantly replaces it with a clean formatted result (works on photos too).

### AI chart analysis
- DM one or more chart screenshots and the bot runs a structured Smart Money
  Concepts analysis via **Claude vision**, then posts the setup to the channel.
- Multi-timeframe aware — send several charts and it uses the higher TFs for bias
  and the lowest for entry precision.

## Tech Stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.12 |
| Telegram bot | aiogram 3 (long-polling) |
| Channel history | Pyrogram (pyrofork) MTProto client |
| AI analysis | Anthropic Claude (vision) |
| Persistence | SQLite (aiosqlite) |
| Packaging | Docker + Compose v2 |

## Documentation

- [Bot Commands](docs/bot-commands.md) — manual posting, lifecycle
  (`active`/`close`/`cancel`), pips calculator, auto-edit, env setup.
- [Architecture](docs/architecture.md) — process model, message flow,
  database schema, design decisions.
- [Deployment Guide](docs/deployment.md) — from a fresh host to a running bot.
- [Operations](docs/operations.md) — monitoring, backups, log rotation,
  updates, troubleshooting.
- [Security](docs/security.md) — threat model, secret management, hardening.

## Quick Start

```bash
git clone <this-repo> xau-signal-bot
cd xau-signal-bot

cp .env.example .env
# Edit .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_OWNER_ID,
#            TELEGRAM_API_ID/HASH (pips), ANTHROPIC_API_KEY (chart analysis)

# First run generates the Pyrogram session for channel history (pips):
python gen_session.py     # follow the prompts, writes the session string

docker compose up -d --build
docker compose logs -f bot
```

Expected startup lines:

```
bot: DB ready at /data/signals.db
bot: Starting Telegram polling
```

Then DM your bot: `active` should reply `📋 No open signals.`

## Repository Layout

```
xau-signal-bot/
├── docker-compose.yml        # single 'bot' service, no exposed ports
├── .env.example
├── gen_session.py            # one-time Pyrogram session generator (pips)
├── README.md                 # this file
├── docs/                     # detailed documentation
└── webhook/                  # the bot application (dir name kept for history)
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── main.py           # entrypoint: init DB, start long-polling
        ├── config.py         # pydantic-settings (all env vars)
        ├── telegram.py       # aiogram bot: DM commands, channel handlers, formatting
        ├── chart_analysis.py # Claude vision chart analysis
        ├── history.py        # Pyrogram MTProto client for channel history
        └── dedup.py          # SQLite: manual_signals + pips_log
```

## License

Private project. Not licensed for redistribution.
