"""
dedup.py — SQLite persistence layer for the XAU Signal bot.

Database file location is controlled by ``settings.db_path`` (default ``/data/signals.db``).
All functions open and close their own connection so they are safe to call concurrently
from multiple asyncio tasks.

Tables
------
pips_log
  Append-only record of every pips result the bot auto-edits in the channel
  (e.g. "+80 pips" or "-30 pips").  Queried by the ``calculate gold pips``
  DM command to produce win/loss summaries.

manual_signals
  Lifecycle tracker for every signal posted manually via the bot's DM interface.
  Stores the original Telegram channel message_id so the bot can later reply to
  (or reference) the exact post when the signal is closed or cancelled.

  Status flow:
    open  ──► closed     (via DM "close <id> +pips" or reply "+pips" to channel post)
        ──► cancelled  (via DM "cancel <id>" or reply "cancel" to channel post)
"""

import json
import time
import aiosqlite
import logging
from pathlib import Path
from app.config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

async def init_db() -> None:
  """Create all tables and indexes if they do not already exist.

  Safe to call on every application startup — uses ``CREATE TABLE IF NOT EXISTS``
  so it is a no-op when the schema is already in place.  Call this once on
  startup before any other DB function.
  """
  Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
  async with aiosqlite.connect(settings.db_path) as db:
    # ------------------------------------------------------------------
    # Table: pips_log
    # Appended to whenever the bot auto-edits a channel message that
    # contains a pips result (e.g. "+80 pips").  Used exclusively by
    # get_pips_summary() to answer "calculate gold pips today/this week".
    # ------------------------------------------------------------------
    await db.execute(
      """
      CREATE TABLE IF NOT EXISTS pips_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         INTEGER NOT NULL,      -- unix timestamp of the edit
        sign       TEXT NOT NULL,         -- '+' for profit, '-' for loss
        pips       INTEGER NOT NULL,      -- absolute pip count (always positive)
        message_id INTEGER,               -- Telegram message_id that was edited
        chat_id    TEXT                   -- Telegram chat_id of the channel
      )
      """
    )
    await db.execute(
      "CREATE INDEX IF NOT EXISTS idx_pips_ts ON pips_log(ts)"
    )

    # ------------------------------------------------------------------
    # Table: manual_signals
    # Tracks every signal posted to the channel via the bot's DM
    # interface ("gold sell 4100-4105 / sl 4110 / tp 95/90/80").
    #
    # Columns:
    #   id                 — auto-increment primary key; shown to the user
    #                        as "#<id>" so they can reference it in commands.
    #   ts                 — unix timestamp when the signal was posted.
    #   action             — 'BUY' or 'SELL'.
    #   entry              — lower edge of the entry zone.
    #   entry_end          — upper edge of the entry zone.
    #   sl                 — stop-loss price.
    #   tps                — JSON array of take-profit prices, e.g. [3835, 3830, 3820].
    #   order_type         — legacy compatibility column; new records use 'zone'.
    #   channel_message_id — Telegram message_id of the post in the channel.
    #                        NULL if the message was posted before tracking was added.
    #                        Used to reply to the exact post when closing/cancelling.
    #   status             — lifecycle state: 'open' | 'closed' | 'cancelled'.
    #   result_pips        — signed pip result recorded on close (positive = profit).
    #                        NULL until the signal is closed.
    #   closed_at          — unix timestamp of close/cancel event. NULL while open.
    # ------------------------------------------------------------------
    await db.execute(
      """
      CREATE TABLE IF NOT EXISTS manual_signals (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        ts                 INTEGER NOT NULL,
        action             TEXT NOT NULL,
        entry              REAL NOT NULL,
        entry_end          REAL,
        sl                 REAL NOT NULL,
        tps                TEXT NOT NULL,
        order_type         TEXT NOT NULL,
        channel_message_id INTEGER,
        status             TEXT NOT NULL DEFAULT 'open',
        result_pips        INTEGER,
        closed_at          INTEGER
      )
      """
    )
    columns = await db.execute_fetchall("PRAGMA table_info(manual_signals)")
    if "entry_end" not in {column[1] for column in columns}:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN entry_end REAL")
    await db.execute(
      # Index on status so open-signal queries stay fast as history grows.
      "CREATE INDEX IF NOT EXISTS idx_manual_signals_status ON manual_signals(status)"
    )
    await db.commit()


# ---------------------------------------------------------------------------
# pips_log helpers
# ---------------------------------------------------------------------------

async def store_pips(sign: str, pips: int, message_id: int = None, chat_id: str = None) -> None:
  """Append one pips result to the log.

  Args:
    sign:       ``'+'`` for a winning trade, ``'-'`` for a loss.
    pips:       Absolute pip count (always positive; sign carries direction).
    message_id: Telegram message_id of the edited channel post (optional).
    chat_id:    Telegram chat_id of the channel (optional).
  """
  async with aiosqlite.connect(settings.db_path) as db:
    await db.execute(
      "INSERT INTO pips_log (ts, sign, pips, message_id, chat_id) VALUES (?, ?, ?, ?, ?)",
      (int(time.time()), sign, pips, message_id, str(chat_id) if chat_id else None),
    )
    await db.commit()


async def get_pips_summary(start_ts: int, end_ts: int) -> dict:
  """Aggregate pips results within a Unix timestamp range.

  Args:
    start_ts: Inclusive start of the period (Unix timestamp).
    end_ts:   Inclusive end of the period (Unix timestamp).

  Returns:
    A dict with keys:
      wins       — number of winning trades in the period.
      win_pips   — total pips gained from winners.
      losses     — number of losing trades in the period.
      loss_pips  — total pips lost from losers (positive integer).
      net        — net pips (win_pips - loss_pips); negative means net loss.
      total      — total number of trades recorded (wins + losses).
  """
  async with aiosqlite.connect(settings.db_path) as db:
    cursor = await db.execute(
      "SELECT sign, pips FROM pips_log WHERE ts >= ? AND ts <= ?",
      (start_ts, end_ts),
    )
    rows = await cursor.fetchall()
  wins   = [(s, p) for s, p in rows if s == '+']
  losses = [(s, p) for s, p in rows if s == '-']
  net    = sum(p if s == '+' else -p for s, p in rows)
  return {
    'wins': len(wins), 'win_pips': sum(p for _, p in wins),
    'losses': len(losses), 'loss_pips': sum(p for _, p in losses),
    'net': net, 'total': len(rows),
  }


# ---------------------------------------------------------------------------
# Manual signal lifecycle
# ---------------------------------------------------------------------------

async def store_manual_signal(
  ts: int,
  action: str,
  entry: float,
  entry_end: float,
  sl: float,
  tps: list[float],
  channel_message_id: int | None = None,
) -> int:
  """Insert a new manual signal record and return its auto-generated id.

  Called immediately after the bot successfully posts the signal to the channel
  so the returned id can be shown to the user ("✅ Sent to channel (signal #4)").

  Args:
    ts:                 Unix timestamp of the post.
    action:             ``'BUY'`` or ``'SELL'``.
    entry:              Lower edge of the entry zone.
    entry_end:          Upper edge of the entry zone.
    sl:                 Stop-loss price.
    tps:                List of take-profit prices in order (e.g. [3835.0, 3830.0]).
    channel_message_id: Telegram message_id of the posted channel message.
              If provided, close/cancel commands can reply to the exact post.

  Returns:
    The new row's auto-incremented primary key (``id``).
  """
  async with aiosqlite.connect(settings.db_path) as db:
    cur = await db.execute(
      """
      INSERT INTO manual_signals
        (ts, action, entry, entry_end, sl, tps, order_type, channel_message_id)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      """,
      (ts, action, entry, entry_end, sl, json.dumps(tps), "zone", channel_message_id),
    )
    await db.commit()
    return cur.lastrowid


async def set_manual_signal_channel_id(row_id: int, channel_message_id: int) -> None:
  """Back-fill the channel_message_id for an already-inserted manual signal.

  Useful if the message_id was not available at insert time (e.g. a retry
  scenario where the signal was stored before the send completed).

  Args:
    row_id:             Primary key of the ``manual_signals`` row.
    channel_message_id: Telegram message_id to store.
  """
  async with aiosqlite.connect(settings.db_path) as db:
    await db.execute(
      "UPDATE manual_signals SET channel_message_id = ? WHERE id = ?",
      (channel_message_id, row_id),
    )
    await db.commit()


async def get_open_signals() -> list[dict]:
  """Return all signals currently in ``status = 'open'``, oldest first.

  Used by the ``active`` DM command to show the owner a snapshot of live trades.

  Returns:
    List of dicts with keys: id, ts, action, entry, entry_end, sl, and tps.
    ``tps`` is deserialized from JSON so callers receive a plain Python list.
  """
  async with aiosqlite.connect(settings.db_path) as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT id, ts, action, entry, entry_end, sl, tps "
      "FROM manual_signals WHERE status = 'open' ORDER BY ts ASC"
    )
    rows = await cur.fetchall()
  return [
    {
      "id": r["id"],
      "ts": r["ts"],
      "action": r["action"],
      "entry": r["entry"],
      "entry_end": r["entry_end"],
      "sl": r["sl"],
      "tps": json.loads(r["tps"]),
    }
    for r in rows
  ]


async def get_manual_signal_by_channel_id(channel_message_id: int) -> dict | None:
  """Look up an open manual signal by its Telegram channel message_id.

  Used when the bot receives a channel reply (e.g. "cancel") and needs to
  find which tracked signal the reply is directed at.

  Args:
    channel_message_id: Telegram message_id of the original signal post.

  Returns:
    Full row as a dict, or ``None`` if no open signal matches.
  """
  async with aiosqlite.connect(settings.db_path) as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE channel_message_id = ? AND status = 'open'",
      (channel_message_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def close_manual_signal(row_id: int, result_pips: int) -> dict | None:
  """Mark a signal as closed and record the pip result.

  Transitions ``status`` from ``'open'`` → ``'closed'`` and stores
  ``result_pips`` and ``closed_at``.  The caller should then post a result
  reply in the channel using the returned ``channel_message_id``.

  Args:
    row_id:      Primary key of the signal to close.
    result_pips: Signed pip result — positive for profit, negative for loss.

  Returns:
    The row dict as it was *before* the update (so ``channel_message_id``
    is available for the channel reply), or ``None`` if the signal was not
    found or was already closed/cancelled.
  """
  async with aiosqlite.connect(settings.db_path) as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE id = ? AND status = 'open'", (row_id,)
    )
    row = await cur.fetchone()
    if row is None:
      return None
    await db.execute(
      "UPDATE manual_signals SET status = 'closed', result_pips = ?, closed_at = ? WHERE id = ?",
      (result_pips, int(time.time()), row_id),
    )
    await db.commit()
    return dict(row)


async def cancel_manual_signal_by_channel_id(channel_message_id: int) -> dict | None:
  """Cancel an open signal identified by the Telegram channel message_id.

  Triggered when the channel owner replies ``cancel`` directly to the signal
  post in the channel.  Transitions ``status`` from ``'open'`` → ``'cancelled'``.

  Args:
    channel_message_id: Telegram message_id of the original signal post.

  Returns:
    The row dict as it was before the update, or ``None`` if no open signal
    was found with that channel_message_id.
  """
  async with aiosqlite.connect(settings.db_path) as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE channel_message_id = ? AND status = 'open'",
      (channel_message_id,),
    )
    row = await cur.fetchone()
    if row is None:
      return None
    row = dict(row)
    await db.execute(
      "UPDATE manual_signals SET status = 'cancelled', closed_at = ? WHERE id = ?",
      (int(time.time()), row["id"]),
    )
    await db.commit()
    return row


async def cancel_manual_signal(row_id: int) -> dict | None:
  """Cancel an open signal by its primary key.

  Triggered by the ``cancel <id>`` DM command.  Transitions ``status`` from
  ``'open'`` → ``'cancelled'``.

  Args:
    row_id: Primary key of the signal to cancel.

  Returns:
    The row dict as it was before the update, or ``None`` if the signal was
    not found or was already closed/cancelled.
  """
  async with aiosqlite.connect(settings.db_path) as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE id = ? AND status = 'open'", (row_id,)
    )
    row = await cur.fetchone()
    if row is None:
      return None
    await db.execute(
      "UPDATE manual_signals SET status = 'cancelled', closed_at = ? WHERE id = ?",
      (int(time.time()), row_id),
    )
    await db.commit()
    return dict(row)
