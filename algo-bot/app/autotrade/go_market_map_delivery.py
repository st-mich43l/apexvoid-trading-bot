"""Owner delivery of the Go Analysis Engine's read-only zone view."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import logging

from app.autotrade.go_market_map import load_go_market_map, render_go_market_map
from app.bot.client import delete_scanner_message, send_scanner_with_retry
from app.core.config import runtime_config
from app.persistence import redis_state

log = logging.getLogger(__name__)

_TTL_SECONDS = 7 * 24 * 3600


def market_map_telegram_key(symbol: str) -> str:
  return f"auto_trade:market_map_telegram:{symbol.upper()}"


async def send_current_market_map(symbol: str, now: datetime | None = None) -> bool:
  if not runtime_config.telegram.telegram_owner_id:
    return False
  client = redis_state.get_client()
  market_map = await load_go_market_map(symbol, client)
  if market_map is None:
    return False
  key = market_map_telegram_key(symbol)
  previous = await _load_message(client, key)
  if previous is not None:
    try:
      await delete_scanner_message(previous["chat_id"], previous["message_id"])
    except Exception:
      log.info("previous Go market map delete failed symbol=%s", symbol, exc_info=True)
  sent = await send_scanner_with_retry(
    render_go_market_map(market_map, symbol),
    chat_id=int(runtime_config.telegram.telegram_owner_id),
  )
  await client.set(
    key,
    json.dumps({
      "chat_id": int(runtime_config.telegram.telegram_owner_id),
      "message_id": int(sent.message_id),
      "updated_at": int((now or datetime.now(timezone.utc)).timestamp()),
    }, separators=(",", ":")),
    ex=_TTL_SECONDS,
  )
  return True


async def _load_message(client, key: str) -> dict | None:
  raw = await client.get(key)
  if not raw:
    return None
  try:
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    chat_id = int(payload["chat_id"])
    message_id = int(payload["message_id"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  return {"chat_id": chat_id, "message_id": message_id} if message_id > 0 else None
