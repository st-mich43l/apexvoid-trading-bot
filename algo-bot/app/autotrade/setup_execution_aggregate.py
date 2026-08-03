"""SetupExecutionAggregate: one authoritative read across the three canonical
execution stores (P0 spec).

`analysis:setup:{setup_id}` (business state), `auto_trade:execution_confirmation:
{setup_id}` (execution timing) and `execution:plan_state:{plan_id}`
(publication) can each lag the others by one write - a projection writer
(Telegram card, route outcome) that trusts only its own local return value can
show a status one step behind what actually happened. This module is the
single place that reconciles all three into one effective answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.autotrade.execution_confirmation import load_execution_confirmation
from app.autotrade.setup_lifecycle import TERMINAL_STATES, load_setup
from app.autotrade.trade_plan_stream import read_plan_state


def v7_plan_id(setup_id: str) -> str:
  # Mirrors worker.py::_v7_plan_id exactly (plan_id is deterministic from
  # setup_id, so the aggregate never needs the StrategyMatch object itself).
  return f"v7:{setup_id}"


@dataclass(frozen=True)
class SetupExecutionAggregate:
  setup_id: str
  setup_state: str | None
  execution_phase: str | None
  plan_id: str
  plan_state: str | None
  expires_at: int | None
  terminal: bool
  effective_projection_state: str
  effective_reason_code: str
  status_line: str | None


# Execution-confirmation phase -> the card/status-priority vocabulary
# setup_card.py's CARD_STATUS_PRIORITY already uses.
_PHASE_TO_PROJECTION_STATE = {
  "immediate_confirmation": "preflight",
  "waiting_retest": "waiting_retest",
  "in_zone_waiting_m1": "trigger_ready",
  "trigger_ready": "trigger_ready",
  "trigger_price_left_zone": "waiting_retest",
  "published": "plan_published",
  "expired": "terminal",
  "invalidated": "terminal",
}

# setup_lifecycle state -> the same vocabulary, used when there is no (yet)
# execution-confirmation record - eg. before WORKER_ACKNOWLEDGED.
_SETUP_STATE_TO_PROJECTION_STATE = {
  "discovered": "analysis_only",
  "watching": "analysis_only",
  "touched": "analysis_only",
  "forming": "analysis_only",
  "confirmed": "queued",
  "ready_event_enqueued": "queued",
  "worker_acknowledged": "queued",
  "armed_waiting_trigger": "preflight",
  "plan_built": "plan_published",
  "plan_published": "plan_published",
  "armed": "executor_armed",
  "cancelled": "terminal",
  "invalidated": "terminal",
  "expired": "terminal",
  "consumed": "terminal",
}

STATUS_LINE_BY_PROJECTION_STATE = {
  "analysis_only": None,
  "queued": "🟡 <b>QUEUED</b> · worker acknowledgement pending",
  "preflight": "🟡 <b>PREFLIGHT</b> · dynamic execution checks in progress",
  "waiting_retest": (
    "🟠 <b>WAITING RETEST</b> · executable quote is outside "
    "the confirmed entry zone"
  ),
  "trigger_ready": "🟢 <b>TRIGGER READY</b> · waiting for the M1 trigger candle",
  # Do not advertise PLAN PUBLISHED on the forming card — root stays SETUP
  # FORMING until fill / TERMINAL. Publication is mechanical, not a card status.
  "plan_published": None,
  "executor_armed": (
    "🟢 <b>EXECUTOR ARMED</b> · waiting for mechanical entry activation"
  ),
  "terminal": None,
}


async def resolve_setup_execution_aggregate(
  client: Any,
  setup_id: str,
  *,
  symbol: str | None = None,
) -> SetupExecutionAggregate:
  """Single authoritative read across setup/confirmation/plan state.

  Projection writers should derive "what should this look like right now"
  from here instead of trusting whichever local variable they happen to be
  holding.
  """
  setup = await load_setup(client, setup_id)
  setup_state = setup.state if setup is not None else None
  expires_at = setup.expires_at if setup is not None else None
  terminal = bool(setup_state in TERMINAL_STATES) if setup_state else False

  confirmation = await load_execution_confirmation(client, setup_id)
  execution_phase = confirmation.phase if confirmation is not None else None

  plan_id = v7_plan_id(setup_id)
  plan_state = await read_plan_state(client, plan_id)

  effective_projection_state = _resolve_projection_state(
    setup_state=setup_state,
    execution_phase=execution_phase,
    plan_state=plan_state,
    terminal=terminal,
  )
  effective_reason_code = _resolve_reason_code(
    setup_state=setup_state,
    execution_phase=execution_phase,
    effective_projection_state=effective_projection_state,
  )
  status_line = STATUS_LINE_BY_PROJECTION_STATE.get(effective_projection_state)

  return SetupExecutionAggregate(
    setup_id=setup_id,
    setup_state=setup_state,
    execution_phase=execution_phase,
    plan_id=plan_id,
    plan_state=plan_state,
    expires_at=expires_at,
    terminal=terminal,
    effective_projection_state=effective_projection_state,
    effective_reason_code=effective_reason_code,
    status_line=status_line,
  )


def _resolve_projection_state(
  *,
  setup_state: str | None,
  execution_phase: str | None,
  plan_state: str | None,
  terminal: bool,
) -> str:
  # Publication is the most authoritative signal available: if a plan is
  # actually on the stream, the projection must say so even if the setup
  # record or confirmation phase haven't caught up yet (P0-8/P1-2 -
  # publication must win over a stale/lagging intermediate projection, and
  # must never regress back to "waiting"/terminal because of a late expiry
  # sweep).
  if plan_state == "published":
    return "plan_published"
  if plan_state in {"armed", "consumed"}:
    return "executor_armed"
  if terminal:
    return "terminal"
  if execution_phase is not None:
    mapped = _PHASE_TO_PROJECTION_STATE.get(execution_phase)
    if mapped is not None:
      return mapped
  if setup_state is not None:
    mapped = _SETUP_STATE_TO_PROJECTION_STATE.get(setup_state)
    if mapped is not None:
      return mapped
  return "analysis_only"


def _resolve_reason_code(
  *,
  setup_state: str | None,
  execution_phase: str | None,
  effective_projection_state: str,
) -> str:
  if effective_projection_state == "terminal":
    if setup_state == "expired":
      return "setup_expired_before_execution"
    if setup_state == "invalidated":
      return "structure_broke"
    if setup_state == "cancelled":
      return "setup_cancelled"
    return "setup_terminal"
  if execution_phase == "waiting_retest":
    return "waiting_retest_entry_zone"
  if execution_phase == "trigger_price_left_zone":
    return "trigger_price_left_zone"
  if effective_projection_state == "plan_published":
    return "candidate_published"
  if effective_projection_state == "executor_armed":
    return "executor_armed"
  if effective_projection_state == "queued":
    return "worker_acknowledgement_pending"
  return execution_phase or setup_state or "unknown"
