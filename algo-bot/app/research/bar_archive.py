"""Archiver: copies the ctrader-feed's closed bars and quote statistics from Redis into Postgres, incrementally and idempotently.

Architecture (see docs/research-data.md): the feed (C#) writes closed bars to Redis ZSETs ``bars:{SYMBOL}:{TF}`` (score = bar open time)
and publishes ``bars:new`` / ``spots:new`` notifications; the Go engine and Algo Bot read the same ZSETs. This task is one more
READ-ONLY consumer of that plane. It never writes to Redis except one health hash, and it never feeds anything back into trading.

* A notification is only a hint. It marks a (symbol, timeframe) dirty; the flusher then reads every stored-or-newer bar of that series
  from the ZSET (``>= last stored open``, so a re-published last bar is seen) and upserts them. Lost notifications, a restart or a Postgres
  outage therefore lose nothing the ZSET still holds: the next flush or the periodic reconcile reads it. The buffer is the dirty set,
  bounded by symbols x timeframes by construction.
* Quotes (``price:{SYMBOL}:spot``) are folded into per-minute statistics in memory (a few open minutes per symbol) and written when a
  minute completes.
* Everything that is stored is stored as published; gaps are detected (closure-aware, see ``bar_store.find_gaps``) and counted, never filled.

The task is off unless ``runtime.market_data_archive.enabled`` is true; with the key absent it returns at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import runtime_config
from app.marketdata.ohlc import _normalize_price
from app.persistence import redis_state
from app.research import bar_store
from app.research.bar_store import BarRow, QuoteMinute

log = logging.getLogger(__name__)

HEALTH_KEY = "research:bar_archive:health"
DEFAULT_SYMBOLS = ("XAU", "EURUSD", "GBPUSD", "GBPJPY", "USDJPY")
DEFAULT_TIMEFRAMES = ("M1", "M5", "M15", "H1")
DEFAULT_RETENTION_DAYS = {"M1": 180, "M5": 730, "M15": 730, "H1": 1825, "quote_minutes": 180, "outcome_labels": 730}


@dataclass(frozen=True)
class ArchiveConfig:
  enabled: bool = False
  symbols: tuple[str, ...] = DEFAULT_SYMBOLS
  timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES
  bars_channel: str = "bars:new"
  spots_channel: str = "spots:new"
  quotes_enabled: bool = True
  flush_interval_seconds: float = 2.0
  reconcile_interval_seconds: float = 120.0
  full_reconcile_interval_seconds: float = 3600.0
  read_batch_bars: int = 2500
  max_open_quote_minutes: int = 40
  prune_interval_seconds: float = 86400.0
  stale_after_seconds: float = 600.0
  retention_days: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_RETENTION_DAYS))


def _node(root: Any, name: str, default: Any) -> Any:
  try:
    value = getattr(root, name)
  except AttributeError:
    return default
  return default if value is None else value


def load_config(root: Any | None = None) -> ArchiveConfig:
  """Read ``runtime.market_data_archive``; every key is optional and an absent section means disabled."""
  runtime = (runtime_config if root is None else root).runtime
  section = _node(runtime, "market_data_archive", None)
  if section is None:
    return ArchiveConfig()
  feed = _node(runtime, "feed", None)
  retention = dict(DEFAULT_RETENTION_DAYS)
  raw_retention = _node(section, "retention_days", None)
  if raw_retention is not None:
    for key in list(retention):
      retention[key] = int(_node(raw_retention, key, retention[key]))
  return ArchiveConfig(
    enabled=bool(_node(section, "enabled", False)),
    symbols=tuple(str(s).upper() for s in _node(section, "symbols", DEFAULT_SYMBOLS)),
    timeframes=tuple(str(t).upper() for t in _node(section, "timeframes", DEFAULT_TIMEFRAMES)),
    bars_channel=str(_node(feed, "bars_channel", "bars:new")) if feed is not None else "bars:new",
    spots_channel=str(_node(section, "spots_channel", "spots:new")),
    quotes_enabled=bool(_node(section, "quotes_enabled", True)),
    flush_interval_seconds=float(_node(section, "flush_interval_seconds", 2.0)),
    reconcile_interval_seconds=float(_node(section, "reconcile_interval_seconds", 120.0)),
    full_reconcile_interval_seconds=float(_node(section, "full_reconcile_interval_seconds", 3600.0)),
    read_batch_bars=int(_node(section, "read_batch_bars", 2500)),
    max_open_quote_minutes=int(_node(section, "max_open_quote_minutes", 40)),
    prune_interval_seconds=float(_node(section, "prune_interval_seconds", 86400.0)),
    stale_after_seconds=float(_node(section, "stale_after_seconds", 600.0)),
    retention_days=retention,
  )


def parse_bar_member(symbol: str, timeframe: str, member: Any, score: float) -> BarRow | None:
  """One ZSET member (the feed's JSON bar) to a row; None when it is not a usable bar (never repaired)."""
  try:
    raw = member.decode() if isinstance(member, bytes) else str(member)
    data = json.loads(raw)
    open_ts = int(float(data.get("t", score)))
    values = [float(data[k]) for k in ("o", "h", "l", "c")]
    volume = float(data.get("v", 0) or 0)
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  normalized = [_normalize_price(symbol, v) for v in values]
  return BarRow(
    symbol, timeframe, open_ts, normalized[0], normalized[1], normalized[2], normalized[3], volume,
    normalized=any(a != b for a, b in zip(values, normalized)),
  )


def parse_spot(raw: Any) -> tuple[float, float, int] | None:
  """(bid, ask, feed timestamp) of ``price:{SYMBOL}:spot``; None when it is not a usable quote."""
  if raw is None:
    return None
  try:
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    bid, ask, ts = float(payload["bid"]), float(payload["ask"]), int(payload["ts"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0 or ask < bid:
    return None
  return bid, ask, ts


class QuoteAggregator:
  """Folds quotes into per-minute statistics. Hard-bounded: at most ``max_open`` minutes are held at any time; beyond it the oldest is dropped and counted."""

  def __init__(self, max_open: int):
    self._max_open = max(2, int(max_open))
    self._minutes: dict[tuple[str, int], list[float]] = {}  # (symbol, minute) -> [n, sum, min, max, bid, ask, first_ts, last_ts]
    self._last_seen: dict[str, tuple[int, float, float]] = {}
    self.dropped_minutes = 0  # minutes released unwritten because the hard bound was hit (a visible hole in quote coverage)

  def add(self, symbol: str, bid: float, ask: float, ts: int) -> bool:
    """False for a repeat of the previous sample (the feed re-publishes unchanged quotes), True when it was counted."""
    previous = self._last_seen.get(symbol)
    sample = (ts, bid, ask)
    if previous == sample:
      return False
    self._last_seen[symbol] = sample
    key = (symbol, ts - ts % 60)
    spread = ask - bid
    entry = self._minutes.get(key)
    if entry is None:
      self._minutes[key] = [1, spread, spread, spread, bid, ask, ts, ts]
    else:
      entry[0] += 1
      entry[1] += spread
      entry[2] = min(entry[2], spread)
      entry[3] = max(entry[3], spread)
      if ts >= entry[7]:
        entry[4], entry[5], entry[7] = bid, ask, ts
      entry[6] = min(entry[6], ts)
    while len(self._minutes) > self._max_open:  # hard bound, enforced on every insert
      del self._minutes[min(self._minutes, key=lambda k: k[1])]
      self.dropped_minutes += 1
    return True

  def pop_complete(self, now: int, *, force_all: bool = False) -> list[QuoteMinute]:
    """Minutes that ended before ``now``'s minute (all of them when ``force_all``)."""
    current = now - now % 60
    release = [k for k in sorted(self._minutes, key=lambda k: k[1]) if force_all or k[1] < current]
    out = []
    for key in release:
      n, total, low, high, bid, ask, first_ts, last_ts = self._minutes.pop(key)
      out.append(QuoteMinute(key[0], key[1], int(n), bid, ask, low, high, total / n, int(first_ts), int(last_ts)))
    return out

  @property
  def open_minutes(self) -> int:
    return len(self._minutes)


def health_status(health: dict[str, str], *, now: float, stale_after_seconds: float) -> tuple[bool, list[str]]:
  """Monitoring verdict from the health hash: (healthy, reasons). Used by ``tools/research_data.py status`` (exit code) and any watchdog."""
  if not health:
    return False, ["no health record (archiver never ran or the key expired)"]
  reasons: list[str] = []
  updated = float(health.get("updated_at", 0) or 0)
  if now - updated > stale_after_seconds:
    reasons.append(f"health record is {int(now - updated)}s old (limit {int(stale_after_seconds)}s)")
  if health.get("state") not in ("running",):
    reasons.append(f"state={health.get('state')}")
  if int(float(health.get("consecutive_errors", 0) or 0)) > 0:
    reasons.append(f"{health.get('consecutive_errors')} consecutive errors: {health.get('last_error', '')[:160]}")
  if int(float(health.get("gaps_unexplained", 0) or 0)) > 0:
    reasons.append(f"{health.get('gaps_unexplained')} unexplained missing bars in the last 7 days")
  return not reasons, reasons


class BarArchiver:
  def __init__(self, client: Any, db_factory: Any, cfg: ArchiveConfig, *, clock=time.time):
    self._client = client
    self._db_factory = db_factory  # async context manager yielding an asyncpg connection
    self._cfg = cfg
    self._clock = clock
    self._dirty: set[tuple[str, str]] = set()
    self._quotes = QuoteAggregator(cfg.max_open_quote_minutes)
    self._quote_last_poll: dict[str, float] = {}
    self._stats = {"bars_inserted": 0, "bars_revised": 0, "bars_unchanged": 0, "quote_minutes_written": 0, "errors_total": 0, "refused_bars": 0}
    self._consecutive_errors = 0
    self._last_error = ""
    self._last_reconcile = 0.0
    self._last_full_reconcile = 0.0
    self._last_prune = 0.0
    self._gaps_unexplained = 0
    self._reported_gaps: set[tuple[str, str, int]] = set()
    self._series_state: dict[str, dict[str, int]] = {}
    self._state = "starting"

  # -- notification intake (cheap, never blocks on Postgres) --------------------------------------------------------------------

  def on_bar_message(self, data: Any) -> None:
    text = data.decode() if isinstance(data, bytes) else str(data or "")
    parts = text.strip().split(":", 2)
    if len(parts) != 3:
      return
    symbol, timeframe = parts[0].upper(), parts[1].upper()
    if symbol in self._cfg.symbols and timeframe in self._cfg.timeframes:
      self._dirty.add((symbol, timeframe))

  async def on_spot_message(self, data: Any) -> None:
    if not self._cfg.quotes_enabled:
      return
    text = data.decode() if isinstance(data, bytes) else str(data or "")
    symbol = text.strip().split(":", 1)[0].upper()
    if symbol not in self._cfg.symbols:
      return
    now = self._clock()
    if now - self._quote_last_poll.get(symbol, 0.0) < 0.2:
      return
    self._quote_last_poll[symbol] = now
    spot = parse_spot(await self._client.get(f"price:{symbol}:spot"))
    if spot is not None:
      self._quotes.add(symbol, *spot)

  # -- bars ---------------------------------------------------------------------------------------------------------------------

  async def _read_series(self, symbol: str, timeframe: str, min_score: float | str) -> list[BarRow]:
    entries = await self._client.zrangebyscore(
      f"bars:{symbol}:{timeframe}", min_score, "+inf", start=0, num=self._cfg.read_batch_bars, withscores=True,
    )
    rows = []
    for member, score in entries:
      row = parse_bar_member(symbol, timeframe, member, score)
      if row is None:
        self._stats["refused_bars"] += 1
      else:
        rows.append(row)
    return rows

  async def sync_series(self, symbol: str, timeframe: str, *, ingest_path: str, full: bool = False) -> bar_store.UpsertResult:
    """Read the series from the ZSET (from the last stored bar on, or all of it when ``full``) and upsert it. Idempotent."""
    now = int(self._clock())
    result = bar_store.UpsertResult()
    async with self._db_factory() as db:
      start: float | str = "-inf"
      if not full:
        last = await bar_store.latest_open_ts(db, symbol, timeframe)
        if last is not None:
          start = last
      while True:
        rows = await self._read_series(symbol, timeframe, start)
        if not rows:
          break
        result += await bar_store.upsert_bars(db, rows, ingest_path=ingest_path, now=now)
        newest = max(r.open_ts for r in rows)
        if len(rows) < self._cfg.read_batch_bars or newest == start:
          break
        start = newest
      last = await bar_store.latest_open_ts(db, symbol, timeframe)
    self._series_state[f"{symbol}:{timeframe}"] = {"last_open_ts": last or 0, "checked_at": now}
    self._stats["bars_inserted"] += result.inserted
    self._stats["bars_revised"] += result.revised
    self._stats["bars_unchanged"] += result.unchanged
    return result

  async def reconcile(self, *, full: bool, ingest_path: str) -> None:
    for symbol in self._cfg.symbols:
      for timeframe in self._cfg.timeframes:
        await self.sync_series(symbol, timeframe, ingest_path=ingest_path, full=full)
    self._last_reconcile = self._clock()
    if full:
      self._last_full_reconcile = self._last_reconcile
    await self.detect_gaps()

  async def detect_gaps(self) -> None:
    """Count (and log once each) bars missing from the last 7 days that no scheduled closure explains."""
    since = int(self._clock()) - 7 * 86400
    unexplained = 0
    async with self._db_factory() as db:
      for symbol in self._cfg.symbols:
        for timeframe in self._cfg.timeframes:
          rows = await db.fetch(
            "SELECT open_ts FROM market_bars WHERE symbol = $1 AND timeframe = $2 AND open_ts >= $3 ORDER BY open_ts", symbol, timeframe, since,
          )
          for gap in bar_store.find_gaps(symbol, timeframe, [int(r["open_ts"]) for r in rows]):
            if gap.unexplained == 0:
              continue
            unexplained += gap.unexplained
            key = (symbol, timeframe, gap.after_ts)
            if key not in self._reported_gaps:
              self._reported_gaps.add(key)
              log.warning(
                "bar archive gap symbol=%s timeframe=%s after=%s before=%s missing=%d unexplained=%d",
                symbol, timeframe, gap.after_ts, gap.before_ts, gap.missing, gap.unexplained,
              )
    self._gaps_unexplained = unexplained

  # -- quotes -------------------------------------------------------------------------------------------------------------------

  async def flush_quotes(self, *, force_all: bool = False) -> None:
    minutes = self._quotes.pop_complete(int(self._clock()), force_all=force_all)
    if not minutes:
      return
    try:
      async with self._db_factory() as db:
        self._stats["quote_minutes_written"] += await bar_store.upsert_quote_minutes(db, minutes, now=int(self._clock()))
    except Exception:
      # Put nothing back: a minute that could not be written is a visible hole in quote coverage (spread falls back to the assumption).
      self._stats["errors_total"] += 1
      raise

  # -- lifecycle ----------------------------------------------------------------------------------------------------------------

  async def tick(self) -> None:
    """One flusher pass: dirty series, quotes, periodic reconcile and prune, then the health record. Errors are counted and retried."""
    try:
      dirty, self._dirty = self._dirty, set()
      try:
        for symbol, timeframe in sorted(dirty):
          await self.sync_series(symbol, timeframe, ingest_path=bar_store.INGEST_LIVE)
      except Exception:
        self._dirty |= dirty  # nothing is lost: the series is read again from the ZSET on the next pass
        raise
      await self.flush_quotes()
      now = self._clock()
      if now - self._last_full_reconcile >= self._cfg.full_reconcile_interval_seconds:
        await self.reconcile(full=True, ingest_path=bar_store.INGEST_CATCHUP)
      elif now - self._last_reconcile >= self._cfg.reconcile_interval_seconds:
        await self.reconcile(full=False, ingest_path=bar_store.INGEST_CATCHUP)
      if now - self._last_prune >= self._cfg.prune_interval_seconds:
        async with self._db_factory() as db:
          deleted = await bar_store.prune(db, self._cfg.retention_days, now=int(now))
        self._last_prune = now
        log.info("bar archive prune deleted=%s", deleted)
      self._consecutive_errors = 0
      self._state = "running"
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      self._consecutive_errors += 1
      self._stats["errors_total"] += 1
      self._last_error = f"{type(exc).__name__}: {exc}"
      self._state = "degraded"
      if self._consecutive_errors == 1:
        log.exception("bar archive tick failed; retrying with backoff (nothing is lost while the Redis windows still hold the bars)")
      else:
        log.warning("bar archive tick failed again (consecutive=%d): %s", self._consecutive_errors, self._last_error)
    await self._publish_health()

  async def _publish_health(self) -> None:
    now = self._clock()
    series = {
      key: {**value, "lag_seconds": int(now - value["last_open_ts"]) if value["last_open_ts"] else None}
      for key, value in sorted(self._series_state.items())
    }
    fields = {
      "updated_at": str(int(now)),
      "state": self._state,
      "consecutive_errors": str(self._consecutive_errors),
      "last_error": self._last_error,
      "dirty_series": str(len(self._dirty)),
      "open_quote_minutes": str(self._quotes.open_minutes),
      "quote_minutes_dropped": str(self._quotes.dropped_minutes),
      "gaps_unexplained": str(self._gaps_unexplained),
      "last_reconcile_at": str(int(self._last_reconcile)),
      "last_prune_at": str(int(self._last_prune)),
      "series": json.dumps(series, separators=(",", ":")),
      **{k: str(v) for k, v in self._stats.items()},
    }
    try:
      await self._client.hset(HEALTH_KEY, mapping=fields)
    except Exception:
      log.exception("bar archive health write failed")

  async def run(self) -> None:
    async with self._db_factory() as db:
      await bar_store.ensure_schema(db)
    await self.reconcile(full=True, ingest_path=bar_store.INGEST_CATCHUP)
    self._state = "running"
    await self._publish_health()
    pubsub = self._client.pubsub()
    channels = [self._cfg.bars_channel] + ([self._cfg.spots_channel] if self._cfg.quotes_enabled else [])
    await pubsub.subscribe(*channels)
    log.info(
      "bar archive started symbols=%s timeframes=%s channels=%s retention_days=%s",
      ",".join(self._cfg.symbols), ",".join(self._cfg.timeframes), ",".join(channels), self._cfg.retention_days,
    )
    listener = asyncio.create_task(self._listen(pubsub), name="bar-archive-listen")
    try:
      while True:
        # Healthy: the flush interval. After errors: doubling, capped at one minute, so an outage costs neither log volume nor load.
        delay = self._cfg.flush_interval_seconds * (2 ** min(self._consecutive_errors, 6) if self._consecutive_errors else 1)
        await asyncio.sleep(min(delay, 60.0))
        if listener.done():
          listener.result()  # re-raise: the supervisor restarts the whole task
        await self.tick()
    finally:
      listener.cancel()
      await asyncio.gather(listener, return_exceptions=True)
      try:
        await self.flush_quotes(force_all=True)
      except Exception:
        log.exception("bar archive final quote flush failed")
      await pubsub.unsubscribe(*channels)
      await pubsub.close()

  async def _listen(self, pubsub: Any) -> None:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      channel = message.get("channel")
      channel = channel.decode() if isinstance(channel, bytes) else str(channel)
      if channel == self._cfg.bars_channel:
        self.on_bar_message(message.get("data"))
      else:
        try:
          await self.on_spot_message(message.get("data"))
        except Exception:
          log.exception("bar archive quote sample failed")


async def bar_archive_loop() -> None:
  """Supervised entry point: returns at once (no restart) unless the archive is enabled."""
  cfg = load_config()
  if not cfg.enabled:
    log.info("bar archive disabled (runtime.market_data_archive.enabled is not true)")
    return
  from app.persistence import store

  await BarArchiver(redis_state.get_client(), store._connect, cfg).run()
