"""Durable Redis-event ingestion for ``/trade_stats``.

Trade-result persistence used to be a side effect of the owner Telegram
delivery cursor. That coupled accounting to notification configuration and,
on the first deployment of the SQL tables, left older retained executor
events unrecorded. This module owns a separate cursor, backfills the retained
stream oldest-first at startup, then tails it continuously.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from app.core.config import runtime_config
from app.persistence import redis_state
from app.persistence.store import record_auto_trade_event

log = logging.getLogger(__name__)

STATS_EVENT_CURSOR_KEY = "auto_trade:stats_event_cursor"


def _text(value: object) -> str:
  return value.decode() if isinstance(value, bytes) else str(value)


def _complete_outcome(event: dict) -> str | None:
  event_type = str(event.get("type") or "")
  if event_type in {"opened", "add", "manual_opened", "order_filled"}:
    return "fill"
  if event_type not in {"group_result", "position_closed", "manual_closed"}:
    return None
  reason = str(event.get("reason_code") or event.get("close_reason") or "").casefold()
  message = str(event.get("message") or "").casefold()
  if "stop" in reason or "group_stop" in reason or "sl" == reason:
    return "sl"
  if "take_profit" in reason or "tp" in reason or "target" in reason:
    return "tp"
  if "group_stop_loss" in message or "stop loss" in message:
    return "sl"
  if "take profit" in message or "tp" in message:
    return "tp"
  result_pips = event.get("group_realized_pips")
  if result_pips is None:
    result_pips = event.get("result_pips")
  try:
    pips = float(result_pips) if result_pips is not None else None
  except (TypeError, ValueError):
    pips = None
  if pips is None:
    return None
  if pips < 0:
    return "sl"
  if pips > 0:
    return "tp"
  return None


async def _emit_funnel_complete(client, event: dict) -> None:
  outcome = _complete_outcome(event)
  if outcome is None:
    return
  from app.autotrade.reaction_funnel import (
    emit_plan_complete_event,
    funnel_bucket,
    normalize_setup_type,
    BUCKET_SCALP,
  )
  from app.scalping.context import classify_session
  from app.scalping.risk import (
    apply_daily_reset,
    load_risk,
    record_scalp_outcome,
    save_risk,
  )

  strategy = normalize_setup_type(
    event.get("setup")
    or event.get("strategy")
    or event.get("setup_type")
  )
  await emit_plan_complete_event(
    client,
    symbol=str(event.get("symbol") or "XAU"),
    strategy=None if strategy is None else str(strategy),
    reason_code=str(
      event.get("reason_code")
      or event.get("close_reason")
      or event.get("type")
      or "plan_complete"
    ),
    outcome=outcome,
    group_id=(
      None if event.get("group_id") is None else str(event.get("group_id"))
    ),
    candidate_id=(
      None
      if event.get("candidate_id") is None
      else str(event.get("candidate_id"))
    ),
    family=(
      None
      if event.get("strategy_family") is None
      else str(event.get("strategy_family"))
    ),
    measured={
      "event_type": event.get("type"),
      "group_realized_pips": event.get("group_realized_pips"),
      "result_pips": event.get("result_pips"),
    },
  )
  # Keep HFS risk streak / R counters alive (was never wired before).
  if funnel_bucket(strategy, family=event.get("strategy_family")) != BUCKET_SCALP:
    return
  symbol = str(event.get("symbol") or "XAU")
  try:
    state = await load_risk(client, symbol)
    now = int(event.get("timestamp") or time.time())
    state = apply_daily_reset(
      state, runtime_config, now=now, session=classify_session(now, runtime_config)
    )
    result_pips = event.get("group_realized_pips")
    if result_pips is None:
      result_pips = event.get("result_pips")
    try:
      pips = float(result_pips or 0.0)
    except (TypeError, ValueError):
      pips = 0.0
    stop_raw = event.get("stop_pips") or event.get("initial_stop_pips")
    try:
      stop_pips = float(stop_raw) if stop_raw is not None else None
    except (TypeError, ValueError):
      stop_pips = None
    if outcome == "fill":
      state = record_scalp_outcome(
        state,
        result_pips=0.0,
        stop_pips=stop_pips,
        now=now,
        opened=True,
        group_id=(
          None if event.get("group_id") is None else str(event.get("group_id"))
        ),
      )
    else:
      state = record_scalp_outcome(
        state,
        result_pips=pips,
        stop_pips=stop_pips,
        now=now,
        closed=True,
        group_id=(
          None if event.get("group_id") is None else str(event.get("group_id"))
        ),
      )
    await save_risk(client, symbol, state)
  except Exception:
    log.exception("hfs risk state update failed symbol=%s", symbol)


async def process_auto_trade_stats_entries(
  client,
  entries,
  *,
  cursor: str,
) -> str:
  """Persist a batch and advance only after each event is safely handled."""
  for entry_id, fields in entries:
    try:
      raw_payload = fields.get("payload")
      if raw_payload is None:
        raw_payload = fields.get(b"payload")
      event = json.loads(_text(raw_payload))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
      log.warning("Invalid auto-trade stats event %s: %s", entry_id, exc)
    else:
      await record_auto_trade_event(event)
      try:
        await _emit_funnel_complete(client, event)
      except Exception:
        log.exception(
          "funnel complete emit failed entry_id=%s type=%s",
          entry_id,
          event.get("type") if isinstance(event, dict) else None,
        )
    cursor = _text(entry_id)
    await client.set(STATS_EVENT_CURSOR_KEY, cursor)
  return cursor


async def backfill_retained_auto_trade_stats(client) -> str:
  """Catch the journal up to the retained stream tail before commands start."""
  stored = await client.get(STATS_EVENT_CURSOR_KEY)
  cursor = _text(stored) if stored else "0-0"
  start = f"({cursor}" if stored else "-"
  entries = await client.xrange(
    runtime_config.contract.streams.events,
    min=start,
    max="+",
  )
  if entries:
    cursor = await process_auto_trade_stats_entries(
      client,
      entries,
      cursor=cursor,
    )
  elif not stored:
    await client.set(STATS_EVENT_CURSOR_KEY, cursor)
  log.info(
    "Auto-trade stats backfill complete events=%s cursor=%s",
    len(entries),
    cursor,
  )
  return cursor


async def auto_trade_stats_ingestion_loop() -> None:
  """Tail executor events independently from Telegram delivery."""
  client = redis_state.get_client()
  cursor = await backfill_retained_auto_trade_stats(client)
  log.info("Auto-trade stats ingestion active from Redis cursor %s", cursor)
  while True:
    try:
      batches = await client.xread(
        {runtime_config.contract.streams.events: cursor},
        count=100,
        block=5000,
      )
      for _, entries in batches:
        cursor = await process_auto_trade_stats_entries(
          client,
          entries,
          cursor=cursor,
        )
    except asyncio.CancelledError:
      raise
    except Exception:
      stored = await client.get(STATS_EVENT_CURSOR_KEY)
      cursor = _text(stored) if stored else cursor
      log.exception(
        "Auto-trade stats ingestion failed at cursor %s; retrying",
        cursor,
      )
      await asyncio.sleep(5)
