"""Generic catch-all handlers included after command routers."""

import logging
import time
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import Message

from app.broadcast import broadcast_entry
from app.config import settings
from app.dedup import (
  event_in_window,
  get_manual_signal,
  store_manual_signal,
  store_pips,
)
from app.parsing import _PIPS_RE, _is_owner, _parse_manual
from app.pips_format import wing_icons
from app.symbols import tier_for_channel

log = logging.getLogger(__name__)
router = Router(name="fallback")


def _event_guard_timing(ts_utc: int, now: int) -> str:
  delta = ts_utc - now
  if delta < 0:
    return f"started {max(1, abs(delta) // 60)}m ago"
  hours, remainder = divmod(delta, 3600)
  minutes = remainder // 60
  return f"in {hours}h {minutes}m"


@router.message(F.chat.type == "private", F.text)
async def handle_private_signal(msg: Message) -> None:
  """Parse manual signal DM and post to channel."""
  if not _is_owner(msg):
    return
  sig = _parse_manual(msg.text or "")
  if not sig:
    await msg.answer(
      "Format:\n\n"
      "<code>gold sell entry zone (4100-4105)\nsl 4110\ntp 95/90/80</code>\n\n"
      "TP: absolute prices or last 2 digits. Any count.\n\n"
      "Commands: <code>/help</code>"
    )
    return
  now = int(time.time())
  event = await event_in_window(
    now,
    int(settings.event_guard_hours * 3600),
  )
  if event and settings.news_guard_block:
    await msg.answer(
      f"⚠️ Signal not posted: {escape(event['title'])} "
      f"{_event_guard_timing(event['ts_utc'], now)} — expect volatility"
    )
    return
  rec = await store_manual_signal(
    ts=now,
    action=sig['action'],
    entry=sig['entry'],
    entry_end=sig['entry_end'],
    sl=sig['sl'],
    tps=sig['tps'],
    setup_type=sig['setup_type'],
    confluence=sig['confluence'],
    symbol="XAU",
    visibility=sig["visibility"],
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
  await msg.answer(f"✅ Sent to channel (#{rec['daily_seq']})")
  log.info(
    "Manual signal #%d (daily #%d): %s XAUUSD @ %s-%s",
    rec["id"], rec["daily_seq"], sig['action'], sig['entry'], sig['entry_end'],
  )


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
  if not settings.auto_book_bare_pips:
    return
  await _handle_pips(msg, msg.caption or "", has_photo=True)


@router.channel_post(F.text)
async def handle_profit_text(msg: Message) -> None:
  if not settings.auto_book_bare_pips:
    return
  await _handle_pips(msg, msg.text or "", has_photo=False)
