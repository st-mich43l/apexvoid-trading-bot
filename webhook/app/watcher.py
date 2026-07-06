import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from app.config import settings
from app.broadcast import fanout_update
from app.dedup import get_open_signals
from app.price import get_xau_price
from app.symbols import pip_for

log = logging.getLogger(__name__)

_alerts: dict[int, set[str]] = {}


def clear_sl_alert(row_id: int) -> None:
  """Allow an updated stop-loss level to produce a fresh alert."""
  _alerts.get(row_id, set()).discard("SL")


def mark_tp_alert(row_id: int, tp_number: int) -> None:
  """Prevent a manual TP notification from being repeated by the watcher."""
  _alerts.setdefault(row_id, set()).add(f"TP{tp_number}")


def _market_open() -> bool:
  """Return whether XAU is outside its approximate weekend closure."""
  now = datetime.now(timezone.utc)
  weekday = now.weekday()
  if weekday == 4 and now.hour >= 21:
    return False
  if weekday == 5:
    return False
  if weekday == 6 and now.hour < 22:
    return False
  return True


def _price_text(price: float) -> str:
  return f"{price:.2f}".rstrip("0").rstrip(".")


def _render_level_alert(
  tier: str,
  kind: str,
  key: str,
  seq: int,
  display_price: str,
  pips: int,
) -> str:
  if tier == "public":
    if kind == "TP":
      return (
        f"🎯 {key} (+{pips} pips)"
        if settings.public_show_pips
        else "🎯 TP hit"
      )
    return (
      f"🛡 SL (-{pips} pips)"
      if settings.public_show_pips
      else "🛡 SL hit"
    )
  if kind == "SL":
    return (
      f"⚠️ <b>NEAR SL</b> | #{seq}\n"
      f"📉 Price: <b>{display_price}</b>\n"
      f"❌ Loss: <b>-{pips} pips</b>\n\n"
      f"<i>Reply to confirm:</i> "
      f"<code>close #{seq} -{pips}</code>"
    )
  return (
    f"🎯 <b>TP HIT</b> | #{seq}\n"
    f"💰 Level: <b>{key}</b>\n"
    f"📈 Price: <b>{display_price}</b>\n"
    f"✅ Profit: <b>+{pips} pips</b>\n\n"
    f"<i>Reply to confirm:</i> "
    f"<code>close #{seq} +{pips}</code>"
  )


async def _evaluate(sig: dict, price: float) -> None:
  """Send advisory level alerts without changing signal lifecycle state."""
  alerted = _alerts.setdefault(sig["id"], set())
  seq = sig.get("daily_seq") or sig["id"]
  entry_end = sig["entry_end"] if sig["entry_end"] is not None else sig["entry"]
  entry_mid = (sig["entry"] + entry_end) / 2
  pips = round(
    abs(price - entry_mid) / pip_for(sig.get("symbol", "XAU"))
  )
  display_price = _price_text(price)

  sl_hit = (
    price <= sig["sl"] if sig["action"] == "BUY"
    else price >= sig["sl"]
  )
  if sl_hit:
    if "SL" not in alerted:
      await fanout_update(
        sig,
        lambda tier: _render_level_alert(
          tier, "SL", "SL", seq, display_price, pips
        ),
      )
      alerted.add("SL")
    return

  for index, tp in enumerate(sig["tps"]):
    key = f"TP{index + 1}"
    tp_hit = price >= tp if sig["action"] == "BUY" else price <= tp
    if not tp_hit or key in alerted:
      continue
    await fanout_update(
      sig,
      lambda tier: _render_level_alert(
        tier, "TP", key, seq, display_price, pips
      ),
    )
    alerted.add(key)


async def _watcher_tick(session: aiohttp.ClientSession) -> None:
  sigs = [
    sig for sig in await get_open_signals("XAU")
    if sig["fill_state"] == "filled"
  ]
  if not sigs or not _market_open():
    return
  price = await get_xau_price(session)
  if price is None:
    return
  for sig in sigs:
    await _evaluate(sig, price)


async def watcher_loop() -> None:
  """Poll XAU for active signals and send notify-only level hints."""
  if not settings.twelvedata_api_key:
    log.info("Price watcher disabled: TWELVEDATA_API_KEY not set")
    return

  async with aiohttp.ClientSession() as session:
    while True:
      try:
        await _watcher_tick(session)
      except Exception:
        log.exception("watcher tick failed")
      await asyncio.sleep(settings.track_interval)
