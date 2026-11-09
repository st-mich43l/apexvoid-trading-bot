"""Durable closed-bar and quote-minute store (Postgres) for research and replay.

This module is persistence only: it stores what the ctrader-feed published, exactly as published, so any Go opportunity can later be
replayed against the same bars. It is not market analysis and computes no technical fact.

Tables (all created by ``ensure_schema``; nothing here runs unless the archiver or a tool calls it):

``market_bars``
  One row per (symbol, timeframe, open_ts): the feed's closed bar. ``open_ts`` is the bar's OPEN time in epoch seconds (the feed's
  ZSET score and the engine's ``Candle.Time``); the bar closed at ``open_ts + timeframe seconds``. A bar the feed re-published with
  different values is updated in place, ``revisions`` is incremented and the superseded values go to ``market_bar_revisions``.
``market_bar_revisions``
  Append-only history of superseded bar values.
``market_quote_minutes``
  Per symbol and UTC minute: the quotes the feed published (count, last bid/ask, min/max/mean spread; a late quote merges into its minute). Quotes are throttled by the
  feed to at most 4 a second per symbol; the count says how complete a minute is.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

TIMEFRAME_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
SOURCE_FEED_REDIS = "ctrader-feed/redis-zset"

INGEST_LIVE = "live"
INGEST_CATCHUP = "catchup"
INGEST_BACKFILL = "backfill"

_NEW_YORK = ZoneInfo("America/New_York")

SCHEMA_STATEMENTS = (
  """
  CREATE TABLE IF NOT EXISTS market_bars (
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    open_ts       BIGINT NOT NULL,
    open          DOUBLE PRECISION NOT NULL,
    high          DOUBLE PRECISION NOT NULL,
    low           DOUBLE PRECISION NOT NULL,
    close         DOUBLE PRECISION NOT NULL,
    volume        DOUBLE PRECISION NOT NULL,
    ohlc_hash     TEXT NOT NULL,
    source        TEXT NOT NULL,
    ingest_path   TEXT NOT NULL,
    normalized    BOOLEAN NOT NULL DEFAULT FALSE,
    revisions     INTEGER NOT NULL DEFAULT 0,
    first_seen_at BIGINT NOT NULL,
    PRIMARY KEY (symbol, timeframe, open_ts)
  )
  """,
  "CREATE INDEX IF NOT EXISTS idx_market_bars_tf_ts ON market_bars(timeframe, open_ts)",
  """
  CREATE TABLE IF NOT EXISTS market_bar_revisions (
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    open_ts       BIGINT NOT NULL,
    revision      INTEGER NOT NULL,
    open          DOUBLE PRECISION NOT NULL,
    high          DOUBLE PRECISION NOT NULL,
    low           DOUBLE PRECISION NOT NULL,
    close         DOUBLE PRECISION NOT NULL,
    volume        DOUBLE PRECISION NOT NULL,
    ohlc_hash     TEXT NOT NULL,
    superseded_at BIGINT NOT NULL,
    PRIMARY KEY (symbol, timeframe, open_ts, revision)
  )
  """,
  """
  CREATE TABLE IF NOT EXISTS market_quote_minutes (
    symbol      TEXT NOT NULL,
    minute_ts   BIGINT NOT NULL,
    quote_count INTEGER NOT NULL,
    bid_last    DOUBLE PRECISION NOT NULL,
    ask_last    DOUBLE PRECISION NOT NULL,
    spread_min  DOUBLE PRECISION NOT NULL,
    spread_max  DOUBLE PRECISION NOT NULL,
    spread_mean DOUBLE PRECISION NOT NULL,
    first_ts    BIGINT NOT NULL,
    last_ts     BIGINT NOT NULL,
    recorded_at BIGINT NOT NULL,
    PRIMARY KEY (symbol, minute_ts)
  )
  """,
  "CREATE INDEX IF NOT EXISTS idx_market_quote_minutes_ts ON market_quote_minutes(minute_ts)",
)


@dataclass(frozen=True)
class BarRow:
  symbol: str
  timeframe: str
  open_ts: int
  open: float
  high: float
  low: float
  close: float
  volume: float
  normalized: bool = False

  @property
  def ohlc_hash(self) -> str:
    return bar_hash(self.open, self.high, self.low, self.close, self.volume)


@dataclass(frozen=True)
class QuoteMinute:
  symbol: str
  minute_ts: int
  quote_count: int
  bid_last: float
  ask_last: float
  spread_min: float
  spread_max: float
  spread_mean: float
  first_ts: int
  last_ts: int


@dataclass(frozen=True)
class UpsertResult:
  inserted: int = 0
  unchanged: int = 0
  revised: int = 0

  def __add__(self, other: "UpsertResult") -> "UpsertResult":
    return UpsertResult(self.inserted + other.inserted, self.unchanged + other.unchanged, self.revised + other.revised)


def bar_hash(open_: float, high: float, low: float, close: float, volume: float) -> str:
  """Content identity of a bar: the same published values always hash the same (repr round-trips a float exactly)."""
  text = "|".join(repr(float(v)) for v in (open_, high, low, close, volume))
  return hashlib.sha1(text.encode("ascii")).hexdigest()[:16]


def valid_bar(row: BarRow) -> bool:
  """The OHLC consistency the Go replay also enforces; an inconsistent bar is refused, never repaired."""
  values = (row.open, row.high, row.low, row.close, row.volume)
  if any(v != v or v in (float("inf"), float("-inf")) for v in values):
    return False
  return (
    row.high >= max(row.open, row.close, row.low)
    and row.low <= min(row.open, row.close, row.high)
    and row.volume >= 0
    and row.open_ts > 0
    and row.timeframe in TIMEFRAME_SECONDS
  )


async def ensure_schema(db) -> None:
  for statement in SCHEMA_STATEMENTS:
    await db.execute(statement)


async def upsert_bars(db, rows: list[BarRow], *, ingest_path: str, now: int, source: str = SOURCE_FEED_REDIS) -> UpsertResult:
  """Idempotent write: a bar already stored with the same values is left alone, one with different values is revised (old values kept)."""
  rows = [row for row in rows if valid_bar(row)]
  if not rows:
    return UpsertResult()
  inserted = unchanged = revised = 0
  async with db.transaction():
    for symbol, timeframe in sorted({(r.symbol, r.timeframe) for r in rows}):
      group = [r for r in rows if r.symbol == symbol and r.timeframe == timeframe]
      existing = {
        int(rec["open_ts"]): rec
        for rec in await db.fetch(
          "SELECT open_ts, open, high, low, close, volume, ohlc_hash, revisions FROM market_bars "
          "WHERE symbol = $1 AND timeframe = $2 AND open_ts = ANY($3::bigint[])",
          symbol, timeframe, [r.open_ts for r in group],
        )
      }
      new_rows = []
      for row in group:
        old = existing.get(row.open_ts)
        if old is None:
          new_rows.append(row)
        elif old["ohlc_hash"] == row.ohlc_hash:
          unchanged += 1
        else:
          revision = int(old["revisions"]) + 1
          await db.execute(
            "INSERT INTO market_bar_revisions (symbol, timeframe, open_ts, revision, open, high, low, close, volume, ohlc_hash, superseded_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) ON CONFLICT DO NOTHING",
            symbol, timeframe, row.open_ts, revision, old["open"], old["high"], old["low"], old["close"], old["volume"], old["ohlc_hash"], now,
          )
          await db.execute(
            "UPDATE market_bars SET open=$4, high=$5, low=$6, close=$7, volume=$8, ohlc_hash=$9, revisions=$10, normalized=$11 "
            "WHERE symbol=$1 AND timeframe=$2 AND open_ts=$3",
            symbol, timeframe, row.open_ts, row.open, row.high, row.low, row.close, row.volume, row.ohlc_hash, revision, row.normalized,
          )
          revised += 1
      if new_rows:
        await db.executemany(
          "INSERT INTO market_bars (symbol, timeframe, open_ts, open, high, low, close, volume, ohlc_hash, source, ingest_path, normalized, first_seen_at) "
          "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT (symbol, timeframe, open_ts) DO NOTHING",
          [
            (r.symbol, r.timeframe, r.open_ts, r.open, r.high, r.low, r.close, r.volume, r.ohlc_hash, source, ingest_path, r.normalized, now)
            for r in new_rows
          ],
        )
        inserted += len(new_rows)
  return UpsertResult(inserted, unchanged, revised)


async def upsert_quote_minutes(db, rows: list[QuoteMinute], *, now: int) -> int:
  """Write completed quote minutes. A second write for the same minute (a late quote after the minute was flushed) is MERGED into the
  stored row (counts add, extremes widen, the mean is count-weighted, the last bid/ask is the later one), never overwritten."""
  if not rows:
    return 0
  await db.executemany(
    "INSERT INTO market_quote_minutes (symbol, minute_ts, quote_count, bid_last, ask_last, spread_min, spread_max, spread_mean, first_ts, last_ts, recorded_at) "
    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
    "ON CONFLICT (symbol, minute_ts) DO UPDATE SET "
    "spread_mean = (market_quote_minutes.spread_mean * market_quote_minutes.quote_count + EXCLUDED.spread_mean * EXCLUDED.quote_count) "
    "              / (market_quote_minutes.quote_count + EXCLUDED.quote_count), "
    "bid_last = CASE WHEN EXCLUDED.last_ts >= market_quote_minutes.last_ts THEN EXCLUDED.bid_last ELSE market_quote_minutes.bid_last END, "
    "ask_last = CASE WHEN EXCLUDED.last_ts >= market_quote_minutes.last_ts THEN EXCLUDED.ask_last ELSE market_quote_minutes.ask_last END, "
    "quote_count = market_quote_minutes.quote_count + EXCLUDED.quote_count, "
    "spread_min = LEAST(market_quote_minutes.spread_min, EXCLUDED.spread_min), spread_max = GREATEST(market_quote_minutes.spread_max, EXCLUDED.spread_max), "
    "first_ts = LEAST(market_quote_minutes.first_ts, EXCLUDED.first_ts), last_ts = GREATEST(market_quote_minutes.last_ts, EXCLUDED.last_ts), "
    "recorded_at = EXCLUDED.recorded_at",
    [(r.symbol, r.minute_ts, r.quote_count, r.bid_last, r.ask_last, r.spread_min, r.spread_max, r.spread_mean, r.first_ts, r.last_ts, now) for r in rows],
  )
  return len(rows)


async def latest_open_ts(db, symbol: str, timeframe: str) -> int | None:
  value = await db.fetchval("SELECT max(open_ts) FROM market_bars WHERE symbol = $1 AND timeframe = $2", symbol, timeframe)
  return None if value is None else int(value)


async def read_bars(db, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[tuple[int, float, float, float, float, float]]:
  """Bars with ``start_ts <= open_ts < end_ts`` oldest first, as (open_ts, open, high, low, close, volume)."""
  rows = await db.fetch(
    "SELECT open_ts, open, high, low, close, volume FROM market_bars "
    "WHERE symbol = $1 AND timeframe = $2 AND open_ts >= $3 AND open_ts < $4 ORDER BY open_ts",
    symbol, timeframe, int(start_ts), int(end_ts),
  )
  return [(int(r["open_ts"]), r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows]


async def read_spreads(db, symbol: str, start_ts: int, end_ts: int) -> dict[int, tuple[float, float, float, int]]:
  """Per UTC minute (minute_ts -> (spread_min, spread_max, spread_mean, quote_count)) for ``start_ts <= minute_ts < end_ts``."""
  rows = await db.fetch(
    "SELECT minute_ts, spread_min, spread_max, spread_mean, quote_count FROM market_quote_minutes "
    "WHERE symbol = $1 AND minute_ts >= $2 AND minute_ts < $3",
    symbol, int(start_ts), int(end_ts),
  )
  return {
    int(r["minute_ts"]): (float(r["spread_min"]), float(r["spread_max"]), float(r["spread_mean"]), int(r["quote_count"])) for r in rows
  }


async def prune(db, retention_days: dict[str, int], *, now: int) -> dict[str, int]:
  """Delete rows older than each key's retention: a timeframe, ``quote_minutes`` or ``outcome_labels``; returns rows deleted per key."""
  deleted: dict[str, int] = {}
  for timeframe, days in retention_days.items():
    cutoff = now - int(days) * 86400
    if timeframe == "quote_minutes":
      status = await db.execute("DELETE FROM market_quote_minutes WHERE minute_ts < $1", cutoff)
    elif timeframe == "outcome_labels":
      if await db.fetchval("SELECT to_regclass('opportunity_outcome_labels')") is None:
        deleted[timeframe] = 0
        continue
      status = await db.execute("DELETE FROM opportunity_outcome_labels WHERE created_at < $1", cutoff)
    else:
      status = await db.execute("DELETE FROM market_bars WHERE timeframe = $1 AND open_ts < $2", timeframe, cutoff)
      await db.execute("DELETE FROM market_bar_revisions WHERE timeframe = $1 AND open_ts < $2", timeframe, cutoff)
    try:
      deleted[timeframe] = int(status.split()[-1])
    except (ValueError, IndexError):
      deleted[timeframe] = 0
  return deleted


# -- gap detection -----------------------------------------------------------------------------------------------------------------


def market_closed(open_ts: int) -> bool:
  """Whether a bar opening at ``open_ts`` falls in a scheduled closure of the FX / metals feed, on the New York clock.

  Observed on the live feed (XAU, 29 Oct - 9 Nov 2026): no bar opens from 17:00 through 18:00 New York time on weekdays (the daily break),
  and none from Friday 16:58 through Sunday 18:00 (the weekly closure), whichever side of daylight saving the date is. This is a heuristic
  for classifying a gap, never a reason to invent a bar: a gap that is not inside it is reported as unexplained.
  """
  local = datetime.fromtimestamp(open_ts, _NEW_YORK)
  minutes = local.hour * 60 + local.minute
  weekday = local.weekday()  # Monday 0 .. Sunday 6
  if weekday == 5:
    return True
  if weekday == 4 and minutes >= 16 * 60 + 58:
    return True  # the last Friday M1 bar opens at 16:57 New York time
  if weekday == 6 and minutes <= 18 * 60:
    return True
  return 17 * 60 <= minutes <= 18 * 60 and weekday in (0, 1, 2, 3)


@dataclass(frozen=True)
class Gap:
  symbol: str
  timeframe: str
  after_ts: int  # last stored open before the gap
  before_ts: int  # first stored open after it
  missing: int  # expected bars absent in between
  unexplained: int  # of those, the ones not inside a scheduled closure

  @property
  def classification(self) -> str:
    return "closure" if self.unexplained == 0 else "unexplained"


def find_gaps(symbol: str, timeframe: str, open_times: list[int]) -> list[Gap]:
  """Gaps between consecutive stored bars (``open_times`` ascending), each split into closure and unexplained missing bars."""
  step = TIMEFRAME_SECONDS[timeframe]
  gaps: list[Gap] = []
  for previous, current in zip(open_times, open_times[1:]):
    if current - previous <= step:
      continue
    expected = range(previous + step, current, step)
    missing = len(expected)
    unexplained = sum(1 for ts in expected if not market_closed(ts))
    gaps.append(Gap(symbol, timeframe, previous, current, missing, unexplained))
  return gaps
