"""P0-10: one-shot startup reconciliation for Redis state left over from
before this branch's invariants existed - an active record with no
expiry-index entry (created before setup_lifecycle._save started syncing
one), an orphaned forming card whose setup already expired or vanished, a
forming-status snapshot with no card left to apply it to, or a terminal
setup whose route_outcome snapshot was never corrected. Runs once at
process start (see main.py), before the continuous sweeper's own first
pass - a migration/rollout safety net, not a replacement for it.

Every scan uses SCAN (never KEYS, never blocks Redis for the duration of
the pass), every write is idempotent, and nothing is ever deleted unless
it can be positively identified as orphaned against the canonical
setup_lifecycle record. No Redis FLUSH of any kind.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.autotrade.lifecycle import increment_metric
from app.autotrade.setup_card import (
  assert_or_repair_forming_projection,
  forming_status_key,
  kill_setup_card,
  load_forming_card,
  save_forming_reconcile_pending,
)
from app.autotrade.setup_lifecycle import (
  PUBLICATION_WON_STATES,
  TERMINAL_STATES,
  load_setup,
  setup_expiry_index_key,
)
from app.autotrade.terminal_cleanup import finalize_terminal_setup
from app.bot.client import delete_scanner_message, edit_scanner_message_text

log = logging.getLogger("bot")

_SCAN_BATCH = 500
_SETUP_KEY_PREFIX = "analysis:setup:"
_FORMING_MESSAGE_PREFIX = "auto_trade:forming_message:"
_FORMING_STATUS_PREFIX = "auto_trade:forming_status:"

# route_outcome.RouteStatus values that already correctly describe a
# terminal setup - anything else on a terminal setup's route_outcome
# snapshot is stale and needs finalize_terminal_setup's rewrite.
_TERMINAL_CONSISTENT_ROUTE_STATUSES = frozenset({
  "expired", "blocked", "executor_rejected",
  "duplicate_suppressed", "arbitration_suppressed",
  "order_filled",
})


async def _scan_keys(client: Any, pattern: str):
  cursor = 0
  while True:
    cursor, keys = await client.scan(cursor, match=pattern, count=_SCAN_BATCH)
    for key in keys:
      yield key.decode() if isinstance(key, bytes) else key
    if cursor == 0 or cursor == "0":
      break


async def _reconcile_canonical_setups(client: Any) -> None:
  """Repair analysis:setup_expiry membership; correct any terminal
  setup's route_outcome snapshot that still reads as non-terminal.
  """
  from app.autotrade.route_outcome import route_outcome_key

  async for key in _scan_keys(client, f"{_SETUP_KEY_PREFIX}*"):
    setup_id = key[len(_SETUP_KEY_PREFIX):]
    try:
      record = await load_setup(client, setup_id)
      if record is None:
        continue

      if record.state in TERMINAL_STATES:
        raw = await client.get(route_outcome_key(record.symbol, setup_id))
        needs_repair = True
        if raw:
          try:
            payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            needs_repair = (
              payload.get("status") not in _TERMINAL_CONSISTENT_ROUTE_STATUSES
            )
          except (TypeError, ValueError, json.JSONDecodeError):
            needs_repair = True
        if needs_repair:
          await finalize_terminal_setup(
            client, setup_id,
            terminal_state=record.state,
            reason_code="startup_reconciliation",
            source="startup_reconciliation",
          )
          await increment_metric(client, "route_projection_repaired")
        continue

      if record.expires_at is None or record.state in PUBLICATION_WON_STATES:
        continue
      score = await client.zscore(setup_expiry_index_key(), setup_id)
      if score is not None and int(score) == int(record.expires_at):
        continue
      await client.zadd(setup_expiry_index_key(), {setup_id: record.expires_at})
      await increment_metric(client, "setup_expiry_index_repaired")
    except Exception:
      # One setup's repair failure (eg. a Telegram error while killing its
      # card via finalize_terminal_setup) must not abort reconciliation
      # for every other setup in this scan.
      log.exception(
        "startup reconciliation: canonical setup repair failed setup_id=%s",
        setup_id,
      )


async def _reconcile_forming_cards(client: Any) -> None:
  """Remove any forming card whose setup is missing, terminal, or past
  its own expires_at by wall-clock time; repair status/text drift on the
  rest via the same invariant helper the send path itself uses.
  """
  now = int(time.time())
  async for key in _scan_keys(client, f"{_FORMING_MESSAGE_PREFIX}*"):
    setup_id = key[len(_FORMING_MESSAGE_PREFIX):]
    try:
      record = await load_setup(client, setup_id)
      if record is None:
        await kill_setup_card(
          client, setup_id,
          reason_code="startup_reconciliation_missing_setup",
          delete_fn=delete_scanner_message,
          edit_fn=edit_scanner_message_text,
        )
        await increment_metric(
          client, "missing_canonical_setup_projection_removed",
        )
        continue

      stale_by_wall_clock = (
        record.expires_at is not None
        and now >= int(record.expires_at)
        and record.state not in PUBLICATION_WON_STATES
        and record.state not in TERMINAL_STATES
      )
      if record.state in TERMINAL_STATES or stale_by_wall_clock:
        await kill_setup_card(
          client, setup_id,
          reason_code="startup_reconciliation_orphan_card",
          delete_fn=delete_scanner_message,
          edit_fn=edit_scanner_message_text,
        )
        await increment_metric(client, "orphan_forming_card_removed")
        continue

      repaired = await assert_or_repair_forming_projection(
        client, setup_id, edit_fn=edit_scanner_message_text,
      )
      if repaired:
        await increment_metric(client, "forming_card_projection_repaired")
    except Exception:
      # One card's Telegram failure (eg. a bad token, a deleted chat) must
      # not abort reconciliation for every other setup in this scan.
      log.exception(
        "startup reconciliation: forming card repair failed setup_id=%s",
        setup_id,
      )


async def _reconcile_forming_status_without_card(client: Any) -> None:
  """A status snapshot with no card at all (the card send never
  completed, or the card key already expired/was cleared) needs the same
  P0-6 reconciliation-pending marker a live delivery race produces.
  """
  async for key in _scan_keys(client, f"{_FORMING_STATUS_PREFIX}*"):
    setup_id = key[len(_FORMING_STATUS_PREFIX):]
    card = await load_forming_card(client, setup_id)
    if card is not None:
      continue
    record = await load_setup(client, setup_id)
    if record is None or record.state in TERMINAL_STATES:
      continue
    raw = await client.get(forming_status_key(setup_id))
    if not raw:
      continue
    try:
      payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError, json.JSONDecodeError):
      continue
    await save_forming_reconcile_pending(
      client, setup_id,
      status_line=str(payload.get("status_line") or ""),
      priority=int(payload.get("priority") or 0),
      reason_code="startup_reconciliation_status_without_card",
    )
    await increment_metric(client, "forming_status_pending_card")


async def reconcile_startup_state(client: Any) -> None:
  """Called once from main.py before any background task starts reading
  this state, so the sweeper/delivery loops never race a still-in-progress
  reconciliation pass.
  """
  log.info("startup reconciliation beginning")
  try:
    await _reconcile_canonical_setups(client)
    await _reconcile_forming_cards(client)
    await _reconcile_forming_status_without_card(client)
  except Exception:
    log.exception("startup reconciliation failed - continuing startup")
  else:
    log.info("startup reconciliation complete")
