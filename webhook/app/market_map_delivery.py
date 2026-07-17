"""Market-map cache, owner delivery, and periodic scheduling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo

from app.config import settings
from app.dedup import get_meta, set_meta
from app.market_map import (
  MarketMap,
  build_map,
  map_materially_changed,
  market_map_from_payload,
  market_map_payload,
  render_market_map,
)
from app.symbols import SYMBOLS
from app.tg_core import send_scanner_with_retry

log = logging.getLogger(__name__)

_META_SCAN_KEY = "last_map_scan"
_META_MAP_PREFIX = "last_market_map"
_LOOP_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class CachedAnalysis:
  analysis: object
  price: float
  asof: datetime


_cache: dict[str, CachedAnalysis] = {}


def cache_analysis(
  symbol: str,
  analysis,
  price: float,
  asof,
) -> None:
  if analysis is None:
    return
  timestamp = asof.to_pydatetime() if hasattr(asof, "to_pydatetime") else asof
  if not isinstance(timestamp, datetime):
    timestamp = datetime.now(timezone.utc)
  if timestamp.tzinfo is None:
    timestamp = timestamp.replace(tzinfo=timezone.utc)
  _cache[symbol.upper()] = CachedAnalysis(analysis, float(price), timestamp)


def clear_market_map_cache() -> None:
  _cache.clear()


async def get_current_market_map(symbol: str) -> MarketMap | None:
  symbol = symbol.upper()
  cached = _cache.get(symbol)
  if cached is None:
    from app.scanner import _load_market_context_for_symbol

    ctx, _ = await _load_market_context_for_symbol(symbol)
    analysis = getattr(ctx, "analysis", None) if ctx is not None else None
    if analysis is None:
      return None
    frame = analysis.frames.get(settings.scanner_exec_tf.upper())
    price = (
      float(ctx.spot_price)
      if getattr(ctx, "spot_price", None) is not None
      else float(frame["close"].iloc[-1])
    )
    cache_analysis(symbol, analysis, price, frame.index[-1])
    cached = _cache.get(symbol)
  if cached is None:
    return None
  return build_map(cached.analysis, cached.price, settings)


async def render_current_market_map(
  symbol: str,
  now: datetime | None = None,
) -> str | None:
  market_map = await get_current_market_map(symbol)
  if market_map is None:
    return None
  local_tz = ZoneInfo(settings.seq_reset_tz)
  display_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
  return render_market_map(market_map, symbol, display_now, settings)


async def send_current_market_map(
  symbol: str,
  now: datetime | None = None,
) -> bool:
  if not settings.telegram_owner_id:
    return False
  text = await render_current_market_map(symbol, now)
  if text is None:
    return False
  await send_scanner_with_retry(text, chat_id=settings.telegram_owner_id)
  return True


async def _market_map_scan_tick(now: datetime | None = None) -> bool:
  if not settings.map_session_send or not settings.telegram_owner_id:
    return False
  now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
  scan_key = _scan_bucket_key(now, settings.map_scan_interval_minutes)
  if await get_meta(_META_SCAN_KEY) == scan_key:
    return False

  evaluated = False
  sent = False
  for symbol in _map_symbols():
    market_map = await get_current_market_map(symbol)
    if market_map is None:
      continue
    evaluated = True
    payload_key = f"{_META_MAP_PREFIX}:{symbol}"
    previous = _load_previous_map(await get_meta(payload_key))
    if not map_materially_changed(
      previous,
      market_map,
      settings.map_change_min,
    ):
      continue
    display_now = now.astimezone(ZoneInfo(settings.seq_reset_tz))
    text = render_market_map(market_map, symbol, display_now, settings)
    await send_scanner_with_retry(text, chat_id=settings.telegram_owner_id)
    await set_meta(payload_key, market_map_payload(market_map))
    sent = True

  if evaluated:
    await set_meta(_META_SCAN_KEY, scan_key)
  return sent


async def market_map_scan_loop() -> None:
  if not settings.map_session_send:
    log.info("Market Map automatic delivery disabled")
    return
  while True:
    try:
      await _market_map_scan_tick()
    except asyncio.CancelledError:
      raise
    except Exception:
      log.exception("Market Map periodic delivery failed")
    await asyncio.sleep(_LOOP_INTERVAL_SECONDS)


def _scan_bucket_key(now: datetime, interval_minutes: int) -> str:
  interval_seconds = max(1, int(interval_minutes)) * 60
  timestamp = int(now.timestamp())
  opened = datetime.fromtimestamp(
    timestamp - timestamp % interval_seconds,
    timezone.utc,
  )
  return opened.isoformat(timespec="minutes").replace("+00:00", "Z")


def _map_symbols() -> list[str]:
  configured = [
    item.strip().upper()
    for item in settings.scanner_symbols.split(",")
    if item.strip()
  ]
  return [symbol for symbol in configured if symbol in SYMBOLS]


def _load_previous_map(payload: str | None) -> MarketMap | None:
  if payload is None:
    return None
  try:
    return market_map_from_payload(payload)
  except (KeyError, TypeError, ValueError):
    return None
