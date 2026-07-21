"""Owner controls and Telegram delivery for cTrader auto-trade events."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from html import escape

from app import redis_state
from app.config import settings
from app.tg_core import send_scanner_with_retry

log = logging.getLogger(__name__)

_CURSOR_KEY = "auto_trade:telegram_event_cursor"
_PAUSED_KEY = "auto_trade:paused"
_STATS_KEY = "auto_trade:stats"
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


def render_auto_trade_event(event: dict) -> str | None:
  event_type = str(event.get("type", ""))
  if event_type not in _NOTIFY_TYPES:
    return None
  titles = {
    "ready": "🤖 <b>Auto Trader ready</b>",
    "dry_run": "🧪 <b>Auto Trader dry run</b>",
    "opened": "🟢 <b>Auto trade opened</b>",
    "add": "➕ <b>Auto trade scale-in</b>",
    "zone_planned": "📐 <b>Auto zone fill planned</b>",
    "zone_expired": "⌛ <b>Auto zone leg expired</b>",
    "take_profit": "💰 <b>Auto trade partial TP</b>",
    "stop_moved": "🛡 <b>Auto trade stop moved</b>",
    "position_closed": "🛑 <b>Auto position closed</b>",
    "group_result": "📊 <b>Auto trade group result</b>",
    "warning": "⚠️ <b>Auto Trader warning</b>",
    "error": "⚠️ <b>Auto Trader error</b>",
  }
  lines = [titles[event_type], escape(str(event.get("message", "")))]
  position_id = event.get("position_id")
  if position_id is not None:
    lines.append(f"Position: <code>{int(position_id)}</code>")
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
        rail = payload.get("rail")
        if isinstance(rail, dict):
          low = float(rail["low"])
          high = float(rail["high"])
          role = str(rail.get("role") or "rail")
          zone_text = f" · {role} {low:,.2f}–{high:,.2f}"
      except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    gate_line = (
      "\nGate: <b>independent M1 range scalp · raw M5/M15 rails</b>"
      f"\nLast check: <b>{escape(gate_state)}</b>{escape(zone_text)}"
    )
  return (
    "🤖 <b>Auto Trader</b>\n"
    f"Mode: <b>{escape(mode)}</b> · State: <b>{state}</b>\n"
    f"Open positions: <b>{position_count}</b>\n"
    f"Trades today: <b>{daily}/{settings.auto_trade_max_daily_trades}</b>"
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
