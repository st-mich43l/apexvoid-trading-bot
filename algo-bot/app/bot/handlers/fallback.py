"""Generic catch-all handlers included after command routers."""

import json
import logging
import time
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import Message

from app.signals.broadcast import broadcast_entry
from app.autotrade.config_health import CONFIG_HEALTH_KEY
from app.core.config import runtime_config
from app.persistence import redis_state
from app.persistence.store import (
  event_in_window,
  get_manual_signal,
  set_execution_intent,
  set_execution_status,
  store_manual_signal,
  store_pips,
)
from app.signals.manual_intent import build_intent, publish_intent
from app.signals.parsing import _PIPS_RE, _is_owner, _parse_manual
from app.signals.pips_format import wing_icons
from app.core.symbols import tier_for_channel

log = logging.getLogger(__name__)
router = Router(name="fallback")


def manual_signal_usage() -> str:
  return (
    "Format:\n\n"
    "<code>/trade xau buy 4078 / algo</code> — single entry\n"
    "<code>/trade xau buy 4078-75 / algo</code> — entry zone\n\n"
    "<code>/trade eurusd buy 1.15007 / algo</code> — fixed-R/R entry\n"
    "<code>/trade gbpjpy sell 216.168 / sl 216.50 / algo</code>\n\n"
    "The legacy forms without <code>/trade</code> still work.\n"
    "TP: absolute prices or XAU last 2 digits. Any count.\n\n"
    "Commands: <code>/help</code>"
  )


def _event_guard_timing(ts_utc: int, now: int) -> str:
  delta = ts_utc - now
  if delta < 0:
    return f"started {max(1, abs(delta) // 60)}m ago"
  hours, remainder = divmod(delta, 3600)
  minutes = remainder // 60
  return f"in {hours}h {minutes}m"


def _manual_signal_confirmation(
  sig: dict,
  daily_seq: int,
  algo_note: str | None = None,
) -> str:
  base = f"✅ Sent to channel (#{daily_seq})"
  setup = sig.get("setup_type")
  if not setup:
    text = (
      f"{base} · ⚠️ no setup tag — add later with: "
      f"<code>tag #{daily_seq} &lt;setup&gt; **</code>"
    )
  else:
    confluence = sig.get("confluence")
    stars = f" {'⭐' * confluence}" if confluence else ""
    text = f"{base} · setup {escape(setup)}{stars}"
  return f"{text} · {algo_note}" if algo_note else text


async def _arm_algo_intent(signal_id: int, signal: dict) -> str:
  """Arm (or explain why not arming) broker-side execution for a signal.

  Never raises — the channel post has already happened by the time this
  runs, so a failure here must read as "algo arming failed" and nothing
  else, never as the whole DM being rejected.
  """
  if not runtime_config.manual_algo.runtime.enabled:
    return "⚠️ Algo suffix ignored — MANUAL_ALGO_ENABLED is off"
  try:
    raw_health = await redis_state.get_client().get(CONFIG_HEALTH_KEY)
    if raw_health:
      health = json.loads(
        raw_health.decode() if isinstance(raw_health, bytes) else str(raw_health)
      )
      if health.get("state") == "fatal":
        fatal = ",".join(str(item) for item in health.get("fatal") or [])
        return (
          "⛔ Algo request blocked — CONFIG_HEALTH=FATAL"
          + (f" ({fatal})" if fatal else "")
        )
    intent = build_intent(signal, revision=0)
    await set_execution_intent(
      signal_id,
      intent_id=intent.intent_id,
      status="requested",
      revision=0,
    )
    await publish_intent(intent)
  except Exception as exc:
    log.exception("Failed to arm algo execution for signal #%d", signal_id)
    await set_execution_status(signal_id, "error", error=str(exc))
    return "⚠️ Algo arm failed — signal posted notify-only"
  return "📨 ALGO REQUEST RECEIVED"


async def submit_manual_signal(msg: Message, text: str) -> bool:
  """Submit one owner-authored signal through the canonical manual flow.

  Both the legacy free-text surface and ``/trade`` call this function so a
  newly-configured symbol cannot drift into a second persistence/broadcast/
  execution path.  ``False`` means only that the text was not a signal.
  """
  sig = _parse_manual(text)
  if not sig:
    return False
  symbol = str(sig.get("symbol") or "XAU").upper()
  try:
    effective = runtime_config.for_instrument(symbol)
  except Exception:
    await msg.answer(f"⛔ {escape(symbol)} is not a configured trade symbol.")
    return True
  if symbol not in runtime_config.live_instruments():
    await msg.answer(
      f"⛔ {escape(symbol)} is configured but not live for manual execution."
    )
    return True
  if not effective.manual.enabled:
    await msg.answer(f"⛔ Manual trading is disabled for {escape(symbol)}.")
    return True
  if sig["execution_mode"] == "algo" and not effective.manual.algo_enabled:
    await msg.answer(f"⛔ Algo execution is disabled for {escape(symbol)}.")
    return True
  now = int(time.time())
  event = await event_in_window(
    now,
    int(runtime_config.market_data.calendar.event_guard_hours * 3600),
  )
  if event and runtime_config.market_data.calendar.news_guard_block:
    await msg.answer(
      f"⚠️ Signal not posted: {escape(event['title'])} "
      f"{_event_guard_timing(event['ts_utc'], now)} — expect volatility"
    )
    return True
  rec = await store_manual_signal(
    ts=now,
    action=sig['action'],
    entry=sig['entry'],
    entry_end=sig['entry_end'],
    sl=sig['sl'],
    tps=sig['tps'],
    setup_type=sig['setup_type'],
    confluence=sig['confluence'],
    symbol=sig.get("symbol", "XAU"),
    visibility=sig["visibility"],
    execution_mode=sig["execution_mode"],
  )
  guard_text = None
  if event:
    guard_text = (
      f"⚠️ {escape(event['title'])} "
      f"{_event_guard_timing(event['ts_utc'], now)} — expect volatility"
    )
  signal = await get_manual_signal(rec["id"])
  signal["guard_text"] = guard_text
  await broadcast_entry(signal)
  algo_note = None
  if sig["execution_mode"] == "algo":
    algo_note = await _arm_algo_intent(rec["id"], signal)
  await msg.answer(
    _manual_signal_confirmation(sig, rec["daily_seq"], algo_note)
  )
  log.info(
    "Manual signal #%d (daily #%d): %s %s @ %s-%s",
    rec["id"],
    rec["daily_seq"],
    sig["action"],
    sig.get("symbol", "XAU"),
    sig["entry"],
    sig["entry_end"],
  )
  return True


@router.message(F.chat.type == "private", F.text)
async def handle_private_signal(msg: Message) -> None:
  """Parse manual signal DM and post to channel."""
  if not _is_owner(msg):
    return
  if not await submit_manual_signal(msg, msg.text or ""):
    await msg.answer(manual_signal_usage())


async def _handle_pips(msg: Message, text: str, has_photo: bool) -> None:
  if getattr(msg, "from_user", None) and msg.from_user.is_bot:
    return
  if tier_for_channel(msg.chat.id) != "vip":
    return
  m = _PIPS_RE.search(text)
  if not m:
    return
  sign, pips = m.group(1), int(m.group(2))
  if sign == "+":
    new_text = f"✅ Booked +{pips} pips profit! {wing_icons(pips)}"
  else:
    new_text = f"🛑 Stopped out -{pips} pips. Managed & moving on 💪"
  try:
    if has_photo:
      await msg.edit_caption(caption=new_text)
    else:
      await msg.edit_text(text=new_text)
    await store_pips(sign, pips, message_id=msg.message_id, chat_id=msg.chat.id)
    log.info("Edited pips message: %s%d pips", sign, pips)
  except TelegramNetworkError as e:
    log.warning("Failed to edit pips message: %s", e)


@router.channel_post(F.photo)
async def handle_profit_screenshot(msg: Message) -> None:
  if not runtime_config.delivery.presentation.auto_book_bare_pips:
    return
  await _handle_pips(msg, msg.caption or "", has_photo=True)


@router.channel_post(F.text)
async def handle_profit_text(msg: Message) -> None:
  if not runtime_config.delivery.presentation.auto_book_bare_pips:
    return
  await _handle_pips(msg, msg.text or "", has_photo=False)
