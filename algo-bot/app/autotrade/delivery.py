"""Owner controls and Telegram delivery for cTrader auto-trade events."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from html import escape
from typing import Literal

from aiogram.exceptions import TelegramBadRequest

from app.analysis.scanner import clear_active_setup_tracking
from app.autotrade import units
from app.autotrade.volume_pips import (
  format_signed_pips,
  volume_percent,
)
from app.persistence import redis_state
from app.persistence.store import record_auto_trade_event
from app.core.config import settings
from app.bot.client import send_scanner_with_retry
from app.autotrade.lifecycle import LIFECYCLE_STATES, emit_lifecycle
from app.autotrade.range_context import (
  WORKER_SNAPSHOT_TTL_SECONDS,
)
from app.autotrade.range_lifecycle import (
  load_breakout_retest_watch,
)
from app.autotrade.config_health import (
  CONFIG_HEALTH_KEY,
  EXECUTOR_READINESS_KEY,
)

log = logging.getLogger(__name__)

_CURSOR_KEY = "auto_trade:telegram_event_cursor"
_PAUSED_KEY = "auto_trade:paused"
_STATS_KEY = "auto_trade:stats"
_REGIME_ALERT_PENDING_PREFIX = "auto_trade:regime_alert_pending:"
_REGIME_ALERT_SENT_TTL = 86400
_TRADE_MESSAGE_TTL = 7 * 24 * 3600
_FULL_TP_RESULT_TTL = 24 * 3600
_FORMING_REPLY_TYPES = frozenset({
  "strategy_route",
  "opened",
  "rejected",
})
# Lifecycle/event types that stay in Redis + metrics + /auto_status but must
# never become Telegram cards. Keep emission paths intact.
TELEGRAM_SILENT_LIFECYCLE_TYPES = frozenset({
  "candidate_published",
  "order_submitted",
  "order_accepted",
  "managing",
  "position_managing",
  "config_fatal",
  "broker_fatal",
  "broker_account_fatal",
  "executor_readiness_fatal",
  "configuration_health",
  "config_health",
})
_NOTIFY_TYPES = {
  "ready",
  "dry_run",
  "opened",
  "add",
  "zone_planned",
  "zone_expired",
  "take_profit",
  "stop_moved",
  "position_closed",
  "group_result",
  "strategy_route",
  "owner_flatten",
  "warning",
  "error",
  "candidate_published",
  "order_submitted",
  "order_accepted",
  "managing",
  "rejected",
  "config_health",
  "config_fatal",
  "account_capability",
  "range_flip_attempted",
  "range_flip_filled",
}

_AUTO_NAME_RE = re.compile(r"(?i)\bauto[\s-]*(?:trade|trader)\b")
_OPENED_RE = re.compile(
  r"(?i)^(BUY|SELL)\s+([\d.,]+)\s+lots?\s+filled\s+([\d.,]+),\s*"
  r"SL\s+([\d.,]+)\s*·\s*([\d.,]+)p\s+structure\s*·\s*(.+)$"
)
_TP_RE = re.compile(
  r"(?i)^(FULL TP|TP\d+)\s+([+-]?\d+(?:\.\d+)?)\s+pips\s+closed\s+volume\s+(\d+)$"
)
_MONEY_RE = re.compile(r"\$|USD|EUR|GBP|balance|equity|brokerNetProfit", re.I)
_STOP_RE = re.compile(
  r"(?i)^🛡\s+(?:ApexVoid Algo|Algo bot|Auto[\s-]*(?:trade|trader))\s+"
  r"stop\s+(?:→|->)\s+"
  r"([\d.,]+)\s+\(([^)]+)\)(?:\s*·\s*position\s+\d+)?$"
)
_LOT_TEXT_RE = re.compile(r"(?i)(?<!\w)<?[\d.,]+>?\s+lots?\b")
_POSITION_TEXT_RE = re.compile(r"(?i)\bposition\s*[:#]?\s*\d+\b")

DeliveryProfile = Literal["internal", "public"]


def _clean_message(value: object) -> str:
  text = _AUTO_NAME_RE.sub("ApexVoid Algo", str(value or ""))
  text = _POSITION_TEXT_RE.sub("", text)
  text = re.sub(r"\s*·\s*(?=·|$)", "", text)
  return text.strip(" ·")


def _attribution_line(event: dict) -> str | None:
  """Strategy attribution (A4) - the "which setup produced this order"
  question that was previously unanswerable from the Telegram message alone.
  """
  setup = event.get("setup")
  if not setup:
    return None
  parts = [escape(str(setup))]
  regime = event.get("regime")
  if regime:
    parts.append(escape(str(regime)))
  confluence = event.get("confluence")
  if isinstance(confluence, (int, float)) and confluence > 0:
    parts.append("★" * min(3, int(confluence)))
  return f"🧭 {' · '.join(parts)}"


def _targets_line(event: dict) -> str | None:
  raw = event.get("targets_pips")
  if not isinstance(raw, (list, tuple)):
    return None
  try:
    targets = [int(value) for value in raw if int(value) > 0]
  except (TypeError, ValueError):
    return None
  if not targets:
    return None
  ladder = " / ".join(f"+{value}" for value in targets)
  return f"🎯 Targets: <b>{ladder} pips</b>"


def _public_message(event: dict, message: str) -> str:
  cleaned = _LOT_TEXT_RE.sub("", message)
  cleaned = _POSITION_TEXT_RE.sub("", cleaned)
  position_id = event.get("position_id")
  if position_id is not None:
    cleaned = re.sub(
      rf"\b{re.escape(str(position_id))}\b",
      "",
      cleaned,
    )
  cleaned = re.sub(r"\s*·\s*(?=·|$)", "", cleaned)
  return cleaned.strip(" ·")


def _format_range_box_scale_out_targets(
  event: dict,
  direction: str,
  entry: str,
) -> list[str] | None:
  """Owner ORDER FILLED lines for Range Box 30p/50% then Full TP."""
  raw = event.get("targets_pips")
  if not isinstance(raw, (list, tuple)) or len(raw) < 2:
    return None
  setup = str(event.get("setup") or "")
  mode = str(event.get("mode") or "")
  if "Range Box Scalp" not in setup and mode != "auto_box_scalp":
    # Still allow when the opened event only carries the ladder.
    if "TP1 +" not in str(event.get("message") or ""):
      return None
  try:
    trigger = int(raw[0])
    full_tp = int(raw[-1])
  except (TypeError, ValueError):
    return None
  if trigger <= 0 or full_tp <= trigger:
    return None
  fraction = event.get("scale_out_fraction")
  try:
    fraction_text = f"{float(fraction):.0%}" if fraction is not None else "50%"
  except (TypeError, ValueError):
    fraction_text = "50%"
  return [
    f"🎯 TP1: <b>+{trigger} pips</b> · book {escape(fraction_text)}",
    f"🎯 Full TP: <b>+{full_tp} pips</b>",
  ]


def _append_public_footer(lines: list[str], footer: str | None) -> None:
  value = str(footer or "").strip()
  if value:
    lines.extend(["", escape(value)])


def _format_opened(
  event: dict,
  message: str,
  profile: DeliveryProfile,
  footer: str | None,
) -> str | None:
  match = _OPENED_RE.match(message)
  if match is None:
    return None
  direction, _lots, entry, stop, stop_pips, details = match.groups()
  side_icon = "🟢" if direction.upper() == "BUY" else "🔴"
  full_tp = re.search(r"(?i)full TP\s+(\d+)p", details)
  range_box = re.search(r"(?i)range\s+([\d.,]+)-([\d.,]+)", details)
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    "✅ <b>ORDER FILLED</b>",
    f"{side_icon} <b>XAU {direction.upper()} opened</b>",
    "",
    f"📍 Entry: <b>{escape(entry)}</b>",
    f"🛡 SL: <b>{escape(stop)}</b> · {escape(stop_pips)} pips",
  ]
  public_targets = _targets_line(event) if profile == "public" else None
  scale_out_lines = (
    None if profile == "public" else _format_range_box_scale_out_targets(
      event, direction, entry,
    )
  )
  if public_targets is not None:
    lines.append(public_targets)
  elif scale_out_lines:
    lines.extend(scale_out_lines)
  elif full_tp is not None:
    target_pips = int(full_tp.group(1))
    try:
      entry_price = float(entry.replace(",", ""))
      target_price = entry_price + (
        target_pips * units.pip_size("XAU")
        if direction.upper() == "BUY"
        else -target_pips * units.pip_size("XAU")
      )
      lines.append(
        f"🎯 Full TP: <b>{target_price:,.2f}</b> · +{target_pips} pips"
      )
    except ValueError:
      lines.append(f"🎯 Full TP: <b>+{target_pips} pips</b>")
  if range_box is not None:
    lines.append(
      "📦 Box: <b>"
      f"{escape(range_box.group(1))}–{escape(range_box.group(2))}</b>"
    )
  attribution = _attribution_line(event)
  if attribution:
    lines.append(attribution)
  if profile == "public":
    _append_public_footer(lines, footer)
  return "\n".join(lines)


def _event_float(event: dict, *keys: str) -> float | None:
  for key in keys:
    raw = event.get(key)
    if raw is None:
      continue
    try:
      return float(raw)
    except (TypeError, ValueError):
      continue
  return None


def _trade_seq_prefix(event: dict) -> str:
  for key in ("daily_seq", "trade_seq", "seq"):
    raw = event.get(key)
    if raw is None:
      continue
    try:
      return f"#{int(raw)} "
    except (TypeError, ValueError):
      text = str(raw).strip()
      if text:
        return f"#{text} "
  return ""


def _format_take_profit(
  event: dict,
  message: str,
  profile: DeliveryProfile,
) -> str | None:
  match = _TP_RE.match(message)
  if match is None:
    return None
  label, message_pips, message_volume = match.groups()
  full = label.upper() == "FULL TP"
  closed_volume = _event_float(event, "volume")
  if closed_volume is None:
    closed_volume = float(message_volume)
  initial_volume = _event_float(
    event,
    "group_initial_volume",
    "initial_filled_volume",
    "initial_volume",
  )
  remaining_volume = _event_float(event, "remaining_volume")
  if remaining_volume is None:
    remaining_volume = 0.0 if full else None
  leg_realized = _event_float(event, "leg_realized_pips")
  if leg_realized is None:
    leg_realized = float(message_pips)
  seq = _trade_seq_prefix(event)
  is_final = full or (remaining_volume is not None and remaining_volume <= 0)

  # Only the TP level that just fired is reported - no running/total net
  # pip aggregation, here or anywhere else in this module (see
  # _format_group_result and _format_position_closed).
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
  ]
  if is_final:
    lines.append(f"✅ {seq}{label.upper()} closed")
  elif (
    initial_volume is None
    or initial_volume <= 0
    or remaining_volume is None
  ):
    lines.append(
      f"🎯 {seq}{label.upper()} booked · "
      f"closed volume <b>{closed_volume:.0f}</b>"
    )
  else:
    booked_pct = volume_percent(closed_volume, initial_volume)
    lines.append(f"🎯 {seq}{label.upper()} booked {booked_pct:.1f}%")
  lines.append(f"Leg: <b>{format_signed_pips(leg_realized)} pips</b>")

  if profile == "public":
    try:
      stop_pips = float(event.get("stop_pips"))
    except (TypeError, ValueError):
      stop_pips = 0.0
    if stop_pips > 0:
      lines.append(
        f"📐 Result: <b>{leg_realized / stop_pips:+.2f}R</b>"
      )
  text = "\n".join(lines)
  if _MONEY_RE.search(text):
    text = _MONEY_RE.sub("", text)
  return text


def _format_group_result(event: dict, message: str) -> str | None:
  # This card's entire content used to be the net-pip summary; per current
  # policy (no net-pip aggregation anywhere), it no longer fires at all -
  # each TP/close event already reported its own leg pips when it happened.
  return None


def _format_position_closed(event: dict, message: str) -> str:
  seq = _trade_seq_prefix(event)
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    f"🏁 {seq}<b>POSITION CLOSED</b>",
  ]
  cleaned = _MONEY_RE.sub("", message).strip(" ·") if message else ""
  if cleaned:
    lines.extend(["", escape(cleaned)])
  return "\n".join(lines)


def _format_strategy_route(event: dict) -> str | None:
  status = str(event.get("status") or "")
  reason_code = str(event.get("reason_code") or "")
  if status == "candidate_published":
    # "READY" is reserved for the executor accepting and arming a plan
    # (see docs/adr-trade-plan-v7-boundary.md) - Python publishing a
    # candidate is not that, so this must not read "ready".
    headline = "🟢 <b>Algo bot PLAN PUBLISHED</b>"
  elif status == "waiting":
    headline = "🟠 <b>Algo bot WAIT</b>"
  elif status in {"blocked", "executor_rejected"}:
    headline = "🔴 <b>Algo bot BLOCKED</b>"
  else:
    return None
  measured = event.get("measured") or {}
  strategy = escape(str(event.get("strategy") or "StrategyMatch"))
  direction = escape(str(event.get("direction") or ""))
  reason = escape(str(event.get("message") or event.get("reason_code") or ""))
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    headline,
    f"{direction} · <b>{strategy}</b>",
    "",
  ]
  zone_low = measured.get("entry_low")
  zone_high = measured.get("entry_high")
  zone_text = None
  if zone_low is not None and zone_high is not None:
    zone_text = f"{float(zone_low):,.2f}–{float(zone_high):,.2f}"

  if status == "candidate_published":
    route = measured.get("planned_execution_route")
    planned_entry = measured.get("planned_entry_price")
    executor_distance = measured.get("executor_distance_pips")
    executor_limit = measured.get("executor_limit_pips")
    if route:
      lines.append(f"Route: <b>{escape(str(route))}</b>")
    if planned_entry is not None:
      lines.append(f"Planned entry: <b>{float(planned_entry):,.2f}</b>")
    if zone_text:
      lines.append(f"Zone: <b>{zone_text}</b>")
    if executor_distance is not None:
      lines.append(
        f"Executor distance: <b>{float(executor_distance):.1f}p</b>"
      )
    if executor_limit is not None:
      lines.append(
        f"Executor limit: <b>{float(executor_limit):.1f}p</b>"
      )
  elif (
    status == "waiting"
    and reason_code == "executor_entry_envelope_exceeded"
  ):
    quote = measured.get("executor_quote")
    distance = measured.get("executor_distance_pips")
    executor_limit = measured.get("executor_limit_pips")
    if quote is not None:
      lines.append(f"Quote: <b>{float(quote):,.2f}</b>")
    if zone_text:
      lines.append(f"Zone: <b>{zone_text}</b>")
    if distance is not None:
      lines.append(f"Distance: <b>{float(distance):.1f}p</b>")
    if executor_limit is not None:
      lines.append(
        f"Executor limit: <b>{float(executor_limit):.1f}p</b>"
      )
    lines.append("Setup retained")
  else:
    lines.append(f"Reason: {reason}")
    distance = measured.get("distance_pips")
    limit = measured.get("adaptive_limit_pips")
    if distance is not None:
      drift = f"Drift: <b>{float(distance):.1f}p</b>"
      if limit is not None:
        drift += f" · limit {float(limit):.1f}p"
      lines.append(drift)
  if status == "blocked" and measured.get("available_room_pips") is not None:
    lines.append(
      f"Room: {float(measured['available_room_pips']):.0f}p · "
      f"minimum target: {float(measured.get('minimum_target_pips', 0)):.0f}p"
    )
  if (
    status == "waiting"
    and reason_code != "executor_entry_envelope_exceeded"
    and event.get("expires_at")
  ):
    lines.append(f"Match retained until: <code>{event['expires_at']}</code>")
  return "\n".join(lines)


def _format_owner_flatten(event: dict, message: str) -> str:
  body = escape(message) if message else "owner flatten"
  return "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    "🧹 <b>FLATTEN</b>",
    "",
    body,
  ])


def _format_stop_moved(
  event: dict,
  message: str,
  profile: DeliveryProfile,
) -> str | None:
  new_sl = _event_float(
    event,
    "new_stop",
    "stop_price",
    "price",
  )
  if new_sl is None:
    match = _STOP_RE.match(message)
    if match is not None:
      try:
        new_sl = float(str(match.group(1)).replace(",", ""))
      except (TypeError, ValueError):
        new_sl = None
  if new_sl is None:
    return None
  return "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    f"🛡 move SL to <b>{new_sl:,.2f}</b>",
  ])


def render_auto_trade_event(
  event: dict,
  profile: DeliveryProfile = "internal",
  footer: str | None = None,
) -> str | None:
  if profile not in {"internal", "public"}:
    raise ValueError(f"Unknown auto-trade delivery profile: {profile}")
  event_type = str(event.get("type", ""))
  if event_type in TELEGRAM_SILENT_LIFECYCLE_TYPES:
    return None
  if event_type not in _NOTIFY_TYPES:
    return None
  reason_code = str(event.get("reason_code") or event.get("reason") or "")
  if reason_code in {
    "duplicate_reaction_active",
    "already_processed",
    "already_processed:duplicate_reaction_active",
    "already_processed:active_thesis_group",
    "active_thesis_group",
  }:
    return None
  if "duplicate_reaction" in reason_code or "active_thesis_group" in reason_code:
    return None
  message = _clean_message(event.get("message", ""))
  if "already_processed:duplicate_reaction_active" in message:
    return None
  if "already_processed:active_thesis_group" in message:
    return None
  if event_type == "rejected":
    lines = [
      "🤖 <b>ApexVoid Algo</b>",
      "⛔ <b>EXECUTOR REJECTED</b>",
    ]
    strategy = event.get("strategy") or event.get("setup")
    if strategy:
      lines.append(f"Strategy: <b>{escape(str(strategy))}</b>")
    direction = event.get("direction")
    if direction:
      lines.append(f"Direction: <b>{escape(str(direction).upper())}</b>")
    entry = event.get("entry_zone")
    if isinstance(entry, dict):
      try:
        lines.append(
          "Entry: <b>"
          f"{float(entry['low']):,.2f}–{float(entry['high']):,.2f}</b>"
        )
      except (KeyError, TypeError, ValueError):
        pass
    reason = event.get("reason_code")
    if reason:
      lines.append(f"Reason: <code>{escape(str(reason))}</code>")
    elif message:
      lines.append(escape(message))
    return "\n".join(lines)
  if event_type == "opened":
    rendered = _format_opened(
      event,
      message,
      profile,
      footer,
    )
    if rendered:
      return rendered
  if event_type == "take_profit":
    rendered = _format_take_profit(event, message, profile)
    if rendered:
      return rendered
  if event_type == "stop_moved":
    rendered = _format_stop_moved(event, message, profile)
    if rendered:
      return rendered
  if event_type == "group_result":
    return _format_group_result(event, message)
  if event_type == "position_closed":
    return _format_position_closed(event, message)
  if event_type == "strategy_route":
    return _format_strategy_route(event)
  if event_type == "owner_flatten":
    return _format_owner_flatten(event, message)
  labels = {
    "ready": "✅ <b>Engine ready</b>",
    "dry_run": "🧪 <b>Simulation</b>",
    "opened": "✅ <b>ORDER FILLED</b> · <b>Position opened</b>",
    "add": "➕ <b>Scale-in filled</b>",
    "zone_planned": "⌛ <b>WAITING FOR PRICE</b>",
    "zone_expired": "⌛ <b>Entry plan expired</b>",
    "take_profit": "🎯 <b>Take profit hit</b>",
    "stop_moved": "🛡 <b>Risk protected</b>",
    "position_closed": "🏁 <b>POSITION CLOSED</b>",
    "group_result": "📊 <b>Trade result</b>",
    "strategy_route": "🧭 <b>Strategy route</b>",
    "owner_flatten": "🧹 <b>FLATTEN</b>",
    "warning": "⚠️ <b>Warning</b>",
    "error": "⚠️ <b>Execution issue</b>",
    "account_capability": "🧾 <b>Account capability</b>",
    "range_flip_attempted": "🔁 <b>Range flip attempted</b>",
    "range_flip_filled": "✅ <b>Range flip completed</b>",
  }
  lines = ["🤖 <b>ApexVoid Algo</b>", labels[event_type]]
  if profile == "public":
    message = _public_message(event, message)
  if message:
    lines.extend(["", escape(message)])
  if profile == "public" and event_type == "opened":
    _append_public_footer(
      lines,
      footer,
    )
  return "\n".join(lines)


def _forming_message_key(match_id: str) -> str:
  return f"auto_trade:forming_message:{match_id}"


def _message_key(profile: DeliveryProfile, position_id: int) -> str:
  prefix = "auto_trade:msg" if profile == "internal" else "auto_trade:public_msg"
  return f"{prefix}:{position_id}"


def _group_message_key(profile: DeliveryProfile, group_id: str) -> str:
  prefix = (
    "auto_trade:group_msg"
    if profile == "internal"
    else "auto_trade:public_group_msg"
  )
  return f"{prefix}:{group_id}"


def tp_message_key(profile: DeliveryProfile, position_id: int) -> str:
  prefix = "auto_trade:tp_msg" if profile == "internal" else "auto_trade:public_tp_msg"
  return f"{prefix}:{position_id}"


def _full_tp_result_key(profile: DeliveryProfile, group_id: str) -> str:
  return f"auto_trade:full_tp_result:{profile}:{group_id}"


def _telegram_terminal_key(profile: DeliveryProfile, group_id: str) -> str:
  return f"auto_trade:telegram_terminal:{profile}:{group_id}"


def _is_full_take_profit(event: dict) -> bool:
  return (
    event.get("type") == "take_profit"
    and str(event.get("message") or "").upper().startswith("FULL TP ")
  )


def _is_bad_reply_target(error: TelegramBadRequest) -> bool:
  reason = str(error).lower()
  return (
    "reply" in reason
    and ("not found" in reason or "invalid" in reason)
  ) or "message to be replied" in reason


def _event_match_id(event: dict) -> str:
  return str(event.get("match_id") or event.get("candidate_id") or "").strip()


async def _forming_reply_message_id(
  client,
  event: dict,
) -> tuple[int | None, str]:
  match_id = _event_match_id(event)
  if not match_id:
    return None, "event has no match id"
  key = _forming_message_key(match_id)
  raw = await client.get(key)
  if not raw:
    return None, f"stored forming message is missing or expired ({key})"
  try:
    message_id = int(raw)
  except (TypeError, ValueError):
    return None, f"invalid cached message id in {key}"
  if message_id > 0:
    return message_id, ""
  return None, f"invalid cached message id in {key}"


async def _reply_message_id(
  client,
  event: dict,
  profile: DeliveryProfile,
) -> tuple[int | None, str]:
  position_id = event.get("position_id")
  if position_id is None:
    return None, "event has no position id"
  keys = [_message_key(profile, int(position_id))]
  group_id = str(event.get("group_id") or "").strip()
  if event.get("type") == "add" and group_id:
    keys.append(_group_message_key(profile, group_id))
  for key in keys:
    raw = await client.get(key)
    if not raw:
      continue
    try:
      message_id = int(raw)
    except (TypeError, ValueError):
      return None, f"invalid cached message id in {key}"
    if message_id > 0:
      return message_id, ""
    return None, f"invalid cached message id in {key}"
  return None, "stored order message is missing or expired"


async def _resolve_reply_message_id(
  client,
  event: dict,
  profile: DeliveryProfile,
) -> tuple[int | None, str]:
  event_type = str(event.get("type") or "")
  if event_type in _FORMING_REPLY_TYPES:
    forming_reply, reason = await _forming_reply_message_id(client, event)
    if forming_reply is not None:
      return forming_reply, ""
    if event_type in {"strategy_route", "opened", "rejected"}:
      return None, reason
  if event_type == "opened":
    return None, "opened has no reply anchor"
  position_id = event.get("position_id")
  if position_id is not None:
    return await _reply_message_id(client, event, profile)
  return None, "event has no reply anchor"


async def _remember_trade_message(
  client,
  event: dict,
  profile: DeliveryProfile,
  message_id: int,
) -> None:
  position_id = event.get("position_id")
  if position_id is None or message_id <= 0:
    return
  await client.set(
    _message_key(profile, int(position_id)),
    str(message_id),
    ex=_TRADE_MESSAGE_TTL,
  )
  group_id = str(event.get("group_id") or "").strip()
  if event.get("type") == "opened" and group_id:
    await client.set(
      _group_message_key(profile, group_id),
      str(message_id),
      ex=_TRADE_MESSAGE_TTL,
    )


async def _correlate_strategy_route(client, event: dict) -> None:
  match_id = str(event.get("match_id") or "").strip()
  symbol = str(event.get("symbol") or "").upper()
  if not match_id or not symbol:
    return
  key = f"auto_trade:route_outcome:{symbol}:{match_id}"
  current = await _json_key(client, key)
  if not current:
    return
  event_type = str(event.get("type") or "")
  transition = {
    "executor_received": ("executor_received", "executor"),
    "order_submitted": ("order_submitted", "broker"),
    "opened": ("order_filled", "broker"),
    "order_filled": ("order_filled", "broker"),
    "rejected": ("executor_rejected", "executor"),
    "executor_rejected": ("executor_rejected", "executor"),
  }.get(event_type)
  if transition is None:
    return
  status, stage = transition
  now = int(datetime.now(timezone.utc).timestamp())
  current.update({
    "status": status,
    "stage": stage,
    "reason_code": str(
      event.get("reason_code") or event.get("reason") or status
    ),
    "message": str(event.get("message") or status.replace("_", " ")),
    "checked_at": now,
    "candidate_id": event.get("candidate_id") or current.get("candidate_id"),
    "group_id": event.get("group_id") or current.get("group_id"),
    "executor_event_id": (
      event.get("event_id")
      or event.get("lifecycle_id")
      or current.get("executor_event_id")
    ),
  })
  encoded = json.dumps(current, separators=(",", ":"), sort_keys=True)
  pipe = client.pipeline()
  pipe.set(key, encoded, ex=_TRADE_MESSAGE_TTL)
  pipe.set(
    f"auto_trade:last_route_outcome:{symbol}",
    encoded,
    ex=_TRADE_MESSAGE_TTL,
  )
  pipe.xadd(
    f"auto_trade:route_history:{symbol}",
    {"payload": encoded},
    maxlen=1000,
    approximate=True,
  )
  pipe.hincrby(f"auto_trade:metrics:{symbol}", f"strategy_match_{status}", 1)
  await pipe.execute()


async def _deliver_auto_trade_event(
  client,
  event: dict,
  *,
  profile: DeliveryProfile,
  chat_id: int,
  send=None,
) -> bool:
  event_type = str(event.get("type") or "")
  if event_type == "opened":
    symbol = str(event.get("symbol") or "XAU")
    direction = str(event.get("direction") or "").upper()
    await clear_active_setup_tracking(
      client,
      symbol,
      direction=direction or None,
    )
  if profile == "internal":
    await _correlate_strategy_route(client, event)
  if (
    event.get("setup") == "Manual Algo"
    or event.get("stream") == "algo_manual"
  ):
    # Manual /algo signals already get their lifecycle update on the
    # VIP/public channel via app.signals.manual_execution's reconcile loop
    # (trade_ops.post_result -> broadcast.fanout_update) - the "opened"
    # event is already suppressed here by using a distinct type
    # ("manual_opened"), but take_profit/stop_moved/position_closed reuse
    # the SAME shared event types the autonomous engines use, so without
    # this check the owner would also get a duplicate "ApexVoid Algo" DM
    # for a signal they typed themselves.
    return False
  group_id = str(event.get("group_id") or "").strip()
  if group_id and await client.exists(_full_tp_result_key(profile, group_id)):
    if event_type in {"group_result", "position_closed"}:
      return False
  if event_type in {"group_result", "position_closed"} and group_id:
    claimed = await client.set(
      _telegram_terminal_key(profile, group_id),
      event_type,
      nx=True,
      ex=_TRADE_MESSAGE_TTL,
    )
    if not claimed:
      return False
  text = render_auto_trade_event(event, profile=profile)
  if not text:
    return False
  send = send or send_scanner_with_retry
  position_id = event.get("position_id")
  match_id = _event_match_id(event)
  reply_to, reason = await _resolve_reply_message_id(client, event, profile)
  if reply_to is None and (
    event_type in _FORMING_REPLY_TYPES
    or (event_type != "opened" and position_id is not None)
  ):
    log.info(
      "Auto-trade reply unavailable for %s (%s): %s; sending standalone",
      match_id or position_id or event_type,
      profile,
      reason,
    )
  try:
    sent = await send(text, reply_to=reply_to, chat_id=chat_id)
  except TelegramBadRequest as error:
    if reply_to is None or not _is_bad_reply_target(error):
      raise
    log.info(
      "Auto-trade reply rejected for %s (%s): %s; retrying standalone",
      match_id or position_id or event_type,
      profile,
      error,
    )
    sent = await send(text, reply_to=None, chat_id=chat_id)
  if event_type in {"opened", "add"}:
    await _remember_trade_message(
      client,
      event,
      profile,
      int(sent.message_id),
    )
  if event_type == "take_profit" and position_id is not None:
    await client.set(
      tp_message_key(profile, int(position_id)),
      str(sent.message_id),
      ex=_TRADE_MESSAGE_TTL,
    )
  if _is_full_take_profit(event) and group_id:
    await client.set(
      _full_tp_result_key(profile, group_id),
      "1",
      ex=_FULL_TP_RESULT_TTL,
    )
  return True


async def auto_trade_status_text() -> str:
  """Compact owner status — essentials only (Telegram 4096-safe)."""
  client = redis_state.get_client()
  paused = await client.get(_PAUSED_KEY) == "1"
  date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
  daily = int(await client.get(f"auto_trade:daily:{date_key}:trades") or 0)
  position_count = 0
  async for _ in client.scan_iter(match="auto_trade:position:*"):
    position_count += 1
  primary_symbol = next(
    (
      item.strip().upper()
      for item in settings.auto_trade_symbols.split(",")
      if item.strip()
    ),
    "XAU",
  )
  config_health = await _json_key(client, CONFIG_HEALTH_KEY)
  readiness = await _json_key(client, EXECUTOR_READINESS_KEY)
  mode = (
    "disabled"
    if not settings.auto_trade_enabled
    else "dry run"
    if settings.auto_trade_dry_run
    else "demo trading"
  )
  state = "paused" if paused else "running"
  selected_text = "none"
  execution_state = "-"
  why = ""
  if settings.auto_trade_enabled:
    execution_state = "waiting"
    raw = await client.get(f"auto_trade:last_gate:{primary_symbol}")
    if raw:
      try:
        payload = json.loads(raw)
        checked_at = _snapshot_checked_at(payload.get("checked_at"))
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if (
          checked_at is not None
          and now_ts - checked_at > WORKER_SNAPSHOT_TTL_SECONDS
        ):
          execution_state = (
            "stale · "
            f"{max(1, (now_ts - checked_at) // 60)}m ago"
          )
        else:
          execution_state = str(
            payload.get("state") or execution_state
          ).replace("_", " ")
          selected = str(payload.get("selected_strategy") or "")
          selected_tf = str(payload.get("selected_timeframe") or "")
          direction = str(payload.get("direction") or "")
          published = payload.get("published_candidate")
          if isinstance(published, dict):
            selected = str(published.get("source_strategy") or selected)
            selected_tf = str(published.get("timeframe") or selected_tf)
            direction = str(published.get("direction") or direction)
          if selected:
            selected_text = " · ".join(
              item for item in (selected, direction, selected_tf) if item
            )
          reasons = payload.get("reasons")
          if isinstance(reasons, list) and reasons and selected_text == "none":
            why = str(reasons[-1])
      except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    match_build_raw = await client.get(
      f"auto_trade:last_match_build:{primary_symbol}"
    )
    breakout_watch = await load_breakout_retest_watch(client, primary_symbol)
    if (
      breakout_watch
      and str(breakout_watch.get("state") or "") == "waiting"
      and selected_text == "none"
    ):
      direction = str(breakout_watch.get("direction") or "")
      why = f"breakout-retest {direction}".strip()
    elif match_build_raw and selected_text == "none" and not why:
      try:
        match_build = json.loads(match_build_raw)
        stage = str(match_build.get("stage") or "")
        if stage == "match_build_rejected":
          reason = str(match_build.get("reason") or "unknown")
          if reason != "no_detection_result" or not breakout_watch:
            why = reason
        elif stage == "match_ready":
          strategy = str(match_build.get("strategy") or "")
          direction = str(match_build.get("direction") or "")
          why = f"ready · {strategy} {direction}".strip(" ·")
      except (TypeError, ValueError, json.JSONDecodeError):
        pass
  config_state = str((config_health or {}).get("state") or "unknown")
  ready = bool((readiness or {}).get("ready"))
  lines = [
    "🤖 <b>Algo bot</b>",
    f"{escape(mode)} · <b>{state}</b>",
    f"Open <b>{position_count}</b> · today <b>{daily}</b>",
    f"{escape(selected_text)} · {escape(execution_state)}",
    f"Config <b>{escape(config_state)}</b> · ready <b>{ready}</b>",
  ]
  if why:
    lines.append(f"Why: {escape(why)}")
  return "\n".join(lines)





async def _json_key(client, key: str) -> dict:
  raw = await client.get(key)
  if not raw:
    return {}
  try:
    value = json.loads(raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return {}
  return value if isinstance(value, dict) else {}


def _snapshot_checked_at(raw: object) -> int | None:
  if raw is None:
    return None
  text = str(raw)
  try:
    if text.endswith("Z"):
      text = text[:-1] + "+00:00"
    return int(datetime.fromisoformat(text).timestamp())
  except ValueError:
    return None


async def _record_group_result(client, event: dict) -> None:
  if event.get("type") != "group_result":
    return
  reaction_id = event.get("reaction_id")
  thesis_id = event.get("thesis_id")
  if reaction_id:
    from app.autotrade.reaction_identity import (
      dump_claim,
      parse_reaction_claim,
      reaction_claim_key,
    )
    key = reaction_claim_key(str(reaction_id))
    existing = parse_reaction_claim(await client.get(key))
    if existing is not None:
      existing["state"] = "closed"
      await client.set(key, dump_claim(existing))
      if not thesis_id:
        thesis_id = existing.get("thesis_id")
  if thesis_id:
    from app.autotrade.worker import _mark_thesis_terminal_waiting_exit
    await _mark_thesis_terminal_waiting_exit(
      client,
      thesis_id=str(thesis_id),
      reaction_id=str(reaction_id) if reaction_id else None,
    )
  group_id = str(event.get("group_id") or "").strip()
  if not group_id:
    return
  claimed = await client.set(
    f"auto_trade:stats:group:{group_id}",
    "1",
    nx=True,
  )
  if not claimed:
    return
  had_adds = bool(event.get("had_adds"))
  realized = float(event.get("group_realized_pnl") or 0)
  counterfactual = float(event.get("counterfactual_pnl") or 0)
  realized_pips = float(event.get("group_realized_pips") or 0)
  counterfactual_pips = float(event.get("counterfactual_pips") or 0)
  await client.hincrby(_STATS_KEY, "groups", 1)
  await client.hincrby(
    _STATS_KEY,
    "with_adds" if had_adds else "without_adds",
    1,
  )
  await client.hincrbyfloat(_STATS_KEY, "realized_pnl", realized)
  await client.hincrbyfloat(_STATS_KEY, "realized_pips", realized_pips)
  if had_adds:
    await client.hincrbyfloat(
      _STATS_KEY,
      "counterfactual_pnl",
      counterfactual,
    )
    await client.hincrbyfloat(
      _STATS_KEY,
      "counterfactual_pips",
      counterfactual_pips,
    )
    delta = realized - counterfactual
    await client.hincrbyfloat(_STATS_KEY, "add_delta_pnl", delta)
    await client.hincrby(
      _STATS_KEY,
      "adds_improved" if delta > 0 else "adds_degraded",
      1,
    )


async def set_auto_trade_paused(paused: bool) -> None:
  client = redis_state.get_client()
  if paused:
    await client.set(_PAUSED_KEY, "1")
  else:
    await client.delete(_PAUSED_KEY)


async def _check_regime_alerts(client) -> None:
  """Consume any regime mis-tuning flags worker.py wrote to Redis.

  worker.py cannot import app.bot.client (architecture guard test), so it
  only flags a pending alert key; this function - called from the existing
  auto_trade_events_loop poll below - is the delivery side that actually
  sends the owner DM, deduping via a companion "sent" key so a flag never
  fires twice within its cooldown window.
  """
  async for key in client.scan_iter(match=f"{_REGIME_ALERT_PENDING_PREFIX}*"):
    symbol = key[len(_REGIME_ALERT_PENDING_PREFIX):]
    sent_key = f"auto_trade:regime_alert_sent:{symbol}"
    claimed = await client.set(
      sent_key,
      "1",
      nx=True,
      ex=_REGIME_ALERT_SENT_TTL,
    )
    if not claimed:
      continue
    raw = await client.get(key)
    if not raw:
      continue
    try:
      payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
      continue
    chop = float(payload.get("chop_share", 0.0))
    trend = float(payload.get("trend_share", 0.0))
    breakout = float(payload.get("breakout_share", 0.0))
    text = (
      "⚠️ <b>ApexVoid Algo</b>\n"
      f"Regime mix looks chop-heavy for {escape(symbol)}: "
      f"chop {chop:.0%} · trend {trend:.0%} · breakout {breakout:.0%} "
      "over the trailing 24h. Trend/breakout thresholds may need tuning."
    )
    if settings.telegram_owner_id:
      await send_scanner_with_retry(text, chat_id=settings.telegram_owner_id)


async def _process_owner_entries(
  client,
  entries,
  *,
  cursor: str,
  chat_id: int,
  send=None,
) -> str:
  for entry_id, fields in entries:
    try:
      event = json.loads(fields["payload"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
      log.warning("Invalid auto-trade event %s: %s", entry_id, exc)
    else:
      await record_auto_trade_event(event)
      # Current executors persist lifecycle before publishing this event.
      # Keep the bridge only for events from an older executor.
      if not event.get("lifecycle_id"):
        await _record_lifecycle_event(client, event)
      await _record_group_result(client, event)
      await _deliver_auto_trade_event(
        client,
        event,
        profile="internal",
        chat_id=chat_id,
        send=send,
      )
    cursor = entry_id
    await client.set(_CURSOR_KEY, cursor)
  return cursor


async def _record_lifecycle_event(client, event: dict) -> None:
  state = str(event.get("state") or "")
  if state not in LIFECYCLE_STATES:
    state = {
      "opened": "order_filled",
      "add": "order_filled",
      "take_profit": "partially_closed"
      if int(event.get("remaining_volume") or 0) > 0 else "closed",
      "position_closed": "closed",
      "group_result": "closed",
      "rejected": "rejected",
      "zone_expired": "expired",
      "error": "error",
      "config_fatal": "error",
    }.get(str(event.get("type") or ""), "")
  if state not in LIFECYCLE_STATES:
    return
  position_id = event.get("position_id")
  await emit_lifecycle(
    client,
    state,
    symbol=str(event.get("symbol") or "XAU"),
    candidate_id=event.get("candidate_id"),
    correlation_id=event.get("lifecycle_id"),
    match_id=event.get("match_id"),
    range_id=event.get("range_id"),
    group_id=event.get("group_id"),
    strategy=event.get("setup") or event.get("strategy"),
    strategy_family=event.get("strategy_family"),
    direction=event.get("direction"),
    timeframe=event.get("timeframe"),
    entry_zone=event.get("entry_zone"),
    current_price=event.get("price"),
    target_plan=event.get("targets_pips"),
    stop_plan={"stop_pips": event.get("stop_pips")}
    if event.get("stop_pips") is not None else None,
    position_ids=[] if position_id is None else [int(position_id)],
    reason_code=event.get("reason_code"),
    message=str(event.get("message") or ""),
    account_type=event.get("account_type"),
    broker=event.get("broker"),
  )
  if state == "order_filled":
    await emit_lifecycle(
      client,
      "managing",
      symbol=str(event.get("symbol") or "XAU"),
      candidate_id=event.get("candidate_id"),
      correlation_id=event.get("lifecycle_id"),
      match_id=event.get("match_id"),
      range_id=event.get("range_id"),
      group_id=event.get("group_id"),
      strategy=event.get("setup"),
      strategy_family=event.get("strategy_family"),
      direction=event.get("direction"),
      position_ids=[] if position_id is None else [int(position_id)],
      message="position is under independent group management",
      account_type=event.get("account_type"),
      broker=event.get("broker"),
    )


async def _auto_trade_owner_events_loop(*, chat_id: int) -> None:
  client = redis_state.get_client()
  cursor = await client.get(_CURSOR_KEY)
  if not cursor:
    latest = await client.xrevrange(
      settings.auto_trade_event_stream,
      count=1,
    )
    cursor = latest[0][0] if latest else "0-0"
    await client.set(_CURSOR_KEY, cursor)
  log.info(
    "Auto-trade owner delivery active for chat %s from Redis cursor %s",
    chat_id,
    cursor,
  )

  while True:
    try:
      await _check_regime_alerts(client)
      batches = await client.xread(
        {settings.auto_trade_event_stream: cursor},
        count=20,
        block=5000,
      )
      for _, entries in batches:
        cursor = await _process_owner_entries(
          client,
          entries,
          cursor=cursor,
          chat_id=chat_id,
        )
    except asyncio.CancelledError:
      raise
    except Exception:
      cursor = str(await client.get(_CURSOR_KEY) or cursor)
      log.exception(
        "Auto-trade owner delivery failed at cursor %s; retrying",
        cursor,
      )
      await asyncio.sleep(5)


async def auto_trade_events_loop() -> None:
  if not settings.auto_trade_enabled or not settings.telegram_owner_id:
    return
  await _auto_trade_owner_events_loop(chat_id=settings.telegram_owner_id)
