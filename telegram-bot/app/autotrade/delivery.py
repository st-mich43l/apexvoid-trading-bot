"""Owner controls and Telegram delivery for cTrader auto-trade events."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from html import escape

from app.persistence import redis_state
from app.core.config import settings
from app.bot.client import send_scanner_with_retry
from app.autotrade.worker import regime_share_24h

log = logging.getLogger(__name__)

_CURSOR_KEY = "auto_trade:telegram_event_cursor"
_PAUSED_KEY = "auto_trade:paused"
_STATS_KEY = "auto_trade:stats"
_REGIME_ALERT_PENDING_PREFIX = "auto_trade:regime_alert_pending:"
_REGIME_ALERT_SENT_TTL = 86400
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


def _clean_message(value: object) -> str:
  return _AUTO_NAME_RE.sub("ApexVoid Algo", str(value or "")).strip()


def _position_line(event: dict) -> str | None:
  position_id = event.get("position_id")
  if position_id is None:
    return None
  return f"🆔 Position: <code>{int(position_id)}</code>"


def _format_opened(event: dict, message: str) -> str | None:
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
  if full_tp is not None:
    target_pips = int(full_tp.group(1))
    try:
      entry_price = float(entry.replace(",", ""))
      target_price = entry_price + (
        target_pips * 0.1 if direction.upper() == "BUY" else -target_pips * 0.1
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
  lines.append(f"📊 Size: <b>{escape(lots)} lot</b>")
  position = _position_line(event)
  if position:
    lines.extend(["", position])
  return "\n".join(lines)


def _format_take_profit(event: dict, message: str) -> str | None:
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
  if full:
    lines.append("🏁 Position closed in full")
  price = event.get("price")
  if price is not None:
    lines.append(f"📍 Exit: <b>{float(price):,.2f}</b>")
  position = _position_line(event)
  if position:
    lines.extend(["", position])
  return "\n".join(lines)


def _format_stop_moved(event: dict, message: str) -> str | None:
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
  position = _position_line(event)
  if position:
    lines.extend(["", position])
  return "\n".join(lines)


def render_auto_trade_event(event: dict) -> str | None:
  event_type = str(event.get("type", ""))
  if event_type not in _NOTIFY_TYPES:
    return None
  message = _clean_message(event.get("message", ""))
  if event_type == "opened":
    rendered = _format_opened(event, message)
    if rendered:
      return rendered
  if event_type == "take_profit":
    rendered = _format_take_profit(event, message)
    if rendered:
      return rendered
  if event_type == "stop_moved":
    rendered = _format_stop_moved(event, message)
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
  if message:
    lines.extend(["", escape(message)])
  position = _position_line(event)
  if position and f"position {event.get('position_id')}" not in message.lower():
    lines.extend(["", position])
  return "\n".join(lines)


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
  gate_line = ""
  if settings.auto_trade_enabled:
    gate_state = "waiting for M1 close"
    zone_text = ""
    raw = await client.get("auto_trade:last_gate")
    if raw:
      try:
        payload = json.loads(raw)
        gate_state = str(payload.get("state") or gate_state)
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
    regime_line = "\nRegime: <b>warming up</b>"
    primary_symbol = next(
      (
        item.strip().upper()
        for item in settings.auto_trade_symbols.split(",")
        if item.strip()
      ),
      "XAU",
    )
    try:
      shares = await regime_share_24h(client, primary_symbol)
    except Exception:
      shares = None
    if shares is not None:
      regime_line = (
        "\nRegime (24h): chop "
        f"<b>{shares.get('chop', 0.0):.0%}</b> · trend "
        f"<b>{shares.get('trend', 0.0):.0%}</b> · breakout "
        f"<b>{shares.get('breakout', 0.0):.0%}</b>"
      )
    gate_line = (
      "\nGate: <b>independent M1 two-edge box scalp</b>"
      f"\nLast check: <b>{escape(gate_state)}</b>{escape(zone_text)}"
      f"{regime_line}"
    )
  return (
    "🤖 <b>ApexVoid Algo</b>\n"
    f"Mode: <b>{escape(mode)}</b> · State: <b>{state}</b>\n"
    f"Open positions: <b>{position_count}</b>\n"
    f"Trades today: <b>{daily}</b> · <b>unlimited</b>"
    f"\nMeasured groups: <b>{group_count}</b> · adds "
    f"<b>{with_adds}</b> · no adds <b>{without_adds}</b>"
    f"{gate_line}"
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


async def auto_trade_events_loop() -> None:
  if not settings.auto_trade_enabled or not settings.telegram_owner_id:
    return
  client = redis_state.get_client()
  cursor = await client.get(_CURSOR_KEY)
  if not cursor:
    latest = await client.xrevrange(
      settings.auto_trade_event_stream,
      count=1,
    )
    cursor = latest[0][0] if latest else "0-0"
    await client.set(_CURSOR_KEY, cursor)
  log.info("Auto-trade event delivery active from Redis cursor %s", cursor)

  while True:
    try:
      await _check_regime_alerts(client)
      batches = await client.xread(
        {settings.auto_trade_event_stream: cursor},
        count=20,
        block=5000,
      )
      for _, entries in batches:
        for entry_id, fields in entries:
          cursor = entry_id
          await client.set(_CURSOR_KEY, cursor)
          try:
            event = json.loads(fields["payload"])
          except (KeyError, TypeError, json.JSONDecodeError) as exc:
            log.warning("Invalid auto-trade event %s: %s", entry_id, exc)
            continue
          await _record_group_result(client, event)
          text = render_auto_trade_event(event)
          if text:
            await send_scanner_with_retry(
              text,
              chat_id=settings.telegram_owner_id,
            )
    except asyncio.CancelledError:
      raise
    except Exception:
      log.exception("Auto-trade event delivery failed; retrying")
      await asyncio.sleep(5)
