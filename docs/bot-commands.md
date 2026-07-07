# Bot Commands & Channel Features

`@void_xau_scalper_bot` is an interactive Telegram bot: it accepts DM commands
from the owner to post manual signals, manage their lifecycle, query pips stats,
and analyse chart screenshots.

All DM commands are owner-only.  The channel auto-features (pips editing, cancel-by-reply) run passively without any command.

---

## Owner Security

Set `TELEGRAM_OWNER_ID` in `.env` to your numeric Telegram user ID.  When set, every private message that is **not** from you is silently dropped — the bot does not reply.

```
TELEGRAM_OWNER_ID=123456789
```

Find your ID: message `@userinfobot` or `@getidsbot` on Telegram.

If `TELEGRAM_OWNER_ID` is not set, any user who knows the bot's username can trigger commands.  **Always set this in production.**

After changing `.env` run:

```bash
docker compose up -d
```

---

## Manual Signal Posting

Send the following template as a **DM to the bot**.  The bot parses it and posts a formatted signal to the channel.

### Template

```
gold sell entry zone (4100-4105)
sl 4110
tp 95/90/80
```

The shorter first line is also accepted:

```
gold sell 4100-4105
```

### Fields

| Field | Values | Description |
|---|---|---|
| `gold` | literal | Always `gold` (XAUUSD) |
| direction | `buy` / `sell` | Trade direction |
| entry zone | e.g. `4100-4105` | Lower and upper entry prices |
| `sl` | e.g. `4110` | Stop-loss — full price |
| `tp` | e.g. `95/90/80` | Take-profit levels — see shorthand below |

### TP Shorthand

If a TP value is **less than 100** the bot expands it to a full price automatically using the entry's hundred-base.

**Example:** SELL zone `4100-4105`, `tp 95/90/80`
- Base = `int(4100 / 100) * 100` = `4100`
- Raw TP1 = `4100 + 95` = `4195`; wrong side, so shift to `4095`
- Raw TP2 becomes `4090`
- Raw TP3 becomes `4080`

If the computed price ends up on the **wrong side of entry**, the bot shifts it by ±100 automatically.

You can also pass full prices directly: `tp 3835/3830/3820`.  Any number of TP levels is accepted.

### Entry Options

Append these suffixes to a manual signal:

```text
gold sell 4100-4105 / sl 4110 / tp 95/90/80 / scalp
gold sell 4100-4105 / sl 4110 / tp 95/90/80 / scalp nhanh
gold sell 4100-4105 / sl 4110 / tp 95/90/80 / vip
gold sell 4100-4105 / sl 4110 / tp 95/90/80 / setup ob-retest ***
```

| Option | Effect |
|---|---|
| `/ scalp`, `/ scalp nhanh`, `/ quick scalp` | Marks the trade internally as `scalp` for review/stats. It does not add a channel tag. |
| `/ vip` | Publishes the signal and later lifecycle updates to VIP only. |
| `/ setup <name> [*|**|***]` | Sets the internal setup label and optional confidence grade. If used with `/ scalp`, explicit `/ setup` wins. |

### Channel Output

The bot posts a message like:

```
📉 SELL XAUUSD  🔔

⚡️ Entry Zone:  4,100 - 4,105
🛡 SL:     4,110  ·  risk 10
💰 TP1:   4,095  ·  0.5R
💰 TP2:   4,090  ·  1.0R
💰 TP3:   4,080  ·  2.0R
```

Risk and R values use the conservative edge of the entry zone.

### Bot Reply

After posting, the bot replies to your DM:

```
✅ Sent to channel (signal #4)
```

Keep the `#id` — you use it with `close` and `cancel` commands.

---

## Signal Lifecycle Commands

Every manual signal has a status: **open → closed** or **open → cancelled**.

### `active` — list open signals

```
active
```

Shows all signals currently in `open` status, oldest first.

**Example reply:**

```
📋 Open Signals (2)

#3  📉 SELL @ 4,100 - 4,105
  SL 4,110  · TP 4,095/4,090/4,080
  Opened 2h ago

#4  📈 BUY @ 4,100 - 4,105
  SL 4,095  · TP 4,110/4,120/4,130
  Opened 15m ago
```

---

### `close <id> <+/- pips>` — close a signal

```
close 3 +80
close 3 -30
```

Marks signal `#3` as closed, records the pip result, and posts a result reply to the original signal message in the channel.

**Channel result post (profit):**

```
✅ Closed: +80 pips 💰
```

**Channel result post (loss):**

```
🛑 Closed: -30 pips
```

Bot replies to your DM: `#3 marked closed (+80 pips).`

If the signal is not found or already closed: `⚠️ Signal #3 not found or already closed.`

---

### `/trade_uncclose` — undo a mistaken close

```text
/trade_uncclose XAU #3
```

Restores signal `#3` to `open/running` when it was closed in the bot by
mistake. If the signal had earlier partial bookings, the bot removes only the
latest close leg and keeps the earlier partials. Linked `pips_log` accounting
for the mistaken final close is removed so stats stop counting it.

Alias:

```text
/trade_restore XAU #3
```

Channel correction:

```text
♻️ #3 restored — trade still running
```

Public channels receive the same correction without the internal `#id`.

---

### `/trade_tp` — notify a TP manually

```text
/trade_tp XAU #3 1 +56
```

Posts `🎯 TP1 (+56 pips)` to the channels where signal `#3` was originally
published. This is notify-only: it does not close the signal, book pips, or
change performance accounting.

---

### `cancel <id>` — cancel via DM

```
cancel 3
```

Marks signal `#3` as cancelled and posts `❌ Signal cancelled.` as a reply to the original channel post.

Bot replies to your DM: `#3 cancelled.`

---

### Cancel by channel reply

Instead of DMing `cancel <id>`, you can **reply directly to the signal post in the channel** with just:

```
cancel
```

The bot:
1. Looks up which tracked signal that channel message belongs to.
2. Marks it as cancelled.
3. Deletes your `cancel` reply (so the channel stays clean).
4. Posts `❌ Signal cancelled.` as a reply to the original signal.

If you reply `cancel` to a non-tracked message (e.g. a pips post), the bot silently ignores it.

---

## Pips Calculator

Query the pip results recorded in the channel over a period.

### Command

```
calculate gold pips today
calculate gold pips yesterday
calculate gold pips this week
calculate gold pips last week
```

### How it works

The bot reads from the `pips_log` table, which is populated whenever the bot auto-edits a channel pips message (see [Auto-Edit Pips Messages](#auto-edit-pips-messages) below).

### Example reply

```
📊 Gold Pips — Today

✅ Wins:    3 trades  +210 pips
❌ Losses:  1 trade   -45 pips
──────────────
💰 Net:    +165 pips
```

## Auto-Edit Pips Messages

This feature runs **automatically** — no command needed.

When a message is posted in the channel containing a pips result like:

```
+80 pips
-30 pips
+1500Pips
```

The bot immediately edits that message to a clean formatted version:

| Original | Edited to |
|---|---|
| `+80 pips` | `✅ Booked +80 pips profit! 💸` |
| `-30 pips` | `🛑 Stopped out -30 pips. Managed & moving on 💪` |

Works for both plain text messages and **photo captions** (e.g. a profit screenshot with a pips caption).

The raw `+/-N pips` text also triggers auto-edit on edited messages if the channel post is later updated.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | Channel ID where signals are posted (e.g. `-1001234567890`) |
| `TELEGRAM_OWNER_ID` | Required for DMs | Your numeric Telegram user ID; privileged DMs are disabled when unset |
| `ANTHROPIC_API_KEY` | Optional | Enables chart screenshot analysis via Claude vision |
| `DB_PATH` | Optional | SQLite database path (default: `/data/signals.db`) |
| `LOG_LEVEL` | Optional | Python log level (default: `INFO`) |

---

## Database Tables

All state is persisted in SQLite at `DB_PATH`.

### `manual_signals` — signal lifecycle

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment id shown as `#<id>` in commands |
| `ts` | INTEGER | Unix timestamp of when the signal was posted |
| `action` | TEXT | `BUY` or `SELL` |
| `entry` | REAL | Lower edge of the entry zone |
| `entry_end` | REAL | Upper edge of the entry zone |
| `sl` | REAL | Stop-loss price |
| `tps` | TEXT | JSON array of TP prices e.g. `[3835.0, 3830.0, 3820.0]` |
| `order_type` | TEXT | Legacy compatibility column; new signals use `zone` |
| `channel_message_id` | INTEGER | Telegram message_id in the channel — used to reply on close/cancel |
| `status` | TEXT | `open` / `closed` / `cancelled` |
| `result_pips` | INTEGER | Signed pip result recorded on close (NULL while open) |
| `closed_at` | INTEGER | Unix timestamp of close/cancel event (NULL while open) |

### `pips_log` — pips history

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `ts` | INTEGER | Unix timestamp of the auto-edit |
| `sign` | TEXT | `+` (profit) or `-` (loss) |
| `pips` | INTEGER | Absolute pip count (always positive) |
| `message_id` | INTEGER | Telegram message_id that was edited |
| `chat_id` | TEXT | Channel chat_id |

---

## Quick Reference Card

| What you type | Where | What happens |
|---|---|---|
| `gold sell 4100-4105\nsl 4110\ntp 95/90/80` | DM | Posts signal to channel, returns `#id` |
| `active` | DM | Lists all open signals |
| `close 3 +80` | DM | Closes #3, posts +80 pips reply in channel |
| `close 3 -30` | DM | Closes #3, posts -30 pips reply in channel |
| `cancel 3` | DM | Cancels #3, posts cancelled reply in channel |
| `cancel` _(reply to signal post)_ | Channel | Cancels that signal, cleans up reply |
| `calculate gold pips today` | DM | Pips summary for today |
| `calculate gold pips this week` | DM | Pips summary for this week |
| `+80 pips` _(posted in channel)_ | Channel | Bot auto-edits to profit message |
| `-30 pips` _(posted in channel)_ | Channel | Bot auto-edits to loss message |
