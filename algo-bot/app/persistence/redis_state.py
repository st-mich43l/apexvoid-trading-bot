"""Redis-backed watcher state — TP/SL progress and the per-symbol bar cursor.

Kept out of Postgres on purpose: this is transient, high-churn polling state,
not trade accounting. It survives a bot restart so alerts are not replayed and
sequential TP progress is not lost. The shared client mirrors the lazy-singleton
pattern used for the asyncpg pool in ``store.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import runtime_config

log = logging.getLogger(__name__)

# Progress keys outlive an open trade comfortably, then self-expire so closed
# signals do not accumulate forever.
_KEY_PREFIX = "watcher"
_PROGRESS_TTL = 30 * 24 * 3600
_CURSOR_TTL = 2 * 24 * 3600

_client: redis.Redis | None = None

_REDIS_RETRY_ERRORS = (RedisConnectionError, RedisTimeoutError, OSError, TimeoutError)
_COMPONENT_HEALTH_TTL = 24 * 3600
_STABLE_RUNTIME_RESET_SECONDS = 60.0


def _build_client() -> redis.Redis:
  return redis.Redis.from_url(
    runtime_config.bootstrap.redis.url,
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


def is_transient_redis_error(exc: BaseException) -> bool:
  """True for Redis connection/DNS failures that justify a client reset.

  Intentionally narrow: bare ``timeout`` in an unrelated exception message
  (broker fill, statement lock, schema validation) must NOT be treated as a
  Redis blip — those stay fatal so the supervisor does not poison siblings.
  """
  if isinstance(exc, (RedisConnectionError, RedisTimeoutError)):
    return True
  cause = getattr(exc, "__cause__", None)
  if isinstance(cause, (RedisConnectionError, RedisTimeoutError)):
    return True
  # OSError/TimeoutError only when the message is clearly a Redis transport
  # failure (DNS/connect), not a generic application timeout.
  if isinstance(exc, (OSError, TimeoutError)) or (
    cause is not None and isinstance(cause, (OSError, TimeoutError))
  ):
    text = str(exc).casefold()
    if cause is not None:
      text = f"{text} {str(cause).casefold()}"
    redis_transport = (
      "name or service not known",
      "connection refused",
      "connection reset",
      "connection closed",
      "server closed the connection",
      "error -2 connecting to redis",
      "connecting to redis",
    )
    if any(marker in text for marker in redis_transport):
      return True
  return False


def component_health_key(name: str) -> str:
  return f"auto_trade:component_health:{name}"


async def publish_component_health(
  *,
  component: str,
  state: str,
  error: str | None = None,
  retry_count: int = 0,
) -> None:
  """Persist per-loop health for /algo_status (ready|degraded_retrying|fatal).

  ``fatal`` is written without TTL so it cannot silently expire; ready and
  degraded_retrying keep a rolling TTL.
  """
  payload = {
    "component": component,
    "state": state,
    "retry_count": int(retry_count),
    "error": (error or "")[:500] or None,
    "updated_at": int(time.time()),
  }
  try:
    key = component_health_key(component)
    body = json.dumps(payload, separators=(",", ":"))
    if state == "fatal":
      await get_client().set(key, body)
    else:
      await get_client().set(key, body, ex=_COMPONENT_HEALTH_TTL)
  except Exception:
    log.exception("component health publish failed component=%s", component)


_FATAL_ALERT_DEDUP_PREFIX = "auto_trade:component_fatal_alert:"


async def _alert_owner_component_fatal(
  *,
  component: str,
  error: str,
) -> None:
  """DM the owner once per component when a supervised loop goes fatal."""
  owner_id = runtime_config.delivery.telegram.telegram_owner_id
  if not owner_id:
    return
  client = get_client()
  dedup_key = f"{_FATAL_ALERT_DEDUP_PREFIX}{component}"
  try:
    claimed = await client.set(dedup_key, "1", nx=True)
  except Exception:
    log.exception("fatal alert dedup failed component=%s", component)
    claimed = True
  if not claimed:
    return
  text = (
    f"⚠️ <b>Algo component fatal</b>\n"
    f"<code>{component}</code>\n"
    f"{(error or 'unknown')[:400]}"
  )
  try:
    from app.bot.client import send_scanner_with_retry

    await send_scanner_with_retry(text, chat_id=int(owner_id))
  except Exception:
    log.exception("fatal owner alert failed component=%s", component)


async def load_component_health(component: str) -> dict | None:
  raw = await get_client().get(component_health_key(component))
  if not raw:
    return None
  try:
    payload = json.loads(raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  return payload if isinstance(payload, dict) else None


async def list_fatal_components() -> list[dict]:
  """Return component health payloads currently marked fatal."""
  client = get_client()
  out: list[dict] = []
  async for key in client.scan_iter(match="auto_trade:component_health:*"):
    raw = await client.get(key)
    if not raw:
      continue
    try:
      payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
      continue
    if isinstance(payload, dict) and payload.get("state") == "fatal":
      out.append(payload)
  return out


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
        f"(url={runtime_config.bootstrap.redis.url})"
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
  stable_runtime_reset_seconds: float = _STABLE_RUNTIME_RESET_SECONDS,
) -> None:
  """Supervise a background loop: Redis blips retry; programming bugs are fatal.

  Transient Redis failures reset the shared client and retry with backoff.
  Backoff resets after a stable run longer than ``stable_runtime_reset_seconds``.
  Any other exception publishes ``fatal`` component health and re-raises so the
  task dies instead of infinitely restarting and poisoning sibling loops.
  Clean returns (feature disabled) are not restarted.
  """
  backoff = 1.0
  retry_count = 0
  while True:
    started = time.monotonic()
    try:
      await publish_component_health(
        component=name, state="ready", retry_count=0,
      )
      await factory()
      return
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      runtime = time.monotonic() - started
      if is_transient_redis_error(exc):
        if runtime >= stable_runtime_reset_seconds:
          backoff = 1.0
          retry_count = 0
        retry_count += 1
        log.exception(
          "%s redis transient failure; resetting client, retry in %.1fs "
          "(attempt %d, ran %.1fs)",
          name,
          backoff,
          retry_count,
          runtime,
        )
        await publish_component_health(
          component=name,
          state="degraded_retrying",
          error=str(exc),
          retry_count=retry_count,
        )
        await reset_client()
        await asyncio.sleep(backoff)
        backoff = min(max_backoff_seconds, backoff * 2)
        continue
      log.exception(
        "%s fatal non-redis failure; stopping component (ran %.1fs)",
        name,
        runtime,
      )
      await publish_component_health(
        component=name,
        state="fatal",
        error=str(exc),
        retry_count=retry_count,
      )
      await _alert_owner_component_fatal(component=name, error=str(exc))
      raise


def _cursor_key(symbol: str) -> str:
  return f"{_KEY_PREFIX}:cursor:{symbol.upper()}"


def _progress_key(row_id: int) -> str:
  return f"{_KEY_PREFIX}:progress:{row_id}"


def _tp_ordinals_key(row_id: int) -> str:
  return f"{_KEY_PREFIX}:tp_ordinals:{row_id}"


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
  """Advance the highest alerted TP (monotonic — never moves backwards).

  Correct for a single-ladder signal (one position climbing TP1, TP2, ...
  in order - the watcher's own use case). A manual /algo signal can be
  several independent entry legs with disjoint ordinal ranges (e.g.
  shallow booking TP3/TP4 while a sibling leg's own TP1/TP2 arrive later
  or out of order) - "highest ever seen" incorrectly treats a legitimate,
  never-before-booked lower ordinal from a DIFFERENT leg as stale. See
  tp_ordinal_already_booked/mark_tp_ordinal_booked for the per-ordinal
  dedup that path needs instead.
  """
  client = _get_client()
  key = _progress_key(row_id)
  current = int(await client.hget(key, "tp") or 0)
  if tp_number > current:
    await client.hset(key, "tp", tp_number)
    await client.expire(key, _PROGRESS_TTL)


async def tp_ordinal_already_booked(row_id: int, ordinal: int) -> bool:
  """True if this specific TP ordinal was already booked for this signal.

  Unlike set_tp_progress's single "highest ever" scalar, this tracks the
  full set of booked ordinals so a multi-leg manual /algo signal's
  siblings can each book their own, disjoint ordinals in any order.
  """
  return bool(
    await _get_client().sismember(_tp_ordinals_key(row_id), ordinal)
  )


async def mark_tp_ordinal_booked(row_id: int, ordinal: int) -> None:
  client = _get_client()
  key = _tp_ordinals_key(row_id)
  await client.sadd(key, ordinal)
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
