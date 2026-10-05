"""Closed-bar cycle ownership and ranked TradePlan publication.

One autonomous cycle publishes at most one plan per symbol: a Redis lock whose
token proves release ownership coordinates the ranked fallback, and an owner
record makes a repeated cycle a no-op.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from app.autotrade.arbitration import CandidatePublicationResult, ExecutionIntent
from app.core.config import runtime_config


log = logging.getLogger(__name__)

_COMPARE_AND_DELETE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

def autonomous_cycle_owner_key(symbol: str, cycle_id: str) -> str:
  return f"auto_trade:cycle_owner:{symbol.upper()}:{cycle_id}"


def cycle_route_lock_key(symbol: str, cycle_id: str) -> str:
  return f"auto_trade:cycle_route_lock:{symbol.upper()}:{cycle_id}"


async def acquire_owned_lock(
  client: Any,
  key: str,
  *,
  ttl: int,
) -> str | None:
  """Acquire a Redis lock whose token proves release ownership."""
  token = secrets.token_urlsafe(24)
  acquired = await client.set(key, token, nx=True, ex=max(1, int(ttl)))
  return token if acquired else None


async def release_owned_lock(client: Any, key: str, token: str) -> bool:
  """Release only the lock instance acquired by ``token``.

  If scripting is unavailable, fail closed and let the TTL expire. A plain
  DELETE could remove a successor's lock after this owner's TTL elapsed.
  """
  try:
    result = await client.eval(_COMPARE_AND_DELETE_LUA, 1, key, token)
    return int(result or 0) == 1
  except Exception:
    if not explicit_test_fallback_enabled(client):
      log.exception("unable to compare-and-delete Redis lock %s", key)
      return False
    current = await client.get(key)
    normalized = current.decode() if isinstance(current, bytes) else current
    if normalized != token:
      return False
    await client.delete(key)
    return True


def explicit_test_fallback_enabled(client: Any) -> bool:
  """True only when a test fixture explicitly marks its Redis double."""
  return bool(
    getattr(client, "_apexvoid_allow_non_atomic_test_fallback", False)
  )


async def publish_ranked_cycle(
  client: Any,
  *,
  symbol: str,
  cycle_id: str,
  ordered: tuple[ExecutionIntent, ...],
  publisher: Any,
  lock_ttl: int = 30,
) -> CandidatePublicationResult:
  """Coordinate ranked publication under one closed-bar cycle lock.

  ``publisher`` receives one intent and returns
  :class:`CandidatePublicationResult`. Fallback occurs only after its
  explicit terminal-reject result.
  """
  if not ordered:
    return CandidatePublicationResult.blocked(
      "publication_unavailable", "no_executable_intent",
    )
  owner_key = autonomous_cycle_owner_key(symbol, cycle_id)
  if await client.get(owner_key) is not None:
    return CandidatePublicationResult.blocked("cycle_conflict")
  lock_key = cycle_route_lock_key(symbol, cycle_id)
  token = await acquire_owned_lock(client, lock_key, ttl=lock_ttl)
  if token is None:
    return CandidatePublicationResult.blocked("route_in_progress")
  try:
    # Re-check after acquisition: a publisher may have completed between the
    # optimistic owner read and lock acquisition.
    if await client.get(owner_key) is not None:
      return CandidatePublicationResult.blocked("cycle_conflict")
    last_terminal: CandidatePublicationResult | None = None
    for intent in ordered:
      result = await publisher(intent)
      if result.candidate_id is not None:
        owner = {
          "symbol": symbol.upper(),
          "cycle_id": cycle_id,
          "intent_id": intent.intent_id,
          "setup_id": intent.match_id,
          "plan_id": result.candidate_id,
          "published_at": int(time.time()),
        }
        await client.set(
          owner_key,
          json.dumps(owner, separators=(",", ":"), sort_keys=True),
          ex=max(
            86400, runtime_config.auto_algo.lifecycle.candidate.storage_ttl_seconds,
          ),
          nx=True,
        )
        return result
      if result.blocks_lower_ranked_intents:
        return result
      last_terminal = result
    return last_terminal or CandidatePublicationResult.blocked(
      "publication_unavailable",
    )
  finally:
    await release_owned_lock(client, lock_key, token)
