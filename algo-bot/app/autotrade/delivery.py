"""Owner controls and Telegram delivery for cTrader auto-trade events."""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from html import escape
from typing import Literal

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from app.analysis.scanner import clear_active_setup_tracking
from app.autotrade import units
from app.autotrade.event_integrity import (
  contradictory_archived_tp,
  terminal_loss_at_protective_stop,
  close_at_protective_stop,
  close_at_breakeven,
)
from app.autotrade.volume_pips import (
  format_signed_pips,
  volume_percent,
)
from app.persistence import redis_state
from app.core.config import runtime_config
from app.bot.client import (
  delete_scanner_message,
  edit_scanner_message_text,
  send_scanner_with_retry,
)
from app.autotrade.setup_card import (
  FORMING_ACTIVE_INDEX_KEY,
  card_price_digits,
  edit_forming_card_status,
  edit_forming_card_stop,
  forming_message_key as _setup_card_forming_message_key,
  is_setup_terminal,
  kill_setup_card,
  load_forming_card,
  load_telegram_root_message_id,
  parse_forming_card_symbol,
  published_plan_stop_price,
)
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
_TRADE_MESSAGE_TTL = 7 * 24 * 3600
_FULL_TP_RESULT_TTL = 24 * 3600
# Live 2026-08-11: order_filled arrived and sent standalone (no reply_to)
# while the setup's own root/forming card was still mid-send - a single
# Telegram edit/send round-trip took 17s in production (flood-control
# throttling per the existing TelegramRetryAfter handling), long enough for
# a fast-filling order to race ahead of its own root card. Bounded poll
# before falling back to standalone, same pattern as setup_card.py's
# _await_peer_forming_card but budgeted for a network round-trip, not just
# Redis lock contention. Live 2026-08-12 raised this again after a 247s
# flood dropped the root entirely — fill path now also recreates the card.
_FORMING_REPLY_WAIT_SECONDS = 45.0
_FORMING_REPLY_POLL_SECONDS = 0.5
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
  "plan_expired",
  "plan_cancelled",
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
  "v8_order_submitted",
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
  # TradePlan V8 lifecycle events.
  "plan_armed",
  "order_filled",
  "tp_booked",
  "sl_moved",
  "plan_rejected",
  "plan_expired",
  "plan_cancelled",
}

_TRADE_PLAN_NOTIFY_DEDUP_TYPES = frozenset({
  "order_filled",
  "tp_booked",
  "sl_moved",
  "position_closed",
  "plan_rejected",
  "plan_expired",
  "plan_cancelled",
  "warning",
})

# cTrader reconnect republishes ready/config_health/account_capability on
# every session bootstrap — batch into one owner DM; cooldown suppresses repeats.
_SESSION_NOTIFY_COOLDOWN_TYPES = frozenset({
  "ready",
  "account_capability",
  "config_health",
})
_SESSION_NOTIFY_COOLDOWN_SECONDS = 6 * 3600
_SESSION_BOOTSTRAP_BATCH_KEY = "auto_trade:session_bootstrap_batch"
_SESSION_NOTIFY_SENT_KEY = "auto_trade:session_notify:sent"
_READY_BALANCE_RE = re.compile(
  r"(?i)\bbalance\s+([0-9][0-9,]*(?:\.[0-9]+)?)\b"
)
_LIVE_GRANT_WARNING_RE = re.compile(r"(?i)token grants live account")

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


def _event_symbol(event: dict) -> str:
  return str(event.get("symbol") or "XAU").strip().upper()


def _format_event_price(
  raw: str,
  *,
  digits: int | None = None,
  symbol: str | None = None,
  grouped: bool = False,
) -> str:
  try:
    value = float(raw)
  except (TypeError, ValueError):
    return raw
  precision = digits
  if precision is None:
    precision = (
      int(runtime_config.for_instrument(symbol).units.price_digits)
      if symbol
      else int(runtime_config.contract.instrument.price_digits)
    )
  spec = f",.{max(0, precision)}f" if grouped else f".{max(0, precision)}f"
  return f"{value:{spec}}"


def _broker_lot_size() -> float:
  return _DEFAULT_BROKER_LOT_SIZE


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


def _clean_message(value: object, *, symbol: str | None = None) -> str:
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
    lambda match: (
      f"weighted={_format_event_price(match.group(1), symbol=symbol)}"
    ),
    text,
  )
  text = _AT_PRICE_RE.sub(
    lambda match: (
      f"{match.group(1)}{_format_event_price(match.group(2), symbol=symbol)}"
    ),
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
  symbol = _event_symbol(event)
  side_icon = "🟢" if direction.upper() == "BUY" else "🔴"
  full_tp = re.search(r"(?i)full TP\s+(\d+)p", details)
  range_box = re.search(r"(?i)range\s+([\d.,]+)-([\d.,]+)", details)
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    "✅ <b>ORDER FILLED</b>",
    f"{side_icon} <b>{escape(symbol)} {direction.upper()} opened</b>",
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
        target_pips * units.pip_size(symbol)
        if direction.upper() == "BUY"
        else -target_pips * units.pip_size(symbol)
      )
      lines.append(
        "🎯 Full TP: <b>"
        f"{_format_event_price(str(target_price), symbol=symbol, grouped=True)}"
        f"</b> · +{target_pips} pips"
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


def _use_stop_close_format(event: dict, *, reason: str, cleaned: str) -> bool:
  if terminal_loss_at_protective_stop(event):
    return True
  if reason == "manual_or_external_close" and close_at_protective_stop(event):
    return True
  if _NO_TP_ARCHIVED_RE.search(cleaned or ""):
    return True
  if reason == "stop_loss_or_take_profit" and (
    (_resolve_close_pips(event, cleaned, allow_stop_fallback=True) or 0) < 0
  ):
    return True
  return False

_HIGHEST_TP_ARCHIVED_RE = re.compile(
  r"(?i)highest\s+TP\s+archived\s+(?P<target>TP\d+)"
)
_NO_TP_ARCHIVED_RE = re.compile(r"(?i)\bno\s+TP\s+archived\b")
_LOSING_PIPS_RE = re.compile(
  r"(?i)\blosing\s+(?P<pips>-?\d+(?:\.\d+)?)\s*pips?\b"
)
_WINNING_PIPS_RE = re.compile(
  r"(?i)\bwinning\s+(?P<pips>-?\d+(?:\.\d+)?)\s*pips?\b"
)
_PLAN_CLOSED_AT_RE = re.compile(
  r"(?i)@\s*(?P<price>[0-9]+(?:\.[0-9]+)?)"
)


def _resolve_close_pips(
  event: dict,
  cleaned: str,
  *,
  allow_stop_fallback: bool = False,
) -> float | None:
  """Signed pips for a close card.

  Stop-distance fallback is only for confirmed SL / no-TP-archived cards.
  Manual and unconfirmed broker closes must not invent a loss from SL.
  """
  pips = _event_float(event, "group_realized_pips", "leg_realized_pips")
  if pips is None and cleaned:
    losing_match = _LOSING_PIPS_RE.search(cleaned)
    winning_match = _WINNING_PIPS_RE.search(cleaned)
    parsed = losing_match or winning_match
    if parsed is not None:
      try:
        pips = float(parsed.group("pips"))
      except (TypeError, ValueError):
        pips = None
  if pips is not None:
    return pips
  exit_price = _event_float(event, "price")
  entry = _event_float(event, "entry_price", "weighted_entry", "fill_price")
  direction = str(event.get("direction") or "").upper()
  if entry is not None and exit_price is not None and direction:
    try:
      from app.core.symbols import pip_for
      move = float(exit_price) - float(entry)
      if direction == "SELL":
        move = -move
      return move / pip_for(str(event.get("symbol") or "XAU"))
    except Exception:
      pass
  if not allow_stop_fallback:
    return None
  stop_pips = _event_float(event, "stop_pips")
  if stop_pips is not None and stop_pips > 0:
    return -abs(stop_pips)
  stop_loss = _event_float(event, "stop_loss", "stop_price", "new_stop")
  if entry is None or stop_loss is None or not direction:
    return None
  try:
    from app.core.symbols import pip_for
    move = float(stop_loss) - float(entry)
    if direction == "SELL":
      move = -move
    return move / pip_for(str(event.get("symbol") or "XAU"))
  except Exception:
    return None


def _resolve_no_tp_loss_pips(event: dict, cleaned: str) -> float | None:
  """Net pips for an SL close that never archived a TP."""
  return _resolve_close_pips(event, cleaned, allow_stop_fallback=True)


def _sl_close_result_parts(
  event: dict,
  cleaned: str,
  *,
  html: bool,
) -> list[str]:
  """Build SL / result / @price fragments for a single close status line."""
  parts: list[str] = ["🛡 <b>SL</b>" if html else "🛡 SL"]
  losing = _resolve_no_tp_loss_pips(event, cleaned)
  if losing is not None and losing < 0:
    body = f"❌ Losing: {format_signed_pips(losing)} pips"
    parts.append(f"<b>{body}</b>" if html else body)
  elif losing is not None and losing == 0:
    body = "➖ Result: 0 pips (BE)"
    parts.append(f"<b>{body}</b>" if html else body)
  elif losing is not None:
    body = f"Total: {format_signed_pips(losing)} pips"
    parts.append(f"<b>{body}</b>" if html else body)
  at = _PLAN_CLOSED_AT_RE.search(cleaned) if cleaned else None
  if at is not None:
    price = escape(at.group("price"))
    parts.append(f"@ <b>{price}</b>" if html else f"@ {price}")
  return parts


def _append_signed_result_lines(
  lines: list[str],
  pips: float | None,
  *,
  html: bool,
) -> None:
  if pips is None:
    return
  if pips < 0:
    body = f"❌ Losing: {format_signed_pips(pips)} pips"
  elif pips > 0:
    body = f"✅ Winning: {format_signed_pips(pips)} pips"
  else:
    body = "➖ Result: 0 pips (BE)"
  lines.append(f"<b>{body}</b>" if html else body)


def _format_position_closed(event: dict, message: str) -> str:
  seq = _trade_seq_prefix(event)
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    f"🏁 {seq}<b>POSITION CLOSED</b>",
  ]
  cleaned = _MONEY_RE.sub("", message).strip(" ·") if message else ""
  highest = _HIGHEST_TP_ARCHIVED_RE.search(cleaned) if cleaned else None
  no_tp = _NO_TP_ARCHIVED_RE.search(cleaned) if cleaned else None
  reason = str(event.get("reason_code") or "")
  if contradictory_archived_tp(event, cleaned):
    highest = None
    no_tp = True
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
  elif _use_stop_close_format(event, reason=reason, cleaned=cleaned):
    lines.append(" · ".join(_sl_close_result_parts(event, cleaned, html=True)))
  else:
    reason_label = _CLOSE_REASON_LABELS.get(reason)
    if close_at_breakeven(event, cleaned):
      reason_label = "🛡 Closed at BE stop"
    elif reason_label and close_at_protective_stop(event):
      reason_label = None
    if reason_label:
      lines.append(reason_label)
    if event.get("previous_state") != "partially_closed":
      _append_signed_result_lines(
        lines,
        _resolve_close_pips(event, cleaned, allow_stop_fallback=False),
        html=True,
      )
    exit_price = _event_float(event, "price")
    if exit_price is not None:
      lines.append(
        f"@ <b>{escape(_format_event_price(str(exit_price), symbol=_event_symbol(event)))}</b>"
      )
    elif cleaned and " lot=" not in cleaned.lower() and reason_label is None:
      lines.extend(["", escape(cleaned)])
  if event.get("previous_state") == "partially_closed":
    group_realized = _event_float(event, "group_realized_pips")
    if group_realized is not None:
      lines.append(f"Total: <b>{format_signed_pips(group_realized)} pips</b>")
  return "\n".join(lines)


def _format_strategy_route(event: dict) -> str | None:
  status = str(event.get("status") or "")
  if status != "candidate_published":
    return None
  # "READY" is reserved for the executor accepting and arming a plan
  # (see docs/adr-trade-plan-v8-cutover.md) - Python publishing a
  # candidate is not that, so this must not read "ready".
  headline = "🟢 <b>Algo bot PLAN PUBLISHED</b>"
  measured = event.get("measured") or {}
  symbol = _event_symbol(event)
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
    zone_text = (
      f"{_format_event_price(str(zone_low), symbol=symbol, grouped=True)}–"
      f"{_format_event_price(str(zone_high), symbol=symbol, grouped=True)}"
    )

  route = measured.get("planned_execution_route")
  planned_entry = measured.get("planned_entry_price")
  executor_distance = measured.get("executor_distance_pips")
  executor_limit = measured.get("executor_limit_pips")
  if route:
    lines.append(f"Route: <b>{escape(str(route))}</b>")
  if planned_entry is not None:
    lines.append(
      "Planned entry: <b>"
      f"{_format_event_price(str(planned_entry), symbol=symbol, grouped=True)}"
      "</b>"
    )
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
  symbol = _event_symbol(event)
  cleaned = _clean_message(message, symbol=symbol)
  if contradictory_archived_tp(event, cleaned):
    return None
  match = _TP_BOOKED_RE.match(cleaned)
  target = match.group("target").upper() if match else None
  plan_closed = "PLAN CLOSED" in cleaned.upper()
  price = event.get("price")
  try:
    price_text = (
      None if price is None else _format_event_price(str(price), symbol=symbol)
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
  # Fall back to the cleaned engine line when we could not parse a target.
  if not target and cleaned and not plan_closed:
    lines.extend(["", escape(cleaned)])
  return "\n".join(lines)


def _format_sl_moved(event: dict, message: str) -> str | None:
  """Rich group stop-move card — BE / trail target with price."""
  symbol = _event_symbol(event)
  cleaned = _clean_message(message, symbol=symbol)
  match = _SL_MOVED_RE.match(cleaned)
  price = None
  details = ""
  if match is not None:
    price = match.group("price")
    details = str(match.group("details") or "").strip()
  if price is None:
    price_val = _event_float(event, "price", "stop_price", "new_stop")
    if price_val is not None:
      price = _format_event_price(str(price_val), symbol=symbol)
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
    "🛡 move SL to <b>"
    f"{_format_event_price(str(new_sl), symbol=_event_symbol(event), grouped=True)}"
    "</b>",
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
  message = _clean_message(
    event.get("message", ""),
    symbol=_event_symbol(event),
  )
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
    # TradePlan V8 lifecycle - distinct
    # wording from the V6 labels above so a published plan is never
    # confused with a merely-confirmed setup ("Do not say READY when
    # Python only publishes a plan").
    "plan_armed": "🎯 <b>PLAN ARMED</b>",
    "order_filled": "✅ <b>ORDER FILLED</b>",
    "tp_booked": "🎯 <b>TP COMPLETED</b>",
    "sl_moved": "🛡 <b>GROUP SL MOVED</b>",
    "plan_rejected": "⛔ <b>PLAN REJECTED</b>",
    "plan_expired": "⌛ <b>PLAN EXPIRED</b>",
    "plan_cancelled": "🚫 <b>PLAN CANCELLED</b>",
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


def _manage_msg_key(match_id: str) -> str:
  return f"auto_trade:manage_msg:{match_id}"


def _manage_text_key(match_id: str) -> str:
  return f"auto_trade:manage_text:{match_id}"


async def _save_manage_message(
  client,
  match_id: str,
  *,
  message_id: int,
  text: str,
) -> None:
  pipe = client.pipeline()
  pipe.set(_manage_msg_key(match_id), str(int(message_id)), ex=_TRADE_MESSAGE_TTL)
  pipe.set(_manage_text_key(match_id), text, ex=_TRADE_MESSAGE_TTL)
  await pipe.execute()


async def _load_manage_message(
  client,
  match_id: str,
) -> tuple[int | None, str | None]:
  raw_id, raw_text = await client.mget(
    _manage_msg_key(match_id),
    _manage_text_key(match_id),
  )
  message_id: int | None = None
  if raw_id:
    try:
      message_id = int(raw_id)
    except (TypeError, ValueError):
      message_id = None
  text = None if raw_text is None else (
    raw_text.decode() if isinstance(raw_text, bytes) else str(raw_text)
  )
  if message_id is not None and message_id <= 0:
    message_id = None
  return message_id, text


async def _delete_prior_manage_reply(
  chat_id: int,
  message_id: int | None,
  *,
  match_id: str,
) -> None:
  """Best-effort delete of the prior manage notification (never the root card)."""
  if message_id is None:
    return
  try:
    await delete_scanner_message(chat_id, message_id)
  except Exception:
    log.exception(
      "manage reply delete failed setup_id=%s message_id=%s",
      match_id,
      message_id,
    )


async def _post_manage_reply(
  client,
  event: dict,
  *,
  match_id: str,
  chat_id: int,
  send,
  text: str,
  remember: bool = False,
  require_reply_target: bool = False,
) -> int | None:
  """Post a manage reply under the root card and persist Redis keys."""
  reply_to, reason = await _resolve_reply_message_id(client, event, "internal")
  if reply_to is None and match_id:
    # Live 2026-08-12: publish raced Telegram flood → root never created,
    # fill arrived as standalone ORDER FILLED. Recover the root first.
    recovered = await _ensure_root_card_for_manage_reply(
      client,
      event,
      match_id=match_id,
      chat_id=chat_id,
    )
    if recovered is not None:
      reply_to = recovered
      reason = ""
  if reply_to is None:
    event_type = str(event.get("type") or "")
    must_thread = event_type in {
      "order_filled", "opened", "tp_booked", "position_closed",
      "take_profit", "stop_moved", "sl_moved",
    }
    if require_reply_target or must_thread:
      if must_thread and match_id:
        await _schedule_deferred_manage_reply(
          client,
          event,
          match_id=match_id,
          chat_id=chat_id,
          send=send,
          text=text,
          remember=remember,
        )
        log.error(
          "Auto-trade manage reply deferred for %s until root card exists "
          "(%s) — not sending standalone",
          match_id,
          reason,
        )
        return None
      log.info(
        "Auto-trade manage reply unavailable for %s: %s; skipping",
        match_id,
        reason,
      )
      return None
    log.error(
      "Auto-trade manage reply unavailable for %s: %s; sending standalone",
      match_id,
      reason,
    )
  try:
    sent = await send(text, reply_to=reply_to, chat_id=chat_id)
  except TelegramBadRequest as error:
    if reply_to is not None and _is_bad_reply_target(error):
      log.info(
        "Auto-trade manage reply rejected for %s: %s; skipping",
        match_id,
        error,
      )
      return None
    raise
  message_id = int(sent.message_id)
  await _save_manage_message(client, match_id, message_id=message_id, text=text)
  if remember:
    await _remember_trade_message(client, event, "internal", message_id)
  return message_id


async def _ensure_root_card_for_manage_reply(
  client,
  event: dict,
  *,
  match_id: str,
  chat_id: int,
) -> int | None:
  """Create a missing root card so fill/TP/close can reply under it.

  Waits out scanner flood and retries once — never give up on the first
  RetryAfter when a setup_id is known.
  """
  from app.autotrade.setup_card import ensure_root_card_for_setup_id, load_forming_card
  from app.bot.client import note_scanner_flood, wait_out_scanner_flood

  existing = await load_forming_card(client, match_id)
  if existing is not None and int(existing.get("message_id") or 0) > 0:
    return int(existing["message_id"])

  for attempt in range(1, 3):
    try:
      await wait_out_scanner_flood()
      message_id = await ensure_root_card_for_setup_id(
        client,
        match_id,
        symbol=str(event.get("symbol") or "XAU"),
        chat_id=chat_id,
        event=event,
      )
    except TelegramRetryAfter as exc:
      await note_scanner_flood(exc.retry_after)
      wait = max(1, int(exc.retry_after) + 1)
      log.warning(
        "manage_reply_root_card_flood setup_id=%s attempt=%s "
        "retry_after=%ss; waiting then retrying",
        match_id,
        attempt,
        exc.retry_after,
      )
      if attempt >= 2:
        break
      await asyncio.sleep(min(wait, 360))
      continue
    except Exception:
      log.exception(
        "manage_reply_root_card_recovery_failed setup_id=%s attempt=%s",
        match_id,
        attempt,
      )
      return None
    if message_id is not None and message_id > 0:
      log.info(
        "manage_reply_root_card_recovered setup_id=%s message_id=%s",
        match_id,
        message_id,
      )
      return int(message_id)
    return None
  return None


async def _schedule_deferred_manage_reply(
  client,
  event: dict,
  *,
  match_id: str,
  chat_id: int,
  send,
  text: str,
  remember: bool,
) -> None:
  """Post fill/TP/close under the root after flood clears — never standalone."""

  async def _run() -> None:
    from app.bot.client import wait_out_scanner_flood

    try:
      await wait_out_scanner_flood()
      await asyncio.sleep(0.2)
      recovered = await _ensure_root_card_for_manage_reply(
        client,
        event,
        match_id=match_id,
        chat_id=chat_id,
      )
      if recovered is None:
        log.error(
          "deferred_manage_reply_still_no_root setup_id=%s — giving up",
          match_id,
        )
        return
      sent = await send(text, reply_to=recovered, chat_id=chat_id)
      message_id = int(sent.message_id)
      await _save_manage_message(
        client, match_id, message_id=message_id, text=text,
      )
      if remember:
        await _remember_trade_message(client, event, "internal", message_id)
      await _mark_forming_card_position_activated(client, match_id)
      log.info(
        "deferred_manage_reply_posted setup_id=%s message_id=%s "
        "reply_to=%s",
        match_id,
        message_id,
        recovered,
      )
    except Exception:
      log.exception(
        "deferred_manage_reply_failed setup_id=%s",
        match_id,
      )

  asyncio.create_task(
    _run(),
    name=f"deferred-manage-reply:{match_id}",
  )


async def _replace_manage_reply(
  client,
  event: dict,
  *,
  match_id: str,
  chat_id: int,
  send,
  text: str,
  old_message_id: int | None,
  remember: bool = False,
  require_reply_target: bool = False,
) -> int | None:
  """Delete the prior manage notification and reply with updated information.

  The SETUP FORMING root card is left untouched — only the threaded manage
  reply (fills / TP / SL / closed) is replaced.
  """
  await _delete_prior_manage_reply(chat_id, old_message_id, match_id=match_id)
  return await _post_manage_reply(
    client,
    event,
    match_id=match_id,
    chat_id=chat_id,
    send=send,
    text=text,
    remember=remember,
    require_reply_target=require_reply_target,
  )


def _split_manage_fill_and_tps(text: str) -> tuple[str, list[str]]:
  """Split stored manage body into fill header + accumulated TP/close lines."""
  fill_lines: list[str] = []
  append_lines: list[str] = []

  def _is_append(line: str) -> bool:
    stripped = line.lstrip("• ").strip()
    return (
      stripped.startswith("🎯")
      or stripped.startswith("🏁")
      or stripped.startswith("🔐")
      or stripped.startswith("🛰️")
      or (
        stripped.startswith("🛡")
        and ("<b>Stop</b>" in stripped or stripped.startswith("🛡 <b>Stop</b>"))
      )
      or line.startswith("🎯 ·")
      or line.startswith("🏁 ·")
    )

  for line in text.splitlines():
    if _is_append(line):
      append_lines.append(line)
    elif append_lines:
      # Keep stray lines after TP/close with the append block.
      append_lines.append(line)
    else:
      fill_lines.append(line)
  return "\n".join(fill_lines).rstrip(), append_lines


POSITION_ACTIVATED_STATUS_LINE = "✅ <b>POSITION ACTIVATED</b>"


def _format_order_filled_manage_body(event: dict) -> str:
  cleaned = _clean_message(
    event.get("message", ""),
    symbol=_event_symbol(event),
  ) or "order filled"
  return "\n".join([
    "🤖 <b>ApexVoid Algo</b>",
    "✅ <b>ORDER FILLED</b>",
    f"• {escape(cleaned)}",
  ])


def _format_tp_compact_line(event: dict, message: str) -> str | None:
  """One row per TP; stack a new row only when another TP hits.

  Exact owner format::
    🎯 TP1 · 💰 Fill: 4029.98 · ✅ Achieved: +41.0 pips
  """
  symbol = _event_symbol(event)
  cleaned = _clean_message(message, symbol=symbol)
  if contradictory_archived_tp(event, cleaned):
    return None
  match = _TP_BOOKED_RE.match(cleaned)
  target = match.group("target").upper() if match else None
  if target is None:
    tp_match = _TP_RE.match(cleaned)
    if tp_match is not None:
      target = tp_match.group(1).upper()
      if target == "FULL TP":
        target = "FULL"
  if not target:
    # Final target hit closes as position_closed (no separate tp_booked).
    highest = _HIGHEST_TP_ARCHIVED_RE.search(cleaned)
    if highest is not None:
      target = highest.group("target").upper()
  if not target:
    return None
  parts = [f"🎯 {escape(target)}"]
  price = event.get("price")
  try:
    if price is not None:
      parts.append(
        f"💰 Fill: {escape(_format_event_price(str(price), symbol=symbol))}"
      )
  except Exception:
    pass
  archived_pips = _event_float(event, "target_pips", "leg_realized_pips")
  if archived_pips is None and match is None:
    tp_match = _TP_RE.match(cleaned)
    if tp_match is not None:
      try:
        archived_pips = float(tp_match.group(2))
      except (TypeError, ValueError):
        archived_pips = None
  if archived_pips is None:
    archived_pips = _archived_pips_from_close_message(cleaned)
  if archived_pips is not None:
    parts.append(
      f"✅ Achieved: {format_signed_pips(abs(archived_pips))} pips"
    )
  return " · ".join(parts)


_ARCHIVED_PIPS_SUFFIX_RE = re.compile(
  r"(?i)highest\s+TP\s+archived\s+TP\d+"
  r"(?:\s*·\s*(?P<pips>[+-]?\d+(?:\.\d+)?)\s*pips?)?"
)


def _archived_pips_from_close_message(cleaned: str) -> float | None:
  match = _ARCHIVED_PIPS_SUFFIX_RE.search(cleaned)
  if match is None or match.group("pips") is None:
    return None
  try:
    return float(match.group("pips"))
  except (TypeError, ValueError):
    return None


def _manage_has_tp_target(text: str, target: str) -> bool:
  """True when manage body already has a compact line for this TP level."""
  needle = target.upper()
  for line in text.splitlines():
    stripped = line.lstrip("• ").strip()
    if not (
      stripped.startswith("🎯 ·")
      or stripped.startswith("🎯 ")
      or line.startswith("🎯 ·")
    ):
      continue
    # Matches "• 🎯 TP1", "🎯 · TP1 · …", and legacy "🎯 · <b>TP1</b> · …".
    if (
      f"🎯 {needle}" in stripped
      or f"🎯 · {needle}" in stripped
      or f"· {needle} ·" in stripped
      or f"· <b>{needle}</b> ·" in stripped
      or stripped.rstrip().endswith(needle)
      or stripped.rstrip().endswith(f"<b>{needle}</b>")
    ):
      return True
  return False


def _format_position_closed_compact_line(event: dict, message: str) -> str:
  """Close trailer for the manage reply — SL status stays on one line."""
  cleaned = _MONEY_RE.sub("", message).strip(" ·") if message else ""
  highest = _HIGHEST_TP_ARCHIVED_RE.search(cleaned) if cleaned else None
  no_tp = _NO_TP_ARCHIVED_RE.search(cleaned) if cleaned else None
  reason = str(event.get("reason_code") or "")
  if contradictory_archived_tp(event, cleaned):
    highest = None
    no_tp = True
  if highest is not None:
    # Highest level is already on a 🎯 line; only add exit price here.
    at = _PLAN_CLOSED_AT_RE.search(cleaned)
    if at is not None:
      return f"🏁 POSITION CLOSED · @ {escape(at.group('price'))}"
    return "🏁 POSITION CLOSED"
  if (
    _use_stop_close_format(event, reason=reason, cleaned=cleaned)
    or terminal_loss_at_protective_stop(event)
  ):
    parts = ["🏁 POSITION CLOSED", *_sl_close_result_parts(
      event, cleaned, html=False,
    )]
    return " · ".join(parts)
  reason_label = _CLOSE_REASON_LABELS.get(reason)
  if close_at_breakeven(event, cleaned):
    reason_label = "🛡 Closed at BE stop"
  elif reason_label and close_at_protective_stop(event):
    reason_label = None
  parts = ["🏁 POSITION CLOSED"]
  if reason_label:
    parts.append(reason_label)
  pips = _resolve_close_pips(event, cleaned, allow_stop_fallback=False)
  if pips is not None and pips < 0:
    parts.append(f"❌ Losing: {format_signed_pips(pips)} pips")
  elif pips is not None and pips > 0:
    parts.append(f"✅ Winning: {format_signed_pips(pips)} pips")
  elif pips is not None:
    parts.append("➖ Result: 0 pips (BE)")
  return " · ".join(parts)


def _format_be_trail_head_status(event: dict, message: str) -> tuple[str, str, float | None]:
  """Short BE/trail manage-reply line + optional stop price."""
  symbol = _event_symbol(event)
  cleaned = _clean_message(message, symbol=symbol)
  match = _SL_MOVED_RE.match(cleaned)
  price_text = None
  details = ""
  if match is not None:
    price_text = match.group("price")
    details = str(match.group("details") or "").strip()
  price_val = _event_float(event, "price", "stop_price", "new_stop")
  if price_text is None and price_val is not None:
    price_text = _format_event_price(str(price_val), symbol=symbol)
  if price_val is None and price_text is not None:
    try:
      price_val = float(str(price_text).replace(",", ""))
    except (TypeError, ValueError):
      price_val = None
  if price_val is None:
    stop_match = _STOP_RE.match(cleaned)
    if stop_match is not None:
      try:
        price_val = float(str(stop_match.group(1)).replace(",", ""))
        price_text = _format_event_price(str(price_val), symbol=symbol)
      except (TypeError, ValueError):
        pass
  upper = cleaned.upper()
  if "TO BE" in upper or "BREAK" in upper or upper.startswith("GROUP SL MOVED TO BE"):
    kind = "BE"
    icon = "🔐"
    state = "sl_moved"
  elif "TRAIL" in upper or "trail" in details.lower():
    kind = "Trail"
    icon = "🛰️"
    state = "sl_moved"
  else:
    kind = "Stop"
    icon = "🛡"
    state = "sl_moved"
  if price_text:
    status = f"{icon} <b>{escape(kind)}</b> · {escape(str(price_text))}"
  else:
    status = f"{icon} <b>{escape(kind)}</b>"
  return status, state, price_val


def _is_manage_be_trail_line(line: str) -> bool:
  stripped = line.lstrip("• ").strip()
  return (
    stripped.startswith("🔐")
    or stripped.startswith("🛰️")
    or (
      stripped.startswith("🛡")
      and ("<b>Stop</b>" in stripped or stripped.startswith("🛡 <b>Stop</b>"))
    )
  )


def _upsert_manage_be_trail_line(text: str, trail_line: str) -> str:
  """Replace prior BE/Trail/Stop status line, else append."""
  out: list[str] = []
  replaced = False
  for line in text.splitlines():
    if _is_manage_be_trail_line(line):
      if not replaced:
        out.append(trail_line)
        replaced = True
      continue
    out.append(line)
  if not replaced:
    out.append(trail_line)
  return "\n".join(out)


async def _mark_forming_card_position_activated(client, match_id: str) -> None:
  """Move SETUP FORMING head from publish/queued → POSITION ACTIVATED."""
  try:
    await edit_forming_card_status(
      client,
      match_id,
      POSITION_ACTIVATED_STATUS_LINE,
      state="order_filled",
      reason_code="order_filled",
      edit_fn=edit_scanner_message_text,
    )
  except Exception:
    log.exception(
      "forming card POSITION ACTIVATED edit failed setup_id=%s",
      match_id,
    )
  # A fill can never precede publication, so the plan's stop is guaranteed
  # to exist by now even if an earlier publish-time patch raced the card's
  # own creation and missed - re-apply it here so an activated position
  # never shows a blank/placeholder Stop line.
  try:
    stop_price = await published_plan_stop_price(client, match_id)
    if stop_price is not None:
      card = await load_forming_card(client, match_id)
      symbol = (
        parse_forming_card_symbol(str(card["text"]))
        if card and card.get("text")
        else None
      ) or str(event.get("symbol") or "XAU")
      await edit_forming_card_stop(
        client,
        match_id,
        stop_price,
        digits=card_price_digits(symbol),
        edit_fn=edit_scanner_message_text,
      )
  except Exception:
    log.exception(
      "forming card POSITION ACTIVATED stop refresh failed setup_id=%s",
      match_id,
    )


async def _deliver_compact_order_filled(
  client,
  event: dict,
  *,
  match_id: str,
  chat_id: int,
  send,
) -> bool:
  # Reply keeps ORDER FILLED; root SETUP FORMING card becomes POSITION ACTIVATED.
  body = _format_order_filled_manage_body(event)
  manage_id, manage_text = await _load_manage_message(client, match_id)
  new_text = body
  if manage_text:
    _, tp_lines = _split_manage_fill_and_tps(manage_text)
    if tp_lines:
      new_text = f"{body}\n" + "\n".join(tp_lines)
  # Create a missing root first (publish can lag the fill), then rewrite
  # WAITING FILL → POSITION ACTIVATED before the manage reply.
  await _ensure_root_card_for_manage_reply(
    client, event, match_id=match_id, chat_id=chat_id,
  )
  await _mark_forming_card_position_activated(client, match_id)
  await _replace_manage_reply(
    client,
    event,
    match_id=match_id,
    chat_id=chat_id,
    send=send,
    text=new_text,
    old_message_id=manage_id,
    remember=True,
  )
  return True


async def _deliver_compact_tp_booked(
  client,
  event: dict,
  *,
  match_id: str,
  chat_id: int,
  send,
) -> bool | None:
  message = str(event.get("message") or "")
  line = _format_tp_compact_line(event, message)
  if line is None:
    return None
  manage_id, manage_text = await _load_manage_message(client, match_id)
  if manage_text and line in manage_text:
    return True
  if manage_text:
    new_text = f"{manage_text.rstrip()}\n{line}"
  else:
    new_text = "\n".join([
      "🤖 <b>ApexVoid Algo</b>",
      "• ✅ <b>ORDER FILLED</b>",
      "",
      line,
    ])
  await _replace_manage_reply(
    client,
    event,
    match_id=match_id,
    chat_id=chat_id,
    send=send,
    text=new_text,
    old_message_id=manage_id,
  )
  return True


async def _deliver_compact_position_closed(
  client,
  event: dict,
  *,
  match_id: str,
  chat_id: int,
  send,
) -> bool:
  """Replace manage reply under the forming card with close (and missing TP).

  Final target hits emit position_closed without a separate tp_booked, so
  this path also appends the archived TP compact line when missing.
  The prior manage notification is deleted and a fresh reply is posted.
  The root card is also resolved here: a fill can race the card create, so
  close must not leave IN ZONE · WAITING FILL on a dead trade.
  """
  message = str(event.get("message") or "")
  close_line = _format_position_closed_compact_line(event, message)
  tp_line = _format_tp_compact_line(event, message)

  def _compose(base: str) -> str:
    text = base.rstrip()
    if tp_line:
      highest = _HIGHEST_TP_ARCHIVED_RE.search(message)
      target = highest.group("target").upper() if highest else None
      if target and not _manage_has_tp_target(text, target):
        text = f"{text}\n{tp_line}"
    if "POSITION CLOSED" not in text:
      text = f"{text}\n{close_line}"
    return text

  manage_id, manage_text = await _load_manage_message(client, match_id)
  already_closed = bool(manage_text and "POSITION CLOSED" in manage_text)
  if manage_text:
    new_text = _compose(manage_text)
  else:
    new_text = _compose("\n".join([
      "🤖 <b>ApexVoid Algo</b>",
      "✅ <b>ORDER FILLED</b>",
      "",
    ]))
  # Fill may have raced card create; never leave WAITING FILL after close.
  await kill_setup_card(
    client,
    match_id,
    reason_code=str(event.get("reason_code") or "position_closed"),
    delete_fn=delete_scanner_message,
    edit_fn=edit_scanner_message_text,
  )
  if already_closed:
    return True
  await _replace_manage_reply(
    client,
    event,
    match_id=match_id,
    chat_id=chat_id,
    send=send,
    text=new_text,
    old_message_id=manage_id,
  )
  return True


async def _deliver_compact_be_trail(
  client,
  event: dict,
  *,
  match_id: str,
  chat_id: int,
  send,
) -> bool:
  """Replace manage reply with BE/Trail; leave Trade-area Stop on the root card."""
  message = str(event.get("message") or "")
  status_line, _state, _price_val = _format_be_trail_head_status(event, message)

  manage_id, manage_text = await _load_manage_message(client, match_id)
  if manage_text:
    new_text = _upsert_manage_be_trail_line(manage_text, status_line)
    await _replace_manage_reply(
      client,
      event,
      match_id=match_id,
      chat_id=chat_id,
      send=send,
      text=new_text,
      old_message_id=manage_id,
      remember=True,
    )
  else:
    body = "\n".join([
      "🤖 <b>ApexVoid Algo</b>",
      status_line,
    ])
    await _post_manage_reply(
      client,
      event,
      match_id=match_id,
      chat_id=chat_id,
      send=send,
      text=body,
      remember=True,
      require_reply_target=True,
    )
  return True


async def _deliver_compact_manage(
  client,
  event: dict,
  *,
  chat_id: int,
  send,
) -> bool | None:
  """Owner-chat compact path for fill / TP / BE-trail lifecycle events.

  Returns True/False when handled; None to fall through to generic delivery.
  """
  event_type = str(event.get("type") or "")
  match_id = _event_match_id(event)
  if not match_id:
    return None
  if event_type in {"plan_expired", "plan_cancelled", "plan_rejected"}:
    await kill_setup_card(
      client,
      match_id,
      reason_code=str(event.get("reason_code") or event_type),
      delete_fn=delete_scanner_message,
      edit_fn=edit_scanner_message_text,
    )
    await client.srem(FORMING_ACTIVE_INDEX_KEY, match_id)
    return True
  if event_type == "order_filled":
    return await _deliver_compact_order_filled(
      client, event, match_id=match_id, chat_id=chat_id, send=send,
    )
  if event_type in {"tp_booked", "take_profit"}:
    return await _deliver_compact_tp_booked(
      client, event, match_id=match_id, chat_id=chat_id, send=send,
    )
  if event_type == "position_closed":
    return await _deliver_compact_position_closed(
      client, event, match_id=match_id, chat_id=chat_id, send=send,
    )
  if event_type in {"sl_moved", "stop_moved"}:
    return await _deliver_compact_be_trail(
      client, event, match_id=match_id, chat_id=chat_id, send=send,
    )
  return None


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

  TradePlan V8 events carry ``match_id`` = setup_id and ``candidate_id`` /
  ``group_id`` = ``v8:{setup_id}``. Prefer the real setup id; when only a
  plan id is present, strip the ``v8:`` prefix so we still find the root
  card instead of falling back to a standalone message.
  """
  for key in ("match_id", "setup_id"):
    value = str(event.get(key) or "").strip()
    if value:
      return value[3:] if value.startswith("v8:") else value
  for key in ("candidate_id", "plan_id", "group_id", "correlation_id"):
    value = str(event.get(key) or "").strip()
    if value.startswith("v8:") and len(value) > 3:
      return value[3:]
  return str(event.get("candidate_id") or "").strip()


async def _lookup_forming_reply_message_id(
  client,
  match_id: str,
) -> int | None:
  # Prefer the live forming card address over telegram_root — root can go
  # stale if the card was re-posted while the root key lagged behind.
  card = await load_forming_card(client, match_id)
  if card is not None:
    try:
      message_id = int(card["message_id"])
    except (KeyError, TypeError, ValueError):
      message_id = 0
    if message_id > 0:
      return message_id
  root_id = await load_telegram_root_message_id(client, match_id)
  if root_id is not None and root_id > 0:
    return root_id
  return None


async def _forming_reply_message_id(
  client,
  event: dict,
) -> tuple[int | None, str]:
  match_id = _event_match_id(event)
  if not match_id:
    return None, "event has no match id"
  message_id = await _lookup_forming_reply_message_id(client, match_id)
  if message_id is not None:
    return message_id, ""
  # Live 2026-08-11: a fast fill/close can reach here before its own
  # setup's root card has finished sending (Telegram flood-control can
  # stretch a single edit/send to 17s+) - poll instead of committing to a
  # standalone message the instant the first lookup comes up empty.
  deadline = time.monotonic() + _FORMING_REPLY_WAIT_SECONDS
  while time.monotonic() < deadline:
    if await is_setup_terminal(client, match_id):
      break
    await asyncio.sleep(_FORMING_REPLY_POLL_SECONDS)
    message_id = await _lookup_forming_reply_message_id(client, match_id)
    if message_id is not None:
      return message_id, ""
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
    runtime_config.delivery.lifecycle.thread_lifecycle
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


async def _session_bootstrap_pending(client) -> bool:
  return not bool(await client.get(_SESSION_NOTIFY_SENT_KEY))


async def _stash_session_bootstrap_line(
  client,
  event_type: str,
  message: str,
  *,
  append: bool = False,
) -> dict[str, str]:
  raw = await client.get(_SESSION_BOOTSTRAP_BATCH_KEY)
  batch: dict[str, str] = {}
  if raw:
    try:
      loaded = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
      if isinstance(loaded, dict):
        batch = {str(k): str(v) for k, v in loaded.items() if v}
    except (TypeError, ValueError, json.JSONDecodeError):
      batch = {}
  if message:
    if append and batch.get(event_type):
      batch[event_type] = f"{batch[event_type]}\n{message}"
    else:
      batch[event_type] = message
  await client.set(
    _SESSION_BOOTSTRAP_BATCH_KEY,
    json.dumps(batch, separators=(",", ":"), sort_keys=True),
    ex=120,
  )
  return batch


def _format_session_bootstrap_message(batch: dict[str, str]) -> str:
  lines = ["🤖 <b>ApexVoid Algo</b>", "✅ <b>Engine ready</b>"]
  ready = str(batch.get("ready") or "").strip()
  balance_match = _READY_BALANCE_RE.search(ready)
  if balance_match:
    try:
      balance_val = float(balance_match.group(1).replace(",", ""))
    except ValueError:
      balance_val = None
    if balance_val is not None:
      lines.extend(["", f"💰 Balance <b>${balance_val:,.2f}</b>"])
    ready = _READY_BALANCE_RE.sub("", ready)
    ready = re.sub(r"\s{2,}", " ", ready).strip(" :")
  if ready:
    lines.extend(["", escape(ready)])
  warning = batch.get("warning")
  if warning:
    lines.extend(["", f"⚠️ {escape(warning)}"])
  config = batch.get("config_health")
  if config:
    lines.extend(["", f"🩺 {escape(config)}"])
  capability = batch.get("account_capability")
  if capability:
    lines.extend(["", f"🧾 {escape(capability)}"])
  return "\n".join(lines)


def _should_flush_session_bootstrap(
  event_type: str,
  batch: dict[str, str],
) -> bool:
  if not batch.get("ready"):
    return False
  if event_type == "account_capability":
    return True
  # Reconnect path publishes ready only (no config_health prelude).
  if event_type == "ready" and "config_health" not in batch:
    return True
  return False


async def _flush_session_bootstrap_notify(
  client,
  *,
  chat_id: int,
  send=None,
) -> bool:
  raw = await client.get(_SESSION_BOOTSTRAP_BATCH_KEY)
  batch: dict[str, str] = {}
  if raw:
    try:
      loaded = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
      if isinstance(loaded, dict):
        batch = {str(k): str(v) for k, v in loaded.items() if v}
    except (TypeError, ValueError, json.JSONDecodeError):
      batch = {}
  if not batch.get("ready"):
    return False
  claimed = await client.set(
    _SESSION_NOTIFY_SENT_KEY,
    "1",
    nx=True,
    ex=_SESSION_NOTIFY_COOLDOWN_SECONDS,
  )
  if not claimed:
    await client.delete(_SESSION_BOOTSTRAP_BATCH_KEY)
    return False
  text = _format_session_bootstrap_message(batch)
  await client.delete(_SESSION_BOOTSTRAP_BATCH_KEY)
  send_fn = send or send_scanner_with_retry
  await send_fn(text, chat_id=chat_id)
  return True


async def _deliver_auto_trade_event(
  client,
  event: dict,
  *,
  profile: DeliveryProfile,
  chat_id: int,
  send=None,
) -> bool:
  event_type = str(event.get("type") or "")
  if profile == "internal" and event_type == "warning":
    message = _clean_message(
      event.get("message", ""),
      symbol=_event_symbol(event),
    )
    if await _session_bootstrap_pending(client):
      await _stash_session_bootstrap_line(
        client, event_type, message, append=True,
      )
      return False
    # Same 6h window as Engine ready: live-grant noise is session-bootstrap
    # only — don't leave a lone ⚠️ DM when the ready batch was suppressed.
    if _LIVE_GRANT_WARNING_RE.search(message):
      return False
  if profile == "internal" and event_type in _SESSION_NOTIFY_COOLDOWN_TYPES:
    message = _clean_message(
      event.get("message", ""),
      symbol=_event_symbol(event),
    )
    batch = await _stash_session_bootstrap_line(client, event_type, message)
    if not _should_flush_session_bootstrap(event_type, batch):
      return False
    return await _flush_session_bootstrap_notify(
      client, chat_id=chat_id, send=send,
    )
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
  if event_type in _TRADE_PLAN_NOTIFY_DEDUP_TYPES:
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
      # prefixes; fall back to type+message digest for durability across
      # restarts. Use sha256 (not Python's salted hash()) so dedup survives
      # process restarts and matches across workers.
      digest = hashlib.sha256(event_key.encode("utf-8")).hexdigest()[:16]
      claimed = await client.set(
        f"auto_trade:v8_notify:{plan_id}:{event_type}:{digest}",
        "1",
        nx=True,
        ex=_TRADE_MESSAGE_TTL,
      )
      if not claimed:
        return False
  send = send or send_scanner_with_retry
  if profile == "internal":
    compact = await _deliver_compact_manage(
      client,
      event,
      chat_id=chat_id,
      send=send,
    )
    if compact is not None:
      return compact
  text = render_auto_trade_event(event, profile=profile)
  if not text:
    return False
  position_id = event.get("position_id")
  match_id = _event_match_id(event)
  root_edited = False
  reply_to, reason = await _resolve_reply_message_id(client, event, profile)
  if reply_to is None and (
    event_type in _FORMING_REPLY_TYPES
    or event_type in _FORMING_REPLY_PREFERRED_TYPES
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
  # The executor already maintains this active-position set.  Counting it is
  # O(1); SCAN over the full Redis keyspace became seconds of avoidable work
  # once multi-symbol bars/telemetry grew into tens of thousands of keys.
  scard = getattr(client, "scard", None)
  if callable(scard):
    position_count = int(await scard("auto_trade:positions") or 0)
  else:
    position_count = len(await client.smembers("auto_trade:positions"))
  try:
    from app.persistence.store import count_pending_algo_signals
    manual_pending = await count_pending_algo_signals()
  except Exception:
    log.exception("algo_status manual algo pending count failed")
    manual_pending = 0
  primary_symbol = next(
    (
      item.strip().upper()
      for item in runtime_config.contract.instrument.symbols.split(",")
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
  spot = await _json_key(client, f"price:{primary_symbol}:spot")
  cooldown_line = None
  for direction in ("BUY", "SELL"):
    cooldown_key = f"auto_trade:zone:cooldown:{primary_symbol}:{direction}"
    cooldown = await _json_key(client, cooldown_key)
    if (
      cooldown.get("reason") == "stop_loss"
      and cooldown.get("confidence") == "confirmed"
    ):
      ttl = await client.ttl(cooldown_key)
      if isinstance(ttl, int) and ttl > 0:
        cooldown_line = f"🧊 Cooldown <b>{direction}</b> · {max(1, ttl // 60)}m left"
        break
  mode = (
    "disabled"
    if not runtime_config.runtime.auto_trade.enabled
    else "dry run"
    if runtime_config.runtime.auto_trade.dry_run
    else "demo trading"
  )
  state = "paused" if paused else "running"
  profile = str(
    (config_health or {}).get("profile")
    or runtime_config.runtime.profile
    or "conservative"
  )
  selected_text = "none"
  execution_state = "-"
  why = ""
  regime = ""
  if runtime_config.runtime.auto_trade.enabled:
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
  state_icon = "⏸️" if paused else "▶️"
  lines = [
    "🤖 <b>Algo bot</b>",
    f"{state_icon} {escape(mode)} · <b>{state}</b> · {escape(profile)}",
    f"📊 Open <b>{position_count}</b> · groups <b>{group_count}</b> · "
    f"today <b>{daily}</b> · algo <b>{manual_pending}</b>",
  ]
  if isinstance(executor, dict):
    try:
      equity_val = float(executor.get("account_equity"))
    except (TypeError, ValueError):
      equity_val = None
    try:
      balance_val = float(executor.get("account_balance"))
    except (TypeError, ValueError):
      balance_val = None
    # 0 before the engine's first broker account snapshot arrives - omit
    # rather than show a misleading $0.00.
    if equity_val or balance_val:
      parts = []
      if equity_val is not None:
        parts.append(f"Equity <b>${equity_val:,.2f}</b>")
      if balance_val is not None and balance_val != equity_val:
        parts.append(f"Balance <b>${balance_val:,.2f}</b>")
      if parts:
        lines.append(f"💰 {' · '.join(parts)}")
  try:
    bid = float(spot["bid"])
    ask = float(spot["ask"])
  except (KeyError, TypeError, ValueError):
    pass
  else:
    from app.core.symbols import pip_for
    pip = pip_for(primary_symbol) or 0.0
    spread = f"{(ask - bid) / pip:.1f}p" if pip else f"{ask - bid:.2f}"
    lines.append(
      f"💹 {escape(primary_symbol)} "
      f"<b>{_format_event_price(str(bid), symbol=primary_symbol, grouped=True)}</b>/"
      f"<b>{_format_event_price(str(ask), symbol=primary_symbol, grouped=True)}</b> · "
      f"spread {spread}"
    )
  today_line = await _today_algo_scorecard_line()
  if today_line:
    lines.append(today_line)
  lines.append(f"🎯 {escape(selected_text)} · {escape(execution_state)}")
  config_icon = "🩺" if config_state == "ok" else "⚠️"
  ready_icon = "🟢" if ready else "🔴"
  health_bits = [
    f"{config_icon} Config <b>{escape(config_state)}</b>",
    f"{ready_icon} ready <b>{ready}</b>",
  ]
  if regime:
    health_bits.insert(0, f"🧭 Regime <b>{escape(regime)}</b>")
  lines.append(" · ".join(health_bits))
  if cooldown_line:
    lines.append(cooldown_line)
  engine_updated_at = executor.get("updated_at") if isinstance(executor, dict) else None
  if engine_updated_at:
    try:
      age = int(datetime.now(timezone.utc).timestamp()) - int(engine_updated_at)
    except (TypeError, ValueError):
      age = 0
    # Reconcile refreshes this snapshot roughly every ~15s while the
    # engine's alive - anything well past that means the process is
    # stuck, crashed, or lost its broker connection with no other signal
    # of that anywhere in /algo_status today.
    if age > 60:
      lines.append(f"🔌 Engine stale · last seen {max(1, age // 60)}m ago")
  open_book = await _open_trade_plan_book_lines(client)
  lines.extend(open_book)
  route_line = _compact_route_line(last_route)
  if route_line:
    lines.append(f"🧵 Route: {escape(route_line)}")
  if why:
    lines.append(f"❓ Why: {escape(why)}")
  if runtime_config.runtime.auto_trade.enabled:
    # Supervisor marks programming bugs as fatal (Redis blips stay retrying).
    try:
      fatals = await redis_state.list_fatal_components()
    except Exception:
      log.exception("algo_status fatal component scan failed")
      fatals = []
    for item in fatals[:3]:
      name = escape(str(item.get("component") or "component"))
      err = escape(str(item.get("error") or "fatal")[:80])
      lines.append(f"🔥 <b>{name}</b> fatal · {err}")
    if len(fatals) > 3:
      lines.append(f"➕ +{len(fatals) - 3} more fatal components")
  text = "\n".join(lines)
  # Soft budget for the owner DM; hard clip stays in the handler at 4000.
  if len(text) > 1500:
    text = text[:1490] + "\n…"
  return text


async def _today_algo_scorecard_line() -> str | None:
  """Today's algo_auto W/L/net from the same records as /trade_stats."""
  try:
    from app.persistence.store import get_pips_records
    from app.signals.parsing import _stats_range
    from app.signals.reports import _signed_p, build_stats

    start_ts, end_ts = _stats_range("today")
    records = await get_pips_records(start_ts, end_ts)
    stats = build_stats(
      records,
      [],
      runtime_config.delivery.presentation.seq_reset_tz,
      runtime_config.market_data.sessions.asia_start,
      runtime_config.market_data.sessions.london_start,
      runtime_config.market_data.sessions.ny_start,
    )
  except Exception:
    log.exception("algo_status today scorecard failed")
    return None
  algo = (stats.get("by_stream") or {}).get("algo_auto") or {}
  trades = int(algo.get("trades") or 0)
  if trades <= 0:
    return None
  wins = int(algo.get("wins") or 0)
  losses = int(algo.get("losses") or 0)
  net = algo.get("total_pips") or 0
  net_icon = "🟢" if float(net or 0) >= 0 else "🔴"
  return (
    f"{net_icon} Today · <b>{wins}W/{losses}L</b> · "
    f"net <b>{escape(_signed_p(net))}</b>"
  )


async def _open_trade_plan_book_lines(client, *, limit: int = 3) -> list[str]:
  """Compact open TradePlan lines: direction · setup · stage."""
  try:
    from app.autotrade.active_exposure import load_active_exposures
    from app.autotrade.trade_plan_stream import read_trade_plan
  except Exception:
    log.exception("algo_status open-book import failed")
    return []
  try:
    exposures = await load_active_exposures(client)
  except Exception:
    log.exception("algo_status open-book load failed")
    return []
  plans = [item for item in exposures if item.source == "v8_plan" and item.plan_id]
  if not plans:
    return []
  lines: list[str] = []
  for item in plans[: max(1, limit)]:
    setup = "plan"
    stage_label = "open"
    try:
      plan = await read_trade_plan(client, str(item.plan_id))
      if plan is not None and plan.analysis.strategy:
        setup = str(plan.analysis.strategy)
    except Exception:
      log.exception(
        "algo_status open-book plan read failed plan_id=%s",
        item.plan_id,
      )
    try:
      raw = await client.get(f"execution:plan_runtime:{item.plan_id}")
      if raw:
        payload = json.loads(
          raw.decode() if isinstance(raw, bytes) else str(raw)
        )
        if isinstance(payload, dict):
          group_stage = str(
            payload.get("group_stage") or payload.get("GroupStage") or ""
          )
          stage = str(payload.get("stage") or payload.get("Stage") or "")
          stage_label = (group_stage or stage or "open").replace("_", " ")
          be = payload.get("break_even_applied")
          if be is None:
            be = payload.get("BreakEvenApplied")
          if be is True:
            stage_label = f"{stage_label} · BE".strip(" ·")
    except Exception:
      log.exception(
        "algo_status open-book runtime read failed plan_id=%s",
        item.plan_id,
      )
    direction_icon = "🟢" if str(item.direction).upper() == "BUY" else "🔴"
    lines.append(
      f"{direction_icon} Open: <b>{escape(item.direction)}</b> · "
      f"{escape(setup)} · {escape(stage_label)}"
    )
  extra = len(plans) - limit
  if extra > 0:
    lines.append(f"➕ +{extra} more")
  return lines


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
      try:
        await _deliver_auto_trade_event(
          client,
          event,
          profile="internal",
          chat_id=chat_id,
          send=send,
        )
      except TelegramRetryAfter as exc:
        # Do not stall the stream for 60s: one flood-limited card must not
        # block later fills. Retry once after a short wait, then advance.
        wait = min(5.0, max(0.0, float(exc.retry_after)))
        log.warning(
          "Auto-trade event %s flood-limited retry_after=%s; "
          "waiting %.1fs then retrying once",
          entry_id,
          exc.retry_after,
          wait,
        )
        if wait > 0:
          await asyncio.sleep(wait)
        try:
          await _deliver_auto_trade_event(
            client,
            event,
            profile="internal",
            chat_id=chat_id,
            send=send,
          )
        except TelegramRetryAfter:
          log.warning(
            "Auto-trade event %s still flood-limited; advancing cursor",
            entry_id,
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


# RetryAfter on a single event is handled inside _process_owner_entries so
# the stream cursor keeps moving. This loop-level handler is only for a
# flood that escapes that path (e.g. regime-alert send).
_OWNER_LOOP_FLOOD_BACKOFF_SECONDS = 5


async def _auto_trade_owner_events_loop(*, chat_id: int) -> None:
  client = redis_state.get_client()
  cursor = await client.get(_CURSOR_KEY)
  if not cursor:
    latest = await client.xrevrange(
      runtime_config.contract.streams.events,
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
      batches = await client.xread(
        {runtime_config.contract.streams.events: cursor},
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
    except TelegramRetryAfter as exc:
      cursor = str(await client.get(_CURSOR_KEY) or cursor)
      log.warning(
        "Auto-trade owner delivery flood-limited (retry_after=%ds) at "
        "cursor %s; backing off %ds before retrying the same entry",
        exc.retry_after,
        cursor,
        _OWNER_LOOP_FLOOD_BACKOFF_SECONDS,
      )
      await asyncio.sleep(_OWNER_LOOP_FLOOD_BACKOFF_SECONDS)
    except Exception:
      cursor = str(await client.get(_CURSOR_KEY) or cursor)
      log.exception(
        "Auto-trade owner delivery failed at cursor %s; retrying",
        cursor,
      )
      await asyncio.sleep(5)


async def auto_trade_events_loop() -> None:
  if (
    not runtime_config.runtime.auto_trade.enabled
    or not runtime_config.delivery.telegram.telegram_owner_id
  ):
    return
  await _auto_trade_owner_events_loop(
    chat_id=runtime_config.delivery.telegram.telegram_owner_id
  )
