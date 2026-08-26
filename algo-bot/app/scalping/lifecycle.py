"""Scalp opportunity lifecycle transitions."""

from __future__ import annotations

from typing import Any

from app.scalping.models import (
  ACTIVE_STATES,
  ARMED,
  CANCELLED,
  COMPLETED,
  DISCOVERED,
  EXECUTABLE,
  EXPIRED,
  INVALIDATED,
  MISSED,
  PUBLISHED,
  RETEST_WAIT,
  ScalpLifecycleRecord,
  TERMINAL_STATES,
  TOUCHED,
  TRIGGERED,
)


_ALLOWED: dict[str, frozenset[str]] = {
  DISCOVERED: frozenset({ARMED, INVALIDATED, EXPIRED, CANCELLED}),
  ARMED: frozenset({TOUCHED, INVALIDATED, EXPIRED, CANCELLED, MISSED}),
  TOUCHED: frozenset({TRIGGERED, RETEST_WAIT, INVALIDATED, EXPIRED, MISSED, CANCELLED}),
  TRIGGERED: frozenset({EXECUTABLE, RETEST_WAIT, INVALIDATED, EXPIRED, MISSED, CANCELLED}),
  RETEST_WAIT: frozenset({TRIGGERED, EXECUTABLE, INVALIDATED, EXPIRED, MISSED, CANCELLED}),
  EXECUTABLE: frozenset({PUBLISHED, INVALIDATED, EXPIRED, MISSED, CANCELLED}),
  PUBLISHED: frozenset({COMPLETED, INVALIDATED, CANCELLED}),
}


def transition(
  record: ScalpLifecycleRecord,
  new_state: str,
  *,
  reason: str,
  now: int,
) -> ScalpLifecycleRecord:
  current = record.state
  if current in TERMINAL_STATES:
    return record
  allowed = _ALLOWED.get(current, frozenset())
  if new_state not in allowed and new_state not in TERMINAL_STATES:
    return ScalpLifecycleRecord(
      opportunity_id=record.opportunity_id,
      episode_id=record.episode_id,
      state=current,
      context_id=record.context_id,
      updated_at=int(now),
      reason_code="invalid_transition",
      measured={**record.measured, "attempted": new_state, "from": current},
    )
  return ScalpLifecycleRecord(
    opportunity_id=record.opportunity_id,
    episode_id=record.episode_id,
    state=new_state,
    context_id=record.context_id,
    updated_at=int(now),
    reason_code=reason,
    measured=dict(record.measured),
  )


def requires_rearm(
  *,
  last_zone_mid: float,
  price: float,
  atr: float,
  rearm_distance_atr: float,
) -> bool:
  if atr <= 0:
    return abs(price - last_zone_mid) > 0
  return abs(price - last_zone_mid) >= float(rearm_distance_atr) * float(atr)


def lifecycle_key(symbol: str, opportunity_id: str) -> str:
  return f"scalp:lifecycle:{symbol.upper()}:{opportunity_id}"


def active_key(symbol: str) -> str:
  return f"scalp:active:{symbol.upper()}"


async def save_lifecycle(client: Any, symbol: str, record: ScalpLifecycleRecord) -> None:
  await client.set(lifecycle_key(symbol, record.opportunity_id), record.to_json())
  if record.state in ACTIVE_STATES:
    await client.sadd(active_key(symbol), record.opportunity_id)
  else:
    await client.srem(active_key(symbol), record.opportunity_id)


async def load_lifecycle(
  client: Any,
  symbol: str,
  opportunity_id: str,
) -> ScalpLifecycleRecord | None:
  raw = await client.get(lifecycle_key(symbol, opportunity_id))
  if raw is None:
    return None
  return ScalpLifecycleRecord.from_json(raw)


# Armed/discovered without fill must not linger and confuse ops / caps.
_STALE_ARMED_STATES = frozenset({DISCOVERED, ARMED})
_DEFAULT_STALE_ARMED_SEC = 15 * 60


async def prune_stale_active(
  client: Any,
  symbol: str,
  *,
  now: int,
  max_age_sec: int = _DEFAULT_STALE_ARMED_SEC,
) -> int:
  """Expire stale armed/discovered entries and drop them from scalp:active."""
  raw_ids = await client.smembers(active_key(symbol))
  if not raw_ids:
    return 0
  pruned = 0
  for token in raw_ids:
    oid = token.decode() if isinstance(token, (bytes, bytearray)) else str(token)
    record = await load_lifecycle(client, symbol, oid)
    if record is None:
      await client.srem(active_key(symbol), oid)
      pruned += 1
      continue
    if record.state not in _STALE_ARMED_STATES:
      continue
    age = int(now) - int(record.updated_at or 0)
    if age < int(max_age_sec):
      continue
    expired = transition(
      record, EXPIRED, reason="stale_armed_expired", now=int(now),
    )
    await save_lifecycle(client, symbol, expired)
    pruned += 1
  return pruned
