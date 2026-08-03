"""Market-map cache, owner delivery, and periodic scheduling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from zoneinfo import ZoneInfo

from app.core.config import runtime_config
from app.persistence import redis_state
from app.persistence.store import get_meta, set_meta
from app.analysis.market_map import (
  MarketMap,
  build_map,
  map_materially_changed,
  market_map_from_payload,
  market_map_payload,
  render_market_map,
)
from app.core.symbols import SYMBOLS
from app.bot.client import delete_scanner_message, send_scanner_with_retry
from app.autotrade.map_strategy import market_map_display_key

log = logging.getLogger(__name__)

_META_SCAN_KEY = "last_map_scan"
_META_MAP_PREFIX = "last_market_map"
_LOOP_INTERVAL_SECONDS = 60
_MARKET_MAP_TELEGRAM_TTL_SECONDS = 7 * 24 * 3600


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


def market_map_telegram_key(symbol: str) -> str:
  return f"auto_trade:market_map_telegram:{symbol.upper()}"


async def get_current_market_map(symbol: str) -> MarketMap | None:
  symbol = symbol.upper()
  cached = _cache.get(symbol)
  if cached is None:
    from app.analysis.scanner import _load_market_context_for_symbol

    ctx, _ = await _load_market_context_for_symbol(symbol)
    analysis = getattr(ctx, "analysis", None) if ctx is not None else None
    if analysis is None:
      return None
    frame = analysis.frames.get(
      runtime_config.market_data.scanner.execution_timeframe.upper()
    )
    price = (
      float(ctx.spot_price)
      if getattr(ctx, "spot_price", None) is not None
      else float(frame["close"].iloc[-1])
    )
    cache_analysis(symbol, analysis, price, frame.index[-1])
    cached = _cache.get(symbol)
  if cached is None:
    return None
  return build_map(cached.analysis, cached.price)


async def render_current_market_map(
  symbol: str,
  now: datetime | None = None,
) -> str | None:
  market_map = await get_current_market_map(symbol)
  if market_map is None:
    return None
  local_tz = ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
  display_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
  return render_market_map(market_map, symbol, display_now)


async def send_current_market_map(
  symbol: str,
  now: datetime | None = None,
) -> bool:
  if not runtime_config.delivery.telegram.telegram_owner_id:
    return False
  market_map = await get_current_market_map(symbol)
  if market_map is None:
    return False
  local_tz = ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
  display_now = now.astimezone(local_tz) if now else datetime.now(local_tz)
  text = render_market_map(market_map, symbol, display_now)
  await _replace_owner_market_map_message(symbol, text)
  await _remember_displayed_map(symbol, market_map)
  return True


def _xau_weekend_closed(now: datetime) -> bool:
  """XAU approx weekend closure: Fri 21:00 UTC through Sun 22:00 UTC."""
  now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(
    tzinfo=timezone.utc,
  )
  weekday = now.weekday()
  if weekday == 4 and now.hour >= 21:
    return True
  if weekday == 5:
    return True
  if weekday == 6 and now.hour < 22:
    return True
  return False


async def _market_map_scan_tick(now: datetime | None = None) -> bool:
  if (
    not runtime_config.delivery.market_map.session_send
    or not runtime_config.delivery.telegram.telegram_owner_id
  ):
    return False
  now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
  if _xau_weekend_closed(now):
    return False
  scan_key = _scan_bucket_key(
    now, runtime_config.analysis.market_map.scan_interval_minutes,
  )
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
      runtime_config.analysis.market_map.change_min,
    ):
      if previous is not None:
        await _remember_displayed_map(symbol, previous)
      continue
    display_now = now.astimezone(
      ZoneInfo(runtime_config.delivery.presentation.seq_reset_tz)
    )
    text = render_market_map(market_map, symbol, display_now)
    await _replace_owner_market_map_message(symbol, text)
    await _remember_displayed_map(symbol, market_map)
    await set_meta(payload_key, market_map_payload(market_map))
    sent = True

  if evaluated:
    await set_meta(_META_SCAN_KEY, scan_key)
  return sent


async def market_map_scan_loop() -> None:
  if not runtime_config.delivery.market_map.session_send:
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


async def _replace_owner_market_map_message(symbol: str, text: str) -> None:
  """Send the latest owner map and delete the previous Telegram message."""
  chat_id = int(runtime_config.delivery.telegram.telegram_owner_id)
  client = redis_state.get_client()
  key = market_map_telegram_key(symbol)
  previous = await _load_market_map_telegram(client, key)
  if previous is not None:
    try:
      await delete_scanner_message(previous["chat_id"], previous["message_id"])
    except Exception:
      log.info(
        "previous market map delete failed symbol=%s chat_id=%s message_id=%s",
        symbol,
        previous["chat_id"],
        previous["message_id"],
        exc_info=True,
      )
  sent = await send_scanner_with_retry(text, chat_id=chat_id)
  await client.set(
    key,
    json.dumps(
      {
        "chat_id": chat_id,
        "message_id": int(sent.message_id),
        "updated_at": int(datetime.now(timezone.utc).timestamp()),
      },
      separators=(",", ":"),
    ),
    ex=_MARKET_MAP_TELEGRAM_TTL_SECONDS,
  )


async def _load_market_map_telegram(client, key: str) -> dict | None:
  raw = await client.get(key)
  if not raw:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    data = json.loads(text)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  if not isinstance(data, dict):
    return None
  try:
    chat_id = int(data["chat_id"])
    message_id = int(data["message_id"])
  except (KeyError, TypeError, ValueError):
    return None
  if message_id <= 0:
    return None
  return {"chat_id": chat_id, "message_id": message_id}


async def _remember_displayed_map(
  symbol: str,
  market_map: MarketMap,
) -> None:
  ttl = max(
    3600,
    max(1, int(runtime_config.analysis.market_map.scan_interval_minutes)) * 120,
  )
  await redis_state.get_client().set(
    market_map_display_key(symbol),
    market_map_payload(market_map),
    ex=ttl,
  )


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
    for item in runtime_config.market_data.scanner.symbols.split(",")
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
