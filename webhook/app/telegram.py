import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from html import escape
from typing import Optional
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import Message

from app.config import settings
from app.chart_analysis import analyse_chart_image
from app.dedup import (
  store_pips, get_pips_summary,
  store_manual_signal, set_manual_signal_channel_id,
  get_open_signals, close_manual_signal, cancel_manual_signal,
  cancel_manual_signal_by_channel_id,
)

log = logging.getLogger(__name__)

bot = Bot(
  token=settings.telegram_bot_token,
  default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# Matches: +100 pips / -50 pips / +1500Pips / -30 PIPS
_PIPS_RE = re.compile(r'([+-])\s*(\d+)\s*pips?', re.IGNORECASE)

# Manual signal template (DM to bot):
#   gold sell entry zone (4100-4105)
#   sl 4110
#   tp 95/90/80   (absolute or 2-digit shorthand, any count)
_MANUAL_RE = re.compile(
  r'gold\s+(buy|sell)\s+(?:entry\s+zone\s*)?\(?\s*([\d.]+)\s*[-–—]\s*([\d.]+)\s*\)?\s*[\r\n]+'
  r'\s*sl\s+([\d.]+)\s*[\r\n]+'
  r'\s*tp\s+([\d./]+)',
  re.IGNORECASE,
)

_TP_ICONS = ['💰'] * 5


def _expand_tp(val: float, entry: float, action: str) -> float:
  """Expand a 2-digit shorthand TP (e.g. 35) to a full price using entry's base."""
  if val >= 100:
    return val  # already an absolute price
  base = int(entry / 100) * 100
  price = base + val
  # Adjust by one hundred if the result is on the wrong side of entry
  if action == 'SELL' and price >= entry:
    price -= 100
  elif action == 'BUY' and price <= entry:
    price += 100
  return price


def _parse_manual(text: str) -> Optional[dict]:
  m = _MANUAL_RE.search(text.strip())
  if not m:
    return None
  action, entry_a, entry_b, sl, tp_raw = m.groups()
  action = action.upper()
  entry_low, entry_high = sorted((float(entry_a), float(entry_b)))
  sl = float(sl)
  # Use the edge with the greatest exposure for conservative risk/R values.
  rr_entry = entry_low if action == 'SELL' else entry_high
  tps = [_expand_tp(float(v), rr_entry, action) for v in tp_raw.strip().split('/') if v.strip()]
  if not tps:
    return None
  risk = abs(rr_entry - sl)
  return {
    'action': action,
    'entry': entry_low,
    'entry_end': entry_high,
    'rr_entry': rr_entry,
    'sl': sl,
    'tps': tps,
    'risk': risk,
  }


def _fmt_rr_val(tp: float, entry: float, risk: float) -> str:
  return f"{abs(tp - entry) / risk:.1f}R" if risk > 0 else '-'


def _format_manual_signal(sig: dict) -> str:
  action = sig['action']
  risk = sig['risk']
  lines = [
    f"{_action_icon(action)} <b>{escape(action)} XAUUSD</b>  🔔",
    "",
    f"⚡️ Entry Zone:  <b>{_fmt_price(sig['entry'])} - {_fmt_price(sig['entry_end'])}</b>",
    f"🛡 SL:     <b>{_fmt_price(sig['sl'])}</b>  ·  risk <b>{_fmt_price(risk)}</b>",
  ]
  for i, tp in enumerate(sig['tps']):
    ico = _TP_ICONS[i] if i < len(_TP_ICONS) else '💰'
    lines.append(f"{ico} TP{i+1}:   <b>{_fmt_price(tp)}</b>  ·  <b>{_fmt_rr_val(tp, sig['rr_entry'], risk)}</b>")
  return "\n".join(lines)


_CALC_RE = re.compile(
  r'calculate\s+gold\s+pips\s+(today|yesterday|this\s+week|last\s+week)',
  re.IGNORECASE,
)


def _period_range(period: str) -> tuple[int, int]:
  now = datetime.now(timezone.utc)
  today = now.replace(hour=0, minute=0, second=0, microsecond=0)
  p = period.lower().replace('  ', ' ')
  if p == 'today':
    return int(today.timestamp()), int(now.timestamp())
  if p == 'yesterday':
    return int((today - timedelta(days=1)).timestamp()), int(today.timestamp())
  if p == 'this week':
    monday = today - timedelta(days=now.weekday())
    return int(monday.timestamp()), int(now.timestamp())
  if p == 'last week':
    this_monday = today - timedelta(days=now.weekday())
    last_monday = this_monday - timedelta(weeks=1)
    return int(last_monday.timestamp()), int(this_monday.timestamp())
  return int(today.timestamp()), int(now.timestamp())


def _is_owner(msg: Message) -> bool:
  if not settings.telegram_owner_id:
    return True  # open if not configured — set TELEGRAM_OWNER_ID to lock down
  return msg.from_user is not None and msg.from_user.id == settings.telegram_owner_id


@dp.message(F.chat.type == "private", F.text.regexp(r'(?i)calculate\s+gold\s+pips'))
async def handle_calculate(msg: Message) -> None:
  if not _is_owner(msg):
    return
  m = _CALC_RE.search(msg.text or "")
  if not m:
    await msg.answer("Try: <code>calculate gold pips today</code> or <code>this week</code>")
    return
  period = m.group(1).lower()
  start_ts, end_ts = _period_range(period)
  await msg.answer("🔍 Scanning channel history…")
  try:
    s = await get_pips_summary(start_ts, end_ts)
  except RuntimeError as e:
    await msg.answer(f"⚠️ {e}")
    return

  if s['total'] == 0:
    await msg.answer(f"📊 No pips results found for <b>{period}</b>.")
    return

  net_icon = '💰' if s['net'] >= 0 else '🔻'
  net_sign = '+' if s['net'] >= 0 else ''
  label = period.title()
  lines = [
    f"📊 <b>Gold Pips — {label}</b>",
    "",
    f"✅ Wins:    {s['wins']} trade{'s' if s['wins'] != 1 else ''}  <b>+{s['win_pips']} pips</b>",
    f"❌ Losses:  {s['losses']} trade{'s' if s['losses'] != 1 else ''}  <b>-{s['loss_pips']} pips</b>",
    "──────────────",
    f"{net_icon} Net:    <b>{net_sign}{s['net']} pips</b>",
  ]
  await msg.answer("\n".join(lines))


_ACTIVE_RE = re.compile(r'^active$', re.IGNORECASE)
_CLOSE_RE  = re.compile(r'^close\s+(\d+)\s+([+-]\s*\d+)', re.IGNORECASE)
_CANCEL_RE = re.compile(r'^cancel\s+(\d+)$', re.IGNORECASE)


def _ago(ts: int) -> str:
  secs = int(time.time()) - ts
  if secs < 60:
    return f"{secs}s ago"
  if secs < 3600:
    return f"{secs // 60}m ago"
  if secs < 86400:
    return f"{secs // 3600}h ago"
  return f"{secs // 86400}d ago"


@dp.message(F.chat.type == "private", F.text.regexp(r'(?i)^active$'))
async def handle_active(msg: Message) -> None:
  if not _is_owner(msg):
    return
  signals = await get_open_signals()
  if not signals:
    await msg.answer("📋 No open signals.")
    return
  lines = [f"📋 <b>Open Signals ({len(signals)})</b>", ""]
  for s in signals:
    icon = '📈' if s['action'] == 'BUY' else '📉'
    entry_end = s['entry_end'] if s['entry_end'] is not None else s['entry']
    entry_display = _fmt_price(s['entry'])
    if entry_end != s['entry']:
      entry_display += f" - {_fmt_price(entry_end)}"
    tps_str = '/'.join(_fmt_price(t) for t in s['tps'])
    lines.append(
      f"<b>#{s['id']}</b>  {icon} {s['action']} @ {entry_display}\n"
      f"  SL {_fmt_price(s['sl'])}  · TP {tps_str}\n"
      f"  Opened {_ago(s['ts'])}"
    )
  await msg.answer("\n\n".join(lines))


@dp.message(F.chat.type == "private", F.text.regexp(r'(?i)^close\s+\d+\s+[+-]'))
async def handle_close(msg: Message) -> None:
  if not _is_owner(msg):
    return
  m = _CLOSE_RE.search((msg.text or "").strip())
  if not m:
    await msg.answer("Usage: <code>close &lt;id&gt; +50</code> or <code>close &lt;id&gt; -30</code>")
    return
  row_id = int(m.group(1))
  pips = int(m.group(2).replace(' ', ''))
  row = await close_manual_signal(row_id, pips)
  if row is None:
    await msg.answer(f"⚠️ Signal #{row_id} not found or already closed.")
    return

  if pips >= 0:
    result_text = f"✅ Closed: <b>+{pips} pips</b> 💰"
  else:
    result_text = f"🛑 Closed: <b>{pips} pips</b>"

  channel_msg_id = row.get("channel_message_id")
  if channel_msg_id:
    try:
      await _send_with_retry(result_text, reply_to=channel_msg_id)
    except Exception as e:
      log.warning("Could not post close reply to channel: %s", e)

  await msg.answer(f"#{row_id} marked closed ({'+' if pips >= 0 else ''}{pips} pips).")
  log.info("Manual signal #%d closed: %+d pips", row_id, pips)


@dp.message(F.chat.type == "private", F.text.regexp(r'(?i)^cancel\s+\d+$'))
async def handle_cancel(msg: Message) -> None:
  if not _is_owner(msg):
    return
  m = _CANCEL_RE.search((msg.text or "").strip())
  if not m:
    await msg.answer("Usage: <code>cancel &lt;id&gt;</code>")
    return
  row_id = int(m.group(1))
  row = await cancel_manual_signal(row_id)
  if row is None:
    await msg.answer(f"⚠️ Signal #{row_id} not found or already closed/cancelled.")
    return

  channel_msg_id = row.get("channel_message_id")
  if channel_msg_id:
    try:
      await _send_with_retry("❌ Signal cancelled.", reply_to=channel_msg_id)
    except Exception as e:
      log.warning("Could not post cancel reply to channel: %s", e)

  await msg.answer(f"#{row_id} cancelled.")
  log.info("Manual signal #%d cancelled", row_id)


# Per-user photo buffer — batches all photos sent within PHOTO_WINDOW seconds.
# Works regardless of media_group_id (handles sequential sends too).
# {user_id: {"photos": [...], "first_msg": msg, "thinking": msg|None, "task": task}}
_photo_buffer: dict[int, dict] = {}
PHOTO_WINDOW = 2.0  # seconds to wait for more photos from the same user


async def _flush_photo_buffer(user_id: int) -> None:
  await asyncio.sleep(PHOTO_WINDOW)
  entry = _photo_buffer.pop(user_id, None)
  if not entry:
    return
  thinking = entry.get("thinking")
  first_msg = entry["first_msg"]
  if thinking is None:
    thinking = await first_msg.answer("🔍 Processing…")
  await _run_chart_analysis(entry["photos"], first_msg, thinking)


async def _run_chart_analysis(photos: list, first_msg: Message, thinking: Message) -> None:
  count = len(photos)
  try:
    await thinking.edit_text(f"🔍 Analysing {count} chart{'s' if count > 1 else ''}…")
    images = [await bot.download(p) for p in photos]
    analysis_html = await analyse_chart_image(images, media_type="image/jpeg")
  except Exception as e:
    log.error("Chart analysis error: %s", e)
    await thinking.edit_text(f"⚠️ Analysis failed: {e}")
    return

  await thinking.edit_text(f"📊 <b>Chart Analysis</b>\n\n{analysis_html}")
  try:
    await _send_with_retry(f"📊 <b>Chart Analysis</b>\n\n{analysis_html}")
    await first_msg.answer("✅ Pushed to channel.")
  except Exception as e:
    log.warning("Could not push chart analysis to channel: %s", e)
    await first_msg.answer("⚠️ Could not push to channel.")


@dp.message(F.chat.type == "private", F.photo)
async def handle_chart_photo(msg: Message) -> None:
  """Analyse chart screenshot(s) sent as DM photo(s), reply in DM and push to channel."""
  if not _is_owner(msg):
    return

  user_id = msg.from_user.id
  photo = msg.photo[-1]  # largest available size

  # All dict writes before any await — prevents race between concurrent handlers
  is_leader = user_id not in _photo_buffer
  if is_leader:
    _photo_buffer[user_id] = {"photos": [], "first_msg": msg, "thinking": None, "task": None}

  entry = _photo_buffer[user_id]
  entry["photos"].append(photo)

  old_task = entry.get("task")
  if old_task and not old_task.done():
    old_task.cancel()
  entry["task"] = asyncio.create_task(_flush_photo_buffer(user_id))

  # Only the first photo sends the status message
  if is_leader:
    thinking = await msg.answer("🔍 Collecting charts…")
    if user_id in _photo_buffer:
      _photo_buffer[user_id]["thinking"] = thinking


@dp.message(F.chat.type == "private")
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
      "Commands: <code>active</code> · <code>close &lt;id&gt; +50</code> · <code>cancel &lt;id&gt;</code>"
    )
    return
  sent = await _send_with_retry(_format_manual_signal(sig))
  row_id = await store_manual_signal(
    ts=int(time.time()),
    action=sig['action'],
    entry=sig['entry'],
    entry_end=sig['entry_end'],
    sl=sig['sl'],
    tps=sig['tps'],
    channel_message_id=sent.message_id,
  )
  await msg.answer(f"✅ Sent to channel (signal #{row_id})")
  log.info(
    "Manual signal #%d: %s XAUUSD @ %s-%s",
    row_id, sig['action'], sig['entry'], sig['entry_end'],
  )


async def _handle_pips(msg: Message, text: str, has_photo: bool) -> None:
  m = _PIPS_RE.search(text)
  if not m:
    return
  sign, pips = m.group(1), int(m.group(2))
  if sign == "+":
    icon_count = 1 if pips <= 100 else 2 if pips < 300 else 3
    new_text = f"✅ Booked +{pips} pips profit! {'💸' * icon_count}"
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


@dp.channel_post(F.text.regexp(r'(?i)^cancel$'), F.reply_to_message)
async def handle_channel_cancel(msg: Message) -> None:
  orig_id = msg.reply_to_message.message_id
  row = await cancel_manual_signal_by_channel_id(orig_id)
  if row is None:
    return  # not a tracked signal — ignore silently
  # Delete the admin's "cancel" message to keep channel clean
  try:
    await bot.delete_message(msg.chat.id, msg.message_id)
  except Exception:
    pass
  # Post a cancellation notice as reply to the original signal
  try:
    await _send_with_retry("❌ Signal cancelled.", reply_to=orig_id)
  except Exception as e:
    log.warning("Could not post cancel notice to channel: %s", e)
  log.info("Channel-reply cancel: signal #%d (msg %d) cancelled", row["id"], orig_id)


@dp.channel_post(F.photo)
async def handle_profit_screenshot(msg: Message) -> None:
  await _handle_pips(msg, msg.caption or "", has_photo=True)


@dp.channel_post(F.text)
async def handle_profit_text(msg: Message) -> None:
  await _handle_pips(msg, msg.text or "", has_photo=False)



def _action_icon(action: str) -> str:
  return "📈" if action == "BUY" else "📉"


def _trim_number(value: float, decimals: int = 2, grouping: bool = False) -> str:
  fmt = f"{{:,.{decimals}f}}" if grouping else f"{{:.{decimals}f}}"
  return fmt.format(value).rstrip("0").rstrip(".")


def _fmt_price(value: Optional[float]) -> str:
  if value is None:
    return "-"
  abs_value = abs(value)
  if abs_value >= 1:
    return _trim_number(value, decimals=2, grouping=True)
  return _trim_number(value, decimals=5, grouping=False)


_MAX_SEND_ATTEMPTS = 3


async def _send_with_retry(text: str, reply_to: int | None = None) -> Message:
  """Send a Telegram message with exponential-backoff retry on network errors."""
  for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
    try:
      return await bot.send_message(
        chat_id=settings.telegram_chat_id,
        text=text,
        reply_to_message_id=reply_to,
      )
    except TelegramRetryAfter as e:
      log.warning("Telegram rate-limited; waiting %ds (attempt %d/%d)", e.retry_after, attempt, _MAX_SEND_ATTEMPTS)
      await asyncio.sleep(e.retry_after)
    except TelegramNetworkError as e:
      if attempt == _MAX_SEND_ATTEMPTS:
        raise
      wait = 2 ** attempt
      log.warning("Telegram send failed (attempt %d/%d): %s — retrying in %ds", attempt, _MAX_SEND_ATTEMPTS, e, wait)
      await asyncio.sleep(wait)
  # All attempts were rate-limits (TelegramRetryAfter) that never succeeded.
  raise RuntimeError(f"Telegram send failed after {_MAX_SEND_ATTEMPTS} attempts")
