"""Crash-safe Redis publication and ownership for executor candidates."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import secrets
import time
from typing import Any, Iterator

from app.autotrade.arbitration import CandidatePublicationResult, ExecutionIntent
from app.autotrade.candidate_execution_state import published_candidate_record
from app.core.config import runtime_config, settings


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
local published = '{"candidate_id":"' .. ARGV[13] .. '","stream_event_id":"'
  .. event_id .. '","state":"published","lease_token":null,'
  .. '"lease_expires_at":null,"outcome":null,"updated_at":'
  .. ARGV[14] .. ',"version":1}'
redis.call('SET', KEYS[1], published, 'EX', ARGV[1])
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

_COMPARE_AND_DELETE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

_RECONCILE_PUBLICATION_LUA = """
local known_states = {
  published = true,
  processing = true,
  broker_submitting = true,
  ordered = true,
  completed = true,
  rejected = true,
  retryable_error = true,
  broker_outcome_unknown = true,
  broker_reconciling = true,
  dry_run = true,
  flip_pending = true,
  integrity_error = true,
}

local function candidate_compatible(raw, candidate_id, event_id)
  if raw == false then
    return true
  end
  if raw == 'published' or raw == 'processing' or raw == 'dry_run' then
    return true
  end
  if string.sub(raw, 1, 8) == 'ordered:' then
    return true
  end
  if string.sub(raw, 1, 9) == 'rejected:' then
    return true
  end
  if string.sub(raw, 1, 13) == 'flip_pending:' then
    return true
  end
  if string.sub(raw, 1, 1) ~= '{' then
    -- An unrecognised legacy marker is never assumed to be retryable.
    return false
  end
  -- Structured records are decoded, never pattern-matched: an id or error
  -- string carrying JSON punctuation must not be able to fake a field.
  local ok, record = pcall(cjson.decode, raw)
  if not ok or type(record) ~= 'table' then
    return false
  end
  local current_candidate = record['candidate_id']
  local current_event = record['stream_event_id']
  local current_state = record['state']
  -- Structured records must explicitly carry a supported version: a missing
  -- or malformed version is never interpreted as version 1.
  local version = tonumber(record['version'])
  if version == nil or version ~= 1 then
    return false
  end
  -- Structured records require exact identity. Missing fields are a conflict.
  if type(current_candidate) ~= 'string' or current_candidate == '' then
    return false
  end
  if type(current_event) ~= 'string' or current_event == '' then
    return false
  end
  if current_candidate ~= candidate_id then
    return false
  end
  -- `pending` is the mid-publish placeholder written before XADD assigns the
  -- durable stream event id. It is non-empty identity, not a missing field.
  if current_event ~= 'pending' and event_id ~= '' and current_event ~= event_id then
    return false
  end
  return type(current_state) == 'string' and known_states[current_state] == true
end

local function validate_claim(key, expected, value, actual)
  if key == '' then
    return true
  end
  if actual == false then
    return true
  end
  if actual == expected or actual == value then
    return true
  end
  return false
end

local candidate_id = ARGV[12]
local event_id = ARGV[2]
local published = ARGV[13]

local candidate_current = redis.call('GET', KEYS[1])
if not candidate_compatible(candidate_current, candidate_id, event_id) then
  return 2
end
if not validate_claim(KEYS[2], event_id, event_id, redis.call('GET', KEYS[2])) then
  return 2
end
if not validate_claim(KEYS[3], ARGV[3], ARGV[4], redis.call('GET', KEYS[3])) then
  return 2
end
if not validate_claim(KEYS[4], ARGV[6], ARGV[7], redis.call('GET', KEYS[4])) then
  return 2
end
if not validate_claim(KEYS[5], ARGV[9], ARGV[10], redis.call('GET', KEYS[5])) then
  return 2
end

local function restore_missing(key, value, ttl)
  if key == '' then
    return true
  end
  if redis.call('GET', key) ~= false then
    return true
  end
  if tonumber(ttl) > 0 then
    redis.call('SET', key, value, 'EX', ttl)
  else
    redis.call('SET', key, value)
  end
  return true
end

if candidate_current == false then
  redis.call('SET', KEYS[1], published, 'EX', ARGV[1])
end
restore_missing(KEYS[2], event_id, ARGV[1])
restore_missing(KEYS[3], ARGV[4], ARGV[5])
restore_missing(KEYS[4], ARGV[7], ARGV[8])
restore_missing(KEYS[5], ARGV[10], ARGV[11])
return 1
"""


@dataclass(frozen=True)
class PublicationOwnership:
  """Claims required to reconstruct one authoritative stream publication."""

  candidate_id: str
  candidate_ttl: int
  reaction_key: str = ""
  reaction_payload: str = ""
  expected_reaction_payload: str = ""
  reaction_ttl: int = 0
  thesis_key: str = ""
  thesis_payload: str = ""
  expected_thesis_payload: str = ""
  thesis_ttl: int = 0
  ownership_key: str = ""
  ownership_payload: str = ""
  expected_ownership_payload: str = ""
  ownership_ttl: int = 0

  def to_dict(self) -> dict[str, Any]:
    return {
      "candidate_id": self.candidate_id,
      "candidate_ttl": self.candidate_ttl,
      "reaction_key": self.reaction_key,
      "reaction_payload": self.reaction_payload,
      "expected_reaction_payload": self.expected_reaction_payload,
      "reaction_ttl": self.reaction_ttl,
      "thesis_key": self.thesis_key,
      "thesis_payload": self.thesis_payload,
      "expected_thesis_payload": self.expected_thesis_payload,
      "thesis_ttl": self.thesis_ttl,
      "ownership_key": self.ownership_key,
      "ownership_payload": self.ownership_payload,
      "expected_ownership_payload": self.expected_ownership_payload,
      "ownership_ttl": self.ownership_ttl,
    }


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


def _payload_with_publication_ownership(
  payload: str,
  ownership: PublicationOwnership,
) -> str:
  """Embed recovery inputs in the authoritative stream event."""
  try:
    parsed = json.loads(payload)
  except (TypeError, ValueError, json.JSONDecodeError):
    parsed = {"raw_payload": payload}
  if not isinstance(parsed, dict):
    parsed = {"candidate_payload": parsed}
  parsed["_publication_ownership"] = ownership.to_dict()
  return json.dumps(parsed, separators=(",", ":"), sort_keys=True)


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
  recovery = PublicationOwnership(
    candidate_id=candidate_id,
    candidate_ttl=ttl,
    reaction_key=reaction_key,
    reaction_payload=reaction_payload,
    expected_reaction_payload=expected_reaction_payload,
    reaction_ttl=int(reaction_ttl),
    thesis_key=thesis_key,
    thesis_payload=thesis_payload,
    expected_thesis_payload=expected_thesis_payload,
    thesis_ttl=int(thesis_ttl),
    ownership_key=ownership_key,
    ownership_payload=ownership_payload,
    expected_ownership_payload=expected_ownership_payload,
    ownership_ttl=int(ownership_ttl),
  )
  payload = _payload_with_publication_ownership(payload, recovery)
  updated_at = int(time.time())
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
      candidate_id,
      updated_at,
    )
    if isinstance(result, (list, tuple)) and result:
      try:
        code = int(result[0] or 0)
      except (TypeError, ValueError):
        recovered = await reconcile_candidate_publication(
          client,
          stream=stream,
          candidate_id=candidate_id,
        )
        if recovered.published:
          await _mark_atomic_readiness(
            client,
            ready=True,
            reason_code="atomic_publication_reconciled",
          )
          return recovered
        await _mark_atomic_readiness(
          client,
          ready=False,
          reason_code="atomic_publish_invalid_response",
        )
        return AtomicPublishResult(
          False, "atomic_publish_unavailable", None,
        )
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
      recovered = await reconcile_candidate_publication(
        client,
        stream=stream,
        candidate_id=candidate_id,
      )
      if recovered.published:
        await _mark_atomic_readiness(
          client,
          ready=True,
          reason_code="atomic_publication_reconciled",
        )
        return recovered
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
  claimed = await client.set(
    key,
    published_candidate_record(
      candidate_id=candidate_id,
      stream_event_id="pending",
      updated_at=int(time.time()),
    ),
    ex=ttl,
    nx=True,
  )
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
    await client.set(
      key,
      published_candidate_record(
        candidate_id=candidate_id,
        stream_event_id=event_id,
        updated_at=int(time.time()),
      ),
      ex=ttl,
    )
    await client.set(event_key, event_id, ex=ttl)
    return AtomicPublishResult(True, "published", event_id)
  except Exception:
    await client.delete(key, event_key)
    await rollback_claims()
    raise


async def reconcile_candidate_publication(
  client: Any,
  *,
  stream: str,
  event_id: str | None = None,
  candidate_id: str | None = None,
) -> AtomicPublishResult:
  """Rebuild markers/claims from one authoritative stream event.

  The function never calls XADD. It exists because Redis Lua is isolated,
  not transactional with rollback: a runtime error after XADD can leave a
  durable stream event without its secondary indexes.
  """
  if event_id is not None:
    try:
      entries = await client.xrange(stream, min=event_id, max=event_id)
    except TypeError:
      entries = await client.xrange(stream, event_id, event_id)
  else:
    try:
      recent = await client.xrevrange(stream, count=500)
    except Exception:
      recent = []
    entries = []
    for candidate_event_id, fields in recent:
      raw = fields.get("payload") or fields.get(b"payload")
      if isinstance(raw, bytes):
        raw = raw.decode()
      try:
        body = json.loads(str(raw))
      except (TypeError, ValueError, json.JSONDecodeError):
        continue
      if str(body.get("candidate_id") or "") == str(candidate_id or ""):
        event_id = (
          candidate_event_id.decode()
          if isinstance(candidate_event_id, bytes)
          else str(candidate_event_id)
        )
        entries = [(candidate_event_id, fields)]
        break
  if not entries:
    return AtomicPublishResult(False, "orphan_event_missing", None)
  assert event_id is not None
  _, fields = entries[0]
  raw_payload = fields.get("payload") or fields.get(b"payload")
  if isinstance(raw_payload, bytes):
    raw_payload = raw_payload.decode()
  try:
    payload = json.loads(str(raw_payload))
    raw_ownership = payload["_publication_ownership"]
    ownership = PublicationOwnership(
      candidate_id=str(raw_ownership["candidate_id"]),
      candidate_ttl=max(60, int(raw_ownership["candidate_ttl"])),
      reaction_key=str(raw_ownership.get("reaction_key") or ""),
      reaction_payload=str(raw_ownership.get("reaction_payload") or ""),
      expected_reaction_payload=str(
        raw_ownership.get("expected_reaction_payload") or ""
      ),
      reaction_ttl=int(raw_ownership.get("reaction_ttl") or 0),
      thesis_key=str(raw_ownership.get("thesis_key") or ""),
      thesis_payload=str(raw_ownership.get("thesis_payload") or ""),
      expected_thesis_payload=str(
        raw_ownership.get("expected_thesis_payload") or ""
      ),
      thesis_ttl=int(raw_ownership.get("thesis_ttl") or 0),
      ownership_key=str(raw_ownership.get("ownership_key") or ""),
      ownership_payload=str(raw_ownership.get("ownership_payload") or ""),
      expected_ownership_payload=str(
        raw_ownership.get("expected_ownership_payload") or ""
      ),
      ownership_ttl=int(raw_ownership.get("ownership_ttl") or 0),
    )
  except (
    KeyError, TypeError, ValueError, json.JSONDecodeError,
  ):
    return AtomicPublishResult(False, "orphan_metadata_invalid", event_id)
  try:
    result = await client.eval(
      _RECONCILE_PUBLICATION_LUA,
      5,
      candidate_key(ownership.candidate_id),
      candidate_stream_event_key(ownership.candidate_id),
      ownership.reaction_key,
      ownership.thesis_key,
      ownership.ownership_key,
      ownership.candidate_ttl,
      event_id,
      ownership.expected_reaction_payload,
      ownership.reaction_payload,
      ownership.reaction_ttl,
      ownership.expected_thesis_payload,
      ownership.thesis_payload,
      ownership.thesis_ttl,
      ownership.expected_ownership_payload,
      ownership.ownership_payload,
      ownership.ownership_ttl,
      ownership.candidate_id,
      published_candidate_record(
        candidate_id=ownership.candidate_id,
        stream_event_id=event_id,
        updated_at=int(time.time()),
      ),
    )
  except Exception:
    await _mark_atomic_readiness(
      client,
      ready=False,
      reason_code="publication_reconciliation_unavailable",
    )
    return AtomicPublishResult(
      False, "publication_reconciliation_unavailable", event_id,
    )
  if int(result or 0) != 1:
    return AtomicPublishResult(False, "reconciliation_conflict", event_id)
  return AtomicPublishResult(True, "reconciled", event_id)


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
            86400, runtime_config.lifecycle.candidate.storage_ttl_seconds,
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
