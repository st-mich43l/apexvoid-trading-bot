# Architecture

## Component Overview

The system is a single-process Telegram bot that helps a human post and track
XAUUSD signals by hand, with optional AI chart analysis. It does not place
orders and it does not receive webhooks. Everything is single-node.

```
┌──────────────────────────────────────────────────────────────┐
│                    Single host (Docker)                      │
│                                                              │
│   ┌────────────────────────────────────────────────────┐    │
│   │  bot  (python -m app.main)                         │    │
│   │   ├─ aiogram Dispatcher (long-polling)             │    │
│   │   ├─ manual signal parse + formatting              │    │
│   │   ├─ signal lifecycle (SQLite)                     │    │
│   │   ├─ pips calculator (local SQLite log)            │    │
│   │   └─ chart analysis (Claude vision)                │    │
│   └───────────────┬────────────────────────────────────┘    │
│                   │  SQLite (/data/signals.db)               │
└───────────────────┼──────────────────────────────────────────┘
                    │ outbound HTTPS
        ┌───────────┴───────────────┐
        ▼                           ▼
  api.telegram.org           api.anthropic.com
  (bot polling)              (chart vision)
```

All external connections are **outbound**. No inbound ports are opened; the
container publishes nothing.

## Process Model

`app/main.py` is the entrypoint:

1. `init_db()` creates the SQLite tables if absent.
2. `dp.start_polling(bot, ...)` begins the aiogram long-poll loop, which owns
   its own SIGINT/SIGTERM shutdown and closes the bot session on exit.

There is no ASGI server, no lifespan, and no background webhook. The single
asyncio loop handles all Telegram updates.

## Message Flows

### Manual signal (DM → channel)

1. Owner DMs `gold sell entry zone (4100-4105) / sl 4110 / tp 95/90/80`.
2. `_parse_manual` extracts action, entry zone, SL, and TP list (expanding
   2-digit shorthand against the entry base).
3. The formatted HTML signal is posted to the channel via `_send_with_retry`.
4. A row is inserted into `manual_signals` with the channel `message_id` so
   later `close`/`cancel` commands can reply to the exact post.

### Lifecycle commands (DM)

- `active` → lists open rows from `manual_signals`.
- `close <id> +80` → marks the row closed, records signed pips, replies in the
  channel.
- `cancel <id>` (DM) or `cancel` (reply to the channel post) → marks cancelled.

### Pips (channel auto-edit + calculator)

- Posting `+80 pips` / `-30 pips` in the channel triggers an edit into a clean
  result string and appends a row to `pips_log`.
- `calculate gold pips today|this week|...` aggregates `pips_log` over the
  period and replies with wins/losses/net.

### Chart analysis (DM photo → channel)

1. Owner DMs one or more chart screenshots. Photos within a 2-second window are
   batched per user.
2. Images are downloaded and sent to Claude vision with a structured SMC prompt.
3. The parsed setup is rendered as Telegram HTML and posted to the channel.

## Persistence

Two SQLite tables in `/data/signals.db`.

### `manual_signals` — DM signal lifecycle

```sql
CREATE TABLE manual_signals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 INTEGER NOT NULL,
    action             TEXT NOT NULL,      -- 'BUY' or 'SELL'
    entry              REAL NOT NULL,      -- lower edge of entry zone
    entry_end          REAL,               -- upper edge of entry zone
    sl                 REAL NOT NULL,
    tps                TEXT NOT NULL,      -- JSON array: [3835.0, 3830.0]
    order_type         TEXT NOT NULL,      -- legacy; new signals use 'zone'
    channel_message_id INTEGER,            -- Telegram msg id in the channel
    status             TEXT NOT NULL DEFAULT 'open',  -- open/closed/cancelled
    result_pips        INTEGER,            -- signed; NULL until closed
    closed_at          INTEGER             -- Unix ts; NULL while open
);
```

Status transitions: `open` → `closed` (via `close <id>`) or `cancelled`
(via `cancel <id>` DM or `cancel` channel reply).

### `pips_log` — auto-edit history

```sql
CREATE TABLE pips_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,    -- Unix timestamp of the edit
    sign       TEXT NOT NULL,       -- '+' profit / '-' loss
    pips       INTEGER NOT NULL,    -- absolute pip count
    message_id INTEGER,             -- Telegram message_id edited
    chat_id    TEXT                 -- channel chat_id
);
```

## Design Decisions

- **Long-polling, not webhooks.** The bot only initiates outbound connections,
  so it needs no public IP, no domain, no TLS, and no reverse proxy. The entire
  inbound attack surface is gone.
- **Signal-only, not auto-execution.** The bot forwards data to a human; it
  never places orders. Manual confirmation is a deliberate speed bump.
- **Owner lock.** All DM handlers check `TELEGRAM_OWNER_ID`. With it unset the
  privileged DM interface is disabled.
- **SQLite, not Postgres.** Write volume is a handful of rows per day; a
  bind-mounted SQLite file is simpler and fully adequate.
- **Local accounting.** The pips calculator aggregates the SQLite `pips_log`
  populated when lifecycle results are booked.
- **Single node, no HA.** For a personal tool a second node is not worth the
  complexity; `restart: unless-stopped` covers common downtime.
