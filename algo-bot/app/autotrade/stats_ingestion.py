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
from app.autotrade.event_integrity import contradictory_archived_tp
from app.persistence import redis_state
from app.persistence.store import (
  reconcile_orphan_auto_trade_result,
  record_auto_trade_event,
)

log = logging.getLogger(__name__)

STATS_EVENT_CURSOR_KEY = "auto_trade:stats_event_cursor"


def _text(value: object) -> str:
  return value.decode() if isinstance(value, bytes) else str(value)


async def _maybe_reconcile_group_from_runtime(client, group_id: str | None) -> None:
  """Recover /trade_stats rows when close never landed (unknown_leg_close)."""
  if not group_id:
    return
  raw = await client.get(f"execution:plan_runtime:{group_id}")
  if raw is None:
    return
  try:
    runtime = json.loads(_text(raw))
  except (TypeError, json.JSONDecodeError):
    return
  if not isinstance(runtime, dict):
    return
  recovery = None
  recovery_raw = await client.get(f"execution:plan_recovery:{group_id}")
  if recovery_raw is not None:
    try:
      loaded = json.loads(_text(recovery_raw))
    except (TypeError, json.JSONDecodeError):
      loaded = None
    if isinstance(loaded, dict):
      recovery = loaded
  try:
    await reconcile_orphan_auto_trade_result(
      str(group_id), runtime, recovery=recovery,
    )
  except Exception:
    log.exception(
      "plan_runtime stats reconcile failed group_id=%s", group_id,
    )


def _complete_outcome(event: dict) -> str | None:
  event_type = str(event.get("type") or "")
  if event_type in {"opened", "add", "manual_opened", "order_filled"}:
    return "fill"
  if event_type not in {"group_result", "position_closed", "manual_closed"}:
    return None
  if contradictory_archived_tp(event):
    return "sl"
  reason = str(event.get("reason_code") or event.get("close_reason") or "").casefold()
  message = str(event.get("message") or "").casefold()
  if "stop" in reason or "group_stop" in reason or "sl" == reason:
    return "sl"
  if "take_profit" in reason or "tp" in reason or "target" in reason:
    return "tp"
  if "manual" in reason or "external" in reason:
    pass
  elif "group_stop_loss" in message or "stop loss" in message:
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


async def _track_scalp_event(client, event: dict) -> None:
  """Accumulate exit-path traces and open excursions for scalp events."""
  from app.autotrade.reaction_funnel import BUCKET_SCALP, funnel_bucket, normalize_setup_type
  from app.scalping.outcomes import (
    ExitTrace,
    apply_trace_event,
    clear_excursion,
    excursion_from_opportunity,
    finalize_live_outcome,
    classify_exit_path,
    legs_for_exit_path,
    load_bind,
    load_excursion,
    load_exit_trace,
    reconcile_ledger_r,
    resolve_stop_pips_from_signal,
    save_excursion,
    save_exit_trace,
    save_live_outcome,
    volume_weighted_r,
    EXIT_UNKNOWN,
  )
  from app.scalping.models import ScalpOpportunity
  from app.scalping.telemetry import incr

  strategy = normalize_setup_type(
    event.get("setup") or event.get("strategy") or event.get("setup_type")
  )
  if funnel_bucket(strategy, family=event.get("strategy_family")) != BUCKET_SCALP:
    return

  symbol = str(event.get("symbol") or "XAU")
  group_id = event.get("group_id") or event.get("setup_id") or event.get("candidate_id")
  if group_id is None:
    return
  gid = str(group_id)
  trace = await load_exit_trace(client, gid)
  if trace is None:
    trace = ExitTrace(group_id=gid)
  trace = apply_trace_event(trace, event)
  await save_exit_trace(client, trace)

  event_type = str(event.get("type") or "")
  now = int(event.get("timestamp") or time.time())

  # On fill: start excursion tracking if we can resolve the opportunity.
  if event_type in {"opened", "add", "manual_opened", "order_filled"}:
    bound = await load_bind(client, gid)
    if bound:
      opp_id = str(bound.get("opportunity_id") or "")
      existing = await load_excursion(client, symbol, opp_id) if opp_id else None
      if existing is None and opp_id:
        stop = await resolve_stop_pips_from_signal(
          client, symbol=symbol, opportunity_id=opp_id, group_id=gid,
        )
        signal_id = bound.get("signal_id")
        opportunity = None
        if signal_id:
          raw = await client.get(f"scalp:signal:{symbol.upper()}:{signal_id}")
          if raw:
            try:
              payload = json.loads(raw)
              opportunity = ScalpOpportunity.from_json(
                json.dumps(payload.get("opportunity") or {})
              ) if payload.get("opportunity") else None
            except Exception:
              opportunity = None
        entry = event.get("entry_price") or event.get("fill_price") or event.get("price")
        try:
          entry_price = float(entry) if entry is not None else None
        except (TypeError, ValueError):
          entry_price = None
        if opportunity is not None and entry_price is not None and stop and stop > 0:
          try:
            from app.autotrade import units as _units
            pip = float(_units.pip_size(symbol))
          except Exception:
            pip = 0.1
          state = excursion_from_opportunity(
            opportunity,
            entry_price=entry_price,
            group_id=gid,
            match_id=str(bound.get("match_id") or gid),
            opened_at=now,
            pip_size=pip,
          )
          await save_excursion(client, state)

  # Mid-trade BE / TP: bump legs_filled on the excursion.
  if event_type in {"tp_booked", "take_profit", "group_sl_moved_to_be", "sl_moved"}:
    bound = await load_bind(client, gid)
    if bound:
      opp_id = str(bound.get("opportunity_id") or "")
      state = await load_excursion(client, symbol, opp_id) if opp_id else None
      if state is not None and event_type in {"tp_booked", "take_profit"}:
        state.legs_filled = max(int(state.legs_filled), 1)
        if "tp2" in str(event.get("target") or event.get("message") or "").casefold():
          state.legs_filled = max(int(state.legs_filled), 2)
        await save_excursion(client, state)


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
    unwrap_risk_state,
  )
  from app.scalping.outcomes import (
    classify_exit_path,
    clear_excursion,
    finalize_live_outcome,
    legs_for_exit_path,
    load_bind,
    load_excursion,
    load_exit_trace,
    reconcile_ledger_r,
    resolve_stop_pips_from_signal,
    save_live_outcome,
    volume_weighted_r,
    EXIT_UNKNOWN,
  )
  from app.scalping.telemetry import incr
  from app.scalping.lifecycle import load_lifecycle, save_lifecycle
  from app.scalping.models import COMPLETED

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
  group_id = (
    None if event.get("group_id") is None else str(event.get("group_id"))
  )
  now = int(event.get("timestamp") or time.time())
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
  if stop_pips is None or stop_pips <= 0:
    stop_pips = await resolve_stop_pips_from_signal(
      client, symbol=symbol, group_id=group_id,
    )

  exit_path = EXIT_UNKNOWN
  realized_r_override: float | None = None
  opportunity_id: str | None = None
  excursion_for_risk = None
  if group_id:
    trace = await load_exit_trace(client, group_id)
    if trace is not None:
      # Ensure the completing event is folded in before classification.
      from app.scalping.outcomes import apply_trace_event, save_exit_trace
      trace = apply_trace_event(trace, event)
      await save_exit_trace(client, trace)
      exit_path = classify_exit_path(trace)
      if exit_path == EXIT_UNKNOWN:
        await incr(client, symbol, "exit_path_unknown")
    bound = await load_bind(client, group_id)
    if bound:
      opportunity_id = str(bound.get("opportunity_id") or "") or None
  if opportunity_id:
    excursion_for_risk = await load_excursion(client, symbol, opportunity_id)
    if (
      excursion_for_risk is not None
      and float(excursion_for_risk.stop_pips) > 0
    ):
      # Prefer realized fill-to-stop R unit stamped on the excursion.
      stop_pips = float(excursion_for_risk.stop_pips)

  if (
    outcome != "fill"
    and stop_pips is not None
    and stop_pips > 0
    and exit_path != EXIT_UNKNOWN
  ):
    excursion = excursion_for_risk
    if excursion is None and opportunity_id:
      excursion = await load_excursion(client, symbol, opportunity_id)
    ratios = (
      excursion.ladder_ratios if excursion is not None else (0.5, 0.5)
    )
    multiples = (
      excursion.ladder_r_multiples if excursion is not None else (1.0, 2.0)
    )
    leg_ratios, leg_r = legs_for_exit_path(
      exit_path, ladder_ratios=ratios, ladder_r_multiples=multiples,
    )
    realized_r_override = volume_weighted_r(
      exit_path=exit_path,
      stop_pips=float(stop_pips),
      leg_close_ratios=leg_ratios,
      leg_r_multiples=leg_r,
    )

  try:
    state = await load_risk(client, symbol)
    state = apply_daily_reset(
      state, runtime_config, now=now, session=classify_session(now, runtime_config)
    )
    if outcome == "fill":
      result = record_scalp_outcome(
        state,
        result_pips=0.0,
        stop_pips=stop_pips,
        now=now,
        opened=True,
        group_id=group_id,
      )
    else:
      result = record_scalp_outcome(
        state,
        result_pips=pips,
        stop_pips=stop_pips,
        now=now,
        closed=True,
        group_id=group_id,
        r_multiple=realized_r_override,
      )
      if result.skipped_no_stop:
        await incr(client, symbol, "risk_accrual_skipped_no_stop")
        log.warning(
          "hfs risk accrual skipped: no stop_pips group_id=%s stop_pips=%s",
          group_id,
          stop_pips,
        )
    await save_risk(client, symbol, unwrap_risk_state(result))
    ledger_delta = result.accrued_r
  except Exception:
    log.exception(
      "hfs risk state update failed symbol=%s group_id=%s stop_pips=%s",
      symbol,
      group_id,
      stop_pips,
    )
    return

  # Finalise live outcome record on close.
  if outcome == "fill" or not opportunity_id:
    return
  try:
    excursion = await load_excursion(client, symbol, opportunity_id)
    if excursion is None:
      return
    if stop_pips is not None and stop_pips > 0:
      # Prefer the invariant stop already on the excursion; never recompute.
      pass
    live = finalize_live_outcome(
      excursion,
      exit_path=exit_path,
      realized_pips=pips,
      closed_at=now,
    )
    await save_live_outcome(client, live)
    await clear_excursion(client, symbol, opportunity_id)
    await reconcile_ledger_r(
      client,
      symbol=symbol,
      opportunity_id=opportunity_id,
      realized_r=live.realized_r,
      ledger_delta=ledger_delta,
    )
    # Attach exit_path onto the scalp lifecycle measured dict when present.
    try:
      record = await load_lifecycle(client, symbol, opportunity_id)
      if record is not None:
        measured = {
          **dict(record.measured),
          "exit_path": exit_path,
          "realized_r": live.realized_r,
          "mfe_pips": live.mfe_pips,
          "mae_pips": live.mae_pips,
        }
        from app.scalping.models import ScalpLifecycleRecord
        updated = ScalpLifecycleRecord(
          opportunity_id=record.opportunity_id,
          episode_id=record.episode_id,
          state=COMPLETED if record.state != COMPLETED else record.state,
          context_id=record.context_id,
          updated_at=now,
          reason_code=exit_path,
          measured=measured,
        )
        # Only force COMPLETED when transition allows; otherwise just patch measured.
        from app.scalping.lifecycle import transition
        moved = transition(record, COMPLETED, reason=exit_path, now=now)
        if moved.reason_code == "invalid_transition":
          updated = ScalpLifecycleRecord(
            opportunity_id=record.opportunity_id,
            episode_id=record.episode_id,
            state=record.state,
            context_id=record.context_id,
            updated_at=now,
            reason_code=record.reason_code,
            measured=measured,
          )
          await save_lifecycle(client, symbol, updated)
        else:
          await save_lifecycle(
            client,
            symbol,
            ScalpLifecycleRecord(
              opportunity_id=moved.opportunity_id,
              episode_id=moved.episode_id,
              state=moved.state,
              context_id=moved.context_id,
              updated_at=moved.updated_at,
              reason_code=moved.reason_code,
              measured=measured,
            ),
          )
    except Exception:
      log.exception(
        "scalp lifecycle exit_path attach failed opportunity_id=%s",
        opportunity_id,
      )
  except Exception:
    log.exception(
      "scalp live outcome finalise failed symbol=%s group_id=%s opportunity_id=%s",
      symbol,
      group_id,
      opportunity_id,
    )


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
      event_type = str(event.get("type") or "")
      group_id = event.get("group_id") or event.get("candidate_id")
      if event_type in {
        "opened", "add", "manual_opened", "order_filled", "warning",
        "group_result", "position_closed", "manual_closed",
      }:
        # Close-before-fill / unknown_leg_close: fill or recovery warning may
        # be the only chance to materialize the journal row.
        await _maybe_reconcile_group_from_runtime(
          client,
          None if group_id is None else str(group_id),
        )
      try:
        await _track_scalp_event(client, event)
      except Exception:
        log.exception(
          "scalp outcome track failed entry_id=%s type=%s",
          entry_id,
          event.get("type") if isinstance(event, dict) else None,
        )
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
