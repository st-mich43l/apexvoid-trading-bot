import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from app.config import settings
from app.broadcast import fanout_update
from app.dedup import get_open_signals
from app.pips_format import wing_icons
from app.price import get_xau_bars
from app.symbols import pip_for
from app import redis_state

log = logging.getLogger(__name__)


async def clear_sl_alert(row_id: int) -> None:
  """Allow an updated stop-loss level to produce a fresh alert."""
  await redis_state.clear_sl_flag(row_id)


async def mark_tp_alert(row_id: int, tp_number: int) -> None:
  """Prevent a manual TP notification from being repeated by the watcher."""
  await redis_state.set_tp_progress(row_id, tp_number)


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
        f"🎯 {key} +{pips} pips {wing_icons(pips)}"
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
    )
  return (
    f"🎯 <b>TP HIT</b> | #{seq}\n"
    f"💰 Level: <b>{key}</b>\n"
    f"📈 Price: <b>{display_price}</b>\n"
    f"✅ Profit: <b>+{pips} pips</b> {wing_icons(pips)}\n\n"
  )

async def _evaluate(sig: dict, bar: dict, progress: dict) -> bool:
  """Advisory level alerts for one OHLC bar. Mutates ``progress`` in place.

  Returns ``True`` when the signal has hit its stop and no further bars should
  be evaluated for it. Does not touch trade accounting — notify-only.
  """
  # A stopped-out trade is done: stop all further alerts until the SL flag is
  # cleared (e.g. the stop is manually moved via clear_sl_alert).
  if progress["sl"]:
    return True

  seq = sig.get("daily_seq") or sig["id"]
  entry_end = sig["entry_end"] if sig["entry_end"] is not None else sig["entry"]
  entry_mid = (sig["entry"] + entry_end) / 2
  pip = pip_for(sig.get("symbol", "XAU"))
  is_buy = sig["action"] == "BUY"

  sl_hit = bar["low"] <= sig["sl"] if is_buy else bar["high"] >= sig["sl"]
  if sl_hit:
    touch = bar["low"] if is_buy else bar["high"]
    pips = round(abs(touch - entry_mid) / pip)
    display_price = _price_text(touch)
    await fanout_update(
      sig,
      lambda tier: _render_level_alert(
        tier, "SL", "SL", seq, display_price, pips
      ),
    )
    progress["sl"] = True
    await redis_state.set_sl_flag(sig["id"])
    return True

  # Sequential TPs: only alert TP(n) once every earlier TP has been alerted.
  tps = sig["tps"]
  while progress["tp"] < len(tps):
    idx = progress["tp"]
    tp = tps[idx]
    tp_hit = bar["high"] >= tp if is_buy else bar["low"] <= tp
    if not tp_hit:
      break
    touch = bar["high"] if is_buy else bar["low"]
    pips = round(abs(touch - entry_mid) / pip)
    key = f"TP{idx + 1}"
    # Lazy import mirrors the watcher<->trade_ops pattern; avoids an import cycle.
    from app.telegram import build_tp_close_kb
    await fanout_update(
      sig,
      lambda tier, k=key, dp=_price_text(touch), p=pips: _render_level_alert(
        tier, "TP", k, seq, dp, p
      ),
      markup_fn=lambda tier, s=sig["id"], t=idx + 1, p=pips: (
        build_tp_close_kb(s, t, p) if tier == "vip" else None
      ),
    )
    progress["tp"] = idx + 1
    await redis_state.set_tp_progress(sig["id"], idx + 1)
  return False


async def _watcher_tick(session: aiohttp.ClientSession) -> None:
  filled = [
    sig for sig in await get_open_signals("XAU")
    if sig["fill_state"] == "filled"
  ]
  if not filled or not _market_open():
    return
  # Load progress once and drop signals already stopped out: they have nothing
  # left to alert, so keeping them would poll Tiingo forever for no reason
  # (e.g. an SL-hit signal that was never manually closed).
  active = []
  for sig in filled:
    progress = await redis_state.get_progress(sig["id"])
    if not progress["sl"]:
      active.append((sig, progress))
  if not active:
    return  # no actionable signal -> skip the Tiingo request entirely
  bars = await get_xau_bars(session)
  if not bars:
    return
  cursor = await redis_state.get_cursor("XAU")
  new_bars = [b for b in bars if cursor is None or b["date"] > cursor]
  if not new_bars:
    return
  if cursor is None:
    # Cold start: anchor the cursor without replaying the day's history.
    await redis_state.set_cursor("XAU", new_bars[-1]["date"])
    return
  for sig, progress in active:
    for bar in new_bars:
      if await _evaluate(sig, bar, progress):
        break
  await redis_state.set_cursor("XAU", new_bars[-1]["date"])


async def watcher_loop() -> None:
  """Poll XAU for active signals and send notify-only level hints."""
  if not settings.tiingo_api_key:
    log.info("Price watcher disabled: TIINGO_API_KEY not set")
    return

  async with aiohttp.ClientSession() as session:
    while True:
      try:
        await _watcher_tick(session)
      except Exception:
        log.exception("watcher tick failed")
      await asyncio.sleep(settings.track_interval)
