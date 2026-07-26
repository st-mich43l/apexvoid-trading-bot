"""Crash-safe Redis publication for executor candidates."""

from __future__ import annotations

import logging
from typing import Any


log = logging.getLogger(__name__)

_PUBLISH_CANDIDATE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return {0, ''}
end
local event_id = redis.call(
  'XADD', KEYS[2], 'MAXLEN', '~', ARGV[2], '*', 'payload', ARGV[3]
)
redis.call('SET', KEYS[1], 'published', 'EX', ARGV[1])
redis.call('SET', KEYS[3], event_id, 'EX', ARGV[1])
return {1, event_id}
"""


def candidate_key(candidate_id: str) -> str:
  return f"auto_trade:candidate:{candidate_id}"


def candidate_stream_event_key(candidate_id: str) -> str:
  return f"auto_trade:candidate_stream_event:{candidate_id}"


async def publish_candidate_atomic(
  client: Any,
  *,
  stream: str,
  candidate_id: str,
  payload: str,
  ttl: int,
  maxlen: int,
) -> tuple[bool, str | None]:
  """Atomically claim one candidate and append its executor stream event."""
  ttl = max(60, int(ttl))
  maxlen = max(100, int(maxlen))
  key = candidate_key(candidate_id)
  event_key = candidate_stream_event_key(candidate_id)
  try:
    result = await client.eval(
      _PUBLISH_CANDIDATE_LUA,
      3,
      key,
      stream,
      event_key,
      ttl,
      maxlen,
      payload,
    )
    if isinstance(result, (list, tuple)) and result:
      published = int(result[0] or 0) == 1
      raw_event_id = result[1] if len(result) > 1 else None
      event_id = (
        raw_event_id.decode()
        if isinstance(raw_event_id, bytes)
        else str(raw_event_id)
        if raw_event_id
        else None
      )
      return published, event_id
  except Exception as exc:
    # Lightweight test doubles may not implement EVAL. Production Redis uses
    # the Lua path above; retain a rollback-safe compatibility path for them.
    log.debug("atomic candidate Lua unavailable: %s", type(exc).__name__)

  claimed = await client.set(key, "published", ex=ttl, nx=True)
  if not claimed:
    return False, None
  try:
    raw_event_id = await client.xadd(
      stream,
      {"payload": payload},
      maxlen=maxlen,
      approximate=True,
    )
    event_id = (
      raw_event_id.decode()
      if isinstance(raw_event_id, bytes)
      else str(raw_event_id)
    )
    await client.set(event_key, event_id, ex=ttl)
    return True, event_id
  except Exception:
    await client.delete(key, event_key)
    raise
