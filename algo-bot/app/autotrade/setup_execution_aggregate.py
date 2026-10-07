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



def v8_plan_id(setup_id: str) -> str:
  # Mirrors worker.py::_v8_plan_id exactly (plan_id is deterministic from
  # setup_id, so the aggregate never needs the StrategyMatch object itself).
  return f"v8:{setup_id}"


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
