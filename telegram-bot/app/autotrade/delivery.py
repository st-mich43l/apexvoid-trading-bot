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
from app.persistence import redis_state
from app.persistence.store import record_auto_trade_event
from app.core.config import settings
from app.bot.client import send_scanner_with_retry
from app.autotrade.worker import regime_share_24h

log = logging.getLogger(__name__)

_CURSOR_KEY = "auto_trade:telegram_event_cursor"
_PAUSED_KEY = "auto_trade:paused"
_STATS_KEY = "auto_trade:stats"
_REGIME_ALERT_PENDING_PREFIX = "auto_trade:regime_alert_pending:"
_REGIME_ALERT_SENT_TTL = 86400
_TRADE_MESSAGE_TTL = 7 * 24 * 3600
_FULL_TP_RESULT_TTL = 24 * 3600
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
}

_AUTO_NAME_RE = re.compile(r"(?i)\bauto[\s-]*(?:trade|trader)\b")
_OPENED_RE = re.compile(
  r"(?i)^(BUY|SELL)\s+([\d.,]+)\s+lots?\s+filled\s+([\d.,]+),\s*"
  r"SL\s+([\d.,]+)\s*·\s*([\d.,]+)p\s+structure\s*·\s*(.+)$"
)
_TP_RE = re.compile(
  r"(?i)^(FULL TP|TP\d+)\s+\+(\d+)\s+pips\s+closed\s+volume\s+(\d+)$"
)
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
  direction, lots, entry, stop, stop_pips, details = match.groups()
  side_icon = "🟢" if direction.upper() == "BUY" else "🔴"
  full_tp = re.search(r"(?i)full TP\s+(\d+)p", details)
  range_box = re.search(r"(?i)range\s+([\d.,]+)-([\d.,]+)", details)
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
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
  if profile == "internal":
    lines.append(f"📊 Size: <b>{escape(lots)} lot</b>")
  attribution = _attribution_line(event)
  if attribution:
    lines.append(attribution)
  if profile == "public":
    _append_public_footer(lines, footer)
  return "\n".join(lines)


def _format_take_profit(
  event: dict,
  message: str,
  profile: DeliveryProfile,
) -> str | None:
  match = _TP_RE.match(message)
  if match is None:
    return None
  label, pips, _ = match.groups()
  full = label.upper() == "FULL TP"
  lines = [
    "🤖 <b>ApexVoid Algo</b>",
    "🎯 <b>FULL TAKE PROFIT</b>" if full else f"🎯 <b>{label.upper()} HIT</b>",
    "",
    f"✅ Profit: <b>+{pips} pips</b>",
  ]
  if profile == "public":
    try:
      stop_pips = float(event.get("stop_pips"))
      profit_pips = float(event.get("target_pips") or pips)
    except (TypeError, ValueError):
      stop_pips = 0
      profit_pips = 0
    if stop_pips > 0:
      lines.append(f"📐 Result: <b>+{profit_pips / stop_pips:.2f}R</b>")
  if full:
    lines.append("🏁 Position closed in full")
    result_pips = event.get("group_realized_pips")
    result_pnl = event.get("group_realized_pnl")
    if result_pips is not None or result_pnl is not None:
      lines.extend(["", "📊 <b>Trade result</b>"])
      result_parts = []
      try:
        value = float(result_pips)
        result_parts.append(f"{value:+,.1f} pips")
      except (TypeError, ValueError):
        pass
      try:
        value = float(result_pnl)
        sign = "+" if value >= 0 else "-"
        result_parts.append(f"{sign}${abs(value):,.2f}")
      except (TypeError, ValueError):
        pass
      if result_parts:
        lines.append(f"💰 <b>{' · '.join(result_parts)}</b>")
  price = event.get("price")
  if price is not None:
    lines.append(f"📍 Exit: <b>{float(price):,.2f}</b>")
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
  if event_type not in _NOTIFY_TYPES:
    return None
  message = _clean_message(event.get("message", ""))
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
  labels = {
    "ready": "✅ <b>Engine ready</b>",
    "dry_run": "🧪 <b>Simulation</b>",
    "opened": "🟢 <b>Position opened</b>",
    "add": "➕ <b>Scale-in filled</b>",
    "zone_planned": "📐 <b>Entry plan ready</b>",
    "zone_expired": "⌛ <b>Entry plan expired</b>",
    "take_profit": "🎯 <b>Take profit hit</b>",
    "stop_moved": "🛡 <b>Risk protected</b>",
    "position_closed": "🏁 <b>Position closed</b>",
    "group_result": "📊 <b>Trade result</b>",
    "warning": "⚠️ <b>Warning</b>",
    "error": "⚠️ <b>Execution issue</b>",
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
  if event.get("setup") == "Manual Algo":
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
    primary_symbol = next(
      (
        item.strip().upper()
        for item in settings.auto_trade_symbols.split(",")
        if item.strip()
      ),
      "XAU",
    )
    match_build_line = ""
    match_build_raw = await client.get(
      f"auto_trade:last_match_build:{primary_symbol}"
    )
    if match_build_raw:
      try:
        match_build = json.loads(match_build_raw)
        stage = str(match_build.get("stage") or "")
        if stage == "match_build_rejected":
          reason = str(match_build.get("reason") or "unknown")
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
    strategy_lines = (
      f"\nSelected strategy: <b>{escape(selected_text)}</b>"
      f"\nSource: <b>{escape(selection_source)}</b>"
      f"\nScanner M5: <b>{escape(scanner_state)}</b>"
      "\nMarket Map strategy: "
      f"<b>{escape(market_map_state.replace('_', ' '))}</b>"
      "\nPrivate strategies: "
      f"Range Box <b>{escape(box_state.replace('_', ' '))}</b> · "
      f"Trend <b>{escape(trend_state.replace('_', ' '))}</b>"
      f"\nExecution: <b>{escape(execution_state.replace('_', ' '))}</b>"
      f"{escape(zone_text)}"
      f"{reason_line}"
      f"{match_build_line}"
      f"{map_observability}"
      f"{regime_line}"
    )
  return (
    "🤖 <b>ApexVoid Algo</b>\n"
    f"Mode: <b>{escape(mode)}</b> · State: <b>{state}</b>\n"
    f"Open positions: <b>{position_count}</b>\n"
    f"Trades today: <b>{daily}</b> · <b>unlimited</b>"
    f"\nMeasured groups: <b>{group_count}</b> · adds "
    f"<b>{with_adds}</b> · no adds <b>{without_adds}</b>"
    f"{strategy_lines}"
  )


async def _record_group_result(client, event: dict) -> None:
  if event.get("type") != "group_result":
    return
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
