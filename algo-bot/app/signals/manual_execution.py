"""Consumer + real broker execution for owner-armed manual /algo signals.

Two independent dispatch loops, both no-ops unless
``runtime_config.manual_algo.runtime.enabled``:

- ``bridge_intents_loop``: reads ``manual_trade:intents`` and routes each
  intent to a dedicated per-symbol worker queue.
- ``reconcile_events_loop``: reads ``auto_trade:events`` and routes manual-
  algo lifecycle events to the matching symbol worker.

Each live symbol runs ``_symbol_worker_loop`` — one asyncio task per symbol
with its own event queue and ``position_id → signal_id`` cache so many FX
pairs stay current without one slow symbol blocking the rest.

Both paths drive broker fill/TP/SL/close events through the SAME
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
from typing import Any

from app.bot.client import send_scanner_with_retry
from app.core.config import runtime_config
from app.persistence import redis_state
from app.persistence.store import (
  get_manual_signal,
  get_signal_by_execution_intent_id,
  set_execution_fill,
  set_execution_status,
)
from app.signals import pips_format
from app.signals.fx_manual_algo import (
  close_ratio_weights,
  fx_manual_symbols,
  uses_entry_price_display,
)
from app.signals.manual_intent import ManualTradeIntent

log = logging.getLogger(__name__)

_INTENT_BRIDGE_CURSOR_KEY = "manual_trade:intent_bridge_cursor"
_EVENT_CURSOR_KEY = "manual_trade:algo_event_cursor"

_symbol_queues: dict[str, asyncio.Queue[tuple[str, Any]]] = {}
_symbol_worker_tasks: dict[str, asyncio.Task[None]] = {}

# The distinct fill-event type AutoTradeEngine.cs publishes the FIRST time a
# manual-algo limit order fills (see AdoptPositionAsync in
# ctrader-engine/src/AutoTradeEngine.cs). Deliberately NOT "opened" - that
# type stays reserved for the autonomous engines' own "🤖 ApexVoid Algo"
# owner-DM card (app.autotrade.delivery._format_opened); a manually-typed
# /algo signal is still fundamentally a manual signal and should not
# duplicate that card, only the channel update this loop drives.
_FILL_EVENT_TYPE = "manual_opened"


def _pending_delete_key(intent_id: str) -> str:
  return f"manual_trade:pending_delete:{intent_id}"


def _pending_modify_key(intent_id: str) -> str:
  return f"manual_trade:pending_modify:{intent_id}"


async def mark_pending_delete(intent_id: str) -> None:
  """Remember /trade_delete so manual_cancelled hard-deletes, not cancels."""
  client = redis_state.get_client()
  await client.set(_pending_delete_key(intent_id), "1", ex=3600)


async def consume_pending_delete(intent_id: str) -> bool:
  client = redis_state.get_client()
  key = _pending_delete_key(intent_id)
  raw = await client.get(key)
  if raw is None:
    return False
  await client.delete(key)
  return True


async def mark_pending_modify(intent_id: str) -> None:
  """Remember /trade_modify so manual_cancelled re-arms instead of cancel."""
  client = redis_state.get_client()
  await client.set(_pending_modify_key(intent_id), "1", ex=3600)


async def consume_pending_modify(intent_id: str) -> bool:
  client = redis_state.get_client()
  key = _pending_modify_key(intent_id)
  raw = await client.get(key)
  if raw is None:
    return False
  await client.delete(key)
  return True


def _price(value: object, symbol: str = "XAU") -> str:
  if value is None:
    return "n/a"
  from app.core.symbols import digits_for

  digits = digits_for(symbol)
  return f"{float(value):,.{digits}f}".rstrip("0").rstrip(".")


def _entry_event_text(
  event: dict,
  *,
  symbol: str,
  entry_low: object,
  entry_high: object,
) -> str:
  if entry_low is not None and entry_high is not None:
    low = float(entry_low)
    high = float(entry_high)
    if uses_entry_price_display(symbol, low, high):
      return _price(low, symbol)
    return f"{_price(low, symbol)}-{_price(high, symbol)}"
  return _price(event.get("price"), symbol)


def _manual_algo_symbols() -> tuple[str, ...]:
  symbols = set(runtime_config.live_instruments() or ())
  symbols.update(fx_manual_symbols())
  if not symbols:
    symbols.add("XAU")
  return tuple(sorted(symbol.upper() for symbol in symbols))


def _symbol_queue(symbol: str) -> asyncio.Queue[tuple[str, Any]]:
  symbol = symbol.upper()
  queue = _symbol_queues.get(symbol)
  if queue is None:
    queue = asyncio.Queue()
    _symbol_queues[symbol] = queue
  return queue


def _ensure_symbol_worker(symbol: str) -> None:
  symbol = symbol.upper()
  task = _symbol_worker_tasks.get(symbol)
  if task is not None and not task.done():
    return
  _symbol_worker_tasks[symbol] = asyncio.create_task(
    _symbol_worker_loop(symbol),
    name=f"manual_algo_worker_{symbol}",
  )


def _ensure_manual_algo_workers() -> None:
  for symbol in _manual_algo_symbols():
    _symbol_queue(symbol)
    _ensure_symbol_worker(symbol)


def _is_manual_algo_event(event: dict) -> bool:
  if event.get("stream") == "algo_manual":
    return True
  candidate_id = str(event.get("candidate_id") or "")
  return candidate_id.startswith("manual:")


def _event_route_symbol(event: dict) -> str | None:
  raw = event.get("symbol")
  if raw:
    return str(raw).upper()
  return None


async def _symbol_worker_loop(symbol: str) -> None:
  queue = _symbol_queue(symbol)
  client = redis_state.get_client()
  positions: dict[int, int] = {}
  log.info("Manual-algo symbol worker started symbol=%s", symbol)
  while True:
    kind, payload = await queue.get()
    try:
      if kind == "intent":
        await _publish_intent(client, payload)
      elif kind == "event":
        await _handle_event(client, payload, positions)
    except asyncio.CancelledError:
      raise
    except Exception:
      log.exception(
        "Manual-algo symbol worker failed symbol=%s kind=%s",
        symbol,
        kind,
      )
      await asyncio.sleep(1)
    finally:
      queue.task_done()


async def _enqueue_intent(intent: ManualTradeIntent) -> None:
  symbol = intent.symbol.upper()
  _ensure_symbol_worker(symbol)
  await _symbol_queue(symbol).put(("intent", intent))


async def _enqueue_event(event: dict) -> None:
  symbol = _event_route_symbol(event)
  if symbol is None:
    symbol = await _symbol_from_manual_candidate(event)
  if symbol is None:
    log.warning(
      "manual-algo event dropped; missing symbol type=%s candidate=%s",
      event.get("type"),
      event.get("candidate_id"),
    )
    return
  _ensure_symbol_worker(symbol)
  await _symbol_queue(symbol).put(("event", event))


async def _symbol_from_manual_candidate(event: dict) -> str | None:
  """Recover the worker symbol when the engine omitted it on a lifecycle event."""
  candidate_id = event.get("candidate_id")
  if not candidate_id:
    return None
  sig = await get_signal_by_execution_intent_id(str(candidate_id))
  if sig is None:
    return None
  raw = sig.get("symbol")
  return str(raw or "XAU").upper()



def _target_text(event: dict, symbol: str) -> str:
  targets = event.get("target_prices") or []
  return " / ".join(_price(value, symbol) for value in targets) or "n/a"


async def _send_executor_truth(text: str) -> None:
  if runtime_config.delivery.telegram.telegram_owner_id:
    await send_scanner_with_retry(
      text,
      chat_id=runtime_config.delivery.telegram.telegram_owner_id,
    )


async def _handle_limit_placed(event: dict) -> None:
  from app.signals import trade_ops

  candidate_id = str(event.get("candidate_id") or "")
  if not candidate_id:
    return
  sig = await get_signal_by_execution_intent_id(candidate_id)
  if sig is None:
    return
  await set_execution_status(sig["id"], "pending")
  symbol = sig.get("symbol", "XAU")
  entry = _entry_event_text(
    event,
    symbol=symbol,
    entry_low=event.get("entry_low"),
    entry_high=event.get("entry_high"),
  )
  if runtime_config.manual_algo.runtime.owner_execution_dm_enabled:
    await _send_executor_truth(
      "✅ <b>LIMIT ORDER PLACED</b>\n"
      f"Direction: <b>{event.get('direction') or 'n/a'}</b>\n"
      f"Entry: <code>{entry}</code>\n"
      f"SL: <code>{_price(event.get('stop_loss'), symbol)}</code>\n"
      f"TPs: <code>{_target_text(event, symbol)}</code>\n"
      f"Order ID: <code>{event.get('order_id') or 'n/a'}</code>\n"
      f"Candidate ID: <code>{candidate_id}</code>"
    )
  result = await trade_ops.do_limit_pending({"sid": sig["id"]})
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


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
    "symbol": intent.symbol,
  }
  reference_entry = pips_format.rr_entry(sig)
  targets_pips = [
    max(1, pips_format.pips_between(sig, tp))
    for tp in intent.tps
  ]
  payload = {
    "version": 3,
    "candidate_id": intent.intent_id,
    "symbol": intent.symbol,
    "timeframe": "M1",
    "setup": intent.setup_type or "Manual Algo",
    "mode": "manual_algo",
    # Owner-authored /algo orders bypass scanner/analysis policy by contract.
    # The executor still applies broker-mechanical checks.
    "bypass_analysis_gates": True,
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
  if intent.symbol.upper() in fx_manual_symbols():
    payload["manual_target_weights"] = close_ratio_weights(intent.symbol)
    payload["manual_single_entry"] = True
    multiplier = float(runtime_config.manual_algo.sizing.fx_volume_multiplier)
    if multiplier > 0 and multiplier != 1.0:
      payload["risk_multiplier"] = multiplier
  return payload


async def _publish_intent(client, intent: ManualTradeIntent) -> None:
  candidate = _intent_to_candidate_payload(intent)
  await client.xadd(
    runtime_config.contract.streams.candidates,
    {"payload": json.dumps(candidate, separators=(",", ":"))},
    maxlen=max(100, runtime_config.contract.streams.candidate_maximum_length),
    approximate=True,
  )


async def _process_intent_entries(client, entries, *, cursor: str) -> str:
  for entry_id, fields in entries:
    try:
      payload = json.loads(fields["payload"])
      intent = ManualTradeIntent(**payload)
      await _publish_intent(client, intent)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
      log.warning("Invalid manual trade intent %s: %s", entry_id, exc)
    cursor = entry_id
    await client.set(_INTENT_BRIDGE_CURSOR_KEY, cursor)
  return cursor


async def _dispatch_intent_entries(client, entries, *, cursor: str) -> str:
  for entry_id, fields in entries:
    try:
      payload = json.loads(fields["payload"])
      intent = ManualTradeIntent(**payload)
      await _enqueue_intent(intent)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
      log.warning("Invalid manual trade intent %s: %s", entry_id, exc)
    cursor = entry_id
    await client.set(_INTENT_BRIDGE_CURSOR_KEY, cursor)
  return cursor


async def bridge_intents_loop() -> None:
  if not runtime_config.manual_algo.runtime.enabled:
    return
  _ensure_manual_algo_workers()
  client = redis_state.get_client()
  cursor = await client.get(_INTENT_BRIDGE_CURSOR_KEY)
  if not cursor:
    latest = await client.xrevrange(runtime_config.manual_algo.streams.intents, count=1)
    cursor = latest[0][0] if latest else "0-0"
    await client.set(_INTENT_BRIDGE_CURSOR_KEY, cursor)
  log.info(
    "Manual-algo intent bridge active from Redis cursor %s workers=%s",
    cursor,
    ",".join(_manual_algo_symbols()),
  )

  while True:
    try:
      batches = await client.xread(
        {runtime_config.manual_algo.streams.intents: cursor},
        count=20,
        block=5000,
      )
      for _, entries in batches:
        cursor = await _dispatch_intent_entries(client, entries, cursor=cursor)
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
  candidate_id. get_signal_by_execution_intent_id matches by a 10-char
  PREFIX (comment fields are broker-truncated), so a re-armed signal's
  newer revision (e.g. "manual:89:1" after "manual:89:0") shares the same
  prefix as the original and can win the "most recent" tiebreak - guard by
  requiring an EXACT match against the event's own (always full, untruncated)
  candidate_id, not a single stored broker_position_id. A manual /algo
  signal can be several independent entry legs (shallow/mid/deep) sharing
  one candidate_id but each its own position_id, so requiring position_id
  equality here (the old guard) silently failed to resolve every leg but
  the first one to fill after a restart.
  """
  raw_position_id = event.get("position_id")
  if raw_position_id is not None:
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
    or str(sig.get("execution_intent_id") or "") != str(candidate_id)
  ):
    return None
  if raw_position_id is not None:
    positions[int(raw_position_id)] = sig["id"]
  return sig["id"]


async def _handle_fill_event(
  event: dict,
  positions: dict[int, int],
) -> None:
  from app.signals import trade_ops  # local import breaks the module cycle

  if event.get("stream") != "algo_manual" and not str(
    event.get("candidate_id") or ""
  ).startswith("manual:"):
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
  if runtime_config.manual_algo.runtime.owner_execution_dm_enabled:
    await _send_executor_truth(
      "✅ <b>POSITION OPENED</b>\n"
      f"Direction: <b>{event.get('direction') or sig.get('action')}</b>\n"
      f"Fill price: <code>{_price(price, sig.get('symbol', 'XAU'))}</code>\n"
      f"Volume: <code>{event.get('volume') or 'n/a'}</code>\n"
      f"Position ID: <code>{position_id}</code>"
    )
  # The real, subscriber-facing "🟢 active — order filled" update is what
  # this call posts to the VIP/public channel(s) - that already tells the
  # owner (a member of their own VIP channel) the position is live, so no
  # separate owner-only DM is needed on top of it.
  result = await trade_ops.do_active({"sid": sig["id"]})
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


def _tp_ordinal_reached(sig: dict, target_pips: object) -> int:
  """Highest configured TP ordinal covered by ``target_pips`` (0 if none)."""
  if target_pips is None:
    return 0
  configured = [
    pips_format.pips_between(sig, tp) for tp in sig.get("tps") or []
  ]
  if not configured:
    return 0
  return max(
    (
      index + 1
      for index, configured_pips in enumerate(configured)
      if configured_pips <= int(target_pips)
    ),
    default=0,
  )


def _stop_is_further(sig: dict, new_sl: float, current_sl: float) -> bool:
  """True when ``new_sl`` locks more profit than ``current_sl`` (direction-aware).

  Ladder fills publish one ``stop_moved`` per entry leg. Channel updates must
  follow the furthest stop only — trailing legs at the same or worse level
  are ignored.
  """
  from app.core.symbols import pip_for

  pip = pip_for(sig.get("symbol", "XAU"))
  if abs(new_sl - current_sl) <= pip * 0.5:
    return False
  if str(sig.get("action") or "").upper() == "BUY":
    return new_sl > current_sl
  return new_sl < current_sl


async def _handle_take_profit(event: dict, signal_id: int) -> None:
  """Book + fan out when signal-level TP progress advances.

  A XAU ladder can fill several entry legs that share one channel card. Each
  leg publishes its own ``take_profit`` events; only the furthest TP ordinal
  for the signal is booked and posted — sibling legs at the same (or lower)
  TP are ignored so the channel is not spammed and frac is not double-counted.

  Pip math uses ``broker_fill_price`` (deepest filled entry: deep → mid →
  shallow) so a SELL that filled the deep edge books TP1 from that fill, not
  the shallow zone edge.
  """
  from app.signals import trade_ops

  sig = await get_manual_signal(signal_id)
  if sig is None:
    return
  price = event.get("price")
  if price is None:
    log.error("take_profit event missing price for signal %s", signal_id)
    return
  configured = [
    pips_format.pips_between(sig, tp) for tp in sig.get("tps") or []
  ]
  target_pips = event.get("target_pips")
  reached = _tp_ordinal_reached(sig, target_pips)
  if reached:
    progress = await redis_state.get_progress(signal_id)
    current_tp = int(progress.get("tp") or 0)
    if reached <= current_tp:
      log.info(
        "manual-algo take_profit skipped signal=%s tp=%s already=%s "
        "position_id=%s",
        signal_id,
        reached,
        current_tp,
        event.get("position_id"),
      )
      return
    await redis_state.set_tp_progress(signal_id, reached)
    if target_pips is not None:
      await redis_state.set_runner_pips(signal_id, int(target_pips))
  # Channel pips measure from deepest stored fill (deep → mid → shallow),
  # not from whichever leg's take_profit event arrived first.
  pips = pips_format.signed_result_pips(sig, float(price))
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
  """A broker-detected close (stop loss, or an unconfirmed disappearance)
  for ONE entry leg. A manual /algo signal's entry can be several
  independent legs (shallow/mid/deep) sharing one group - this leg fully
  closing does not mean the SIGNAL is closed while siblings remain open,
  so only a genuine partial (this leg itself still has volume left, e.g.
  the leg's own take-profit ladder booking a slice) is recorded here.
  A leg closing completely is left entirely to ``_handle_group_result``,
  which fires exactly once the whole group has no open volume left and
  carries AutoTradeEngine.cs's own correctly volume-weighted total -
  booking a full close here too would let close_leg's own ledger finalize
  the signal FIRST, using its "pure loss = last leg's own pips" rule that
  silently drops every sibling leg's result (see finalize_manual_group).
  """
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
  if not (event.get("remaining_volume") or 0) > 0:
    return
  pips, frac = _leg_close_pips_and_frac(sig, event, float(price))
  result = await trade_ops._execute_close(
    signal_id, sig.get("symbol", "XAU"), pips, frac,
  )
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


def _leg_close_pips_and_frac(
  sig: dict, event: dict, price: float,
) -> tuple[int, float | None]:
  """This leg's own realized pips and its fraction of the group's total
  volume, from the fields AutoTradeEngine.cs already computes per leg.
  Falls back to the pre-multi-leg calc (whole-signal broker_fill_price)
  only if an event is missing the newer fields (e.g. mid-deploy replay).
  """
  leg_pips = event.get("leg_realized_pips")
  volume = event.get("volume")
  group_initial_volume = event.get("group_initial_volume")
  if leg_pips is None or not volume or not group_initial_volume:
    return pips_format.sl_result_pips(sig, price), None
  return round(float(leg_pips)), round(float(volume) / float(group_initial_volume), 6)


async def _resolve_group_close_pips(signal_id: int, group_pips: float) -> int:
  """Close-card pips for ``group_result`` — never below TPs already booked.

  ``group_realized_pips`` can reflect the last shallow leg's blend while
  channel TP cards already printed higher pips from the deepest fill.
  Prefer the ledger peak and redis runner telemetry when they are higher.
  """
  from app.signals import pips_format

  sig = await get_manual_signal(signal_id)
  pips = round(float(group_pips))
  raw_legs = (sig or {}).get("legs") or "[]"
  if isinstance(raw_legs, str):
    existing = json.loads(raw_legs)
  else:
    existing = list(raw_legs)
  if existing:
    peak = pips_format.legs_achieved_pips(existing)
    if peak > 0:
      pips = max(pips, peak)
  progress = await redis_state.get_progress(signal_id)
  runner = progress.get("runner_pips")
  if isinstance(runner, int) and runner > 0:
    pips = max(pips, runner)
  return pips


async def _handle_group_result(event: dict, signal_id: int) -> None:
  """The single authoritative close for a manual /algo signal - fires once
  AutoTradeEngine.cs confirms every entry leg in the group has no volume
  left, carrying its own correctly volume-weighted (or achieved-TP)
  ``group_realized_pips``. See finalize_manual_group for why this bypasses
  close_leg's own ledger instead of feeding it.
  """
  from app.signals import trade_ops

  pips = event.get("group_realized_pips")
  if pips is None:
    log.error("group_result event missing group_realized_pips for signal %s", signal_id)
    return
  sig = await get_manual_signal(signal_id)
  symbol = (sig or {}).get("symbol", "XAU")
  resolved = await _resolve_group_close_pips(signal_id, float(pips))
  result = await trade_ops._execute_group_close(signal_id, symbol, resolved)
  await trade_ops.post_result(result, symbol)


async def _handle_manual_closed(
  event: dict,
  signal_id: int,
) -> None:
  """The owner-confirmed counterpart to ``_handle_position_closed`` (one
  entry leg closed via /trade_close or /auto_close_all rather than a
  broker-detected stop). Same reasoning: only a genuine partial (this leg
  itself still has volume left) is booked here; a leg finishing completely
  is left to ``_handle_group_result``.
  """
  from app.signals import trade_ops

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
  if not (event.get("remaining_volume") or 0) > 0:
    return
  pips, frac = _leg_close_pips_and_frac(sig, event, float(price))
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


def _stop_move_is_breakeven(sig: dict, new_sl: float, event: dict) -> bool:
  message = str(event.get("message") or "")
  if "BE" in message.upper():
    return True
  entry = pips_format.actual_entry(sig)
  from app.core.symbols import pip_for

  pip = pip_for(sig.get("symbol", "XAU"))
  return abs(new_sl - entry) <= pip * 2


async def _handle_stop_moved(event: dict, signal_id: int) -> None:
  """Automatic broker stop trail (e.g. BE after TP1) for manual-algo fills.

  Owner-initiated /trade_sl publishes ``manual_sl_moved`` instead; both paths
  must fan out through ``trade_ops.post_result`` because delivery.py suppresses
  manual-algo ``stop_moved`` events to avoid duplicate owner DMs.

  Multi-leg ladders emit one stop amend per filled entry leg. Only a stop that
  is further (more profit locked) than the signal's current SL is applied and
  posted — trailing legs at the same level are ignored.
  """
  from app.signals import trade_ops

  price = event.get("price")
  if price is None:
    log.error("stop_moved event missing price for signal %s", signal_id)
    return
  sig = await get_manual_signal(signal_id)
  if sig is None:
    return
  new_sl = float(price)
  current_sl = sig.get("sl")
  if current_sl is not None and not _stop_is_further(sig, new_sl, float(current_sl)):
    log.info(
      "manual-algo stop_moved skipped signal=%s new=%s current=%s "
      "position_id=%s",
      signal_id,
      new_sl,
      current_sl,
      event.get("position_id"),
    )
    return
  is_be = _stop_move_is_breakeven(sig, new_sl, event)
  result = await trade_ops._execute_sl(signal_id, new_sl, is_be=is_be)
  await trade_ops.post_result(result, sig.get("symbol", "XAU"))


async def _handle_manual_cancelled(event: dict) -> None:
  from app.signals import trade_ops
  from app.persistence.store import get_manual_signal

  candidate_id = event.get("candidate_id")
  if not candidate_id:
    return
  intent_token = str(candidate_id)
  sig = await get_signal_by_execution_intent_id(intent_token)
  if sig is None:
    log.warning(
      "manual_cancelled event: no signal for intent token %s", candidate_id,
    )
    return
  # /trade_modify: levels already updated — re-arm a bumped intent and
  # replace VIP/public cards. Do not cancel the signal lifecycle.
  if await consume_pending_modify(intent_token):
    fresh = await get_manual_signal(sig["id"]) or sig
    rearmed = await trade_ops._rearm_algo_after_modify(fresh)
    if rearmed is None:
      await _send_executor_truth(
        f"⚠️ #{fresh.get('daily_seq') or fresh['id']} modify re-arm failed",
      )
      return
    result = await trade_ops._finish_modify(rearmed)
    text = trade_ops.render_result(
      result, rearmed.get("symbol", "XAU"), "vip",
    )
    await _send_executor_truth(text)
    return
  # /trade_delete asked to remove the typo card after broker cancel — not
  # leave a cancelled lifecycle row. Cancel path stays for /trade_cancel.
  if await consume_pending_delete(intent_token):
    result = await trade_ops._execute_delete(sig["id"])
    # Channel posts are already gone via delete_posts; post_result skips
    # fan-out for delete. Owner still needs the final 🗑 deleted ack.
    text = trade_ops.render_result(
      result, sig.get("symbol", "XAU"), "vip",
    )
    await _send_executor_truth(text)
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
  if event_type in {"stop_moved", "sl_moved"}:
    signal_id = await _resolve_signal_id(event, positions)
    if signal_id is None:
      return  # autonomous position; delivery.py owns channel fan-out
    await _handle_stop_moved(event, signal_id)
    return
  if event_type == "group_result":
    # No _resolve_signal_id positions-cache entry: group_result carries no
    # position_id of its own semantic weight (just whichever leg happened
    # to trigger it), so route by candidate_id/execution_intent_id only.
    signal_id = await _resolve_signal_id(event, positions)
    if signal_id is None:
      return
    await _handle_group_result(event, signal_id)
    return
  signal_id = await _resolve_signal_id(event, positions)
  if signal_id is None:
    return  # not a manual-algo position this loop is tracking
  if event_type == "take_profit":
    await _handle_take_profit(event, signal_id)
  elif event_type == "position_closed":
    await _handle_position_closed(event, signal_id)
    if not (event.get("remaining_volume") or 0) > 0:
      position_id = event.get("position_id")
      if position_id is not None:
        positions.pop(int(position_id), None)
  elif event_type == "manual_closed":
    await _handle_manual_closed(event, signal_id)
    if not (event.get("remaining_volume") or 0) > 0:
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
) -> str:
  for entry_id, fields in entries:
    try:
      event = json.loads(fields["payload"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
      log.warning("Invalid auto-trade event %s: %s", entry_id, exc)
    else:
      if _is_manual_algo_event(event):
        await _enqueue_event(event)
    cursor = entry_id
    await client.set(_EVENT_CURSOR_KEY, cursor)
  return cursor


async def _process_event_entries_inline(
  client,
  entries,
  *,
  cursor: str,
  positions: dict[int, int],
) -> str:
  """Direct handler path used by unit tests (bypasses per-symbol queues)."""
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
  if not runtime_config.manual_algo.runtime.enabled:
    return
  _ensure_manual_algo_workers()
  client = redis_state.get_client()
  cursor = await client.get(_EVENT_CURSOR_KEY)
  if not cursor:
    latest = await client.xrevrange(runtime_config.contract.streams.events, count=1)
    cursor = latest[0][0] if latest else "0-0"
    await client.set(_EVENT_CURSOR_KEY, cursor)
  log.info(
    "Manual-algo reconcile loop active from Redis cursor %s workers=%s",
    cursor,
    ",".join(_manual_algo_symbols()),
  )

  while True:
    try:
      batches = await client.xread(
        {runtime_config.contract.streams.events: cursor},
        count=20,
        block=5000,
      )
      for _, entries in batches:
        cursor = await _process_event_entries(
          client, entries, cursor=cursor,
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
    runtime_config.manual_algo.streams.manual_trade_command_stream,
    {"payload": json.dumps(payload, separators=(",", ":"))},
    maxlen=max(100, runtime_config.manual_algo.streams.manual_trade_command_stream_maxlen),
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
  intent_id: str,
  *,
  frac: float | None = None,
) -> None:
  """/trade_close on a filled manual algo signal.

  Routed by intent_id (the signal's group token), not a single position_id
  - a manual /algo signal can be several independent entry legs (shallow/
  mid/deep) sharing one group. AutoTradeEngine.cs's close handler resolves
  every currently-open leg in the group from intent_id and closes each one
  (see HandleCloseCommandAsync), applying ``frac`` uniformly to each leg's
  own remaining volume so a partial /trade_close keeps every leg's
  relative weighting intact. Each leg's own confirmation event already
  carries its own actual executed volume, so - unlike before - there is no
  need to remember the requested frac in Redis for later lookup.
  """
  del signal_id  # kept for logging symmetry with request_cancel/move_sl
  await _xadd_command({
    "type": "close",
    "intent_id": intent_id,
    "frac": frac,
  })


async def request_move_sl(signal_id: int, position_id: int, price: float) -> None:
  """/trade_sl on a filled manual algo signal."""
  await _xadd_command({
    "type": "move_sl",
    "position_id": position_id,
    "price": price,
  })
