"""Redis-backed watcher state — TP/SL progress and the per-symbol bar cursor.

Kept out of Postgres on purpose: this is transient, high-churn polling state,
not trade accounting. It survives a bot restart so alerts are not replayed and
sequential TP progress is not lost. The shared client mirrors the lazy-singleton
pattern used for the asyncpg pool in ``store.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import settings

log = logging.getLogger(__name__)

# Progress keys outlive an open trade comfortably, then self-expire so closed
# signals do not accumulate forever.
_KEY_PREFIX = "watcher"
_PROGRESS_TTL = 30 * 24 * 3600
_CURSOR_TTL = 2 * 24 * 3600

_client: redis.Redis | None = None

_REDIS_RETRY_ERRORS = (RedisConnectionError, RedisTimeoutError, OSError, TimeoutError)


def _build_client() -> redis.Redis:
  return redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=5.0,
    health_check_interval=30,
    retry=Retry(ExponentialBackoff(cap=8, base=0.25), 5),
    retry_on_error=list(_REDIS_RETRY_ERRORS),
  )


def _get_client() -> redis.Redis:
  global _client
  if _client is None:
    _client = _build_client()
  return _client


def get_client() -> redis.Redis:
  """Shared Redis client for transient bot state and bar-feed consumers."""
  return _get_client()


async def close_client() -> None:
  """Close the shared client. Used on shutdown and between tests."""
  global _client
  if _client is not None:
    await _client.aclose()
    _client = None


async def reset_client() -> None:
  """Drop the shared client so the next call opens a fresh connection.

  Needed after Compose recreates Redis: DNS for ``redis`` briefly vanishes and
  pooled sockets point at a dead container. Closing forces re-resolve.
  """
  global _client
  if _client is None:
    return
  client = _client
  _client = None
  try:
    await client.aclose()
  except Exception:
    log.exception("redis client close failed during reset")


async def wait_until_ready(*, timeout_seconds: float = 120.0) -> None:
  """Block until Redis accepts PING, or raise after ``timeout_seconds``."""
  deadline = time.monotonic() + max(1.0, timeout_seconds)
  delay = 0.5
  last_error: BaseException | None = None
  while True:
    try:
      if await get_client().ping():
        log.info("redis ready")
        return
    except Exception as exc:
      last_error = exc
    if time.monotonic() >= deadline:
      raise RuntimeError(
        f"redis not ready within {timeout_seconds:.0f}s "
        f"(url={settings.redis_url})"
      ) from last_error
    log.warning(
      "redis not ready (%s); retrying in %.1fs",
      last_error or "ping returned false",
      delay,
    )
    await reset_client()
    await asyncio.sleep(delay)
    delay = min(10.0, delay * 2)


async def run_supervised(
  name: str,
  factory: Callable[[], Awaitable[None]],
  *,
  max_backoff_seconds: float = 60.0,
) -> None:
  """Run a background coroutine, restarting after Redis/runtime disconnects.

  Fire-and-forget tasks that subscribe to Redis die permanently on a Compose
  recreate blip (``Name or service not known`` / connection closed). This
  supervisor recreates the client and restarts with bounded exponential
  backoff. Clean returns (feature disabled) are not restarted.
  """
  backoff = 1.0
  while True:
    try:
      await factory()
      return
    except asyncio.CancelledError:
      raise
    except Exception:
      log.exception(
        "%s crashed; restarting in %.1fs after redis client reset",
        name,
        backoff,
      )
      await reset_client()
      await asyncio.sleep(backoff)
      backoff = min(max_backoff_seconds, backoff * 2)


def _cursor_key(symbol: str) -> str:
  return f"{_KEY_PREFIX}:cursor:{symbol.upper()}"


def _progress_key(row_id: int) -> str:
  return f"{_KEY_PREFIX}:progress:{row_id}"


async def get_cursor(symbol: str) -> str | None:
  """ISO timestamp of the last OHLC bar already evaluated for ``symbol``."""
  return await _get_client().get(_cursor_key(symbol))


async def set_cursor(symbol: str, iso_ts: str) -> None:
  await _get_client().set(_cursor_key(symbol), iso_ts, ex=_CURSOR_TTL)


async def get_progress(row_id: int) -> dict:
  """Return watcher progress for one signal."""
  raw = await _get_client().hgetall(_progress_key(row_id))
  return {
    "tp": int(raw.get("tp", 0)),
    "sl": raw.get("sl") == "1",
    "runner_pips": int(raw.get("runner_pips", 0)),
  }


async def set_tp_progress(row_id: int, tp_number: int) -> None:
  """Advance the highest alerted TP (monotonic — never moves backwards)."""
  client = _get_client()
  key = _progress_key(row_id)
  current = int(await client.hget(key, "tp") or 0)
  if tp_number > current:
    await client.hset(key, "tp", tp_number)
    await client.expire(key, _PROGRESS_TTL)


async def set_runner_pips(row_id: int, pips: int) -> None:
  """Advance the best post-TP profit alert (monotonic in pips)."""
  client = _get_client()
  key = _progress_key(row_id)
  current = int(await client.hget(key, "runner_pips") or 0)
  if pips > current:
    await client.hset(key, "runner_pips", pips)
    await client.expire(key, _PROGRESS_TTL)


async def clear_sl_alert(row_id: int) -> None:
  """Allow an updated stop-loss level to produce a fresh alert."""
  await clear_sl_flag(row_id)


async def mark_tp_alert(
  row_id: int,
  tp_number: int,
  pips: int | None = None,
) -> None:
  """Prevent a manual TP notification from being repeated by the watcher."""
  await set_tp_progress(row_id, tp_number)
  if pips is not None:
    await set_runner_pips(row_id, pips)


async def set_sl_flag(row_id: int) -> None:
  key = _progress_key(row_id)
  client = _get_client()
  await client.hset(key, "sl", "1")
  await client.expire(key, _PROGRESS_TTL)


async def clear_sl_flag(row_id: int) -> None:
  """Allow an updated stop-loss level to produce a fresh alert."""
  await _get_client().hset(_progress_key(row_id), "sl", "0")
