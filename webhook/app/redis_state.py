"""Redis-backed watcher state — TP/SL progress and the per-symbol bar cursor.

Kept out of Postgres on purpose: this is transient, high-churn polling state,
not trade accounting. It survives a bot restart so alerts are not replayed and
sequential TP progress is not lost. The shared client mirrors the lazy-singleton
pattern used for the asyncpg pool in ``dedup.py``.
"""

import logging

import redis.asyncio as redis

from app.config import settings

log = logging.getLogger(__name__)

# Progress keys outlive an open trade comfortably, then self-expire so closed
# signals do not accumulate forever.
_PROGRESS_TTL = 30 * 24 * 3600
_CURSOR_TTL = 2 * 24 * 3600

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
  global _client
  if _client is None:
    _client = redis.Redis.from_url(
      settings.redis_url,
      decode_responses=True,
    )
  return _client


async def close_client() -> None:
  """Close the shared client. Used on shutdown and between tests."""
  global _client
  if _client is not None:
    await _client.aclose()
    _client = None


def _cursor_key(symbol: str) -> str:
  return f"watch:cursor:{symbol.upper()}"


def _progress_key(row_id: int) -> str:
  return f"watch:progress:{row_id}"


async def get_cursor(symbol: str) -> str | None:
  """ISO timestamp of the last OHLC bar already evaluated for ``symbol``."""
  return await _get_client().get(_cursor_key(symbol))


async def set_cursor(symbol: str, iso_ts: str) -> None:
  await _get_client().set(_cursor_key(symbol), iso_ts, ex=_CURSOR_TTL)


async def get_progress(row_id: int) -> dict:
  """Return ``{"tp": int, "sl": bool}`` — highest TP alerted and SL state."""
  raw = await _get_client().hgetall(_progress_key(row_id))
  return {
    "tp": int(raw.get("tp", 0)),
    "sl": raw.get("sl") == "1",
  }


async def set_tp_progress(row_id: int, tp_number: int) -> None:
  """Advance the highest alerted TP (monotonic — never moves backwards)."""
  client = _get_client()
  key = _progress_key(row_id)
  current = int(await client.hget(key, "tp") or 0)
  if tp_number > current:
    await client.hset(key, "tp", tp_number)
    await client.expire(key, _PROGRESS_TTL)


async def set_sl_flag(row_id: int) -> None:
  key = _progress_key(row_id)
  client = _get_client()
  await client.hset(key, "sl", "1")
  await client.expire(key, _PROGRESS_TTL)


async def clear_sl_flag(row_id: int) -> None:
  """Allow an updated stop-loss level to produce a fresh alert."""
  await _get_client().hset(_progress_key(row_id), "sl", "0")
