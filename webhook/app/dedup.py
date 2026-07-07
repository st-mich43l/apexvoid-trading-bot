"""
dedup.py — SQLite persistence layer for the XAU Signal bot.

Database file location is controlled by ``settings.db_path`` (default ``/data/signals.db``).
All functions open and close their own connection so they are safe to call concurrently
from multiple asyncio tasks.

Tables
------
pips_log
  Append-only record of every pips result the bot auto-edits in the channel
  (e.g. "+80 pips" or "-30 pips"). Queried by ``/trade_pips``
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
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from app.config import settings

log = logging.getLogger(__name__)

_LEG_EPSILON = 1e-9


@asynccontextmanager
async def _connect():
  db = await aiosqlite.connect(settings.db_path)
  try:
    await db.execute("PRAGMA busy_timeout=5000")
    yield db
  finally:
    await db.close()


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
  async with _connect() as db:
    await db.execute("PRAGMA journal_mode=WAL")
    # ------------------------------------------------------------------
    # Table: pips_log
    # Appended to whenever the bot auto-edits a channel message that
    # contains a pips result (e.g. "+80 pips").  Used exclusively by
    # Used by /trade_pips for local period summaries.
    # ------------------------------------------------------------------
    await db.execute(
      """
      CREATE TABLE IF NOT EXISTS pips_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         INTEGER NOT NULL,      -- unix timestamp of the edit
        sign       TEXT NOT NULL,         -- '+' for profit, '-' for loss
        pips       INTEGER NOT NULL,      -- absolute pip count (always positive)
        message_id INTEGER,               -- Telegram message_id that was edited
        chat_id    TEXT,                  -- Telegram chat_id of the channel
        signal_id  INTEGER                -- linked manual_signals row, if any
      )
      """
    )
    pips_columns = await db.execute_fetchall("PRAGMA table_info(pips_log)")
    if "signal_id" not in {column[1] for column in pips_columns}:
      await db.execute("ALTER TABLE pips_log ADD COLUMN signal_id INTEGER")
    await db.execute(
      "CREATE INDEX IF NOT EXISTS idx_pips_ts ON pips_log(ts)"
    )
    await db.execute(
      "CREATE INDEX IF NOT EXISTS idx_pips_signal_id ON pips_log(signal_id)"
    )

    # ------------------------------------------------------------------
    # Table: manual_signals
    # Tracks every signal posted to the channel via the bot's DM
    # interface ("gold sell 4100-4105 / sl 4110 / tp 95/90/80").
    #
    # Columns:
    #   id                 — internal auto-increment primary key.
    #   daily_seq          — daily display number shown as "#<id>".
    #   trade_date         — reset-timezone date that scopes daily_seq.
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
    #   symbol             — instrument key used for routing and pip math.
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
        closed_at          INTEGER,
        daily_seq          INTEGER,
        trade_date         TEXT,
        fill_state         TEXT NOT NULL DEFAULT 'pending',
        filled_at          INTEGER,
        legs               TEXT NOT NULL DEFAULT '[]',
        parent_id          INTEGER,
        setup_type         TEXT,
        confluence         INTEGER,
        note               TEXT,
        symbol             TEXT NOT NULL DEFAULT 'XAU',
        visibility         TEXT NOT NULL DEFAULT 'both'
      )
      """
    )
    columns = await db.execute_fetchall("PRAGMA table_info(manual_signals)")
    column_names = {column[1] for column in columns}
    if "entry_end" not in column_names:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN entry_end REAL")
    if "daily_seq" not in column_names:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN daily_seq INTEGER")
    if "trade_date" not in column_names:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN trade_date TEXT")
    if "fill_state" not in column_names:
      await db.execute(
        "ALTER TABLE manual_signals "
        "ADD COLUMN fill_state TEXT NOT NULL DEFAULT 'pending'"
      )
    if "filled_at" not in column_names:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN filled_at INTEGER")
    if "legs" not in column_names:
      await db.execute(
        "ALTER TABLE manual_signals "
        "ADD COLUMN legs TEXT NOT NULL DEFAULT '[]'"
      )
    if "parent_id" not in column_names:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN parent_id INTEGER")
    if "setup_type" not in column_names:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN setup_type TEXT")
    if "confluence" not in column_names:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN confluence INTEGER")
    if "note" not in column_names:
      await db.execute("ALTER TABLE manual_signals ADD COLUMN note TEXT")
    if "symbol" not in column_names:
      await db.execute(
        "ALTER TABLE manual_signals "
        "ADD COLUMN symbol TEXT NOT NULL DEFAULT 'XAU'"
      )
    if "visibility" not in column_names:
      await db.execute(
        "ALTER TABLE manual_signals "
        "ADD COLUMN visibility TEXT NOT NULL DEFAULT 'both'"
      )
    await db.execute(
      # Index on status so open-signal queries stay fast as history grows.
      "CREATE INDEX IF NOT EXISTS idx_manual_signals_status ON manual_signals(status)"
    )
    await db.execute(
      "CREATE INDEX IF NOT EXISTS idx_manual_signals_trade_date "
      "ON manual_signals(trade_date)"
    )
    await db.execute(
      "CREATE INDEX IF NOT EXISTS idx_manual_signals_symbol_trade_date "
      "ON manual_signals(symbol, trade_date)"
    )
    await db.execute(
      "CREATE INDEX IF NOT EXISTS idx_manual_signals_parent_id "
      "ON manual_signals(parent_id)"
    )
    await db.execute(
      """
      CREATE TABLE IF NOT EXISTS signal_posts (
        signal_id  INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        tier       TEXT NOT NULL,
        PRIMARY KEY (signal_id, channel_id)
      )
      """
    )
    await db.execute(
      "CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_posts_message "
      "ON signal_posts(channel_id, message_id)"
    )
    await db.execute(
      """
      INSERT OR IGNORE INTO signal_posts
        (signal_id, channel_id, message_id, tier)
      SELECT id, ?, channel_message_id, 'vip'
      FROM manual_signals
      WHERE channel_message_id IS NOT NULL
      """,
      (settings.signal_vip_channel_id,),
    )

    await db.execute(
      """
      CREATE TABLE IF NOT EXISTS events (
        event_id  TEXT PRIMARY KEY,
        ts_utc    INTEGER NOT NULL,
        currency  TEXT NOT NULL,
        title     TEXT NOT NULL,
        impact    TEXT NOT NULL,
        forecast  TEXT,
        previous  TEXT,
        actual    TEXT,
        all_day   INTEGER NOT NULL DEFAULT 0,
        source    TEXT NOT NULL DEFAULT 'ff',
        synced_at INTEGER NOT NULL
      )
      """
    )
    await db.execute(
      "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_utc)"
    )
    await db.execute(
      """
      CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT
      )
      """
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Economic calendar
# ---------------------------------------------------------------------------

async def upsert_events(rows: list[dict]) -> None:
  """Insert calendar events, refreshing release values on repeat syncs."""
  if not rows:
    return
  values = [
    (
      row["event_id"], row["ts_utc"], row["currency"], row["title"],
      row["impact"], row.get("forecast"), row.get("previous"),
      row.get("actual"), row.get("all_day", 0),
      row.get("source", "ff"), row["synced_at"],
    )
    for row in rows
  ]
  async with _connect() as db:
    await db.executemany(
      """
      INSERT INTO events (
        event_id, ts_utc, currency, title, impact, forecast, previous,
        actual, all_day, source, synced_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(event_id) DO UPDATE SET
        forecast = excluded.forecast,
        previous = excluded.previous,
        actual = COALESCE(excluded.actual, events.actual),
        synced_at = excluded.synced_at
      """,
      values,
    )
    await db.commit()


async def events_between(start_utc: int, end_utc: int) -> list[dict]:
  """Return stored high-impact events in chronological order."""
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      """
      SELECT * FROM events
      WHERE impact = 'High' AND ts_utc >= ? AND ts_utc < ?
      ORDER BY ts_utc ASC, title ASC
      """,
      (start_utc, end_utc),
    )
    rows = await cur.fetchall()
  return [dict(row) for row in rows]


async def event_in_window(now: int, horizon: int) -> dict | None:
  """Return the nearest timed event from 30m ago through the horizon."""
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      """
      SELECT * FROM events
      WHERE impact = 'High'
        AND all_day = 0
        AND ts_utc >= ?
        AND ts_utc <= ?
      ORDER BY ABS(ts_utc - ?) ASC, ts_utc ASC
      LIMIT 1
      """,
      (now - 1800, now + horizon, now),
    )
    row = await cur.fetchone()
  return dict(row) if row else None


async def get_meta(key: str) -> str | None:
  async with _connect() as db:
    row = await db.execute_fetchall(
      "SELECT value FROM meta WHERE key = ?",
      (key,),
    )
  return row[0][0] if row else None


async def set_meta(key: str, value: str) -> None:
  async with _connect() as db:
    await db.execute(
      """
      INSERT INTO meta (key, value) VALUES (?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value
      """,
      (key, value),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# pips_log helpers
# ---------------------------------------------------------------------------

async def store_pips(
  sign: str,
  pips: int,
  message_id: int = None,
  chat_id: str = None,
  signal_id: int = None,
) -> None:
  """Append one pips result to the log.

  Args:
    sign:       ``'+'`` for a winning trade, ``'-'`` for a loss.
    pips:       Absolute pip count (always positive; sign carries direction).
    message_id: Telegram message_id of the edited channel post (optional).
    chat_id:    Telegram chat_id of the channel (optional).
    signal_id:  Linked manual signal primary key (optional).
  """
  async with _connect() as db:
    await db.execute(
      "INSERT INTO pips_log "
      "(ts, sign, pips, message_id, chat_id, signal_id) "
      "VALUES (?, ?, ?, ?, ?, ?)",
      (
        int(time.time()), sign, pips, message_id,
        str(chat_id) if chat_id else None, signal_id,
      ),
    )
    await db.commit()


async def get_pips_summary(
  start_ts: int,
  end_ts: int,
  symbol: str | None = None,
) -> dict:
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
  async with _connect() as db:
    query = (
      "SELECT p.sign, p.pips FROM pips_log p "
      "LEFT JOIN manual_signals s ON s.id = p.signal_id "
      "WHERE p.ts >= ? AND p.ts <= ?"
    )
    params: list = [start_ts, end_ts]
    if symbol:
      query += " AND s.symbol = ?"
      params.append(symbol.upper())
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
  wins   = [(s, p) for s, p in rows if s == '+']
  losses = [(s, p) for s, p in rows if s == '-']
  net    = sum(p if s == '+' else -p for s, p in rows)
  return {
    'wins': len(wins), 'win_pips': sum(p for _, p in wins),
    'losses': len(losses), 'loss_pips': sum(p for _, p in losses),
    'net': net, 'total': len(rows),
  }


async def get_pips_records(
  start_ts: int,
  end_ts: int,
  symbol: str | None = None,
) -> list[dict]:
  """Return pips rows with their linked signal metadata, oldest first."""
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    query = """
      SELECT p.id, p.ts, p.sign, p.pips, p.signal_id,
             s.ts AS signal_ts, s.action, s.entry, s.entry_end,
             s.parent_id, s.setup_type, s.daily_seq, s.symbol
      FROM pips_log p
      LEFT JOIN manual_signals s ON s.id = p.signal_id
      WHERE p.ts >= ? AND p.ts <= ?
    """
    params: list = [start_ts, end_ts]
    if symbol:
      query += " AND s.symbol = ?"
      params.append(symbol.upper())
    query += " ORDER BY p.ts ASC, p.id ASC"
    cur = await db.execute(query, params)
    rows = await cur.fetchall()
  return [dict(row) for row in rows]


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
  parent_id: int | None = None,
  setup_type: str | None = None,
  confluence: int | None = None,
  symbol: str = "XAU",
  visibility: str = "both",
) -> dict:
  """Insert a manual signal and return its primary and daily display ids.

  Called before posting so the daily display id can be included in the channel
  message, then ``set_manual_signal_channel_id`` back-fills the Telegram id.

  Args:
    ts:                 Unix timestamp of the post.
    action:             ``'BUY'`` or ``'SELL'``.
    entry:              Lower edge of the entry zone.
    entry_end:          Upper edge of the entry zone.
    sl:                 Stop-loss price.
    tps:                List of take-profit prices in order (e.g. [3835.0, 3830.0]).
    symbol:             Instrument key matching ``app.symbols.SYMBOLS``.
    channel_message_id: Telegram message_id of the posted channel message.
              If provided, close/cancel commands can reply to the exact post.

  Returns:
    A dict containing the primary key ``id`` and today's ``daily_seq``.
  """
  async with _connect() as db:
    await db.execute("BEGIN IMMEDIATE")
    symbol = symbol.upper()
    visibility = visibility.lower()
    if visibility not in {"both", "vip"}:
      raise ValueError(f"Invalid visibility: {visibility}")
    tz = ZoneInfo(settings.seq_reset_tz)
    trade_date = datetime.now(tz).date().isoformat()
    row = await db.execute_fetchall(
      "SELECT COUNT(*) FROM manual_signals "
      "WHERE symbol = ? AND trade_date = ?",
      (symbol, trade_date),
    )
    daily_seq = row[0][0] + 1
    cur = await db.execute(
      """
      INSERT INTO manual_signals
        (ts, action, entry, entry_end, sl, tps, order_type,
         channel_message_id, daily_seq, trade_date, parent_id,
         setup_type, confluence, symbol, visibility)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      """,
      (
        ts, action, entry, entry_end, sl, json.dumps(tps), "zone",
        channel_message_id, daily_seq, trade_date, parent_id,
        setup_type, confluence, symbol, visibility,
      ),
    )
    await db.commit()
    return {
      "id": cur.lastrowid,
      "daily_seq": daily_seq,
      "symbol": symbol,
      "visibility": visibility,
    }


async def set_manual_signal_channel_id(row_id: int, channel_message_id: int) -> None:
  """Back-fill the channel_message_id for an already-inserted manual signal.

  Useful if the message_id was not available at insert time (e.g. a retry
  scenario where the signal was stored before the send completed).

  Args:
    row_id:             Primary key of the ``manual_signals`` row.
    channel_message_id: Telegram message_id to store.
  """
  async with _connect() as db:
    await db.execute(
      "UPDATE manual_signals SET channel_message_id = ? WHERE id = ?",
      (channel_message_id, row_id),
    )
    await db.execute(
      """
      INSERT INTO signal_posts (signal_id, channel_id, message_id, tier)
      VALUES (?, ?, ?, 'vip')
      ON CONFLICT(signal_id, channel_id) DO UPDATE SET
        message_id = excluded.message_id,
        tier = 'vip'
      """,
      (row_id, settings.signal_vip_channel_id, channel_message_id),
    )
    await db.commit()


async def insert_signal_post(
  signal_id: int,
  channel_id: int,
  message_id: int,
  tier: str,
) -> None:
  """Record one delivered signal post and retain the VIP compatibility id."""
  async with _connect() as db:
    await db.execute(
      """
      INSERT INTO signal_posts (signal_id, channel_id, message_id, tier)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(signal_id, channel_id) DO UPDATE SET
        message_id = excluded.message_id,
        tier = excluded.tier
      """,
      (signal_id, int(channel_id), message_id, tier),
    )
    if tier == "vip":
      await db.execute(
        "UPDATE manual_signals SET channel_message_id = ? WHERE id = ?",
        (message_id, signal_id),
      )
    await db.commit()


async def get_signal_posts(signal_id: int) -> list[dict]:
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM signal_posts WHERE signal_id = ? "
      "ORDER BY CASE tier WHEN 'vip' THEN 0 ELSE 1 END, channel_id",
      (signal_id,),
    )
    rows = await cur.fetchall()
  return [dict(row) for row in rows]


async def get_signal_by_post(
  channel_id: int,
  message_id: int,
  *,
  open_only: bool = False,
) -> dict | None:
  query = (
    "SELECT s.* FROM signal_posts p "
    "JOIN manual_signals s ON s.id = p.signal_id "
    "WHERE p.channel_id = ? AND p.message_id = ?"
  )
  if open_only:
    query += " AND s.status = 'open'"
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(query, (int(channel_id), message_id))
    row = await cur.fetchone()
  return _decode_signal(row) if row else None


async def get_open_signals(symbol: str | None = None) -> list[dict]:
  """Return all signals currently in ``status = 'open'``, oldest first.

  Used by lifecycle command resolution and the optional price watcher.

  Returns:
    List of dicts with keys: id, ts, action, entry, entry_end, sl, tps,
    channel_message_id, daily_seq, trade_date, fill_state, legs, and review
    metadata. JSON fields are deserialized into plain Python lists.
  """
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    query = (
      "SELECT id, ts, action, entry, entry_end, sl, tps, "
      "channel_message_id, daily_seq, trade_date, fill_state, legs, "
      "parent_id, setup_type, confluence, note, status, result_pips, "
      "symbol, visibility "
      "FROM manual_signals WHERE status = 'open'"
    )
    params = []
    if symbol:
      query += " AND symbol = ?"
      params.append(symbol.upper())
    query += " ORDER BY ts ASC"
    cur = await db.execute(query, params)
    rows = await cur.fetchall()
  return [
    _decode_signal(r)
    for r in rows
  ]


def _decode_signal(row) -> dict:
  """Convert one SQLite signal row and deserialize its JSON fields."""
  result = dict(row)
  result["tps"] = json.loads(result.get("tps") or "[]")
  result["legs"] = json.loads(result.get("legs") or "[]")
  return result


async def get_all_signals(symbol: str | None = None) -> list[dict]:
  """Return every manual signal, oldest first."""
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    query = "SELECT * FROM manual_signals"
    params = []
    if symbol:
      query += " WHERE symbol = ?"
      params.append(symbol.upper())
    query += " ORDER BY ts ASC, id ASC"
    cur = await db.execute(query, params)
    rows = await cur.fetchall()
  return [_decode_signal(row) for row in rows]


async def get_manual_signal(row_id: int) -> dict | None:
  """Return one signal by primary key, regardless of lifecycle state."""
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE id = ?",
      (row_id,),
    )
    row = await cur.fetchone()
  return _decode_signal(row) if row else None


async def undo_last_close_leg(row_id: int) -> dict | None:
  """Undo the latest close booking and leave the signal open again.

  This is used for operator mistakes: the trade was marked closed in the bot,
  but is still running in reality. If the signal had partial legs, only the
  most recent leg is removed so earlier intentional scale-outs remain intact.
  Final-close accounting rows linked to the signal are removed from pips_log.
  """
  async with _connect() as db:
    await db.execute("BEGIN IMMEDIATE")
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE id = ?",
      (row_id,),
    )
    row = await cur.fetchone()
    if row is None or row["status"] == "cancelled":
      return None

    legs = json.loads(row["legs"] or "[]")
    if row["status"] == "open" and not legs:
      return None

    restored_leg = legs.pop() if legs else None
    remaining = max(
      0.0,
      1.0 - sum(float(leg["frac"]) for leg in legs),
    )
    await db.execute(
      "UPDATE manual_signals "
      "SET status = 'open', result_pips = NULL, closed_at = NULL, "
      "legs = ? WHERE id = ?",
      (json.dumps(legs), row_id),
    )
    await db.execute(
      "DELETE FROM pips_log WHERE signal_id = ?",
      (row_id,),
    )
    await db.commit()
    return {
      **_decode_signal(row),
      "status": "open",
      "result_pips": None,
      "closed_at": None,
      "legs": legs,
      "restored_leg": restored_leg,
      "remaining": remaining,
      "previous_status": row["status"],
    }


async def get_manual_signal_any_by_channel_id(
  channel_message_id: int,
) -> dict | None:
  """Return a signal by channel message id, regardless of lifecycle state."""
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE channel_message_id = ?",
      (channel_message_id,),
    )
    row = await cur.fetchone()
  return _decode_signal(row) if row else None


def signal_root(signal: dict) -> int:
  """Return the single-hop root id for a signal or re-entry round."""
  return signal.get("parent_id") or signal["id"]


async def get_signal_cluster(row_id: int) -> list[dict]:
  """Return the root and all linked re-entry rounds for one signal."""
  source = await get_manual_signal(row_id)
  if source is None:
    return []
  root_id = signal_root(source)
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals "
      "WHERE id = ? OR parent_id = ? "
      "ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, ts ASC, id ASC",
      (root_id, root_id, root_id),
    )
    rows = await cur.fetchall()
  return [_decode_signal(row) for row in rows]


async def update_setup(
  row_id: int,
  setup_type: str,
  confluence: int | None,
) -> bool:
  """Set or replace setup metadata on any signal."""
  async with _connect() as db:
    cur = await db.execute(
      "UPDATE manual_signals SET setup_type = ?, confluence = ? WHERE id = ?",
      (setup_type.lower(), confluence, row_id),
    )
    await db.commit()
  return cur.rowcount > 0


async def set_note(row_id: int, note: str) -> bool:
  """Set or replace the journal note on any signal."""
  async with _connect() as db:
    cur = await db.execute(
      "UPDATE manual_signals SET note = ? WHERE id = ?",
      (note, row_id),
    )
    await db.commit()
  return cur.rowcount > 0


async def mark_filled(row_id: int) -> dict | None:
  """Mark a pending open signal filled and return its pre-update row."""
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals "
      "WHERE id = ? AND status = 'open' AND fill_state = 'pending'",
      (row_id,),
    )
    row = await cur.fetchone()
    if row is None:
      return None
    await db.execute(
      "UPDATE manual_signals SET fill_state = 'filled', filled_at = ? "
      "WHERE id = ? AND status = 'open' AND fill_state = 'pending'",
      (int(time.time()), row_id),
    )
    await db.commit()
    return dict(row)


async def close_leg(
  row_id: int,
  pips: int,
  frac: float | None = None,
) -> dict | None:
  """Book one scale-out leg and close the signal when no size remains."""
  async with _connect() as db:
    await db.execute("BEGIN IMMEDIATE")
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE id = ? AND status = 'open'",
      (row_id,),
    )
    row = await cur.fetchone()
    if row is None:
      return None

    legs = json.loads(row["legs"] or "[]")
    used = sum(float(leg["frac"]) for leg in legs)
    remaining = max(0.0, 1.0 - used)
    close_frac = remaining if frac is None else frac
    result_base = {
      "id": row["id"],
      "channel_message_id": row["channel_message_id"],
      "daily_seq": row["daily_seq"],
      "symbol": row["symbol"],
      "visibility": row["visibility"],
    }
    if close_frac <= 0 or close_frac > remaining + _LEG_EPSILON:
      return {
        **result_base,
        "error": "exceeds_remaining",
        "remaining": remaining,
      }

    now = int(time.time())
    legs.append({"frac": close_frac, "pips": pips, "ts": now})
    new_remaining = 1.0 - sum(float(leg["frac"]) for leg in legs)
    if new_remaining <= _LEG_EPSILON:
      net = round(
        sum(float(leg["frac"]) * int(leg["pips"]) for leg in legs)
      )
      await db.execute(
        "UPDATE manual_signals SET status = 'closed', result_pips = ?, "
        "closed_at = ?, legs = ? WHERE id = ? AND status = 'open'",
        (net, now, json.dumps(legs), row_id),
      )
      await db.commit()
      return {
        **result_base,
        "closed": True,
        "net": net,
        "remaining": 0.0,
        "frac": close_frac,
      }

    await db.execute(
      "UPDATE manual_signals SET legs = ? "
      "WHERE id = ? AND status = 'open'",
      (json.dumps(legs), row_id),
    )
    await db.commit()
    return {
      **result_base,
      "closed": False,
      "net": None,
      "remaining": new_remaining,
      "frac": close_frac,
    }


async def update_sl(row_id: int, price: float) -> dict | None:
  """Move the stop loss on an open signal and return its previous row."""
  async with _connect() as db:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
      "SELECT * FROM manual_signals WHERE id = ? AND status = 'open'",
      (row_id,),
    )
    row = await cur.fetchone()
    if row is None:
      return None
    await db.execute(
      "UPDATE manual_signals SET sl = ? WHERE id = ? AND status = 'open'",
      (price, row_id),
    )
    await db.commit()
    return dict(row)


async def get_manual_signal_by_channel_id(channel_message_id: int) -> dict | None:
  """Look up an open manual signal by its Telegram channel message_id.

  Used when the bot receives a channel reply (e.g. "cancel") and needs to
  find which tracked signal the reply is directed at.

  Args:
    channel_message_id: Telegram message_id of the original signal post.

  Returns:
    Full row as a dict, or ``None`` if no open signal matches.
  """
  async with _connect() as db:
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
  async with _connect() as db:
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
  async with _connect() as db:
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
  async with _connect() as db:
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
