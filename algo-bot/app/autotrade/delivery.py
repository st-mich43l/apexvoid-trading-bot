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
from app.core.config import settings
from app.bot.client import (
  delete_scanner_message,
  edit_scanner_message_text,
  send_scanner_with_retry,
)
from app.autotrade.setup_card import (
  edit_forming_card_status,
  edit_forming_card_stop,
  forming_message_key as _setup_card_forming_message_key,
  kill_setup_card,
  load_forming_card,
  load_telegram_root_message_id,
)
from app.autotrade.lifecycle import LIFECYCLE_STATES, emit_lifecycle
from app.autotrade.strategy_match_ready import load_ready_consumer_health
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
# event types that go standalone (never even try to reply) if the forming
# card is missing/expired - as opposed to _FORMING_REPLY_PREFERRED_TYPES
# below, which fall back to the older position_id reply chain instead.
_FORMING_REPLY_TYPES = frozenset({
  "strategy_route",
  "opened",
})
# One forming card per setup (P4): these lifecycle types thread directly to
# the setup's forming card when one exists, same as _FORMING_REPLY_TYPES -
# but fall back to the pre-P4 position_id-based reply chain (_reply_message_id)
# rather than going standalone, since a position with no setup_lifecycle
# record (eg. an older V6 position) still needs its existing thread to work.
_FORMING_REPLY_PREFERRED_TYPES = frozenset({
  "stop_moved",
  "take_profit",
  "position_closed",
  "order_filled",
  "tp_booked",
  "sl_moved",
  "plan_rejected",
})
# "rejected"/"invalidated"/"expired"/"cancelled" never reach render/send at
# all - see _deliver_auto_trade_event, which deletes the forming card and
# returns early instead. No card, no reply anchor, no message. "cancelled"
# is setup_lifecycle's fourth terminal state (eg. a plan build left
# incomplete across a restart) - it belongs here for the same reason the
# other three do, closing what was previously a silent orphan-card gap.
_CARD_TERMINAL_TYPES = frozenset({
  "rejected",
  "invalidated",
  "expired",
  "cancelled",
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
  # "PLAN ARMED" as a separate, user-visible idle-waiting phase is exactly
  # what the direct-publish model must not show - the plan card already
  # says PLAN PUBLISHED; the executor arming it is mechanical detail with
  # no separate lifecycle stage the owner needs to see, and treating it as
  # one invited reading a stuck ARMED card as "still waiting" right when
  # the plan is (or should be) about to fill or expire.
  "plan_armed",
  # Generic "plan published" / Redis write is not a user-facing lifecycle
  # card under one-root-card mode — progress edits the root instead.
  "plan_published",
  "v7_order_submitted",
})
# Preflight route outcomes remain in Redis, route history, metrics, and
# /auto_status, but are operator diagnostics rather than Telegram content.
# The forming card already communicates that Algo is checking the setup;
# only a successfully published route should add a route reply.
_TELEGRAM_SILENT_STRATEGY_ROUTE_STATUSES = frozenset({
  "waiting",
  "blocked",
  "executor_rejected",
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
  # TradePlan V7 (docs/adr-trade-plan-v7-boundary.md Section M).
  "plan_armed",
  "order_filled",
  "tp_booked",
  "sl_moved",
  "plan_rejected",
}

_V7_NOTIFY_DEDUP_TYPES = frozenset({
  "order_filled",
  "tp_booked",
  "sl_moved",
  "position_closed",
  "plan_rejected",
  "warning",
})

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
_VOLUME_LABEL_RE = re.compile(r"(?i)\bvolume\s*=")
_REMAINING_LABEL_RE = re.compile(r"(?i)\bremaining\s*=")
_LEG_VOLUME_RE = re.compile(r"(?i)\b(L\d+)\s*=")
_LOT_EQ_RE = re.compile(
  r"(?i)\b((?:remaining\s+)?lot=)([0-9]+(?:\.[0-9]+)?)"
)
_WEIGHTED_EQ_RE = re.compile(
  r"(?i)\bweighted\s*=\s*([0-9]+(?:\.[0-9]+)?)"
)
_AT_PRICE_RE = re.compile(
  r"(?i)(@\s*)([0-9]+(?:\.[0-9]+)?)"
)
# cTrader XAU LotSize is typically 10_000 volume units per 1.0 lot.
_DEFAULT_BROKER_LOT_SIZE = 10_000.0

DeliveryProfile = Literal["internal", "public"]


def _format_event_price(raw: str, *, digits: int | None = None) -> str:
  try:
    value = float(raw)
  except (TypeError, ValueError):
    return raw
  precision = digits
  if precision is None:
    precision = int(getattr(settings, "auto_trade_xau_price_digits", 2))
  return f"{value:.{max(0, precision)}f}"


def _broker_lot_size() -> float:
  raw = getattr(settings, "auto_trade_broker_lot_size", None)
  try:
    value = float(raw) if raw is not None else _DEFAULT_BROKER_LOT_SIZE
  except (TypeError, ValueError):
    value = _DEFAULT_BROKER_LOT_SIZE
  return value if value > 0 else _DEFAULT_BROKER_LOT_SIZE


def _format_message_lot(raw: str) -> str:
  """Render strategy lots; convert whole broker volume units when needed."""
  from app.autotrade.volume_pips import broker_volume_to_lots, format_lots

  try:
    value = float(raw)
  except (TypeError, ValueError):
    return raw
  lot_size = _broker_lot_size()
  # Legacy / mislabeled lines still carry raw units (800 → 0.08). Already-
  # converted lots (0.08) stay untouched.
  if value >= 100 and abs(value - round(value)) < 1e-9:
    value = broker_volume_to_lots(value, lot_size)
  return format_lots(value)


def _clean_message(value: object) -> str:
  text = _AUTO_NAME_RE.sub("ApexVoid Algo", str(value or ""))
  text = _POSITION_TEXT_RE.sub("", text)
  # Owner-facing fill/TP lines: broker volume units are labeled lot=, and
  # weighted/@ prices must not dump Decimal noise.
  text = _VOLUME_LABEL_RE.sub("lot=", text)
  text = _REMAINING_LABEL_RE.sub("remaining lot=", text)
  text = _LEG_VOLUME_RE.sub(r"\1 lot=", text)
  text = _LOT_EQ_RE.sub(
    lambda match: f"{match.group(1)}{_format_message_lot(match.group(2))}",
    text,
  )
  text = _WEIGHTED_EQ_RE.sub(
    lambda match: f"weighted={_format_event_price(match.group(1))}",
    text,
  )
  text = _AT_PRICE_RE.sub(
    lambda match: f"{match.group(1)}{_format_event_price(match.group(2))}",
    text,
  )
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


_CLOSE_REASON_LABELS = {
  "stop_loss_or_take_profit": "🎯 Closed by broker SL/TP",
  "manual_or_external_close": "✋ Closed manually on platform",
}

_HIGHEST_TP_ARCHIVED_RE = re.compile(
  r"(?i)highest\s+TP\s+archived\s+(?P<target>TP\d+)"
)
_NO_TP_ARCHIVED_RE = re.compile(r"(?i)\bno\s+TP\s+archived\b")
_LOSING_PIPS_RE = re.compile(
  r"(?i)\blosing\s+(?P<pips>-?\d+(?:\.\d+)?)\s*pips?\b"
)
_PLAN_CLOSED_AT_RE = re.compile(
  r"(?i)@\s*(?P<price>[0-9]+(?:\.[0-9]+)?)"
)


def _format_position_closed(event: dict, message: str) -> str:
  seq = _trade_seq_prefix(event)
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    f"🏁 {seq}<b>POSITION CLOSED</b>",
  ]
  reason_label = _CLOSE_REASON_LABELS.get(str(event.get("reason_code") or ""))
  if reason_label:
    lines.append(reason_label)
  cleaned = _MONEY_RE.sub("", message).strip(" ·") if message else ""
  highest = _HIGHEST_TP_ARCHIVED_RE.search(cleaned) if cleaned else None
  no_tp = _NO_TP_ARCHIVED_RE.search(cleaned) if cleaned else None
  # Close card: highest TP archived only — never dump per-leg lot detail.
  if highest is not None:
    archived_pips = _event_float(event, "target_pips")
    pips_suffix = (
      ""
      if archived_pips is None
      else f" · {format_signed_pips(abs(archived_pips))} pips"
    )
    lines.append(
      f"Highest TP archived: <b>{escape(highest.group('target').upper())}"
      f"{pips_suffix}</b>"
    )
    at = _PLAN_CLOSED_AT_RE.search(cleaned)
    if at is not None:
      lines.append(f"@ <b>{escape(at.group('price'))}</b>")
  elif no_tp is not None:
    lines.append("Highest TP archived: <b>none</b>")
    losing = _event_float(event, "group_realized_pips", "leg_realized_pips")
    if losing is None and cleaned:
      losing_match = _LOSING_PIPS_RE.search(cleaned)
      if losing_match is not None:
        try:
          losing = float(losing_match.group("pips"))
        except (TypeError, ValueError):
          losing = None
    if losing is not None and losing < 0:
      lines.append(f"❌ Losing: <b>{format_signed_pips(losing)} pips</b>")
    elif losing is not None and losing == 0:
      lines.append("➖ Result: <b>0 pips (BE)</b>")
    at = _PLAN_CLOSED_AT_RE.search(cleaned)
    if at is not None:
      lines.append(f"@ <b>{escape(at.group('price'))}</b>")
  elif cleaned and " lot=" not in cleaned.lower():
    lines.extend(["", escape(cleaned)])
  # Earlier TP legs on this group each already posted their own card with
  # their own leg pips (see _format_take_profit) - this is the only place
  # that recaps the group's final blended result, so a close that followed
  # one or more partial TPs (e.g. SL hit after being moved to BE/TP2/TP3)
  # doesn't read as a bare, unexplained "position closed" with no result.
  if event.get("previous_state") == "partially_closed":
    group_realized = _event_float(event, "group_realized_pips")
    if group_realized is not None:
      lines.append(f"Total: <b>{format_signed_pips(group_realized)} pips</b>")
  elif (
    no_tp is None
    and highest is None
    and str(event.get("reason_code") or "") == "stop_loss_or_take_profit"
  ):
    # One-shot SL before any TP: surface the loss instead of a bare close.
    group_realized = _event_float(event, "group_realized_pips", "leg_realized_pips")
    if group_realized is not None and group_realized < 0:
      lines.append(f"❌ Losing: <b>{format_signed_pips(group_realized)} pips</b>")
  return "\n".join(lines)


def _format_strategy_route(event: dict) -> str | None:
  status = str(event.get("status") or "")
  if status != "candidate_published":
    return None
  # "READY" is reserved for the executor accepting and arming a plan
  # (see docs/adr-trade-plan-v7-boundary.md) - Python publishing a
  # candidate is not that, so this must not read "ready".
  headline = "🟢 <b>Algo bot PLAN PUBLISHED</b>"
  measured = event.get("measured") or {}
  strategy = escape(str(event.get("strategy") or "StrategyMatch"))
  direction = escape(str(event.get("direction") or ""))
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
  return "\n".join(lines)


def _format_owner_flatten(event: dict, message: str) -> str:
  body = escape(message) if message else "owner flatten"
  return "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    "🧹 <b>FLATTEN</b>",
    "",
    body,
  ])


_TP_BOOKED_RE = re.compile(
  r"(?i)^(?:TP(?:1)?\s+)?COMPLETED\s+"
  r"(?P<target>TP\d+)\s+closed\s+"
  r"(?P<body>.*?)"
  r"(?:;\s*PLAN\s+CLOSED)?"
  r"(?:\s+\((?P<open>\d+)/(?P<total>\d+)\))?\s*$"
)
_SL_MOVED_RE = re.compile(
  r"(?i)^(?:GROUP\s+)?SL\s+MOVED(?:\s+TO\s+BE|\s+to)?\s*"
  r"(?P<price>[0-9]+(?:\.[0-9]+)?)"
  r"(?:\s*\((?P<details>[^)]*)\))?\s*$"
)


def _format_tp_booked(event: dict, message: str) -> str | None:
  """Rich TP archive card — target + fill only (no per-leg / remaining dump)."""
  cleaned = _clean_message(message)
  match = _TP_BOOKED_RE.match(cleaned)
  target = match.group("target").upper() if match else None
  plan_closed = "PLAN CLOSED" in cleaned.upper()
  price = event.get("price")
  try:
    price_text = (
      None if price is None else _format_event_price(str(price))
    )
  except Exception:
    price_text = None

  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    f"🎯 <b>TP COMPLETED</b>"
    + (f" · <b>{escape(target)}</b>" if target else ""),
  ]
  if price_text:
    lines.append(f"💰 Fill: <b>{escape(price_text)}</b>")
  archived_pips = _event_float(event, "target_pips")
  if archived_pips is not None:
    lines.append(
      f"✅ Achieved: <b>{format_signed_pips(abs(archived_pips))} pips</b>"
    )
  if plan_closed:
    lines.append("🏁 <b>PLAN CLOSED</b> · all targets booked")
  attribution = _attribution_line(event)
  if attribution:
    lines.append(attribution)
  # Fall back to the cleaned engine line when we could not parse a target.
  if not target and cleaned and not plan_closed:
    lines.extend(["", escape(cleaned)])
  return "\n".join(lines)


def _format_sl_moved(event: dict, message: str) -> str | None:
  """Rich group stop-move card — BE / trail target with price."""
  cleaned = _clean_message(message)
  match = _SL_MOVED_RE.match(cleaned)
  price = None
  details = ""
  if match is not None:
    price = match.group("price")
    details = str(match.group("details") or "").strip()
  if price is None:
    price_val = _event_float(event, "price", "stop_price", "new_stop")
    if price_val is not None:
      price = _format_event_price(str(price_val))
  upper = cleaned.upper()
  if "TO BE" in upper or upper.startswith("GROUP SL MOVED TO BE"):
    kind = "Break-even"
    icon = "🔐"
  elif "TRAIL" in upper:
    kind = "Trail"
    icon = "🛰️"
  else:
    kind = "Stop update"
    icon = "🛡"
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    f"{icon} <b>GROUP SL MOVED</b> · {escape(kind)}",
  ]
  if price is not None:
    lines.append(f"🛡 New SL: <b>{escape(str(price))}</b>")
  if details:
    lines.append(f"📎 {escape(details)}")
  elif cleaned and price is None:
    lines.extend(["", escape(cleaned)])
  attribution = _attribution_line(event)
  if attribution:
    lines.append(attribution)
  return "\n".join(lines)


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
  if (
    event_type == "strategy_route"
    and str(event.get("status") or "")
      in _TELEGRAM_SILENT_STRATEGY_ROUTE_STATUSES
  ):
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
  if event_type in _CARD_TERMINAL_TYPES:
    # One forming card per setup (P4): rejected/invalidated/expired never
    # become a card - _deliver_auto_trade_event deletes the forming card
    # (kill_setup_card) instead and returns before ever calling this
    # function's send path. No "EXECUTOR REJECTED" text, no notification.
    return None
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
  if event_type == "tp_booked":
    rendered = _format_tp_booked(event, message)
    if rendered:
      return rendered
  if event_type == "sl_moved":
    rendered = _format_sl_moved(event, message)
    if rendered:
      return rendered
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
    # TradePlan V7 (docs/adr-trade-plan-v7-boundary.md Section M) - distinct
    # wording from the V6 labels above so a published plan is never
    # confused with a merely-confirmed setup ("Do not say READY when
    # Python only publishes a plan").
    "plan_armed": "🎯 <b>PLAN ARMED</b>",
    "order_filled": "✅ <b>ORDER FILLED</b>",
    "tp_booked": "🎯 <b>TP COMPLETED</b>",
    "sl_moved": "🛡 <b>GROUP SL MOVED</b>",
    "plan_rejected": "⛔ <b>PLAN REJECTED</b>",
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
  # Delegates to setup_card so scanner.py (which writes the card) and this
  # module (which threads replies to it / deletes it) always agree on the
  # exact same Redis key - match_id and setup_lifecycle's setup_id are the
  # same identifier (setup_id = StrategyMatch.match_id, see worker.py).
  return _setup_card_forming_message_key(match_id)


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
  """Resolve the setup/forming-card id for reply/edit threading.

  TradePlan V7 events carry ``match_id`` = setup_id and ``candidate_id`` /
  ``group_id`` = ``v7:{setup_id}``. Prefer the real setup id; when only a
  plan id is present, strip the ``v7:`` prefix so we still find the root
  card instead of falling back to a standalone message.
  """
  for key in ("match_id", "setup_id"):
    value = str(event.get(key) or "").strip()
    if value:
      return value[3:] if value.startswith("v7:") else value
  for key in ("candidate_id", "plan_id", "group_id", "correlation_id"):
    value = str(event.get(key) or "").strip()
    if value.startswith("v7:") and len(value) > 3:
      return value[3:]
  return str(event.get("candidate_id") or "").strip()


async def _forming_reply_message_id(
  client,
  event: dict,
) -> tuple[int | None, str]:
  match_id = _event_match_id(event)
  if not match_id:
    return None, "event has no match id"
  # Prefer the live forming card address over telegram_root — root can go
  # stale if the card was re-posted while the root key lagged behind.
  card = await load_forming_card(client, match_id)
  if card is not None:
    try:
      message_id = int(card["message_id"])
    except (KeyError, TypeError, ValueError):
      message_id = 0
    if message_id > 0:
      return message_id, ""
  root_id = await load_telegram_root_message_id(client, match_id)
  if root_id is not None and root_id > 0:
    return root_id, ""
  key = _forming_message_key(match_id)
  return None, f"stored forming message is missing or expired ({key})"


async def _apply_zone_watch_outcome_from_event(client, event: dict) -> None:
  """Map fill/reject/expiry Telegram events onto ZoneWatch consume/rearm."""
  event_type = str(event.get("type") or "")
  zone_id = str(
    event.get("zone_id")
    or (event.get("measured") or {}).get("zone_id")
    or event.get("confluence_zone_id")
    or ""
  ).strip()
  if not zone_id:
    return
  plan_id = str(
    event.get("candidate_id") or event.get("plan_id") or event.get("group_id") or ""
  ).strip() or None
  reason = str(event.get("reason_code") or event_type)
  try:
    from app.autotrade.zone_watch import apply_zone_watch_plan_outcome

    if event_type in {"order_filled", "opened"}:
      await apply_zone_watch_plan_outcome(
        client,
        zone_id,
        outcome="fill",
        reason_code=reason,
        plan_id=plan_id,
      )
    elif event_type in {"plan_rejected", "rejected"}:
      await apply_zone_watch_plan_outcome(
        client,
        zone_id,
        outcome="reject",
        reason_code=reason,
        plan_id=plan_id,
      )
    elif event_type in {"expired", "cancelled"}:
      await apply_zone_watch_plan_outcome(
        client,
        zone_id,
        outcome=event_type,
        reason_code=reason,
        plan_id=plan_id,
      )
  except Exception:
    log.exception(
      "zone watch outcome apply failed type=%s zone_id=%s",
      event_type,
      zone_id,
    )


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
  thread_to_card = (
    settings.delivery_thread_lifecycle
    and (event_type in _FORMING_REPLY_TYPES or event_type in _FORMING_REPLY_PREFERRED_TYPES)
  )
  if thread_to_card:
    forming_reply, reason = await _forming_reply_message_id(client, event)
    if forming_reply is not None:
      return forming_reply, ""
    if event_type in _FORMING_REPLY_TYPES:
      return None, reason
    # _FORMING_REPLY_PREFERRED_TYPES: no card found - fall through below to
    # the pre-P4 position_id reply chain instead of going standalone.
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
  if event_type == "setup_status":
    if profile != "internal":
      return False
    match_id = _event_match_id(event)
    status_line = str((event.get("measured") or {}).get("status_line") or "")
    if not match_id or not status_line:
      return False
    return await edit_forming_card_status(
      client,
      match_id,
      status_line,
      reason_code=str(event.get("reason_code") or ""),
      event_id=str(event.get("lifecycle_id") or "") or None,
      edit_fn=edit_scanner_message_text,
    )
  if event_type in _CARD_TERMINAL_TYPES:
    # One forming card per setup (P4): reject/invalidate/expire deletes the
    # card and posts nothing - never a bare "EXECUTOR REJECTED" message.
    # Forming cards are internal/owner-chat only today (see setup_card.py),
    # so this only needs to run once, not once per delivery profile.
    if profile == "internal":
      match_id = _event_match_id(event)
      if match_id:
        await kill_setup_card(
          client,
          match_id,
          reason_code=str(event.get("reason_code") or event_type),
          delete_fn=delete_scanner_message,
          edit_fn=edit_scanner_message_text,
        )
    return False
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
    await _apply_zone_watch_outcome_from_event(client, event)
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
  if event_type in _V7_NOTIFY_DEDUP_TYPES:
    plan_id = str(
      event.get("candidate_id") or event.get("group_id") or ""
    ).strip()
    if plan_id:
      event_key = str(
        event.get("reason_code")
        or event.get("lifecycle_id")
        or f"{event_type}:{event.get('message') or ''}"
      ).strip()
      # Prefer a stable key when the engine already stamped one into message
      # prefixes; fall back to type+message hash for durability across restarts.
      dedup_key = f"auto_trade:v7_notify:{plan_id}:{event_type}:{hash(event_key) & 0xffffffff:x}"
      claimed = await client.set(
        dedup_key,
        "1",
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
  root_edited = False
  # Keep the root setup card status in sync for fills/TP/SL so the owner
  # always sees progress on the original message, then thread the detail
  # as a reply (never a duplicate standalone when the root already has it).
  if (
    profile == "internal"
    and match_id
    and event_type in {"order_filled", "tp_booked", "sl_moved"}
  ):
    status_message = _clean_message(event.get("message", "")) or event_type
    if event_type == "order_filled":
      status_line = f"✅ <b>ORDER FILLED</b> · {escape(status_message)}"
      status_state = "order_filled"
    elif event_type == "tp_booked":
      status_line = f"🎯 <b>TP COMPLETED</b> · {escape(status_message)}"
      status_state = "tp_booked"
    else:
      status_line = f"🛡 <b>GROUP SL MOVED</b> · {escape(status_message)}"
      status_state = "sl_moved"
    edit_result = await edit_forming_card_status(
      client,
      match_id,
      status_line,
      state=status_state,
      reason_code=str(event.get("reason_code") or event_type),
      event_id=str(event.get("lifecycle_id") or "") or None,
      edit_fn=edit_scanner_message_text,
    )
    root_edited = edit_result is not False
  # Trailing / BE stop moves: patch the root Stop line, then reply-thread
  # the compact "move SL to …" notify onto the same forming card.
  if profile == "internal" and match_id and event_type == "stop_moved":
    new_sl = _event_float(event, "new_stop", "stop_price", "price")
    if new_sl is None:
      stop_match = _STOP_RE.match(_clean_message(event.get("message", "")))
      if stop_match is not None:
        try:
          new_sl = float(str(stop_match.group(1)).replace(",", ""))
        except (TypeError, ValueError):
          new_sl = None
    if new_sl is not None:
      try:
        await edit_forming_card_stop(
          client,
          match_id,
          float(new_sl),
          digits=int(getattr(settings, "auto_trade_xau_price_digits", 2)),
          edit_fn=edit_scanner_message_text,
        )
      except Exception:
        log.exception(
          "forming card stop patch failed on stop_moved setup_id=%s",
          match_id,
        )
  reply_to, reason = await _resolve_reply_message_id(client, event, profile)
  if reply_to is None and (
    event_type in _FORMING_REPLY_TYPES
    or event_type in _FORMING_REPLY_PREFERRED_TYPES
    or (event_type != "opened" and position_id is not None)
  ):
    log.info(
      "Auto-trade reply unavailable for %s (%s): %s; %s",
      match_id or position_id or event_type,
      profile,
      reason,
      "card already updated — skipping standalone"
      if root_edited
      else "sending standalone",
    )
    if root_edited and event_type in {"order_filled", "tp_booked", "sl_moved"}:
      return True
  try:
    sent = await send(text, reply_to=reply_to, chat_id=chat_id)
  except TelegramBadRequest as error:
    if reply_to is None or not _is_bad_reply_target(error):
      raise
    log.info(
      "Auto-trade reply rejected for %s (%s): %s; %s",
      match_id or position_id or event_type,
      profile,
      error,
      "card already updated — skipping standalone"
      if root_edited
      else "retrying standalone",
    )
    if root_edited and event_type in {"order_filled", "tp_booked", "sl_moved"}:
      return True
    sent = await send(text, reply_to=None, chat_id=chat_id)
  if event_type in {"opened", "add", "order_filled"}:
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
  """Compact owner status — a few operator details, Telegram 4096-safe."""
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
  executor = await _json_key(
    client, f"auto_trade:executor_snapshot:{primary_symbol}"
  )
  last_route = await _json_key(
    client, f"auto_trade:last_route_outcome:{primary_symbol}"
  )
  mode = (
    "disabled"
    if not settings.auto_trade_enabled
    else "dry run"
    if settings.auto_trade_dry_run
    else "demo trading"
  )
  state = "paused" if paused else "running"
  profile = str(
    (config_health or {}).get("profile")
    or settings.auto_trade_profile
    or "conservative"
  )
  selected_text = "none"
  execution_state = "-"
  why = ""
  regime = ""
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
          regime = str(payload.get("regime") or "").strip()
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
  group_ids = executor.get("group_ids") if isinstance(executor, dict) else None
  group_count = len(group_ids) if isinstance(group_ids, list) else 0
  lines = [
    "🤖 <b>Algo bot</b>",
    f"{escape(mode)} · <b>{state}</b> · {escape(profile)}",
    f"Open <b>{position_count}</b> · groups <b>{group_count}</b> · today <b>{daily}</b>",
    f"{escape(selected_text)} · {escape(execution_state)}",
  ]
  health_bits = [f"Config <b>{escape(config_state)}</b>", f"ready <b>{ready}</b>"]
  if regime:
    health_bits.insert(0, f"Regime <b>{escape(regime)}</b>")
  lines.append(" · ".join(health_bits))
  route_line = _compact_route_line(last_route)
  if route_line:
    lines.append(f"Route: {escape(route_line)}")
  if why:
    lines.append(f"Why: {escape(why)}")
  if settings.auto_trade_enabled:
    # P0-11: a card sitting at "waiting for retest" and a genuinely dead
    # ready-event consumer look identical from selected_text/execution_state
    # alone - surface the consumer's own health so the owner can tell
    # "nothing to do right now" apart from "nothing will ever advance."
    consumer_health = await load_ready_consumer_health(client)
    consumer_state = str((consumer_health or {}).get("state") or "unknown")
    if consumer_state in {"degraded_retrying", "fatal"}:
      lines.append(
        f"⚠️ Ready consumer <b>{escape(consumer_state)}</b> "
        f"(retry {int((consumer_health or {}).get('retry_count') or 0)})"
      )
    elif consumer_state == "unknown":
      lines.append("⚠️ Ready consumer health unknown")
  text = "\n".join(lines)
  # Soft budget for the owner DM; hard clip stays in the handler at 4000.
  if len(text) > 1200:
    text = text[:1190] + "\n…"
  return text


# preflight_reason_code lingers as whatever the last preflight-stage event
# recorded (route_outcome.py) - once a candidate clears every preflight
# check, that's "preflight_allowed"/"preflight_allowed_parent_group", and
# it then sits there as the displayed "why" on every later status check
# even though it explains nothing (it means "passed", not "here's what
# happened or why"). Never show a pass-through code as if it were a
# reason.
_NON_REASON_ROUTE_CODES = {"preflight_allowed", "preflight_allowed_parent_group"}


def _compact_route_line(route: dict) -> str:
  if not route:
    return ""
  strategy = str(route.get("strategy") or "").strip()
  status = str(route.get("status") or "").replace("_", " ").strip()
  reason = str(
    route.get("reason_code") or route.get("preflight_reason_code") or ""
  ).strip()
  bits = [item for item in (strategy, status) if item]
  if (
    reason
    and reason not in _NON_REASON_ROUTE_CODES
    and reason.replace("_", " ") not in status
  ):
    bits.append(reason[:48])
  return " · ".join(bits)





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
