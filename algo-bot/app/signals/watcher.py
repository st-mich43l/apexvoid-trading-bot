import asyncio
import logging
import math
from datetime import datetime, timezone

import aiohttp

from app.core.config import settings
from app.autotrade.delivery import tp_message_key
from app.bot.client import send_scanner_with_retry
from app.signals.broadcast import fanout_update
from app.persistence.store import get_open_signals
from app.bot.keyboards import build_close_kb, build_tp_close_kb
from app.signals.pips_format import pips_between, sl_result_pips, wing_icons
from app.signals.price import get_xau_bars
from app.analysis.ohlc_source import RedisOHLCSource
from app.persistence.redis_state import clear_sl_alert, mark_tp_alert
from app.persistence import redis_state

log = logging.getLogger(__name__)


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
  return f"{price:,.2f}".rstrip("0").rstrip(".")


def _atr_by_date(bars: list[dict], length: int = 14) -> dict[str, float]:
  """Return rolling true-range averages keyed by bar timestamp."""
  result = {}
  ranges = []
  previous_close = None
  for bar in bars:
    high = float(bar["high"])
    low = float(bar["low"])
    true_range = high - low
    if previous_close is not None:
      true_range = max(
        true_range,
        abs(high - previous_close),
        abs(low - previous_close),
      )
    ranges.append(true_range)
    window = ranges[-length:]
    result[bar["date"]] = sum(window) / len(window)
    previous_close = float(bar["close"])
  return result


def _overshoot_context(
  fill_price: float,
  extreme_price: float | None,
  atr: float | None,
) -> str:
  if extreme_price is None:
    return ""
  threshold = 0.0
  if atr is not None and math.isfinite(atr) and atr > 0:
    threshold = 0.1 * atr
  if abs(extreme_price - fill_price) <= threshold:
    return ""
  return f" · ran to <b>{_price_text(extreme_price)}</b>"

def _bar_epoch(bar_date: str) -> float:
  """Parse a Tiingo bar's ISO date (UTC) into epoch seconds.

  Bars are 1-minute and timestamped at the start of the minute. Returns +inf
  on an unparseable date so a bad row is never mistaken for pre-fill history.
  """
  try:
    text = bar_date.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()
  except ValueError:
    return float("inf")

def _render_level_alert(
  tier: str,
  kind: str,
  key: str,
  seq: int,
  fill_price: float,
  pips: int,
  extreme_price: float | None = None,
  atr: float | None = None,
) -> str:
  if tier == "public":
    if kind == "TP":
      return (
        f"🎯 {key} +{pips} pips {wing_icons(pips)}"
        if settings.public_show_pips
        else "🎯 TP hit"
      )
    if kind == "RUNNER":
      return (
        f"🚀 runner +{pips} pips {wing_icons(pips)}"
        if settings.public_show_pips
        else "🚀 runner"
      )
    if not settings.public_show_pips:
      return "🛡 SL hit"
    if pips < 0:
      return f"🛡 SL ({pips} pips)"
    if pips > 0:
      return f"🛡 SL (+{pips} pips)"
    return "🛡 BE (0 pips)"
  if kind == "SL":
    context = _overshoot_context(fill_price, extreme_price, atr)
    if pips < 0:
      result = f"❌ Loss: <b>{pips} pips</b>"
    elif pips > 0:
      result = f"✅ Profit: <b>+{pips} pips</b> {wing_icons(pips)}"
    else:
      result = "➖ Result: <b>0 pips (BE)</b>"
    return (
      f"⚠️ <b>NEAR SL</b> | #{seq}\n"
      f"📉 Fill: <b>{_price_text(fill_price)}</b> (SL){context}\n"
      f"{result}\n\n"
    )
  if kind == "RUNNER":
    return (
      f"🚀 <b>TP RUNNER</b> | #{seq}\n"
      f"📈 Price: <b>{_price_text(fill_price)}</b>\n"
      f"✅ Profit: <b>+{pips} pips</b> {wing_icons(pips)}\n\n"
    )
  context = _overshoot_context(fill_price, extreme_price, atr)
  return (
    f"🎯 <b>TP HIT</b> | #{seq}\n"
    f"📈 Fill: <b>{_price_text(fill_price)}</b> ({key}){context}\n"
    f"✅ Profit: <b>+{pips} pips</b> {wing_icons(pips)}\n\n"
  )


def _sl_fill_price(sig: dict, bar: dict, is_buy: bool) -> float:
  level = float(sig["sl"])
  opened = float(bar["open"])
  if (is_buy and opened < level) or (not is_buy and opened > level):
    return opened
  return level


def _last_tp_floor_pips(sig: dict) -> int:
  tps = sig.get("tps") or []
  if not tps:
    return 0
  return pips_between(sig, float(tps[-1]))


def _tp_hit(touch: float, tp: float, is_buy: bool) -> bool:
  if is_buy:
    return touch >= tp
  if float(tp).is_integer():
    return touch < tp + 1.0
  return touch <= tp


async def _maybe_alert_runner(
  sig: dict,
  bar: dict,
  progress: dict,
  seq: int,
  is_buy: bool,
) -> None:
  """After final TP, keep alerting each new favorable extreme until close."""
  tps = sig.get("tps") or []
  if not tps or progress["tp"] < len(tps):
    return

  touch = bar["high"] if is_buy else bar["low"]
  pips = pips_between(sig, touch)
  previous = max(progress.get("runner_pips", 0), _last_tp_floor_pips(sig))
  if pips <= previous:
    return

  if sig.get("algo_armed"):
    position_id = sig.get("broker_position_id")
    if position_id is None:
      return
    reply_to = await redis_state.get_client().get(
      tp_message_key("internal", int(position_id))
    )
    if not reply_to:
      return
    await send_scanner_with_retry(
      _render_level_alert("vip", "RUNNER", "RUNNER", seq, touch, pips),
      reply_to=int(reply_to),
      chat_id=settings.telegram_owner_id,
      reply_markup=build_tp_close_kb(sig["id"], len(tps), pips),
    )
  else:
    await fanout_update(
      sig,
      lambda tier, price=touch, p=pips: _render_level_alert(
        tier, "RUNNER", "RUNNER", seq, price, p
      ),
      markup_fn=lambda tier, s=sig["id"], t=len(tps), p=pips: (
        build_tp_close_kb(s, t, p) if tier == "vip" else None
      ),
    )
  progress["runner_pips"] = pips
  await redis_state.set_runner_pips(sig["id"], pips)


async def _evaluate(
  sig: dict,
  bar: dict,
  progress: dict,
  atr: float | None = None,
) -> bool:
  """Advisory level alerts for one OHLC bar. Mutates ``progress`` in place.

  Returns ``True`` when the signal has hit its stop and no further bars should
  be evaluated for it. Accounting remains owner-confirmed through the alert's
  pre-filled close button.
  """
  # A stopped-out trade is done: stop all further alerts until the SL flag is
  # cleared (e.g. the stop is manually moved via clear_sl_alert).
  if progress["sl"]:
    return True

  seq = sig.get("daily_seq") or sig["id"]
  is_buy = sig["action"] == "BUY"

  sl_hit = bar["low"] <= sig["sl"] if is_buy else bar["high"] >= sig["sl"]
  if sl_hit:
    extreme = bar["low"] if is_buy else bar["high"]
    fill = _sl_fill_price(sig, bar, is_buy)
    pips = sl_result_pips(sig, fill)
    await fanout_update(
      sig,
      lambda tier: _render_level_alert(
        tier, "SL", "SL", seq, fill, pips, extreme, atr
      ),
      markup_fn=lambda tier, s=sig["id"], t=progress["tp"], p=pips: (
        build_close_kb(s, t, p) if tier == "vip" else None
      ),
    )
    progress["sl"] = True
    await redis_state.set_sl_flag(sig["id"])
    return True

  # Sequential TPs: only alert TP(n) once every earlier TP has been alerted.
  tps = sig["tps"]
  if sig.get("algo_armed"):
    await _maybe_alert_runner(sig, bar, progress, seq, is_buy)
    return False
  while progress["tp"] < len(tps):
    idx = progress["tp"]
    tp = tps[idx]
    touch = bar["high"] if is_buy else bar["low"]
    tp_hit = _tp_hit(touch, tp, is_buy)
    if not tp_hit:
      break
    # Watcher alerts account for each configured target at that target level.
    # A candle may open or wick far beyond it, but that overshoot belongs in
    # `ran to`/runner telemetry and must not inflate the booked TP result.
    fill = float(tp)
    pips = pips_between(sig, fill)
    key = f"TP{idx + 1}"
    await fanout_update(
      sig,
      lambda tier, k=key, price=fill, p=pips: _render_level_alert(
        tier, "TP", k, seq, price, p, touch, atr
      ),
      markup_fn=lambda tier, s=sig["id"], t=idx + 1, p=pips: (
        build_tp_close_kb(s, t, p) if tier == "vip" else None
      ),
    )
    progress["tp"] = idx + 1
    progress["runner_pips"] = max(progress.get("runner_pips", 0), pips)
    await redis_state.set_tp_progress(sig["id"], idx + 1)
    await redis_state.set_runner_pips(sig["id"], pips)
  await _maybe_alert_runner(sig, bar, progress, seq, is_buy)
  return False


def _iso_ms(ts) -> str:
  """Format a UTC timestamp exactly like Tiingo's bar ``date`` field.

  The watcher cursor is compared with plain string ``>``, so every source
  must emit the same fixed-precision format or a bar from one source can
  sort incorrectly against a cursor set by the other.
  """
  return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def _ctrader_bars() -> list[dict] | None:
  """Fetch closed M1 XAU bars from ctrader-feed's Redis ZSET.

  Returns the same shape as ``price.get_xau_bars`` (ascending, ``date`` as a
  Tiingo-format ISO string) so callers don't need to know which source
  produced a bar. Returns ``None`` on any error or if the window is empty.
  """
  try:
    df = await RedisOHLCSource().window("XAU", "M1", 300)
  except Exception:
    log.warning("Could not fetch XAU/USD bars from ctrader-feed")
    return None
  if df.empty:
    return None
  return [
    {
      "date": _iso_ms(ts),
      "open": float(row["open"]),
      "high": float(row["high"]),
      "low": float(row["low"]),
      "close": float(row["close"]),
    }
    for ts, row in df.iterrows()
  ]


def _bars_are_fresh(bars: list[dict]) -> bool:
  age = datetime.now(timezone.utc).timestamp() - _bar_epoch(bars[-1]["date"])
  return age <= settings.watcher_ctrader_stale_seconds


async def _load_bars(session: aiohttp.ClientSession) -> list[dict] | None:
  """ctrader-feed is the primary M1 source; Tiingo is a fallback for a gap.

  Falls through to Tiingo even without TIINGO_API_KEY configured -- that
  request just fails and returns None, same as it always has.
  """
  bars = await _ctrader_bars()
  if bars and _bars_are_fresh(bars):
    return bars
  return await get_xau_bars(session)


async def _watcher_tick(session: aiohttp.ClientSession) -> None:
  filled = [
    sig for sig in await get_open_signals("XAU")
    if sig["fill_state"] == "filled"
  ]
  if not filled or not _market_open():
    return
  # Load progress once and drop signals already stopped out: they have nothing
  # left to alert, so keeping them would poll for bars forever for no reason
  # (e.g. an SL-hit signal that was never manually closed).
  active = []
  for sig in filled:
    progress = await redis_state.get_progress(sig["id"])
    if not progress["sl"]:
      active.append((sig, progress))
  if not active:
    return  # no actionable signal -> skip the bar fetch entirely
  bars = await _load_bars(session)
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
  atr_by_date = _atr_by_date(bars)
  for sig, progress in active:
    # Never react to price action from before the trade went live: a stale
    # global cursor (idle between signals) would otherwise replay the whole
    # day's bars against a freshly-filled signal and trip every TP at once.
    fill_ts = sig.get("filled_at") or sig.get("ts") or 0
    for bar in new_bars:
      if _bar_epoch(bar["date"]) < fill_ts:
        continue
      if await _evaluate(sig, bar, progress, atr_by_date.get(bar["date"])):
        break
  await redis_state.set_cursor("XAU", new_bars[-1]["date"])


async def watcher_loop() -> None:
  """Poll XAU for active signals and send notify-only level hints.

  Reads closed M1 bars from ctrader-feed's Redis window; Tiingo is used only
  as a fallback when that feed is missing or stale, so the loop runs even
  without TIINGO_API_KEY configured -- it just has no fallback for a gap.
  """
  if not settings.tiingo_api_key:
    log.info("Price watcher fallback disabled: TIINGO_API_KEY not set")

  async with aiohttp.ClientSession() as session:
    while True:
      try:
        await _watcher_tick(session)
      except Exception:
        log.exception("watcher tick failed")
      await asyncio.sleep(settings.track_interval)
