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
import time
from typing import Any

from app.bot.client import send_scanner_with_retry, send_with_retry
from app.autotrade.go_plan_cancel import request_plan_cancel
from app.autotrade.trade_plan_stream import publish_trade_plan
from app.core.config import runtime_config
from app.persistence import redis_state
from app.persistence.store import (
  get_manual_signal,
  get_signal_by_execution_intent_id,
  set_execution_fill,
  set_execution_status,
)
from app.signals import pips_format
from app.runtime.instruments import for_instrument, live_instruments
from app.signals.manual_intent import ManualTradeIntent
from app.signals.manual_plan import build_manual_trade_plan, is_manual_plan_id
from app.autotrade.active_exposure import _mget_or_get, normalize_direction, normalize_symbol

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


def _manual_algo_symbols() -> tuple[str, ...]:
  symbols: list[str] = []
  for instrument_id in live_instruments(runtime_config) or ():
    try:
      manual = for_instrument(runtime_config, instrument_id).manual
    except Exception:
      continue
    if manual.enabled and manual.algo_enabled:
      symbols.append(instrument_id.upper())
  return tuple(sorted(set(symbols)))


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
  return event.get("stream") == "algo_manual" or is_manual_plan_id(
    event.get("candidate_id")
  )


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
  # Prefer the signal's own symbol for manual /algo events. PublishAsync used
  # to stamp the session symbol (XAU) on every FX fill/limit event, which
  # parked them on the XAU worker and broke scale-up notify/manage.
  symbol = None
  if _is_manual_algo_event(event):
    symbol = await _symbol_from_manual_candidate(event)
  if symbol is None:
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



async def _send_executor_truth(text: str) -> None:
  """Operational truth from the Auto Algo / scanner bot (rejects, dry-run)."""
  if runtime_config.telegram.telegram_owner_id:
    await send_scanner_with_retry(
      text,
      chat_id=runtime_config.telegram.telegram_owner_id,
    )


async def _send_owner_command_ack(text: str) -> None:
  """Final /trade_* command result on the ApexVoid bot — never Auto Algo.

  Owner DMs ``/trade_delete`` / ``/trade_modify`` on ApexVoid; the pending
  ack already replies there. Broker confirm must not also DM via the
  scanner bot (duplicate "Auto Algo" reply).
  """
  if runtime_config.telegram.telegram_owner_id:
    await send_with_retry(
      text,
      chat_id=runtime_config.telegram.telegram_owner_id,
    )


async def _handle_order_submitted(event: dict) -> None:
  """Record the broker's acceptance of the plan's resting order(s).

  Owner-DM only (off by default); never a channel post - the real "active"
  card is driven by the first fill (_handle_fill_event).
  """
  candidate_id = str(event.get("candidate_id") or "")
  if not candidate_id:
    return
  sig = await get_signal_by_execution_intent_id(candidate_id)
  if sig is None:
    return
  await set_execution_status(sig["id"], "pending")


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


async def _publish_intent(client, intent: ManualTradeIntent) -> None:
  """Publish the owner's /algo intent as a TradePlan V8 (the only order path)."""
  try:
    plan = build_manual_trade_plan(intent)
  except ValueError as exc:
    log.warning("Manual /algo intent %s rejected: %s", intent.intent_id, exc)
    await set_execution_status(
      intent.manual_signal_id, "rejected", error=str(exc),
    )
    await _send_executor_truth(
      "⛔ <b>ORDER REJECTED</b>\n"
      f"Reason: <code>{exc}</code>\n"
      "No broker order submitted"
    )
    return
  await publish_trade_plan(client, plan)


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

  if not _is_manual_algo_event(event):
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
  # Owner 2026-09-14: the pinned entry card must never be deleted/reposted
  # just because a fill arrived - app.signals.broadcast.render_entry always
  # shows the advertised zone-edge risk, fill or no fill, so there is
  # nothing here for a real fill to change on the card. Real accuracy
  # belongs at close time only (trade_ops._achieved_rr already trusts the
  # real fill/deepest leg there).
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
  leg publishes its own ``take_profit`` events, and different legs can own
  disjoint ordinal ranges (shallow-first booking) that arrive interleaved
  or out of order - each ordinal is booked and posted at most once per
  signal (a real repeat of the same ordinal from a sibling leg sharing
  that slice is ignored), not gated on "highest ordinal seen so far".
  An event whose ``target_pips`` cannot be resolved to any configured TP
  ordinal is dropped rather than booked - it cannot be deduped or labeled.

  Pip math uses the booking leg's own ``leg_realized_pips`` (that leg's own
  fill to its own exit), not a group-wide reference - a SELL whose shallow
  leg fills at the zone edge and hits its own target books that leg's own
  real distance, not some other leg's.
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
  if not reached:
    # target_pips did not match any configured TP ordinal (e.g. stale
    # `tps` after a /trade_modify re-arm, or an engine/DB pip-rounding
    # mismatch). Previously this fell through to book+post anyway with
    # tp_number=None (an unlabeled "booked X%" card) and, critically, with
    # NO redis-progress dedup applied at all - every sibling leg whose own
    # event also failed to resolve an ordinal posted its own undeduped
    # message. Observed live on signal 99 (XAU SELL, 2026-08-20 ~03:04
    # UTC): TP1 booked twice (+39p, +38p, 2s apart) and TP2 booked twice
    # (+68p, +66p, 3s apart) before the redis-progress dedup below existed
    # in this function. Fail closed instead: skip the post and log for
    # investigation rather than spam an unlabeled duplicate.
    log.warning(
      "manual-algo take_profit could not resolve a configured TP ordinal "
      "signal=%s target_pips=%s configured=%s position_id=%s - skipping post",
      signal_id,
      target_pips,
      configured,
      event.get("position_id"),
    )
    return
  # Owner-reported 2026-08-19 (signal 102, real XAU SELL): a "highest
  # ordinal ever seen" scalar assumes ordinals only ever climb for ONE
  # ladder, but a multi-leg group's siblings can each own disjoint ordinal
  # ranges (shallow-first booking assigns shallow the early ordinals, mid/
  # deep later ones) and their events can interleave in either order.
  # Confirmed live: shallow booked TP3 then TP4 while a sibling leg's own,
  # never-before-seen TP1 and TP2 arrived in between and after - both got
  # silently dropped ("already=3"/"already=4") because the scalar had
  # already advanced past their ordinal from the OTHER leg's progress.
  # That leg fully closed on that TP2 with no record of it anywhere - not
  # a mislabeled card, an entirely missing one. Track booked ordinals as a
  # set instead: only a genuine repeat of the SAME ordinal is a duplicate.
  if await redis_state.tp_ordinal_already_booked(signal_id, reached):
    log.info(
      "manual-algo take_profit skipped signal=%s tp=%s already booked "
      "position_id=%s",
      signal_id,
      reached,
      event.get("position_id"),
    )
    return
  await redis_state.mark_tp_ordinal_booked(signal_id, reached)
  if target_pips is not None:
    await redis_state.set_runner_pips(signal_id, int(target_pips))
  # Owner-reported 2026-08-20: pips must be the booking leg's own actual
  # entry-to-exit distance, not the signal row's shared shallow risk
  # reference blended across the whole group - AutoTradeEngine.cs computes this
  # correctly per leg (SignedPips from that leg's own EntryPrice) and
  # publishes it as leg_realized_pips on every take_profit event. Only
  # fall back to the conservative shallow-fill calc if an event predates
  # that field.
  leg_pips = event.get("leg_realized_pips")
  pips = (
    round(float(leg_pips)) if leg_pips is not None
    else pips_format.signed_result_pips(sig, float(price))
  )
  # The broker's real target ladder can collapse (BuildTargetPlan skips
  # middle targets when volume is too small for every configured exit), so
  # "is this the last leg" is decided by comparing against the LARGEST
  # configured pip distance, not by counting take_profit events seen.
  frac = None
  if configured and target_pips is not None and int(target_pips) != max(configured):
    frac = round(1.0 / len(configured), 6)
  result = await trade_ops._execute_close(
    signal_id, sig.get("symbol", "XAU"), pips, frac,
    tp_number=reached,
    entry_price=event.get("leg_entry_price"),
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
    entry_price=event.get("leg_entry_price"),
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

  ``group_realized_pips`` can reflect the final leg's weighted blend while
  channel TP cards already printed higher achieved target pips.
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

  sig = await get_manual_signal(signal_id)
  symbol = (sig or {}).get("symbol", "XAU")
  pips = event.get("group_realized_pips")
  if pips is None:
    # V8's terminal position_closed does not always carry the volume-weighted
    # group blend; fall back to the close price against the signal's entry.
    price = event.get("price")
    if sig is None or price is None:
      log.error(
        "group close event has no group_realized_pips or price for signal %s",
        signal_id,
      )
      await set_execution_status(
        signal_id, "error", error="position_closed event missing price",
      )
      return
    pips = pips_format.signed_result_pips(sig, float(price))
  resolved = await _resolve_group_close_pips(signal_id, float(pips))
  result = await trade_ops._execute_group_close(
    signal_id, symbol, resolved,
    entry_price=event.get("leg_entry_price"),
  )
  await trade_ops.post_result(result, symbol)


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
      await _send_owner_command_ack(
        f"⚠️ #{fresh.get('daily_seq') or fresh['id']} modify re-arm failed",
      )
      return
    result = await trade_ops._finish_modify(rearmed)
    text = trade_ops.render_result(
      result, rearmed.get("symbol", "XAU"), "vip",
    )
    await _send_owner_command_ack(text)
    return
  # /trade_delete asked to remove the typo card after broker cancel — not
  # leave a cancelled lifecycle row. Cancel path stays for /trade_cancel.
  if await consume_pending_delete(intent_token):
    result = await trade_ops._execute_delete(sig["id"])
    # Channel posts are already gone via delete_posts; post_result skips
    # fan-out for delete. Final 🗑 deleted ack stays on ApexVoid (command
    # bot), not Auto Algo.
    text = trade_ops.render_result(
      result, sig.get("symbol", "XAU"), "vip",
    )
    await _send_owner_command_ack(text)
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
  """Route a TradePlan V8 lifecycle event for a manual plan to its handler.

  Manual plans use the same V8 events as every other plan (``plan_id`` ==
  ``intent_id`` == ``candidate_id``); the engine labels them ``algo_manual``.
  """
  event_type = event.get("type")
  if event_type == "plan_rejected":
    await _handle_execution_rejected(event)
    return
  if event_type == "v8_order_submitted":
    await _handle_order_submitted(event)
    return
  if event_type == "order_filled":
    await _handle_fill_event(event, positions)
    return
  if event_type == "plan_cancelled":
    await _handle_manual_cancelled(event)
    return
  if event_type == "plan_expired":
    await _handle_manual_expired(event)
    return
  if event_type == "manual_command_error":
    await _handle_command_error(event, positions)
    return
  signal_id = await _resolve_signal_id(event, positions)
  if signal_id is None:
    return  # not a manual-algo position this loop is tracking
  if event_type == "sl_moved":
    await _handle_stop_moved(event, signal_id)
  elif event_type == "manual_sl_moved":
    await _handle_manual_sl_moved(event, signal_id)
  elif event_type == "tp_booked":
    await _handle_take_profit(event, signal_id)
  elif event_type == "position_closed":
    # V8 reports the group's terminal close as a single position_closed with
    # no volume left; anything with volume left is a partial leg close.
    if (event.get("remaining_volume") or 0) > 0:
      await _handle_position_closed(event, signal_id)
    else:
      await _handle_group_result(event, signal_id)
      position_id = event.get("position_id")
      if position_id is not None:
        positions.pop(int(position_id), None)
  # Any other type is informational only for this loop's purposes.


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


async def reconcile_events_loop() -> None:
  if not runtime_config.manual_algo.runtime.enabled:
    return
  _ensure_manual_algo_workers()
  client = redis_state.get_client()
  cursor = await client.get(_EVENT_CURSOR_KEY)
  if not cursor:
    latest = await client.xrevrange(runtime_config.runtime.redis_streams.events, count=1)
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
        {runtime_config.runtime.redis_streams.events: cursor},
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
  """/trade_cancel on an armed (not yet filled) manual algo signal.

  Withdraws the plan through the same single-writer cancel intent every plan
  uses; cTrader Engine cancels the resting legs and publishes ``plan_cancelled``.
  """
  await request_plan_cancel(
    redis_state.get_client(),
    intent_id,
    reason="owner cancelled the armed /algo signal",
    source="owner",
    requested_at=int(time.time()),
  )


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


async def list_open_algo_auto_positions(symbol: str | None = None) -> list[dict]:
  """Open broker legs of live, fully-autonomous (non-manual) TradePlans.

  /trade_close_auto has no owner-typed signal id to resolve from - the owner
  picks a broker position_id directly - so this surfaces the live legs from
  the TradePlan runtime state (``execution:plan_runtime:*``) that
  cTrader Engine persists, excluding manual /algo plans (those keep using
  /trade_close).
  """
  client = redis_state.get_client()
  raw_ids = await client.get("execution:trade_plan_runtime_ids")
  if not raw_ids:
    return []
  plan_ids = [
    item for item in (raw_ids.decode() if isinstance(raw_ids, bytes) else str(raw_ids)).split(",")
    if item.strip() and not is_manual_plan_id(item.strip())
  ]
  raw_states = await _mget_or_get(
    client, [f"execution:plan_runtime:{plan_id}" for plan_id in plan_ids],
  )
  wanted = normalize_symbol(symbol) if symbol else None
  out: list[dict] = []
  for raw in raw_states:
    if not raw:
      continue
    try:
      payload = json.loads(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
      continue
    if not isinstance(payload, dict):
      continue
    row_symbol = normalize_symbol(payload.get("Symbol") or payload.get("symbol"))
    if wanted is not None and row_symbol != wanted:
      continue
    direction = normalize_direction(payload.get("Direction") or payload.get("direction"))
    for leg in payload.get("Legs") or payload.get("legs") or []:
      position_id = leg.get("BrokerPositionId") or leg.get("broker_position_id")
      remaining = leg.get("RemainingVolume") or leg.get("remaining_volume") or 0
      if position_id is None or float(remaining) <= 0:
        continue
      out.append({
        "position_id": int(position_id),
        "symbol": row_symbol,
        "direction": direction,
        "entry_price": leg.get("FillPrice") or leg.get("fill_price"),
        "remaining_volume": remaining,
        "setup": payload.get("PlanId") or payload.get("plan_id"),
      })
  return out


async def request_close_auto_position(position_id: int) -> None:
  """/trade_close_auto: close ONE fully-autonomous broker leg immediately,
  by its own position_id. cTrader Engine refuses a leg that belongs to a
  manual /algo plan, so this can never bypass /trade_close's own
  intent_id/group-aware path for a manual signal.
  """
  await _xadd_command({"type": "close_position", "position_id": position_id})


async def request_move_sl(signal_id: int, position_id: int, price: float) -> None:
  """/trade_sl on a filled manual algo signal."""
  await _xadd_command({
    "type": "move_sl",
    "position_id": position_id,
    "price": price,
  })
