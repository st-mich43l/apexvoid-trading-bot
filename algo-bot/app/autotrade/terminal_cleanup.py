"""P0-9: one coordinator for everything that must happen when a setup
reaches a terminal state (EXPIRED/INVALIDATED/CANCELLED/CONSUMED).

Every path that can produce a terminal setup_lifecycle transition (the
expiry sweeper, scanner invalidation, worker rejection paths) should call
this instead of hand-rolling its own cleanup, so no single call site can
forget a step and leave an orphan Telegram card, a stale route-outcome
snapshot, or a stuck reconciliation marker behind.

Coverage note (explicit, not silently claimed complete): this coordinator
clears the Telegram forming card, the reconciliation-pending marker, the
execution-confirmation transient key, the analysis:setup_expiry index
membership, the route-outcome terminal snapshot, and emits the terminal
lifecycle event. It does NOT yet release a held confluence-zone claim, an
active-thesis claim, ready-event recovery state, or remove a stale
StrategyMatch from scanner-side active collections - those need call-site
-specific context (which zone/thesis was claimed, which StrategyMatch
object) that a setup_id-only coordinator does not have visibility into on
its own. Callers that hold that context should still release it themselves
after calling this.
"""

from __future__ import annotations

import logging
import types
from typing import Any

from app.autotrade.execution_confirmation import execution_confirmation_key
from app.autotrade.lifecycle import emit_lifecycle
from app.autotrade.route_outcome import record_route_outcome
from app.autotrade.setup_card import kill_setup_card
from app.autotrade.setup_lifecycle import TERMINAL_STATES, load_setup, setup_expiry_index_key
from app.bot.client import delete_scanner_message, edit_scanner_message_text

log = logging.getLogger("bot")

# setup_lifecycle terminal state -> app.autotrade.lifecycle.LIFECYCLE_STATES.
# LIFECYCLE_STATES has no "consumed" entry (a fully-executed setup reads as
# "closed" in that vocabulary, matching the V6 execution lifecycle's own
# terminal name).
_LIFECYCLE_STATE_BY_TERMINAL_STATE = {
  "expired": "expired",
  "invalidated": "invalidated",
  "cancelled": "cancelled",
  "consumed": "closed",
}

# setup_lifecycle terminal state -> route_outcome.RouteStatus.
_ROUTE_STATUS_BY_TERMINAL_STATE = {
  "expired": "expired",
  "invalidated": "blocked",
  "cancelled": "blocked",
  "consumed": "order_filled",
}


async def finalize_terminal_setup(
  client: Any,
  setup_id: str,
  *,
  terminal_state: str,
  reason_code: str,
  source: str,
) -> None:
  """Idempotent - safe to call more than once for the same terminal setup.

  Requires the setup_lifecycle transition to already have happened (this
  reads the canonical record to find symbol/expires_at rather than taking
  them as parameters, so the record and this cleanup can never disagree
  about which terminal state actually landed).
  """
  record = await load_setup(client, setup_id)
  if record is None or record.state not in TERMINAL_STATES:
    log.warning(
      "finalize_terminal_setup called on non-terminal or missing setup "
      "setup_id=%s source=%s state=%s",
      setup_id, source, record.state if record else None,
    )
    return

  await kill_setup_card(
    client, setup_id,
    reason_code=reason_code,
    delete_fn=delete_scanner_message,
    edit_fn=edit_scanner_message_text,
  )
  await client.delete(execution_confirmation_key(setup_id))
  await client.zrem(setup_expiry_index_key(), setup_id)

  shim = types.SimpleNamespace(
    match_id=setup_id,
    symbol=record.symbol,
    issued_at=record.created_at,
    expires_at=record.expires_at or 0,
  )
  try:
    await record_route_outcome(
      client, shim,
      stage="scanner" if record.state == "expired" else "entry_invalidation",
      status=_ROUTE_STATUS_BY_TERMINAL_STATE.get(record.state, "blocked"),
      reason_code=reason_code,
      message=f"setup {record.state} source={source}",
      publish_status=False,
    )
  except Exception:
    log.exception(
      "finalize_terminal_setup: route outcome write failed setup_id=%s",
      setup_id,
    )

  lifecycle_state = _LIFECYCLE_STATE_BY_TERMINAL_STATE.get(record.state)
  if lifecycle_state is not None:
    await emit_lifecycle(
      client,
      lifecycle_state,
      symbol=record.symbol,
      match_id=setup_id,
      correlation_id=setup_id,
      reason_code=reason_code,
      message=f"setup finalized terminal_state={record.state} source={source}",
      measured={"expires_at": record.expires_at},
      publish_status=True,
    )
