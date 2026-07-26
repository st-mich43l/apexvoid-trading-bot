"""Crash-safe Redis publication and ownership for executor candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Iterator


log = logging.getLogger(__name__)

_PUBLISH_CANDIDATE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return {2, ''}
end

local function claim_available(key, expected)
  if key == '' then
    return true
  end
  local current = redis.call('GET', key)
  if expected == '' then
    return current == false
  end
  return current == expected
end

if not claim_available(KEYS[4], ARGV[4]) then
  return {3, ''}
end
if not claim_available(KEYS[5], ARGV[6]) then
  return {4, ''}
end
if not claim_available(KEYS[6], ARGV[8]) then
  return {5, ''}
end

local event_id = redis.call(
  'XADD', KEYS[2], 'MAXLEN', '~', ARGV[2], '*', 'payload', ARGV[3]
)
redis.call('SET', KEYS[1], 'published', 'EX', ARGV[1])
redis.call('SET', KEYS[3], event_id, 'EX', ARGV[1])
if KEYS[4] ~= '' then
  if tonumber(ARGV[10]) > 0 then
    redis.call('SET', KEYS[4], ARGV[5], 'EX', ARGV[10])
  else
    redis.call('SET', KEYS[4], ARGV[5])
  end
end
if KEYS[5] ~= '' then
  if tonumber(ARGV[11]) > 0 then
    redis.call('SET', KEYS[5], ARGV[7], 'EX', ARGV[11])
  else
    redis.call('SET', KEYS[5], ARGV[7])
  end
end
if KEYS[6] ~= '' then
  if tonumber(ARGV[12]) > 0 then
    redis.call('SET', KEYS[6], ARGV[9], 'EX', ARGV[12])
  else
    redis.call('SET', KEYS[6], ARGV[9])
  end
end
return {1, event_id}
"""


@dataclass(frozen=True)
class AtomicPublishResult:
  published: bool
  status: str
  event_id: str | None = None

  def __iter__(self) -> Iterator[object]:
    """Keep the historical ``published, event_id =`` call contract."""
    yield self.published
    yield self.event_id


_RESULT_STATUS = {
  1: "published",
  2: "duplicate_candidate",
  3: "duplicate_reaction",
  4: "duplicate_thesis",
  5: "conflict",
}


def candidate_key(candidate_id: str) -> str:
  return f"auto_trade:candidate:{candidate_id}"


def candidate_stream_event_key(candidate_id: str) -> str:
  return f"auto_trade:candidate_stream_event:{candidate_id}"


def autonomous_cycle_owner_key(symbol: str, cycle_id: str) -> str:
  return f"auto_trade:cycle_owner:{symbol.upper()}:{cycle_id}"


def explicit_test_fallback_enabled(client: Any) -> bool:
  """True only when a test fixture explicitly marks its Redis double."""
  return bool(
    getattr(client, "_apexvoid_allow_non_atomic_test_fallback", False)
  )


async def _mark_atomic_readiness(
  client: Any,
  *,
  ready: bool,
  reason_code: str,
) -> None:
  try:
    await client.set(
      "auto_trade:publication_readiness",
      json.dumps({
        "ready": ready,
        "reason_code": reason_code,
      }, separators=(",", ":"), sort_keys=True),
      ex=300,
    )
  except Exception:
    log.exception("unable to persist atomic publication readiness")


async def publish_candidate_atomic(
  client: Any,
  *,
  stream: str,
  candidate_id: str,
  payload: str,
  ttl: int,
  maxlen: int,
  reaction_key: str | None = None,
  reaction_payload: str | None = None,
  expected_reaction_payload: str | None = None,
  reaction_ttl: int = 0,
  thesis_key: str | None = None,
  thesis_payload: str | None = None,
  expected_thesis_payload: str | None = None,
  thesis_ttl: int = 0,
  ownership_key: str | None = None,
  ownership_payload: str | None = None,
  expected_ownership_payload: str | None = None,
  ownership_ttl: int = 0,
  allow_non_atomic_test_fallback: bool = False,
) -> AtomicPublishResult:
  """Atomically own candidate/reaction/thesis and append one stream event.

  Production fails closed when Redis scripting is unavailable. The fallback
  exists only for explicitly opted-in test doubles.
  """
  allow_non_atomic_test_fallback = bool(
    allow_non_atomic_test_fallback or explicit_test_fallback_enabled(client)
  )
  ttl = max(60, int(ttl))
  maxlen = max(100, int(maxlen))
  key = candidate_key(candidate_id)
  event_key = candidate_stream_event_key(candidate_id)
  reaction_key = reaction_key or ""
  reaction_payload = reaction_payload or ""
  expected_reaction_payload = expected_reaction_payload or ""
  thesis_key = thesis_key or ""
  thesis_payload = thesis_payload or ""
  expected_thesis_payload = expected_thesis_payload or ""
  ownership_key = ownership_key or ""
  ownership_payload = ownership_payload or ""
  expected_ownership_payload = expected_ownership_payload or ""
  try:
    result = await client.eval(
      _PUBLISH_CANDIDATE_LUA,
      6,
      key,
      stream,
      event_key,
      reaction_key,
      thesis_key,
      ownership_key,
      ttl,
      maxlen,
      payload,
      expected_reaction_payload,
      reaction_payload,
      expected_thesis_payload,
      thesis_payload,
      expected_ownership_payload,
      ownership_payload,
      int(reaction_ttl),
      int(thesis_ttl),
      int(ownership_ttl),
    )
    if isinstance(result, (list, tuple)) and result:
      code = int(result[0] or 0)
      raw_event_id = result[1] if len(result) > 1 else None
      event_id = (
        raw_event_id.decode()
        if isinstance(raw_event_id, bytes)
        else str(raw_event_id)
        if raw_event_id
        else None
      )
      status = _RESULT_STATUS.get(code, "conflict")
      await _mark_atomic_readiness(
        client,
        ready=True,
        reason_code="atomic_publication_ready",
      )
      return AtomicPublishResult(code == 1, status, event_id)
    await _mark_atomic_readiness(
      client,
      ready=False,
      reason_code="atomic_publish_invalid_response",
    )
    return AtomicPublishResult(
      False, "atomic_publish_unavailable", None,
    )
  except Exception as exc:
    if not allow_non_atomic_test_fallback:
      log.error(
        "atomic candidate publication unavailable: %s",
        type(exc).__name__,
      )
      await _mark_atomic_readiness(
        client,
        ready=False,
        reason_code="atomic_publish_unavailable",
      )
      return AtomicPublishResult(
        False, "atomic_publish_unavailable", None,
      )
    log.debug(
      "using explicit non-atomic test fallback: %s",
      type(exc).__name__,
    )

  # Test-only compatibility path. It remains rollback-safe, but it is not
  # reachable from production because the opt-in defaults to false.
  claimed_keys: list[tuple[str, str | None, int]] = []

  async def rollback_claims() -> None:
    for claimed_key, prior, claimed_ttl in reversed(claimed_keys):
      if prior is None:
        await client.delete(claimed_key)
      else:
        kwargs = {"ex": claimed_ttl} if claimed_ttl > 0 else {}
        await client.set(claimed_key, prior, **kwargs)

  for claim_key, expected, body, claim_ttl, conflict_status in (
    (
      reaction_key,
      expected_reaction_payload,
      reaction_payload,
      reaction_ttl,
      "duplicate_reaction",
    ),
    (
      thesis_key,
      expected_thesis_payload,
      thesis_payload,
      thesis_ttl,
      "duplicate_thesis",
    ),
    (
      ownership_key,
      expected_ownership_payload,
      ownership_payload,
      ownership_ttl,
      "conflict",
    ),
  ):
    if not claim_key:
      continue
    current = await client.get(claim_key)
    prior_ttl = await client.ttl(claim_key) if current is not None else -2
    normalized = (
      current.decode() if isinstance(current, bytes) else current
    )
    if (
      (expected and normalized != expected)
      or (not expected and current is not None)
    ):
      await rollback_claims()
      return AtomicPublishResult(False, conflict_status, None)
    kwargs = {"ex": int(claim_ttl)} if int(claim_ttl) > 0 else {}
    if expected:
      await client.set(claim_key, body, **kwargs)
    else:
      ok = await client.set(claim_key, body, nx=True, **kwargs)
      if not ok:
        await rollback_claims()
        return AtomicPublishResult(False, conflict_status, None)
    claimed_keys.append((claim_key, normalized, int(prior_ttl)))
  claimed = await client.set(key, "published", ex=ttl, nx=True)
  if not claimed:
    await rollback_claims()
    return AtomicPublishResult(False, "duplicate_candidate", None)
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
    return AtomicPublishResult(True, "published", event_id)
  except Exception:
    await client.delete(key, event_key)
    await rollback_claims()
    raise
