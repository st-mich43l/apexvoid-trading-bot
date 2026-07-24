"""Consumer + real broker execution for owner-armed manual /algo signals.

Two independent loops, both no-ops unless ``settings.manual_algo_enabled``:

- ``bridge_intents_loop``: translates each published ``ManualTradeIntent``
  into the dedicated ``mode=manual_algo`` owner-execution contract. The C#
  executor routes that mode before autonomous strategy, bias and scale-in
  gates while retaining broker validation and candidate idempotency.
- ``reconcile_events_loop``: reads ``auto_trade:events`` (the SAME stream
  ``app.autotrade.delivery``'s owner-DM loop reads — a second independent
  ``XREAD`` reader with its own cursor key is safe, there are no consumer
  groups) and drives broker fill/TP/SL/close events through the SAME
  ``trade_ops.py -> post_result -> broadcast.fanout_update`` path a
  manually-typed close/active command already uses, so VIP/public channel
  posts update exactly like a manual command would.

Also publishes owner-override commands (``/trade_close``/``/trade_sl``/
``/trade_cancel`` on an algo-armed/filled signal, routed here by
``trade_ops.py``) onto ``manual_trade:commands`` for AutoTradeEngine.cs's
command poll to execute against the real broker.
"""

import asyncio
import json
import logging

from app.bot.client import send_scanner_with_retry
from app.core.config import settings
from app.persistence import redis_state
from app.persistence.store import (
  get_manual_signal,
  get_signal_by_execution_intent_id,
  set_execution_fill,
  set_execution_status,
)
from app.signals import pips_format
from app.signals.manual_intent import ManualTradeIntent

log = logging.getLogger(__name__)

_INTENT_BRIDGE_CURSOR_KEY = "manual_trade:intent_bridge_cursor"
_EVENT_CURSOR_KEY = "manual_trade:algo_event_cursor"

# The distinct fill-event type AutoTradeEngine.cs publishes the FIRST time a
# manual-algo limit order fills (see AdoptPositionAsync in
# ctrader-engine/src/AutoTradeEngine.cs). Deliberately NOT "opened" - that
# type stays reserved for the autonomous engines' own "🤖 ApexVoid Algo"
# owner-DM card (app.autotrade.delivery._format_opened); a manually-typed
# /algo signal is still fundamentally a manual signal and should not
# duplicate that card, only the channel update this loop drives.
_FILL_EVENT_TYPE = "manual_opened"


def _pending_close_key(position_id: int) -> str:
  return f"manual_trade:pending_close:{position_id}"


def _price(value: object) -> str:
  if value is None:
    return "n/a"
  return f"{float(value):,.2f}"


def _target_text(event: dict) -> str:
  targets = event.get("target_prices") or []
  return " / ".join(_price(value) for value in targets) or "n/a"


async def _send_executor_truth(text: str) -> None:
  if settings.telegram_owner_id:
    await send_scanner_with_retry(
      text,
      chat_id=settings.telegram_owner_id,
    )


async def _handle_limit_placed(event: dict) -> None:
  candidate_id = str(event.get("candidate_id") or "")
  if not candidate_id:
    return
  sig = await get_signal_by_execution_intent_id(candidate_id)
  if sig is not None:
    await set_execution_status(sig["id"], "pending")
  entry_low = event.get("entry_low")
  entry_high = event.get("entry_high")
  entry = (
    f"{_price(entry_low)}-{_price(entry_high)}"
    if entry_low is not None and entry_high is not None
    else _price(event.get("price"))
  )
  await _send_executor_truth(
    "✅ <b>LIMIT ORDER PLACED</b>\n"
    f"Direction: <b>{event.get('direction') or 'n/a'}</b>\n"
    f"Entry: <code>{entry}</code>\n"
    f"SL: <code>{_price(event.get('stop_loss'))}</code>\n"
    f"TPs: <code>{_target_text(event)}</code>\n"
    f"Order ID: <code>{event.get('order_id') or 'n/a'}</code>\n"
    f"Candidate ID: <code>{candidate_id}</code>"
  )


async def _handle_execution_rejected(event: dict) -> None:
  candidate_id = str(event.get("candidate_id") or "")
  reason = str(event.get("reason_code") or "unknown_rejection")
  if candidate_id:
    sig = await get_signal_by_execution_intent_id(candidate_id)
    if sig is not None:
      await set_execution_status(sig["id"], "rejected", error=reason)
  await _send_executor_truth(
    "⛔ <b>ORDER REJECTED</b>\n"
    f"Reason: <code>{reason}</code>\n"
    "No broker order submitted"
  )


async def _handle_dry_run(event: dict) -> None:
  candidate_id = str(event.get("candidate_id") or "")
  if candidate_id:
    sig = await get_signal_by_execution_intent_id(candidate_id)
    if sig is not None:
      await set_execution_status(sig["id"], "dry_run")
  await _send_executor_truth(
    "🧪 <b>DRY-RUN ONLY</b>\n"
    "No broker order submitted"
  )


def _intent_to_candidate_payload(intent: ManualTradeIntent) -> dict:
  """Build the TradeCandidate-shaped dict AutoTradeEngine.cs consumes.

  ``version=3``/``mode="manual_algo"`` is exactly what
  ``IsManualAlgoCandidate`` (ctrader-engine/src/AutoTradeEngine.cs) checks.
  ``candidate_id`` is the intent_id verbatim, reusing the exact SETNX
  candidate-claim idempotency machinery every other candidate type already
  gets for free.

  The reference edge for both ``key_level``/``current_price`` and the
  ``targets_pips`` pip-distance conversion is ``pips_format.rr_entry``'s own
  BUY -> entry_high / SELL -> entry_low convention (the exact same "worst
  realistic fill" edge already used for R:R on every manually-typed signal,
  see ``app.signals.broadcast.render_entry``) - and, by construction, the
  SAME edge AutoTradeEngine.cs's manual-algo path resolves its resting limit
  order to when price is still outside the zone at arm-time
  (ZoneFillPlanner's proximal-edge pattern: zone.High for Buy, zone.Low for
  Sell). When price is already inside the zone at arm-time the real limit
  order fills at the live price instead of this edge, so some slippage
  between this pip estimate and the real fill is expected and accepted -
  the same tolerance every other candidate type already has.
  """
  sig = {
    "action": intent.direction,
    "entry": intent.entry_low,
    "entry_end": intent.entry_high,
    "symbol": "XAU",
  }
  reference_entry = pips_format.rr_entry(sig)
  targets_pips = [
    max(1, pips_format.pips_between(sig, tp))
    for tp in intent.tps
  ]
  return {
    "version": 3,
    "candidate_id": intent.intent_id,
    "symbol": "XAU",
    "timeframe": "M1",
    "setup": intent.setup_type or "Manual Algo",
    "mode": "manual_algo",
    "direction": intent.direction,
    "trigger_ts": str(intent.created_at),
    "created_at": intent.created_at,
    "spot_ts": None,
    # TradeCandidate.CurrentPrice/KeyLevel are non-nullable decimals on the
    # C# side (unlike SpotTs) - reference_entry is a reasonable stand-in and
    # not load-bearing anywhere in AutoTradeEngine.cs's processing (only the
    # live spot quote from ObserveSpotAsync drives actual entry decisions).
    "current_price": reference_entry,
    "key_level": reference_entry,
    "entry_zone": {"low": intent.entry_low, "high": intent.entry_high},
    "confluence": intent.confluence or 1,
    "reasons": ["manual /algo signal"],
    "manual_stop_loss": intent.sl,
    "manual_expires_at": intent.expires_at,
    "targets_pips": targets_pips,
    "manual_take_profits": list(intent.tps),
    "group_id": intent.intent_id,
    "strategy_family": "manual",
    "zone_id": f"manual-zone:{intent.manual_signal_id}",
    "trigger_id": intent.intent_id,
    "parent_group_id": None,
    "structural_source": "owner_instruction",
    "bias": "neutral",
    "relationship_to_bias": "neutral",
  }


async def _process_intent_entries(client, entries, *, cursor: str) -> str:
  for entry_id, fields in entries:
    try:
      payload = json.loads(fields["payload"])
      intent = ManualTradeIntent(**payload)
      candidate = _intent_to_candidate_payload(intent)
      await client.xadd(
        settings.auto_trade_stream,
        {"payload": json.dumps(candidate, separators=(",", ":"))},
        maxlen=max(100, settings.auto_trade_stream_maxlen),
        approximate=True,
      )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
      log.warning("Invalid manual trade intent %s: %s", entry_id, exc)
    cursor = entry_id
    await client.set(_INTENT_BRIDGE_CURSOR_KEY, cursor)
  return cursor


async def bridge_intents_loop() -> None:
  if not settings.manual_algo_enabled:
    return
  client = redis_state.get_client()
  cursor = await client.get(_INTENT_BRIDGE_CURSOR_KEY)
  if not cursor:
    latest = await client.xrevrange(settings.manual_trade_intent_stream, count=1)
    cursor = latest[0][0] if latest else "0-0"
    await client.set(_INTENT_BRIDGE_CURSOR_KEY, cursor)
  log.info("Manual-algo intent bridge active from Redis cursor %s", cursor)

  while True:
    try:
      batches = await client.xread(
        {settings.manual_trade_intent_stream: cursor},
        count=20,
        block=5000,
      )
      for _, entries in batches:
        cursor = await _process_intent_entries(client, entries, cursor=cursor)
    except asyncio.CancelledError:
      raise
    except Exception:
      cursor = str(await client.get(_INTENT_BRIDGE_CURSOR_KEY) or cursor)
      log.exception(
        "Manual-algo intent bridge failed at cursor %s; retrying", cursor,
      )
      await asyncio.sleep(5)


async def _resolve_signal_id(
  event: dict,
  positions: dict[int, int],
) -> int | None:
  """Resolve a broker event's manual_signals id, caching by position_id.

  candidate_id is present on every event type AutoTradeEngine.cs publishes
  for a position (confirmed by reading every PublishAsync call site), so
  even if this process restarted after a manual-algo position filled (losing
  the in-memory cache), a later event still self-heals the mapping via
  candidate_id - guarded by requiring the resolved signal's own
  broker_position_id to match, so a stray candidate_id prefix collision
  can't attach an event to the wrong signal.
  """
  raw_position_id = event.get("position_id")
  if raw_position_id is None:
    return None
  position_id = int(raw_position_id)
  cached = positions.get(position_id)
  if cached is not None:
    return cached
  candidate_id = event.get("candidate_id")
  if not candidate_id:
    return None
  sig = await get_signal_by_execution_intent_id(str(candidate_id))
  if (
    sig is None
    or sig.get("execution_mode") != "algo"
    or str(sig.get("broker_position_id") or "") != str(position_id)
  ):
    return None
  positions[position_id] = sig["id"]
  return sig["id"]


async def _handle_fill_event(
  event: dict,
  positions: dict[int, int],
) -> None:
  from app.signals import trade_ops  # local import breaks the module cycle

  if event.get("stream") != "algo_manual":
    return
  candidate_id = event.get("candidate_id")
  position_id = event.get("position_id")
  if not candidate_id or position_id is None:
    log.warning("manual_opened event missing candidate_id/position_id: %s", event)
    return
  sig = await get_signal_by_execution_intent_id(str(candidate_id))
  if sig is None:
    log.warning(
      "manual_opened event: no signal for intent token %s", candidate_id,
    )
    return
  positions[int(position_id)] = sig["id"]
  price = event.get("price")
  if price is None:
    log.error("manual_opened event missing fill price for signal %s", sig["id"])
    await set_execution_status(sig["id"], "error", error="fill event missing price")
    return
  await set_execution_fill(
    sig["id"], broker_position_id=int(position_id), broker_fill_price=float(price),
  )
  await _send_executor_truth(
    "✅ <b>POSITION OPENED</b>\n"
    f"Direction: <b>{event.get('direction') or sig.get('action')}</b>\n"
    f"Fill price: <code>{_price(price)}</code>\n"
    f"Volume: <code>{event.get('volume') or 'n/a'}</code>\n"
    f"Position ID: <code>{position_id}</code>"
  )
  result = await trade_ops.do_active({"sid": sig["id"]})
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


async def _handle_take_profit(event: dict, signal_id: int) -> None:
  from app.signals import trade_ops

  sig = await get_manual_signal(signal_id)
  if sig is None:
    return
  price = event.get("price")
  if price is None:
    log.error("take_profit event missing price for signal %s", signal_id)
    return
  pips = pips_format.signed_result_pips(sig, float(price))
  configured = [
    pips_format.pips_between(sig, tp) for tp in sig.get("tps") or []
  ]
  target_pips = event.get("target_pips")
  reached = 0
  if target_pips is not None and configured:
    reached = max(
      (
        index + 1
        for index, configured_pips in enumerate(configured)
        if configured_pips <= int(target_pips)
      ),
      default=0,
    )
    if reached:
      await redis_state.set_tp_progress(signal_id, reached)
      await redis_state.set_runner_pips(signal_id, int(target_pips))
  # The broker's real target ladder can collapse (BuildTargetPlan skips
  # middle targets when volume is too small for every configured exit), so
  # "is this the last leg" is decided by comparing against the LARGEST
  # configured pip distance, not by counting take_profit events seen.
  frac = None
  if configured and target_pips is not None and int(target_pips) != max(configured):
    frac = round(1.0 / len(configured), 6)
  result = await trade_ops._execute_close(
    signal_id, sig.get("symbol", "XAU"), pips, frac,
    tp_number=reached or None,
  )
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


async def _handle_position_closed(event: dict, signal_id: int) -> None:
  from app.signals import trade_ops

  sig = await get_manual_signal(signal_id)
  if sig is None:
    return
  price = event.get("price")
  if price is None:
    log.error("position_closed event missing price for signal %s", signal_id)
    await set_execution_status(
      signal_id, "error", error="position_closed event missing price",
    )
    return
  pips = pips_format.sl_result_pips(sig, float(price))
  result = await trade_ops._execute_close(
    signal_id, sig.get("symbol", "XAU"), pips, None,
  )
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


async def _handle_manual_closed(
  client,
  event: dict,
  signal_id: int,
) -> None:
  from app.signals import trade_ops

  position_id = event.get("position_id")
  frac = None
  if position_id is not None:
    key = _pending_close_key(int(position_id))
    raw = await client.get(key)
    if raw:
      try:
        frac = json.loads(raw).get("frac")
      except (TypeError, json.JSONDecodeError):
        frac = None
      await client.delete(key)
  sig = await get_manual_signal(signal_id)
  if sig is None:
    return
  price = event.get("price")
  if price is None:
    log.error("manual_closed event missing price for signal %s", signal_id)
    await set_execution_status(
      signal_id, "error", error="close command missing execution price",
    )
    return
  pips = pips_format.sl_result_pips(sig, float(price))
  result = await trade_ops._execute_close(
    signal_id, sig.get("symbol", "XAU"), pips, frac,
  )
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


async def _handle_manual_sl_moved(event: dict, signal_id: int) -> None:
  from app.signals import trade_ops

  price = event.get("price")
  if price is None:
    return
  result = await trade_ops._execute_sl(signal_id, float(price), is_be=False)
  sig = await get_manual_signal(signal_id)
  await trade_ops.post_result(result, (sig or {}).get("symbol", "XAU"))


async def _handle_manual_cancelled(event: dict) -> None:
  from app.signals import trade_ops

  candidate_id = event.get("candidate_id")
  if not candidate_id:
    return
  sig = await get_signal_by_execution_intent_id(str(candidate_id))
  if sig is None:
    log.warning(
      "manual_cancelled event: no signal for intent token %s", candidate_id,
    )
    return
  await set_execution_status(sig["id"], "cancelled")
  result = await trade_ops._execute_cancel(sig["id"])
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


async def _handle_manual_expired(event: dict) -> None:
  candidate_id = event.get("candidate_id")
  if not candidate_id:
    return
  sig = await get_signal_by_execution_intent_id(str(candidate_id))
  if sig is None:
    log.warning("manual_expired event: no signal for intent token %s", candidate_id)
    return
  await set_execution_status(sig["id"], "expired")


async def _handle_command_error(
  event: dict,
  positions: dict[int, int],
) -> None:
  """A broker-side owner-override command failed - mark the signal's
  execution lifecycle as errored rather than leaving it silently 'pending'
  forever. Not fanned out to the channel; this is an operational failure,
  not a trade-lifecycle update the audience needs to see.
  """
  candidate_id = event.get("candidate_id")
  signal_id = None
  if candidate_id:
    sig = await get_signal_by_execution_intent_id(str(candidate_id))
    if sig is not None:
      signal_id = sig["id"]
  if signal_id is None:
    signal_id = await _resolve_signal_id(event, positions)
  if signal_id is None:
    return
  await set_execution_status(
    signal_id, "error", error=str(event.get("message") or "broker command failed"),
  )


async def _handle_event(
  client,
  event: dict,
  positions: dict[int, int],
) -> None:
  event_type = event.get("type")
  is_manual = event.get("stream") == "algo_manual"
  if event_type == "manual_limit_placed" and is_manual:
    await _handle_limit_placed(event)
    return
  if event_type == "dry_run" and is_manual:
    await _handle_dry_run(event)
    return
  if event_type == "rejected" and is_manual:
    await _handle_execution_rejected(event)
    return
  if event_type == _FILL_EVENT_TYPE:
    await _handle_fill_event(event, positions)
    return
  if event_type == "manual_cancelled":
    await _handle_manual_cancelled(event)
    return
  if event_type == "manual_expired":
    await _handle_manual_expired(event)
    return
  if event_type == "manual_command_error":
    await _handle_command_error(event, positions)
    return
  if event_type == "stop_moved":
    # Informational only - no manual_signals mutation, the existing
    # trailing-stop technique (StopTrailPlanner) needs nothing from here.
    return
  signal_id = await _resolve_signal_id(event, positions)
  if signal_id is None:
    return  # not a manual-algo position this loop is tracking
  if event_type == "take_profit":
    await _handle_take_profit(event, signal_id)
  elif event_type == "position_closed":
    await _handle_position_closed(event, signal_id)
    position_id = event.get("position_id")
    if position_id is not None:
      positions.pop(int(position_id), None)
  elif event_type == "manual_closed":
    await _handle_manual_closed(client, event, signal_id)
    position_id = event.get("position_id")
    if position_id is not None:
      positions.pop(int(position_id), None)
  elif event_type == "manual_sl_moved":
    await _handle_manual_sl_moved(event, signal_id)
  # Any other type (e.g. manual_planned) is informational
  # only for this loop's purposes.


async def _process_event_entries(
  client,
  entries,
  *,
  cursor: str,
  positions: dict[int, int],
) -> str:
  for entry_id, fields in entries:
    try:
      event = json.loads(fields["payload"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
      log.warning("Invalid auto-trade event %s: %s", entry_id, exc)
    else:
      await _handle_event(client, event, positions)
    cursor = entry_id
    await client.set(_EVENT_CURSOR_KEY, cursor)
  return cursor


async def reconcile_events_loop() -> None:
  if not settings.manual_algo_enabled:
    return
  client = redis_state.get_client()
  cursor = await client.get(_EVENT_CURSOR_KEY)
  if not cursor:
    latest = await client.xrevrange(settings.auto_trade_event_stream, count=1)
    cursor = latest[0][0] if latest else "0-0"
    await client.set(_EVENT_CURSOR_KEY, cursor)
  log.info("Manual-algo reconcile loop active from Redis cursor %s", cursor)
  positions: dict[int, int] = {}

  while True:
    try:
      batches = await client.xread(
        {settings.auto_trade_event_stream: cursor},
        count=20,
        block=5000,
      )
      for _, entries in batches:
        cursor = await _process_event_entries(
          client, entries, cursor=cursor, positions=positions,
        )
    except asyncio.CancelledError:
      raise
    except Exception:
      cursor = str(await client.get(_EVENT_CURSOR_KEY) or cursor)
      log.exception(
        "Manual-algo reconcile loop failed at cursor %s; retrying", cursor,
      )
      await asyncio.sleep(5)


async def _xadd_command(payload: dict) -> None:
  client = redis_state.get_client()
  await client.xadd(
    settings.manual_trade_command_stream,
    {"payload": json.dumps(payload, separators=(",", ":"))},
    maxlen=max(100, settings.manual_trade_command_stream_maxlen),
    approximate=True,
  )


async def request_close_all() -> None:
  """Owner `/auto_close_all`: flatten every tracked ApexVoid Algo position."""
  await _xadd_command({"type": "close_all"})


async def request_cancel(intent_id: str) -> None:
  """/trade_cancel on an armed (not yet filled) manual algo signal."""
  await _xadd_command({"type": "cancel_pending", "intent_id": intent_id})


async def request_close(
  signal_id: int,
  position_id: int,
  *,
  frac: float | None = None,
) -> None:
  """/trade_close on a filled manual algo signal.

  Remembers the requested ``frac`` in Redis (not carried on the C# side's
  confirmation event) so ``_handle_manual_closed`` can book the SAME
  fraction the owner asked for once the broker confirms the close.
  """
  client = redis_state.get_client()
  await client.set(
    _pending_close_key(position_id),
    json.dumps({"signal_id": signal_id, "frac": frac}),
    ex=3600,
  )
  await _xadd_command({
    "type": "close",
    "position_id": position_id,
    "frac": frac,
  })


async def request_move_sl(signal_id: int, position_id: int, price: float) -> None:
  """/trade_sl on a filled manual algo signal."""
  await _xadd_command({
    "type": "move_sl",
    "position_id": position_id,
    "price": price,
  })
