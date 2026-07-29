"""Setup lifecycle state machine for TradePlan V7 analysis (Phase 2).

This is a distinct state machine from `app/autotrade/lifecycle.py`'s
`LIFECYCLE_STATES`, which tracks V6 execution progress
(order_planned/order_submitted/managing/...). This module tracks the
*analysis* side: whether a structural reaction is still being watched,
forming, confirmed, acknowledged by the worker, or has produced a plan. Per
docs/adr-trade-plan-v7-boundary.md, a CONFIRMED setup must pass through the
durable READY_EVENT_ENQUEUED and WORKER_ACKNOWLEDGED handoff before it can
begin producing a TradePlan. A setup already past CONFIRMED can never
transition back to it - that is what stops "old confirmations creating new
plans repeatedly" and what stops repeated scanner evaluations from emitting
a new FORMING Telegram card every detector cycle (see `transition_setup`'s
``changed`` return value). Scanner-confirmed reaction setups may continue to
PLAN_BUILT when their executable quote is inside the raw entry zone plus
spread tolerance; farther reactions remain armed for a retest. Non-reaction
families retain their required M1 timing contract. The C# executor stays
mechanical.

Storage: `analysis:setup:{setup_id}` (see docs/redis-contract.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import time
from typing import Any, Mapping

# P0-1: business expiry (the setup's own expires_at) must never be conflated
# with Redis key TTL (data retention). Before this, _ttl_for set the key's
# TTL to roughly expires_at-now - once nothing touched the record again
# after the setup silently passed its business expiry, Redis could delete
# the canonical record at almost exactly the moment it should have
# transitioned to EXPIRED, with no terminal lifecycle event, no card
# deletion, and no audit trail. These two constants keep the record
# readable well past both business expiry and terminal transition.
SETUP_AUDIT_RETENTION_SECONDS = 24 * 3600
TERMINAL_SETUP_RETENTION_SECONDS = 24 * 3600


DISCOVERED = "discovered"
WATCHING = "watching"
TOUCHED = "touched"
FORMING = "forming"
CONFIRMED = "confirmed"
READY_EVENT_ENQUEUED = "ready_event_enqueued"
WORKER_ACKNOWLEDGED = "worker_acknowledged"
# Execution timing state: every CONFIRMED setup passes through this node.
# Scanner-authoritative reactions may continue onward in the same worker call;
# retests and non-reaction strategies wait here for their required M1 timing.
ARMED_WAITING_TRIGGER = "armed_waiting_trigger"
PLAN_BUILT = "plan_built"
PLAN_PUBLISHED = "plan_published"
ARMED = "armed"
CANCELLED = "cancelled"
INVALIDATED = "invalidated"
EXPIRED = "expired"
CONSUMED = "consumed"

SETUP_STATES = (
  DISCOVERED,
  WATCHING,
  TOUCHED,
  FORMING,
  CONFIRMED,
  READY_EVENT_ENQUEUED,
  WORKER_ACKNOWLEDGED,
  ARMED_WAITING_TRIGGER,
  PLAN_BUILT,
  PLAN_PUBLISHED,
  ARMED,
  CANCELLED,
  INVALIDATED,
  EXPIRED,
  CONSUMED,
)

# Analysis-only states: a setup here has never produced a TradePlan and
# never directly causes execution. Mirrors the ADR's split between the
# analysis/watchlist flow and the confirmed execution-plan flow.
ANALYSIS_ONLY_STATES = frozenset({DISCOVERED, WATCHING, TOUCHED, FORMING})

TERMINAL_STATES = frozenset({CANCELLED, INVALIDATED, EXPIRED, CONSUMED})

# Only the durable CONFIRMED -> READY_EVENT_ENQUEUED ->
# WORKER_ACKNOWLEDGED -> ARMED_WAITING_TRIGGER chain may lead to PLAN_BUILT.
# Every terminal state has no outgoing edges. Rearming
# (INVALIDATED/EXPIRED -> DISCOVERED) is deliberately not an edge here - it
# requires `rearm_setup` with a documented condition.
_TRANSITIONS: dict[str, frozenset[str]] = {
  DISCOVERED: frozenset({WATCHING, INVALIDATED, EXPIRED}),
  WATCHING: frozenset({TOUCHED, INVALIDATED, EXPIRED}),
  TOUCHED: frozenset({FORMING, WATCHING, INVALIDATED, EXPIRED}),
  FORMING: frozenset({CONFIRMED, WATCHING, INVALIDATED, EXPIRED}),
  CONFIRMED: frozenset({
    READY_EVENT_ENQUEUED,
    WORKER_ACKNOWLEDGED,
    ARMED_WAITING_TRIGGER,
    INVALIDATED,
    EXPIRED,
  }),
  READY_EVENT_ENQUEUED: frozenset({
    WORKER_ACKNOWLEDGED,
    INVALIDATED,
    EXPIRED,
  }),
  WORKER_ACKNOWLEDGED: frozenset({
    ARMED_WAITING_TRIGGER,
    INVALIDATED,
    EXPIRED,
  }),
  ARMED_WAITING_TRIGGER: frozenset({PLAN_BUILT, INVALIDATED, EXPIRED}),
  PLAN_BUILT: frozenset({PLAN_PUBLISHED, CANCELLED}),
  PLAN_PUBLISHED: frozenset({ARMED, CANCELLED, EXPIRED}),
  ARMED: frozenset({CONSUMED, CANCELLED, EXPIRED}),
  CANCELLED: frozenset(),
  INVALIDATED: frozenset(),
  EXPIRED: frozenset(),
  CONSUMED: frozenset(),
}


class SetupLifecycleError(ValueError):
  """An illegal transition or an operation on an unknown setup_id."""


def setup_key(setup_id: str) -> str:
  return f"analysis:setup:{setup_id}"


def active_thesis_key(symbol: str, thesis_id: str) -> str:
  return f"analysis:active_thesis:{symbol}:{thesis_id}"


def watchlist_key() -> str:
  return "analysis:watchlist"


def setup_expiry_index_key() -> str:
  return "analysis:setup_expiry"


@dataclass(frozen=True)
class SetupRecord:
  setup_id: str
  thesis_id: str
  symbol: str
  state: str
  source_structure_id: str | None = None
  formation_timeframe: str | None = None
  confirmation_timeframe: str | None = None
  formation_bar_ts: int | None = None
  confirmation_bar_ts: int | None = None
  last_emitted_card_state: str | None = None
  telegram_message_id: int | None = None
  expires_at: int | None = None
  invalidation_condition: str | None = None
  created_at: int = field(default_factory=lambda: int(time.time()))
  updated_at: int = field(default_factory=lambda: int(time.time()))

  def to_dict(self) -> dict:
    return {
      "setup_id": self.setup_id,
      "thesis_id": self.thesis_id,
      "symbol": self.symbol,
      "state": self.state,
      "source_structure_id": self.source_structure_id,
      "formation_timeframe": self.formation_timeframe,
      "confirmation_timeframe": self.confirmation_timeframe,
      "formation_bar_ts": self.formation_bar_ts,
      "confirmation_bar_ts": self.confirmation_bar_ts,
      "last_emitted_card_state": self.last_emitted_card_state,
      "telegram_message_id": self.telegram_message_id,
      "expires_at": self.expires_at,
      "invalidation_condition": self.invalidation_condition,
      "created_at": self.created_at,
      "updated_at": self.updated_at,
    }

  @classmethod
  def from_dict(cls, data: Mapping[str, Any]) -> "SetupRecord":
    return cls(
      setup_id=str(data["setup_id"]),
      thesis_id=str(data["thesis_id"]),
      symbol=str(data["symbol"]),
      state=str(data["state"]),
      source_structure_id=data.get("source_structure_id"),
      formation_timeframe=data.get("formation_timeframe"),
      confirmation_timeframe=data.get("confirmation_timeframe"),
      formation_bar_ts=data.get("formation_bar_ts"),
      confirmation_bar_ts=data.get("confirmation_bar_ts"),
      last_emitted_card_state=data.get("last_emitted_card_state"),
      telegram_message_id=data.get("telegram_message_id"),
      expires_at=data.get("expires_at"),
      invalidation_condition=data.get("invalidation_condition"),
      created_at=int(data.get("created_at", time.time())),
      updated_at=int(data.get("updated_at", time.time())),
    )


def _ttl_for(record: SetupRecord) -> int:
  """P0-1: audit-retention TTL, never the raw distance to business expiry.

  Terminal records get a flat retention window (they will never be touched
  again, so there is no "distance to expiry" to add). Active records with a
  known expires_at get retention measured FROM that expiry, so the record
  outlives its own business deadline by a full audit window regardless of
  whether anything writes to it again. Records with no expires_at (existing
  data predating this field, or setups that never got one) keep the old
  flat fallback - explicitly still supported, not a migration requirement.
  """
  if record.state in TERMINAL_STATES:
    return TERMINAL_SETUP_RETENTION_SECONDS
  if record.expires_at is not None:
    now = int(time.time())
    return max(
      SETUP_AUDIT_RETENTION_SECONDS,
      record.expires_at - now + SETUP_AUDIT_RETENTION_SECONDS,
    )
  return 86400


async def _sync_expiry_index(client: Any, record: SetupRecord) -> None:
  """P0-2: keep analysis:setup_expiry in lockstep with the canonical record.

  A terminal record is removed from the index (the sweeper must never look
  at it again); an active record with an expires_at is (re)scored; an
  active record with no expires_at was never indexed and stays that way.
  """
  key = setup_expiry_index_key()
  if record.state in TERMINAL_STATES or record.expires_at is None:
    await client.zrem(key, record.setup_id)
    return
  await client.zadd(key, {record.setup_id: record.expires_at})


async def _save(client: Any, record: SetupRecord) -> None:
  await client.set(
    setup_key(record.setup_id),
    json.dumps(record.to_dict(), separators=(",", ":")),
    ex=_ttl_for(record),
  )
  await _sync_expiry_index(client, record)


async def load_setup(client: Any, setup_id: str) -> SetupRecord | None:
  raw = await client.get(setup_key(setup_id))
  if raw is None:
    return None
  data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
  return SetupRecord.from_dict(data)


async def create_setup(
  client: Any,
  *,
  setup_id: str,
  thesis_id: str,
  symbol: str,
  source_structure_id: str | None = None,
  formation_timeframe: str | None = None,
  expires_at: int | None = None,
  invalidation_condition: str | None = None,
) -> tuple[SetupRecord, bool]:
  """Create a new setup in DISCOVERED, or return the existing one.

  Idempotent by design: the scanner runs every closed bar and will call this
  repeatedly for the same structural reaction. Returns (record, created)
  where created is False when a record already existed - callers use this
  the same way `transition_setup`'s ``changed`` works, to avoid re-emitting
  a "new setup" notification for something already being watched.
  """
  existing = await load_setup(client, setup_id)
  if existing is not None:
    return existing, False
  record = SetupRecord(
    setup_id=setup_id,
    thesis_id=thesis_id,
    symbol=symbol,
    state=DISCOVERED,
    source_structure_id=source_structure_id,
    formation_timeframe=formation_timeframe,
    expires_at=expires_at,
    invalidation_condition=invalidation_condition,
  )
  await _save(client, record)
  return record, True


async def transition_setup(
  client: Any,
  setup_id: str,
  new_state: str,
  *,
  reason_code: str = "",
  **field_updates: Any,
) -> tuple[SetupRecord, bool]:
  """Move a setup to ``new_state``, applying any field updates.

  Returns (record, changed). ``changed`` is False when ``new_state`` equals
  the setup's current state - a repeated detector cycle confirming the same
  state, which must not re-emit a Telegram card or re-run downstream
  side effects. Raises SetupLifecycleError for an unknown setup_id or an
  edge not present in the transition graph (including any attempt to
  re-enter CONFIRMED from a state that already passed it).
  """
  if new_state not in SETUP_STATES:
    raise SetupLifecycleError(f"unknown setup state: {new_state!r}")

  record = await load_setup(client, setup_id)
  if record is None:
    raise SetupLifecycleError(f"transition_setup: unknown setup_id {setup_id!r}")

  if record.state == new_state:
    return record, False

  allowed = _TRANSITIONS.get(record.state, frozenset())
  if new_state not in allowed:
    raise SetupLifecycleError(
      f"illegal setup transition {record.state!r} -> {new_state!r} "
      f"for {setup_id!r}"
      + (f" ({reason_code})" if reason_code else ""),
    )

  updated = replace(
    record,
    state=new_state,
    updated_at=int(time.time()),
    **field_updates,
  )
  await _save(client, updated)
  return updated, True


async def rearm_setup(
  client: Any,
  setup_id: str,
  *,
  rearm_condition: str,
) -> SetupRecord:
  """Explicitly return an INVALIDATED/EXPIRED setup to DISCOVERED.

  This is the only way back into the graph from a terminal analysis state,
  and it requires a caller-supplied, non-empty ``rearm_condition`` (e.g. "new
  structure formed on the same level after a swept liquidity run") - never a
  bare transition, so a rearm always leaves an audit trail of *why*.
  """
  if not rearm_condition:
    raise SetupLifecycleError("rearm_setup requires a non-empty rearm_condition")
  record = await load_setup(client, setup_id)
  if record is None:
    raise SetupLifecycleError(f"rearm_setup: unknown setup_id {setup_id!r}")
  if record.state not in (INVALIDATED, EXPIRED):
    raise SetupLifecycleError(
      f"rearm_setup: {setup_id!r} is {record.state!r}, not invalidated/expired",
    )
  updated = replace(
    record,
    state=DISCOVERED,
    invalidation_condition=None,
    # The old expires_at belonged to the terminal instance being rearmed
    # from; carrying it forward would re-add this setup_id to
    # analysis:setup_expiry with a stale (often already-past) score and the
    # sweeper would immediately re-expire it. A fresh expires_at is set the
    # next time this setup transitions forward (e.g. into CONFIRMED).
    expires_at=None,
    updated_at=int(time.time()),
  )
  await _save(client, updated)
  return updated


async def claim_active_thesis(
  client: Any,
  *,
  symbol: str,
  thesis_id: str,
  setup_id: str,
  ttl: int = 86400,
) -> bool:
  """Enforce "one thesis may have only one active initial TradePlan".

  SETNX-style claim on analysis:active_thesis:{symbol}:{thesis_id}. Returns
  True if this setup now owns the thesis (either newly claimed, or it
  already owned it), False if a *different* setup owns it.
  """
  key = active_thesis_key(symbol, thesis_id)
  claimed = await client.set(key, setup_id, ex=ttl, nx=True)
  if claimed:
    return True
  current = await client.get(key)
  if current is None:
    return False
  current_id = current.decode() if isinstance(current, bytes) else current
  return current_id == setup_id


async def release_active_thesis(
  client: Any,
  *,
  symbol: str,
  thesis_id: str,
  setup_id: str,
) -> None:
  """Release a thesis claim - only if this exact setup still owns it."""
  key = active_thesis_key(symbol, thesis_id)
  current = await client.get(key)
  if current is None:
    return
  current_id = current.decode() if isinstance(current, bytes) else current
  if current_id == setup_id:
    await client.delete(key)
