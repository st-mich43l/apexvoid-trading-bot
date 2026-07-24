"""Owner controls and Telegram delivery for cTrader auto-trade events."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from html import escape
from typing import Literal

from aiogram.exceptions import TelegramBadRequest

from app.autotrade import units
from app.autotrade.volume_pips import (
  format_signed_pips,
  volume_percent,
)
from app.persistence import redis_state
from app.persistence.store import record_auto_trade_event
from app.core.config import settings
from app.bot.client import send_scanner_with_retry
from app.autotrade.worker import regime_share_24h
from app.autotrade.lifecycle import LIFECYCLE_STATES, emit_lifecycle
from app.autotrade.multi_match import (
  deserialize_matches,
  strategy_matches_key,
)
from app.autotrade.range_context import (
  RangeContext,
  range_context_compare_key,
  range_context_key,
  range_context_source_key,
)
from app.autotrade.range_lifecycle import (
  load_breakout_retest_watch,
  status_label_for_retired,
)
from app.autotrade.config_health import (
  CONFIG_HEALTH_KEY,
  CTRADER_MANIFEST_KEY,
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
  r"(?i)^🛡\s+(?:ApexVoid Algo|Auto[\s-]*(?:trade|trader))\s+stop\s+→\s+"
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
  targets = _targets_line(event) if profile == "public" else None
  if targets is not None:
    lines.append(targets)
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
  net_pips = _event_float(event, "group_realized_pips")
  if net_pips is None:
    net_pips = leg_realized
  seq = _trade_seq_prefix(event)
  is_final = full or (remaining_volume is not None and remaining_volume <= 0)

  if is_final:
    lines = [
      "🤖 <b>ApexVoid Algo</b>",
      f"✅ {seq}closed",
      f"Total net: <b>{format_signed_pips(net_pips)} pips</b>",
    ]
  else:
    if (
      initial_volume is None
      or initial_volume <= 0
      or remaining_volume is None
    ):
      return None
    booked_pct = volume_percent(closed_volume, initial_volume)
    lines = [
      "🤖 <b>ApexVoid Algo</b>",
      f"🎯 {seq}{label.upper()} booked {booked_pct:.1f}%",
      f"Leg: <b>{format_signed_pips(leg_realized)} pips</b>",
    ]
    if net_pips is not None:
      lines.append(
        f"Net so far: <b>{format_signed_pips(net_pips)} pips</b>"
      )

  if profile == "public":
    try:
      stop_pips = float(event.get("stop_pips"))
    except (TypeError, ValueError):
      stop_pips = 0.0
    display_pips = net_pips if is_final else leg_realized
    if stop_pips > 0:
      lines.append(
        f"📐 Result: <b>{display_pips / stop_pips:+.2f}R</b>"
      )
  text = "\n".join(lines)
  if _MONEY_RE.search(text):
    text = _MONEY_RE.sub("", text)
  return text


def _format_group_result(event: dict, message: str) -> str:
  net = _event_float(event, "group_realized_pips")
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    "📊 <b>Trade result</b>",
    "",
  ]
  if net is not None:
    lines.append(f"Total net: <b>{format_signed_pips(net)} pips</b>")
  else:
    cleaned = _MONEY_RE.sub("", message).strip(" ·")
    if cleaned:
      lines.append(escape(cleaned))
  return "\n".join(lines)


def _format_position_closed(event: dict, message: str) -> str:
  net = _event_float(event, "group_realized_pips")
  seq = _trade_seq_prefix(event)
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    f"🏁 {seq}<b>POSITION CLOSED</b>",
  ]
  if net is not None:
    lines.extend([
      "",
      f"Total net: <b>{format_signed_pips(net)} pips</b>",
    ])
  elif message:
    cleaned = _MONEY_RE.sub("", message).strip(" ·")
    if cleaned:
      lines.extend(["", escape(cleaned)])
  return "\n".join(lines)


def _format_stop_moved(
  event: dict,
  message: str,
  profile: DeliveryProfile,
) -> str | None:
  match = _STOP_RE.match(message)
  if match is None:
    return None
  stop, label = match.groups()
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    "🛡 <b>Risk protected</b>",
    "",
    f"SL moved to <b>{escape(stop)}</b> · {escape(label)}",
  ]
  return "\n".join(lines)


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


async def _deliver_auto_trade_event(
  client,
  event: dict,
  *,
  profile: DeliveryProfile,
  chat_id: int,
  send=None,
) -> bool:
  event_type = str(event.get("type") or "")
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
  if (
    event_type == "group_result"
    and group_id
    and await client.exists(_full_tp_result_key(profile, group_id))
  ):
    return False
  text = render_auto_trade_event(event, profile=profile)
  if not text:
    return False
  send = send or send_scanner_with_retry
  position_id = event.get("position_id")
  reply_to = None
  if event_type != "opened" and position_id is not None:
    reply_to, reason = await _reply_message_id(client, event, profile)
    if reply_to is None:
      log.info(
        "Auto-trade reply unavailable for position %s (%s): %s; sending standalone",
        position_id,
        profile,
        reason,
      )
  try:
    sent = await send(text, reply_to=reply_to, chat_id=chat_id)
  except TelegramBadRequest as error:
    if reply_to is None or not _is_bad_reply_target(error):
      raise
    log.info(
      "Auto-trade reply rejected for position %s (%s): %s; retrying standalone",
      position_id,
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
  client = redis_state.get_client()
  paused = await client.get(_PAUSED_KEY) == "1"
  date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
  daily = int(await client.get(f"auto_trade:daily:{date_key}:trades") or 0)
  position_count = 0
  async for _ in client.scan_iter(match="auto_trade:position:*"):
    position_count += 1
  raw_stats = await client.hgetall(_STATS_KEY)
  stats = {
    str(key): str(value)
    for key, value in raw_stats.items()
  }
  group_count = int(float(stats.get("groups", "0")))
  with_adds = int(float(stats.get("with_adds", "0")))
  without_adds = int(float(stats.get("without_adds", "0")))
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
  ctrader_manifest = await _json_key(client, CTRADER_MANIFEST_KEY)
  executor = await _json_key(
    client, f"auto_trade:executor_snapshot:{primary_symbol}",
  )
  resolved_range = RangeContext.from_json(
    await client.get(range_context_key(primary_symbol))
  )
  scanner_range = RangeContext.from_json(
    await client.get(range_context_source_key(primary_symbol, "scanner"))
  )
  private_range = RangeContext.from_json(
    await client.get(range_context_source_key(primary_symbol, "private"))
  )
  range_compare = await _json_key(
    client, range_context_compare_key(primary_symbol),
  )
  active_matches = deserialize_matches(
    await client.get(strategy_matches_key(primary_symbol))
  )
  metrics = {
    str(key): int(value)
    for key, value in (
      await client.hgetall(f"auto_trade:metrics:{primary_symbol}")
    ).items()
  }
  thesis_lines: list[str] = []
  try:
    from app.autotrade.reaction_identity import parse_thesis_claim
    async for raw_key in client.scan_iter(
      match="auto_trade:thesis_claim:*", count=20,
    ):
      key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
      claim = parse_thesis_claim(await client.get(key))
      if claim is None:
        continue
      if str(claim.get("symbol") or "").upper() != primary_symbol.upper():
        continue
      tid = str(claim.get("thesis_id") or "")[:10]
      zid = str(claim.get("structural_zone_id") or "")[:10]
      rid = str(claim.get("active_reaction_id") or "")[:10]
      gid = str(claim.get("group_id") or "")[:10]
      state = str(claim.get("state") or "-")
      outside = int(claim.get("outside_bar_count") or 0)
      required = int(
        getattr(settings, "auto_trade_map_reaction_rearm_bars", 3)
      )
      rearm = "yes" if claim.get("rearm_ready") else "no"
      thesis_lines.append(
        f"{tid}/{zid} · {state} · rx={rid} · grp={gid} · "
        f"out={outside}/{required} · rearm={rearm}"
      )
      if len(thesis_lines) >= 3:
        break
  except Exception:
    thesis_lines = []
  reject_summary = await _gate_reject_summary(client, primary_symbol)
  last_lifecycle = await _json_key(
    client, f"auto_trade:last_lifecycle:{primary_symbol}",
  )
  last_guard = await _json_key(
    client, f"auto_trade:last_guard:{primary_symbol}",
  )
  reconcile_raw = await client.hgetall(
    f"auto_trade:zone_reconcile:{primary_symbol}",
  )
  reconcile = {
    str(key): str(value) for key, value in reconcile_raw.items()
  }
  mode = (
    "disabled"
    if not settings.auto_trade_enabled
    else "dry run"
    if settings.auto_trade_dry_run
    else "demo trading"
  )
  state = "paused" if paused else "running"
  strategy_lines = ""
  if settings.auto_trade_enabled:
    execution_state = "waiting for M1 close"
    zone_text = ""
    selected_text = "none"
    selection_source = "none"
    selection_reason = ""
    box_state = "waiting"
    trend_state = "waiting"
    market_map_state = "waiting"
    current_regime = "unknown"
    map_entries_seen: int | None = None
    map_entries_actionable: int | None = None
    map_top: list[dict] = []
    map_filters: dict[str, int] = {}
    map_track_limit: float | None = None
    map_execute_limit: float | None = None
    raw = await client.get("auto_trade:last_gate")
    if raw:
      try:
        payload = json.loads(raw)
        execution_state = str(payload.get("state") or execution_state)
        box_state = str(payload.get("box_state") or box_state)
        trend_state = str(payload.get("trend_state") or trend_state)
        market_map_state = str(
          payload.get("market_map_state") or market_map_state
        )
        current_regime = str(payload.get("regime") or current_regime)
        if payload.get("market_map_entries_seen") is not None:
          map_entries_seen = int(payload["market_map_entries_seen"])
        if payload.get("market_map_entries_actionable") is not None:
          map_entries_actionable = int(
            payload["market_map_entries_actionable"]
          )
        if isinstance(payload.get("market_map_top"), list):
          map_top = [
            item for item in payload["market_map_top"]
            if isinstance(item, dict)
          ][:3]
        if isinstance(payload.get("market_map_filter_counts"), dict):
          map_filters = {
            str(key): int(value)
            for key, value in payload["market_map_filter_counts"].items()
          }
        if payload.get("market_map_track_limit") is not None:
          map_track_limit = float(payload["market_map_track_limit"])
        if payload.get("market_map_execute_limit") is not None:
          map_execute_limit = float(payload["market_map_execute_limit"])
        reasons = payload.get("reasons")
        if isinstance(reasons, list) and reasons:
          selection_reason = str(reasons[-1])
        selected = str(payload.get("selected_strategy") or "")
        selected_tf = str(payload.get("selected_timeframe") or "")
        direction = str(payload.get("direction") or "")
        if selected:
          selected_text = " · ".join(
            item for item in (selected, direction, selected_tf) if item
          )
          source_name = str(payload.get("gate_source") or "")
          selection_source = (
            "scanner detector"
            if source_name == "scanner_strategy_match"
            else "Market Map + M1 reaction"
            if source_name == "market_map_strategy"
            else "private OHLC matcher"
          )
        box = payload.get("box")
        if isinstance(box, dict):
          low = float(box["low"])
          high = float(box["high"])
          tp = payload.get("full_tp_pips")
          zone_text = f" · box {low:,.2f}–{high:,.2f}"
          if tp is not None:
            zone_text += f" · full TP {int(tp)}p"
      except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    scanner_state = "waiting for next M5 scan"
    scanner_raw = await client.get("scanner:last_tick")
    if scanner_raw:
      try:
        scanner_payload = json.loads(scanner_raw)
        detected = scanner_payload.get("detected")
        count = len(detected) if isinstance(detected, list) else 0
        scanner_state = (
          f"{count} setup{'s' if count != 1 else ''} matched"
          if count
          else "no setup matched"
        )
        scalp = scanner_payload.get("scalp")
        if isinstance(scalp, dict) and scalp.get("state"):
          scanner_state += f" · range {str(scalp['state']).replace('_', ' ')}"
      except (TypeError, ValueError, json.JSONDecodeError):
        pass
    regime_line = (
      "\nMarket context: "
      f"<b>{escape(current_regime.replace('_', ' '))}</b>"
      " <i>(telemetry only)</i>"
    )
    match_build_line = ""
    match_build_raw = await client.get(
      f"auto_trade:last_match_build:{primary_symbol}"
    )
    breakout_watch = await load_breakout_retest_watch(client, primary_symbol)
    if (
      breakout_watch
      and str(breakout_watch.get("state") or "") == "waiting"
    ):
      zone_low = breakout_watch.get("zone_low")
      zone_high = breakout_watch.get("zone_high")
      direction = str(breakout_watch.get("direction") or "")
      zone_text = (
        f" at {float(zone_low):,.2f}–{float(zone_high):,.2f}"
        if zone_low is not None and zone_high is not None
        else ""
      )
      match_build_line = (
        "\nStrategyMatch bridge: <b>breakout-retest</b> - "
        f"{escape(direction)} waiting{escape(zone_text)}"
      )
    elif match_build_raw:
      try:
        match_build = json.loads(match_build_raw)
        stage = str(match_build.get("stage") or "")
        if stage == "match_build_rejected":
          reason = str(match_build.get("reason") or "unknown")
          # Stale scanner "no_detection_result" must not hide an active
          # breakout-retest handoff.
          if reason != "no_detection_result" or not breakout_watch:
            measured = match_build.get("measured") or {}
            detail = (
              f" (room {measured['room_pips']} pips)"
              if "room_pips" in measured
              else ""
            )
            match_build_line = (
              "\nStrategyMatch bridge: <b>blocked</b> - "
              f"{escape(reason)}{escape(detail)}"
            )
        elif stage == "match_ready":
          strategy = str(match_build.get("strategy") or "")
          direction = str(match_build.get("direction") or "")
          tp = match_build.get("full_take_profit_pips")
          tp_text = f" · TP {int(tp)}p" if tp is not None else ""
          match_build_line = (
            "\nStrategyMatch bridge: <b>ready</b> - "
            f"{escape(strategy)} {escape(direction)}{escape(tp_text)}"
          )
      except (TypeError, ValueError, json.JSONDecodeError):
        pass
    try:
      shares = await regime_share_24h(client, primary_symbol)
    except Exception:
      shares = None
    if shares is not None:
      regime_line += (
        "\nContext (24h): chop "
        f"<b>{shares.get('chop', 0.0):.0%}</b> · trend "
        f"<b>{shares.get('trend', 0.0):.0%}</b> · breakout "
        f"<b>{shares.get('breakout', 0.0):.0%}</b>"
      )
    reason_line = (
      f"\nWhy no order: {escape(selection_reason)}"
      if selected_text == "none" and selection_reason else
      f"\nWhy: {escape(selection_reason)}" if selection_reason else ""
    )
    map_observability = ""
    if map_entries_seen is not None and map_entries_actionable is not None:
      map_observability = (
        f"\nMap entries: <b>{map_entries_seen}</b> seen · "
        f"<b>{map_entries_actionable}</b> actionable"
      )
      nearest: list[str] = []
      for item in map_top:
        try:
          side = str(item["side"]).upper()
          low = float(item["lo"])
          high = float(item["hi"])
          distance = float(item.get("distance") or 0)
        except (KeyError, TypeError, ValueError):
          continue
        if distance <= 0:
          location = "inside"
        elif (
          map_track_limit is not None
          and map_execute_limit is not None
          and distance <= map_track_limit
        ):
          location = (
            f"{distance:.1f} away · tracked, "
            f"execute within {map_execute_limit:.1f}"
          )
        else:
          location = f"{distance:.1f} away"
        nearest.append(
          f"{side} {low:,.2f}–{high:,.2f} ({location})"
        )
      if nearest:
        map_observability += (
          "\nMap nearest: " + escape(" · ".join(nearest))
        )
      if map_filters:
        map_observability += (
          "\nMap filters: "
          f"side <b>{map_filters.get('side', 0)}</b> · "
          f"actionable <b>{map_filters.get('actionable', 0)}</b> · "
          f"width <b>{map_filters.get('degenerate_width', 0)}</b> · "
          f"distance <b>{map_filters.get('distance', 0)}</b>"
        )
    structural_lines = ""
    structural_metric_names = (
      ("key_level", "key_level_reaction_detected"),
      ("demand", "demand_zone_reaction_detected"),
      ("supply", "supply_zone_reaction_detected"),
      ("session", "session_level_reaction_detected"),
      ("trendline", "trendline_reaction_detected"),
    )
    structural_bits = [
      f"{label} {metrics.get(metric, 0)}"
      for label, metric in structural_metric_names
      if metrics.get(metric, 0)
    ]
    if structural_bits or metrics.get("structural_reaction_match_built"):
      structural_lines = (
        "\nStructural reactions: "
        + escape(" · ".join(structural_bits) if structural_bits else "none")
        + (
          f" · match {metrics.get('structural_reaction_match_built', 0)}"
          f" · published {metrics.get('structural_reaction_candidate_published', 0)}"
          f" · dup {metrics.get('structural_reaction_duplicate_suppressed', 0)}"
        )
      )
      last_structural = next(
        (
          match for match in active_matches
          if match.strategy in {
            "Key Level Reaction",
            "Demand Zone Reaction",
            "Supply Zone Reaction",
            "Session Level Reaction",
            "Trendline Reaction",
          }
        ),
        None,
      )
      if last_structural is not None:
        sid = (last_structural.structural_zone_id or last_structural.zone_id or "")[:10]
        structural_lines += (
          "\nLast structural: "
          f"<b>{escape(last_structural.strategy)}</b> "
          f"{escape(last_structural.direction)} · "
          f"{escape(last_structural.structural_source or '-')} · "
          f"id {escape(sid)} · "
          f"{escape(last_structural.reaction_type or '-')}"
        )
    strategy_lines = (
      f"\nSelected strategy: <b>{escape(selected_text)}</b>"
      f"\nSource: <b>{escape(selection_source)}</b>"
      f"\nScanner M5: <b>{escape(scanner_state)}</b>"
      "\nMarket Map strategy: "
      f"<b>{escape(market_map_state.replace('_', ' '))}</b>"
      "\nPrivate strategies: "
      f"Range Box <b>{escape(box_state.replace('_', ' '))}</b> · "
      f"Trend <b>{escape(trend_state.replace('_', ' '))}</b>"
      f"{structural_lines}"
      f"\nExecution: <b>{escape(execution_state.replace('_', ' '))}</b>"
      f"{escape(zone_text)}"
      f"{reason_line}"
      f"{match_build_line}"
      f"{map_observability}"
      f"{regime_line}"
    )
  account_mode = (
    "demo" if bool((executor or {}).get("demo"))
    else str((ctrader_manifest or {}).get("account_mode") or "unknown")
  )
  hedge_mode = (
    "hedged" if bool((executor or {}).get("hedged"))
    else "non-hedged"
  )
  config_state = str((config_health or {}).get("state") or "unknown")
  config_fatal = (config_health or {}).get("fatal") or []
  executor_dry_run = (
    (ctrader_manifest or {}).get("dry_run")
    if ctrader_manifest is not None else "unknown"
  )
  range_line = "none"
  rail_line = "BUY unknown · SELL unknown"
  barrier_line = "support 0 · resistance 0"
  if resolved_range is not None:
    range_line = (
      f"{resolved_range.lower:,.2f}–{resolved_range.upper:,.2f} · "
      f"{status_label_for_retired(resolved_range) if resolved_range.state == 'retired' else resolved_range.state}"
      f" · {resolved_range.source}"
    )
    buy_state = await _range_side_state(
      client, primary_symbol, resolved_range.range_id, "BUY",
    )
    sell_state = await _range_side_state(
      client, primary_symbol, resolved_range.range_id, "SELL",
    )
    rail_line = f"BUY {buy_state} · SELL {sell_state}"
    barrier_line = (
      f"support {len(resolved_range.supports)} · "
      f"resistance {len(resolved_range.resistances)}"
    )
  scanner_summary = (
    "none"
    if scanner_range is None
    else f"{scanner_range.state} {scanner_range.lower:,.2f}–{scanner_range.upper:,.2f}"
  )
  private_summary = (
    "none"
    if private_range is None
    else f"{private_range.state} {private_range.lower:,.2f}–{private_range.upper:,.2f}"
  )
  comparison = str(
    (range_compare or {}).get("resolution") or "none"
  )
  positions = len((executor or {}).get("position_ids") or [])
  pending = len((executor or {}).get("pending_order_ids") or [])
  groups = len((executor or {}).get("group_ids") or [])
  match_summary = " · ".join(
    f"{item.strategy} {item.direction}"
    for item in active_matches[:6]
  ) or "none"
  metric_summary = " · ".join(
    f"{key}={value}"
    for key, value in sorted(metrics.items())
    if value
  ) or "none"
  lifecycle_summary = (
    "none"
    if not last_lifecycle
    else " · ".join(
      str(item)
      for item in (
        last_lifecycle.get("state"),
        last_lifecycle.get("strategy"),
        last_lifecycle.get("direction"),
        last_lifecycle.get("reason_code"),
      )
      if item
    )
  )
  guard_summary = "none"
  if last_guard:
    measured = last_guard.get("measured") or {}
    room = measured.get("available_room_pips")
    drift = measured.get("effective_pips")
    original_target = measured.get("original_target")
    adjustment = measured.get("adjusted_target")
    barrier_price = measured.get("barrier_price")
    opposing = last_guard.get("opposing_structure") or {}
    opposing_summary = ""
    if isinstance(opposing, dict) and opposing:
      opposing_name = (
        opposing.get("level_kind")
        or opposing.get("side")
        or opposing.get("source_type")
        or "structure"
      )
      opposing_summary = (
        f"opposing {opposing_name} "
        f"{opposing.get('low')}-{opposing.get('high')}"
      )
    detail = " · ".join(
      item for item in (
        f"room {room}p" if room is not None else "",
        f"drift {drift}p" if drift is not None else "",
        (
          f"target {original_target}→{adjustment}"
          if adjustment is not None and original_target is not None
          else f"target {adjustment}"
          if adjustment is not None else ""
        ),
        f"barrier {barrier_price}" if barrier_price is not None else "",
      )
      if item
    )
    guard_summary = " · ".join(
      item for item in (
        str(last_guard.get("strategy") or ""),
        str(last_guard.get("direction") or ""),
        str(last_guard.get("guard") or ""),
        str(last_guard.get("outcome") or ""),
        str(last_guard.get("reason") or ""),
        f"hard block={bool(last_guard.get('hard_block'))}",
        f"source={last_guard.get('source_structure')}"
        if last_guard.get("source_structure") else "",
        opposing_summary,
        detail,
        f"at={last_guard.get('updated_at')}"
        if last_guard.get("updated_at") else "",
      )
      if item
    )
  reconcile_summary = (
    "none"
    if not reconcile else
    " · ".join(
      f"{name}={reconcile.get(name, '0')}"
      for name in (
        "mode",
        "zones_input",
        "zones_shadow_output",
        "zones_trimmed",
        "zones_dropped",
        "candidate_difference_count",
      )
    )
  )
  health_detail = (
    f" · fatal {escape(', '.join(str(item) for item in config_fatal))}"
    if config_fatal else ""
  )
  operations = (
    "\n\n⚙️ <b>Execution contract</b>"
    f"\nProfile: <b>{escape(settings.auto_trade_profile)}</b>"
    f"\nStructural guards: <b>{escape(settings.auto_trade_structural_guard_mode)}</b>"
    f"\nBroker account: <b>{escape(account_mode)} · {escape(hedge_mode)}</b>"
    f"\nExecutor ready: <b>{bool((readiness or {}).get('ready'))}</b>"
    f"\nPython dry-run: <b>{settings.auto_trade_dry_run}</b>"
    f"\nExecutor dry-run: <b>{escape(str(executor_dry_run))}</b>"
    f"\nPython/C# config: <b>{escape(config_state)}</b>{health_detail}"
    f"\nExposure policy: <b>{escape(str((executor or {}).get('exposure_policy') or 'unknown'))}</b>"
    f"\nResolved range: <b>{escape(range_line)}</b>"
    f"\nScanner range: <b>{escape(scanner_summary)}</b>"
    f"\nPrivate range: <b>{escape(private_summary)}</b>"
    f"\nResolution: <b>{escape(comparison)}</b>"
    f"\nBarriers: <b>{escape(barrier_line)}</b>"
    f"\nRails: <b>{escape(rail_line)}</b>"
    f"\nTracked matches: <b>{len(active_matches)}</b> · {escape(match_summary)}"
    f"\nOpen groups: <b>{groups}</b> · positions <b>{positions}</b> · "
    f"pending <b>{pending}</b>"
    f"{chr(10)}Mapped thesis: <b>"
    f"{escape(chr(10).join(thesis_lines) if thesis_lines else 'none')}</b>"
    f"\nMetrics: <code>{escape(metric_summary)}</code>"
    f"\nReject counters: <code>{escape(reject_summary)}</code>"
    f"\nLast execution guard: <b>{escape(guard_summary)}</b>"
    f"\nZone reconcile: <code>{escape(reconcile_summary)}</code>"
    f"\nLast lifecycle: <b>{escape(lifecycle_summary)}</b>"
  )
  return (
    "🤖 <b>ApexVoid Algo</b>\n"
    f"Mode: <b>{escape(mode)}</b> · State: <b>{state}</b>\n"
    f"Open positions: <b>{position_count}</b>\n"
    f"Trades today: <b>{daily}</b> · <b>unlimited</b>"
    f"\nMeasured groups: <b>{group_count}</b> · adds "
    f"<b>{with_adds}</b> · no adds <b>{without_adds}</b>"
    f"{strategy_lines}"
    f"{operations}"
  )


async def _json_key(client, key: str) -> dict:
  raw = await client.get(key)
  if not raw:
    return {}
  try:
    value = json.loads(raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return {}
  return value if isinstance(value, dict) else {}


async def _range_side_state(
  client,
  symbol: str,
  range_id: str,
  direction: str,
) -> str:
  payload = await _json_key(
    client,
    f"auto_trade:range_side:{symbol}:{range_id}:{direction}",
  )
  return str(payload.get("state") or "ARMED").lower()


async def _gate_reject_summary(client, symbol: str) -> str:
  values: list[tuple[str, int]] = []
  pattern = f"auto_trade:gate_reject:{symbol.upper()}:*"
  async for raw_key in client.scan_iter(match=pattern):
    key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
    try:
      count = int(await client.hget(key, "count") or 0)
    except (TypeError, ValueError):
      count = 0
    if count:
      values.append((key.rsplit(":", 1)[-1], count))
  return " · ".join(
    f"{name}={count}"
    for name, count in sorted(values, key=lambda item: (-item[1], item[0]))[:8]
  ) or "none"


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
