import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from html import escape
from typing import Optional
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import (
  BotCommand,
  BotCommandScopeChat,
  BotCommandScopeDefault,
  CallbackQuery,
  InlineKeyboardButton,
  InlineKeyboardMarkup,
  Message,
)

from app.config import settings
from app.chart_analysis import analyse_chart_image
from app.dedup import (
  store_pips, get_pips_summary, get_pips_records,
  store_manual_signal,
  get_open_signals, get_all_signals, get_manual_signal,
  get_signal_by_post,
  get_signal_cluster,
  event_in_window,
)
from app.reports import build_stats, format_review, format_stats
from app.broadcast import broadcast_entry, render_entry
from app.pips_format import wing_icons
from app.symbols import (
  SYMBOLS,
  channel_for_symbol,
  symbol_for_channel,
  tier_for_channel,
)
from app.trade_ops import (
  do_active,
  do_cancel,
  do_close,
  do_delete,
  do_note,
  do_reopen,
  do_sl,
  do_tag,
  do_tp,
  do_uncclose,
  post_result,
  render_result,
)

log = logging.getLogger(__name__)

bot = Bot(
  token=settings.telegram_bot_token,
  default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

OWNER_COMMANDS = [
  BotCommand(command="trade_open", description="[SYMBOL] — list open signals"),
  BotCommand(command="trade_active", description="[SYMBOL] [#id]"),
  BotCommand(command="trade_close", description="[SYMBOL] #id ±pips [%] | be"),
  BotCommand(command="trade_uncclose", description="[SYMBOL] #id"),
  BotCommand(command="trade_tp", description="[SYMBOL] #id TP +pips"),
  BotCommand(command="trade_sl", description="[SYMBOL] #id be|price"),
  BotCommand(command="trade_cancel", description="[SYMBOL] #id"),
  BotCommand(command="trade_delete", description="[SYMBOL] #id — remove a typo"),
  BotCommand(command="trade_reopen", description="[SYMBOL] #id [lo-hi]"),
  BotCommand(command="trade_tag", description="[SYMBOL] #id setup [***]"),
  BotCommand(command="trade_note", description="[SYMBOL] #id text"),
  BotCommand(command="trade_review", description="[SYMBOL] #id"),
  BotCommand(command="trade_stats", description="[SYMBOL] [today|week|month]"),
  BotCommand(command="trade_pips", description="[SYMBOL] [period]"),
  BotCommand(command="help", description="Trade command reference"),
]


async def setup_commands(target_bot: Bot) -> None:
  await target_bot.set_my_commands(
    [],
    scope=BotCommandScopeDefault(),
  )
  if settings.telegram_owner_id:
    await target_bot.set_my_commands(
      OWNER_COMMANDS,
      scope=BotCommandScopeChat(chat_id=settings.telegram_owner_id),
    )

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
_SETUP_SUFFIX_RE = re.compile(
  r'(?i)\s*/\s*setup\s+([a-z0-9][a-z0-9_-]*)'
  r'(?:\s+(\*{1,3}|[1-3]))?\s*$'
)
_SCALP_SUFFIX_RE = re.compile(
  r'(?i)\s*/\s*(?:scalp|scalp[-_\s]*nhanh|quick[-_\s]*scalp)'
  r'(?=\s*(?:/|$))'
)

def _expand_entry_endpoint(value: float, anchor: float) -> float:
  """Expand a short zone endpoint to the closest price around the anchor."""
  if value >= 100:
    return value
  base = int(anchor / 100) * 100
  candidates = (base + value - 100, base + value, base + value + 100)
  return min(candidates, key=lambda price: abs(price - anchor))


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
  raw = text.strip()
  raw, vip_count = re.subn(
    r'(?i)\s*/\s*vip(?=\s*(?:/|$))',
    "",
    raw,
  )
  raw, scalp_count = _SCALP_SUFFIX_RE.subn("", raw)
  setup_type = None
  confluence = None
  setup_match = _SETUP_SUFFIX_RE.search(raw)
  if setup_match:
    setup_type = setup_match.group(1).lower()
    grade = setup_match.group(2)
    if grade:
      confluence = len(grade) if grade.startswith("*") else int(grade)
    raw = raw[:setup_match.start()].rstrip()
  elif scalp_count:
    setup_type = "scalp"
  raw = re.sub(
    r'\s*/\s*(?=(?:sl|tp)\b)',
    "\n",
    raw,
    flags=re.IGNORECASE,
  )
  m = _MANUAL_RE.search(raw)
  if not m:
    return None
  action, entry_a, entry_b, sl, tp_raw = m.groups()
  action = action.upper()
  entry_anchor = float(entry_a)
  entry_other = _expand_entry_endpoint(float(entry_b), entry_anchor)
  entry_low, entry_high = sorted((entry_anchor, entry_other))
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
    'setup_type': setup_type,
    'confluence': confluence,
    'visibility': 'vip' if vip_count else 'both',
  }


def _format_manual_signal(
  sig: dict,
  daily_seq: int,
  symbol: str = "XAU",
) -> str:
  return render_entry(
    {
      **sig,
      "daily_seq": daily_seq,
      "symbol": symbol,
    },
    "vip",
  )


def _period_range(period: str) -> tuple[int, int]:
  now = datetime.now(timezone.utc)
  today = now.replace(hour=0, minute=0, second=0, microsecond=0)
  p = period.lower().replace('  ', ' ')
  if p == "week":
    p = "this week"
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
    return False  # fail-closed: no owner configured -> deny privileged DMs
  return msg.from_user is not None and msg.from_user.id == settings.telegram_owner_id


def _command_args(msg: Message) -> str:
  return (msg.text or "").partition(" ")[2].strip()


def _take_symbol(
  raw: str,
  *,
  default: str | None = "XAU",
) -> tuple[str | None, str]:
  parts = raw.split(maxsplit=1)
  if parts and parts[0].upper() in SYMBOLS:
    return parts[0].upper(), parts[1] if len(parts) > 1 else ""
  return default, raw


def _seq_token(value: str) -> int | None:
  value = value.strip().lstrip("#")
  return int(value) if value.isdigit() else None


@dp.message(Command("trade_pips"), F.chat.type == "private")
async def handle_trade_pips(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw_period = _take_symbol(_command_args(msg), default=None)
  period = (raw_period or "today").lower()
  if period not in {"today", "yesterday", "week", "this week", "last week"}:
    await msg.answer(
      "Usage: <code>/trade_pips [SYMBOL] "
      "[today|yesterday|week|last week]</code>"
    )
    return
  start_ts, end_ts = _period_range(period)
  await msg.answer("📊 Calculating pips…")
  try:
    s = await get_pips_summary(start_ts, end_ts, symbol)
  except RuntimeError as e:
    await msg.answer(f"⚠️ {e}")
    return

  scope = symbol or "All symbols"
  if s['total'] == 0:
    await msg.answer(
      f"📊 No {escape(scope)} pips results found for <b>{period}</b>."
    )
    return

  net_icon = '💰' if s['net'] >= 0 else '🔻'
  net_sign = '+' if s['net'] >= 0 else ''
  label = f"{scope} · {period.title()}"
  lines = [
    f"📊 <b>Trade Pips — {escape(label)}</b>",
    "",
    f"✅ Wins:    {s['wins']} trade{'s' if s['wins'] != 1 else ''}  <b>+{s['win_pips']} pips</b>",
    f"❌ Losses:  {s['losses']} trade{'s' if s['losses'] != 1 else ''}  <b>-{s['loss_pips']} pips</b>",
    "──────────────",
    f"{net_icon} Net:    <b>{net_sign}{s['net']} pips</b>",
  ]
  await msg.answer("\n".join(lines))


_HELP_TEXT = """<b>Trade controls</b>

<b>Channel replies</b>
<code>active [#id]</code>
<code>close #id ±pips [%] | be</code>
<code>sl #id be|price</code>
<code>cancel #id</code>
<code>reopen #id [lo-hi]</code>
<code>tag #id &lt;setup&gt; [***]</code>
<code>note #id &lt;text&gt;</code>

<b>Owner DM commands</b>
<code>/trade_open [SYMBOL]</code>
<code>/trade_active [SYMBOL] [#id]</code>
<code>/trade_close [SYMBOL] #id ±pips [%] | be</code>
<code>/trade_uncclose [SYMBOL] #id</code>
<code>/trade_tp [SYMBOL] #id TP +pips</code>
<code>/trade_sl [SYMBOL] #id be|price</code>
<code>/trade_cancel [SYMBOL] #id</code>
<code>/trade_delete [SYMBOL] #id</code>
<code>/trade_reopen [SYMBOL] #id [lo-hi]</code>
<code>/trade_tag [SYMBOL] #id &lt;setup&gt; [***]</code>
<code>/trade_note [SYMBOL] #id &lt;text&gt;</code>
<code>/trade_review [SYMBOL] #id</code>
<code>/trade_stats [SYMBOL] [today|week|month]</code>
<code>/trade_pips [SYMBOL] [today|yesterday|week|last week]</code>"""


@dp.message(Command("help"), F.chat.type == "private")
async def handle_help(msg: Message) -> None:
  if not _is_owner(msg):
    return
  await msg.answer(_HELP_TEXT)


_ACTIVE_RE = re.compile(r'(?i)^\s*active(?:\s+#?(\d+))?\s*$')
_CLOSE_RE = re.compile(
  r'(?i)^\s*close(?:\s+#?(\d+))?\s+([+-]\d+)\s*'
  r'(?:pips?)?(?:\s+(\d{1,3})\s*%)?\s*$'
)
_CLOSEBE_RE = re.compile(r'(?i)^\s*close(?:\s+#?(\d+))?\s+be\s*$')
_CANCEL_RE = re.compile(r'(?i)^\s*cancel(?:\s+#?(\d+))?\s*$')
_SL_RE = re.compile(
  r'(?i)^\s*sl(?:\s+#?(\d+))?\s+(be|\d+(?:\.\d+)?)\s*$'
)
_REOPEN_RE = re.compile(
  r'(?i)^\s*reopen(?:\s+#?(\d+))?'
  r'(?:\s+([\d.]+)\s*[-–]\s*([\d.]+))?\s*$'
)
_TAG_RE = re.compile(
  r'(?i)^\s*tag\s+#?(\d+)\s+([a-z0-9][a-z0-9_-]*)'
  r'(?:\s+(\*{1,3}|[1-3]))?\s*$'
)
_NOTE_RE = re.compile(r'(?is)^\s*note\s+#?(\d+)\s+(.+?)\s*$')
_TP_RE = re.compile(
  r'(?i)^\s*tp\s+#?(\d+)\s+(?:tp)?(\d+)\s+\+(\d+)\s*(?:pips?)?\s*$'
)


def _today_str() -> str:
  tz = ZoneInfo(settings.seq_reset_tz)
  return datetime.now(tz).date().isoformat()


async def _resolve_sid(
  explicit_seq: int | None,
  reply_to_id: int | None,
  symbol: str = "XAU",
) -> int | None:
  """Resolve a daily display number or reply target to a primary key."""
  opens = await get_open_signals(symbol)
  if explicit_seq is not None:
    todays = [
      s for s in opens
      if s["daily_seq"] == explicit_seq and s["trade_date"] == _today_str()
    ]
    if todays:
      return todays[-1]["id"]
    any_seq = [s for s in opens if s["daily_seq"] == explicit_seq]
    return any_seq[-1]["id"] if any_seq else None
  if reply_to_id is not None:
    row = await get_signal_by_post(
      channel_for_symbol(symbol),
      reply_to_id,
      open_only=True,
    )
    return (
      row["id"]
      if row and row.get("symbol", "XAU") == symbol
      else None
    )
  return opens[0]["id"] if len(opens) == 1 else None


async def _resolve_any_sid(
  explicit_seq: int | None,
  reply_to_id: int | None,
  symbol: str = "XAU",
) -> int | None:
  """Resolve a display number or reply across all lifecycle states."""
  signals = await get_all_signals(symbol)
  if explicit_seq is not None:
    todays = [
      signal for signal in signals
      if (
        signal["daily_seq"] == explicit_seq
        and signal["trade_date"] == _today_str()
      )
    ]
    if todays:
      return todays[-1]["id"]
    matching = [
      signal for signal in signals
      if signal["daily_seq"] == explicit_seq
    ]
    return matching[-1]["id"] if matching else None
  if reply_to_id is not None:
    row = await get_signal_by_post(
      channel_for_symbol(symbol),
      reply_to_id,
    )
    return (
      row["id"]
      if row and row.get("symbol", "XAU") == symbol
      else None
    )
  return signals[0]["id"] if len(signals) == 1 else None


def _parse_close(text: str) -> tuple[int | None, int, float | None] | None:
  match = _CLOSE_RE.match(text)
  if match:
    seq = int(match.group(1)) if match.group(1) else None
    frac = int(match.group(3)) / 100 if match.group(3) else None
    return seq, int(match.group(2)), frac
  match = _CLOSEBE_RE.match(text)
  if match:
    seq = int(match.group(1)) if match.group(1) else None
    return seq, 0, None
  return None


async def _book_leg(
  sid: int,
  pips: int,
  frac: float | None,
  chat_id: str | int,
) -> tuple[dict, str] | None:
  symbol = symbol_for_channel(chat_id) or "XAU"
  result = await do_close({
    "sid": sid,
    "symbol": symbol,
    "pips": pips,
    "frac": frac,
  })
  if not result.get("ok"):
    return None
  return result["row"], render_result(result, symbol)


# ── Owner-only inline "Close" buttons on watcher TP-hit alerts ───────────────
# The watcher attaches build_tp_close_kb() to the VIP TP alert. Pressing it
# expands to Full/partial-% options; a choice books via do_close (same path as
# the /trade_close command). Only the configured owner may act.

_PARTIAL_STEPS = (25, 50, 75)


def build_tp_close_kb(sid: int, tp: int, pips: int) -> InlineKeyboardMarkup:
  """Single Close button for a fresh (or partially-booked) VIP TP alert."""
  return InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(
      text="🔒 Close", callback_data=f"c0:{sid}:{tp}:{pips}"
    ),
  ]])


def _partial_kb(
  sid: int, tp: int, pips: int, remaining: float
) -> InlineKeyboardMarkup:
  """Full + partial %, hiding fractions that exceed what remains open."""
  rem_pct = round(remaining * 100)
  steps = [
    InlineKeyboardButton(
      text=f"{p}%", callback_data=f"c1:{sid}:{tp}:{pips}:{p}"
    )
    for p in _PARTIAL_STEPS if p < rem_pct
  ]
  keyboard = [[InlineKeyboardButton(
    text="✅ Full", callback_data=f"c1:{sid}:{tp}:{pips}:100"
  )]]
  if steps:
    keyboard.append(steps)
  keyboard.append([InlineKeyboardButton(
    text="✖ Cancel", callback_data=f"cx:{sid}:{tp}:{pips}"
  )])
  return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _is_owner_cb(cb: CallbackQuery) -> bool:
  # Fail-closed: no owner configured -> deny (mirrors _is_owner for messages).
  if not settings.telegram_owner_id:
    return False
  return cb.from_user is not None and cb.from_user.id == settings.telegram_owner_id


async def _remaining_fraction(sid: int) -> float | None:
  """Open fraction still un-booked, or None if the signal is not open."""
  signal = await get_manual_signal(sid)
  if signal is None or signal.get("status") != "open":
    return None
  used = sum(float(leg["frac"]) for leg in (signal.get("legs") or []))
  return max(0.0, 1.0 - used)


@dp.callback_query(F.data.startswith("c0:"))
async def handle_close_menu(cb: CallbackQuery) -> None:
  if not _is_owner_cb(cb):
    await cb.answer("⛔ Owner only", show_alert=True)
    return
  _, sid_s, tp_s, pips_s = cb.data.split(":")
  sid = int(sid_s)
  remaining = await _remaining_fraction(sid)
  if remaining is None or remaining <= 0:
    await cb.answer("⚠️ Already closed", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=None)
    return
  await cb.message.edit_reply_markup(
    reply_markup=_partial_kb(sid, int(tp_s), int(pips_s), remaining)
  )
  await cb.answer()


@dp.callback_query(F.data.startswith("cx:"))
async def handle_close_cancel(cb: CallbackQuery) -> None:
  if not _is_owner_cb(cb):
    await cb.answer("⛔ Owner only", show_alert=True)
    return
  _, sid_s, tp_s, pips_s = cb.data.split(":")
  # Back out of the submenu: restore the single Close button, close nothing.
  await cb.message.edit_reply_markup(
    reply_markup=build_tp_close_kb(int(sid_s), int(tp_s), int(pips_s))
  )
  await cb.answer("Cancelled")


@dp.callback_query(F.data.startswith("c1:"))
async def handle_close_book(cb: CallbackQuery) -> None:
  if not _is_owner_cb(cb):
    await cb.answer("⛔ Owner only", show_alert=True)
    return
  _, sid_s, tp_s, pips_s, frac_s = cb.data.split(":")
  sid, tp, pips, frac_pct = int(sid_s), int(tp_s), int(pips_s), int(frac_s)
  frac = None if frac_pct >= 100 else frac_pct / 100
  symbol = symbol_for_channel(cb.message.chat.id) or "XAU"
  result = await do_close({
    "sid": sid, "symbol": symbol, "pips": pips, "frac": frac,
  })
  if not result.get("ok"):
    await cb.answer("⚠️ Already closed or invalid", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=None)
    return
  row = result["row"]
  base = cb.message.html_text or cb.message.text or ""
  if row.get("closed"):
    net = row["net"]
    sign = "+" if net >= 0 else "-"
    await cb.message.edit_text(
      f"{base}\n\n✅ <b>Closed</b> · net <b>{sign}{abs(net)} pips</b>",
      reply_markup=None,
    )
    await cb.answer("Closed")
  else:
    rem_pct = round(row["remaining"] * 100)
    await cb.message.edit_text(
      f"{base}\n\n📊 Booked <b>{frac_pct}%</b> @ +{pips} pips · "
      f"remaining <b>{rem_pct}%</b>",
      reply_markup=build_tp_close_kb(sid, tp, pips),
    )
    await cb.answer(f"Booked {frac_pct}%")


async def _reopen_signal(
  source_id: int,
  entry_a: float | None,
  entry_b: float | None,
) -> tuple[dict, str] | None:
  source = await get_manual_signal(source_id)
  if source is None:
    return None
  symbol = source.get("symbol", "XAU")
  result = await do_reopen({
    "sid": source_id,
    "symbol": symbol,
    "entry_override": (
      (entry_a, entry_b) if entry_a is not None else None
    ),
  })
  if not result.get("ok"):
    return None
  text = await post_result(result, symbol)
  return result["record"], text


async def _move_stop(
  sid: int,
  target: str,
) -> tuple[dict, str] | None:
  signal = await get_manual_signal(sid)
  if signal is None:
    return None
  symbol = signal.get("symbol", "XAU")
  result = await do_sl({
    "sid": sid,
    "symbol": symbol,
    "sl": target,
  })
  if not result.get("ok"):
    return None
  return result["row"], render_result(result, symbol)


def _num(value: float | int) -> str:
  return f"{value:g}"


@dp.message(Command("trade_open"), F.chat.type == "private")
async def handle_trade_open(msg: Message) -> None:
  """List currently-open signals so stale ones (which keep the price watcher
  polling) can be spotted and closed."""
  if not _is_owner(msg):
    return
  symbol, _ = _take_symbol(_command_args(msg), default=None)
  opens = await get_open_signals(symbol)
  if not opens:
    await msg.answer("📭 No open signals.")
    return
  lines = ["📂 <b>Open signals</b>"]
  for sig in opens:
    seq = sig.get("daily_seq") or sig["id"]
    used = sum(float(leg["frac"]) for leg in (sig.get("legs") or []))
    remaining = round(max(0.0, 1.0 - used) * 100)
    entry_end = sig["entry_end"] if sig["entry_end"] is not None else sig["entry"]
    lines.append(
      f"#{seq} {escape(sig.get('symbol', 'XAU'))} {escape(sig['action'])} "
      f"{_num(sig['entry'])}–{_num(entry_end)} · SL {_num(sig['sl'])} · "
      f"{escape(sig['fill_state'])} · {remaining}% open"
    )
  await msg.answer("\n".join(lines))


@dp.message(Command("trade_active"), F.chat.type == "private")
async def handle_trade_active(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  explicit_seq = _seq_token(raw) if raw else None
  sid = await _resolve_sid(explicit_seq, None, symbol)
  if sid is None:
    await msg.answer(
      "⚠️ Signal not found or ambiguous; specify "
      "<code>/trade_active [SYMBOL] #N</code>."
    )
    return
  result = await do_active({
    "sid": sid,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
  })
  text = await post_result(result, symbol)
  await msg.answer(text)


@dp.message(Command("trade_close"), F.chat.type == "private")
async def handle_trade_close(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  parsed = _parse_close(f"close {raw}")
  if parsed is None:
    await msg.answer(
      "Usage: <code>/trade_close [SYMBOL] #N +50 50%</code>, "
      "<code>#N -30</code>, or <code>#N be</code>"
    )
    return
  explicit_seq, pips, frac = parsed
  sid = await _resolve_sid(explicit_seq, None, symbol)
  if sid is None:
    await msg.answer("⚠️ Signal not found.")
    return
  result = await do_close({
    "sid": sid,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
    "pips": pips,
    "frac": "be" if raw.lower().endswith(" be") else frac,
  })
  await msg.answer(await post_result(result, symbol))


@dp.message(Command("trade_tp"), F.chat.type == "private")
async def handle_trade_tp(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  match = _TP_RE.match(f"tp {raw}")
  if not match:
    await msg.answer(
      "Usage: <code>/trade_tp [SYMBOL] #N TP_NUMBER +PIPS</code>"
    )
    return
  seq = int(match.group(1))
  sid = await _resolve_sid(seq, None, symbol)
  if sid is None:
    await msg.answer("⚠️ Open signal not found.")
    return
  result = await do_tp({
    "sid": sid,
    "symbol": symbol,
    "tp_number": int(match.group(2)),
    "pips": int(match.group(3)),
  })
  await msg.answer(await post_result(result, symbol))


@dp.message(Command("trade_uncclose", "trade_restore"), F.chat.type == "private")
async def handle_trade_uncclose(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  seq = _seq_token(raw)
  if seq is None:
    await msg.answer(
      "Usage: <code>/trade_uncclose [SYMBOL] #N</code>"
    )
    return
  sid = await _resolve_any_sid(seq, None, symbol)
  if sid is None:
    await msg.answer("⚠️ Signal not found.")
    return
  result = await do_uncclose({
    "sid": sid,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
  })
  await msg.answer(await post_result(result, symbol))


@dp.message(Command("trade_cancel"), F.chat.type == "private")
async def handle_trade_cancel(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  sid = await _resolve_sid(_seq_token(raw), None, symbol)
  if sid is None:
    await msg.answer("⚠️ Signal not found.")
    return
  result = await do_cancel({
    "sid": sid,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
  })
  await msg.answer(await post_result(result, symbol))


@dp.message(Command("trade_delete"), F.chat.type == "private")
async def handle_trade_delete(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  seq = _seq_token(raw)
  if seq is None:
    await msg.answer(
      "Usage: <code>/trade_delete [SYMBOL] #N</code>"
    )
    return
  sid = await _resolve_any_sid(seq, None, symbol)
  if sid is None:
    await msg.answer("⚠️ Signal not found.")
    return
  result = await do_delete({
    "sid": sid,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
  })
  await msg.answer(await post_result(result, symbol))


@dp.message(Command("trade_sl"), F.chat.type == "private")
async def handle_trade_sl(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  match = _SL_RE.match(f"sl {raw}")
  if not match:
    await msg.answer(
      "Usage: <code>/trade_sl [SYMBOL] #N be|price</code>"
    )
    return
  explicit_seq = int(match.group(1)) if match.group(1) else None
  sid = await _resolve_sid(explicit_seq, None, symbol)
  if sid is None:
    await msg.answer("⚠️ Signal not found.")
    return
  result = await do_sl({
    "sid": sid,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
    "sl": match.group(2),
  })
  await msg.answer(await post_result(result, symbol))


@dp.message(Command("trade_reopen"), F.chat.type == "private")
async def handle_trade_reopen(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  match = _REOPEN_RE.match(f"reopen {raw}")
  if not match:
    await msg.answer(
      "Usage: <code>/trade_reopen [SYMBOL] #N [lo-hi]</code>"
    )
    return
  explicit_seq = int(match.group(1)) if match and match.group(1) else None
  source_id = await _resolve_any_sid(explicit_seq, None, symbol)
  if source_id is None:
    await msg.answer("⚠️ Signal not found.")
    return
  entry_a = float(match.group(2)) if match and match.group(2) else None
  entry_b = float(match.group(3)) if match and match.group(3) else None
  override = (entry_a, entry_b) if entry_a is not None else None
  result = await do_reopen({
    "sid": source_id,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
    "entry_override": override,
  })
  text = await post_result(result, symbol)
  await msg.answer(text)


@dp.message(Command("trade_tag"), F.chat.type == "private")
async def handle_trade_tag(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  match = _TAG_RE.match(f"tag {raw}")
  if not match:
    await msg.answer(
      "Usage: <code>/trade_tag [SYMBOL] #N setup [***]</code>"
    )
    return
  seq = int(match.group(1))
  sid = await _resolve_any_sid(seq, None, symbol)
  if sid is None:
    await msg.answer("⚠️ Signal not found.")
    return
  setup_type = match.group(2).lower()
  grade = match.group(3)
  confluence = None
  if grade:
    confluence = len(grade) if grade.startswith("*") else int(grade)
  result = await do_tag({
    "sid": sid,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
    "seq": seq,
    "setup": setup_type,
    "stars": confluence,
  })
  await msg.answer(await post_result(result, symbol))


@dp.message(Command("trade_note"), F.chat.type == "private")
async def handle_trade_note(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  match = _NOTE_RE.match(f"note {raw}")
  if not match:
    await msg.answer(
      "Usage: <code>/trade_note [SYMBOL] #N text</code>"
    )
    return
  seq = int(match.group(1))
  sid = await _resolve_any_sid(seq, None, symbol)
  if sid is None:
    await msg.answer("⚠️ Signal not found.")
    return
  result = await do_note({
    "sid": sid,
    "symbol": symbol,
    "chat_id": channel_for_symbol(symbol),
    "reply_to": None,
    "seq": seq,
    "text": match.group(2).strip(),
  })
  await msg.answer(await post_result(result, symbol))


@dp.message(Command("trade_review"), F.chat.type == "private")
async def handle_trade_review(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg))
  seq = _seq_token(raw)
  sid = await _resolve_any_sid(seq, None, symbol)
  cluster = await get_signal_cluster(sid) if sid is not None else []
  if not cluster:
    await msg.answer("⚠️ Signal not found.")
    return
  await msg.answer(format_review(cluster))


def _stats_range(period: str) -> tuple[int, int]:
  tz = ZoneInfo(settings.seq_reset_tz)
  now = datetime.now(tz)
  today = now.replace(hour=0, minute=0, second=0, microsecond=0)
  if period == "today":
    start = today
  elif period == "week":
    start = today - timedelta(days=today.weekday())
  elif period == "month":
    start = today.replace(day=1)
  else:
    return 0, int(now.timestamp())
  return int(start.timestamp()), int(now.timestamp())


@dp.message(Command("trade_stats"), F.chat.type == "private")
async def handle_trade_stats(msg: Message) -> None:
  if not _is_owner(msg):
    return
  symbol, raw = _take_symbol(_command_args(msg), default=None)
  period = (raw or "today").lower()
  if period not in {"today", "week", "month", "all"}:
    await msg.answer(
      "Usage: <code>/trade_stats [SYMBOL] [today|week|month]</code>"
    )
    return
  start_ts, end_ts = _stats_range(period)
  records = await get_pips_records(start_ts, end_ts, symbol)
  signals = await get_all_signals(symbol)
  label = f"{symbol} {period}" if symbol else period
  stats = build_stats(
    records,
    signals,
    settings.seq_reset_tz,
    settings.session_asia_start,
    settings.session_london_start,
    settings.session_ny_start,
  )
  await msg.answer(
    format_stats(stats, label)
  )


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


def _event_guard_timing(ts_utc: int, now: int) -> str:
  delta = ts_utc - now
  if delta < 0:
    return f"started {max(1, abs(delta) // 60)}m ago"
  hours, remainder = divmod(delta, 3600)
  minutes = remainder // 60
  return f"in {hours}h {minutes}m"


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


async def _delete_command(msg: Message) -> None:
  try:
    await bot.delete_message(msg.chat.id, msg.message_id)
  except Exception:
    pass


def _channel_symbol(msg: Message) -> str | None:
  if tier_for_channel(msg.chat.id) != "vip":
    return None
  return symbol_for_channel(msg.chat.id)


@dp.channel_post(F.text.regexp(_ACTIVE_RE), F.reply_to_message)
async def handle_channel_active(msg: Message) -> None:
  symbol = _channel_symbol(msg)
  if symbol is None:
    return
  match = _ACTIVE_RE.match(msg.text or "")
  explicit_seq = int(match.group(1)) if match and match.group(1) else None
  reply_to = msg.reply_to_message.message_id
  sid = await _resolve_sid(explicit_seq, reply_to, symbol)
  if sid is None:
    return
  result = await do_active({
    "sid": sid,
    "symbol": symbol,
    "chat_id": msg.chat.id,
    "reply_to": reply_to,
  })
  if not result.get("ok"):
    return
  await post_result(result, symbol)
  await _delete_command(msg)


@dp.channel_post(
  F.text.regexp(_CLOSE_RE) | F.text.regexp(_CLOSEBE_RE),
  F.reply_to_message,
)
async def handle_channel_close(msg: Message) -> None:
  symbol = _channel_symbol(msg)
  if symbol is None:
    return
  parsed = _parse_close(msg.text or "")
  if parsed is None:
    return
  explicit_seq, pips, frac = parsed
  reply_to = msg.reply_to_message.message_id
  sid = await _resolve_sid(explicit_seq, reply_to, symbol)
  if sid is None:
    return
  result = await do_close({
    "sid": sid,
    "symbol": symbol,
    "chat_id": msg.chat.id,
    "reply_to": reply_to,
    "pips": pips,
    "frac": (
      "be"
      if (msg.text or "").strip().lower().endswith(" be")
      else frac
    ),
  })
  if not result.get("ok"):
    return
  await post_result(result, symbol)
  await _delete_command(msg)


@dp.channel_post(F.text.regexp(_CANCEL_RE), F.reply_to_message)
async def handle_channel_cancel(msg: Message) -> None:
  symbol = _channel_symbol(msg)
  if symbol is None:
    return
  match = _CANCEL_RE.match(msg.text or "")
  explicit_seq = int(match.group(1)) if match and match.group(1) else None
  reply_to = msg.reply_to_message.message_id
  sid = await _resolve_sid(explicit_seq, reply_to, symbol)
  if sid is None:
    return
  result = await do_cancel({
    "sid": sid,
    "symbol": symbol,
    "chat_id": msg.chat.id,
    "reply_to": reply_to,
  })
  if not result.get("ok"):
    return
  await post_result(result, symbol)
  await _delete_command(msg)


@dp.channel_post(F.text.regexp(_SL_RE), F.reply_to_message)
async def handle_channel_sl(msg: Message) -> None:
  symbol = _channel_symbol(msg)
  if symbol is None:
    return
  match = _SL_RE.match(msg.text or "")
  explicit_seq = int(match.group(1)) if match and match.group(1) else None
  reply_to = msg.reply_to_message.message_id
  sid = await _resolve_sid(explicit_seq, reply_to, symbol)
  if sid is None:
    return
  result = await do_sl({
    "sid": sid,
    "symbol": symbol,
    "chat_id": msg.chat.id,
    "reply_to": reply_to,
    "sl": match.group(2),
  })
  if not result.get("ok"):
    return
  await post_result(result, symbol)
  await _delete_command(msg)


@dp.channel_post(F.text.regexp(_REOPEN_RE), F.reply_to_message)
async def handle_channel_reopen(msg: Message) -> None:
  symbol = _channel_symbol(msg)
  if symbol is None:
    return
  match = _REOPEN_RE.match(msg.text or "")
  explicit_seq = int(match.group(1)) if match and match.group(1) else None
  reply_to = msg.reply_to_message.message_id
  source_id = await _resolve_any_sid(
    explicit_seq,
    reply_to,
    symbol,
  )
  if source_id is None:
    return
  entry_a = float(match.group(2)) if match and match.group(2) else None
  entry_b = float(match.group(3)) if match and match.group(3) else None
  result = await do_reopen({
    "sid": source_id,
    "symbol": symbol,
    "chat_id": msg.chat.id,
    "reply_to": reply_to,
    "entry_override": (
      (entry_a, entry_b) if entry_a is not None else None
    ),
  })
  if not result.get("ok"):
    # Surface real states (e.g. still_open) to the operator instead of a silent
    # no-op, then still clean up the command like the success path.
    await _send_with_retry(
      render_result(result, symbol, "vip"),
      reply_to=reply_to,
      chat_id=msg.chat.id,
    )
    await _delete_command(msg)
    return
  await post_result(result, symbol)
  await _delete_command(msg)


@dp.channel_post(F.text.regexp(_TAG_RE), F.reply_to_message)
async def handle_channel_tag(msg: Message) -> None:
  symbol = _channel_symbol(msg)
  if symbol is None:
    return
  match = _TAG_RE.match(msg.text or "")
  seq = int(match.group(1))
  reply_to = msg.reply_to_message.message_id
  sid = await _resolve_any_sid(seq, reply_to, symbol)
  if sid is None:
    return
  grade = match.group(3)
  stars = (
    len(grade) if grade and grade.startswith("*")
    else int(grade) if grade else None
  )
  result = await do_tag({
    "sid": sid,
    "symbol": symbol,
    "chat_id": msg.chat.id,
    "reply_to": reply_to,
    "seq": seq,
    "setup": match.group(2).lower(),
    "stars": stars,
  })
  if result.get("ok"):
    await post_result(result, symbol)
    await _delete_command(msg)


@dp.channel_post(F.text.regexp(_NOTE_RE), F.reply_to_message)
async def handle_channel_note(msg: Message) -> None:
  symbol = _channel_symbol(msg)
  if symbol is None:
    return
  match = _NOTE_RE.match(msg.text or "")
  seq = int(match.group(1))
  reply_to = msg.reply_to_message.message_id
  sid = await _resolve_any_sid(seq, reply_to, symbol)
  if sid is None:
    return
  result = await do_note({
    "sid": sid,
    "symbol": symbol,
    "chat_id": msg.chat.id,
    "reply_to": reply_to,
    "seq": seq,
    "text": match.group(2).strip(),
  })
  if result.get("ok"):
    await post_result(result, symbol)
    await _delete_command(msg)


@dp.channel_post(F.photo)
async def handle_profit_screenshot(msg: Message) -> None:
  # Optional legacy behavior; canonical booking is now "close #N ±P".
  if not settings.auto_book_bare_pips:
    return
  await _handle_pips(msg, msg.caption or "", has_photo=True)


@dp.channel_post(F.text)
async def handle_profit_text(msg: Message) -> None:
  # Optional legacy behavior; canonical booking is now "close #N ±P".
  if not settings.auto_book_bare_pips:
    return
  await _handle_pips(msg, msg.text or "", has_photo=False)

_MAX_SEND_ATTEMPTS = 3


async def _send_with_retry(
  text: str,
  reply_to: int | None = None,
  chat_id: int | str | None = None,
  reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
  """Send a Telegram message with exponential-backoff retry on network errors."""
  for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
    try:
      return await bot.send_message(
        chat_id=chat_id or settings.telegram_channel_id,
        text=text,
        reply_to_message_id=reply_to,
        reply_markup=reply_markup,
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
