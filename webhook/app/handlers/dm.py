"""Owner DM command handlers and chart-photo handling."""

import asyncio
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.chart_analysis import analyse_chart_image
from app.config import settings
from app.dedup import (
  get_all_signals,
  get_manual_signal,
  get_open_signals,
  get_pips_records,
  get_pips_summary,
  get_signal_cluster,
)
from app.broadcast import render_entry
from app.parsing import (
  _NOTE_RE,
  _REOPEN_RE,
  _SL_RE,
  _TAG_RE,
  _TP_RE,
  _command_args,
  _is_owner,
  _num,
  _parse_close,
  _period_range,
  _resolve_any_sid,
  _resolve_sid,
  _seq_token,
  _stats_range,
  _take_symbol,
)
from app.reports import build_stats, format_review, format_stats
from app.symbols import channel_for_symbol
from app.tg_core import bot, send_with_retry
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
router = Router(name="dm")

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

# Per-user photo buffer — batches all photos sent within PHOTO_WINDOW seconds.
# Works regardless of media_group_id (handles sequential sends too).
# {user_id: {"photos": [...], "first_msg": msg, "thinking": msg|None, "task": task}}
_photo_buffer: dict[int, dict] = {}
PHOTO_WINDOW = 2.0


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


@router.message(Command("trade_pips"), F.chat.type == "private")
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


@router.message(Command("help"), F.chat.type == "private")
async def handle_help(msg: Message) -> None:
  if not _is_owner(msg):
    return
  await msg.answer(_HELP_TEXT)


@router.message(Command("trade_open"), F.chat.type == "private")
async def handle_trade_open(msg: Message) -> None:
  """List currently-open signals so stale ones can be spotted and closed."""
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


@router.message(Command("trade_active"), F.chat.type == "private")
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


@router.message(Command("trade_close"), F.chat.type == "private")
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


@router.message(Command("trade_tp"), F.chat.type == "private")
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


@router.message(Command("trade_uncclose", "trade_restore"), F.chat.type == "private")
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


@router.message(Command("trade_cancel"), F.chat.type == "private")
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


@router.message(Command("trade_delete"), F.chat.type == "private")
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


@router.message(Command("trade_sl"), F.chat.type == "private")
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


@router.message(Command("trade_reopen"), F.chat.type == "private")
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


@router.message(Command("trade_tag"), F.chat.type == "private")
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


@router.message(Command("trade_note"), F.chat.type == "private")
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


@router.message(Command("trade_review"), F.chat.type == "private")
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


@router.message(Command("trade_stats"), F.chat.type == "private")
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


async def _run_chart_analysis(
  photos: list,
  first_msg: Message,
  thinking: Message,
) -> None:
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
    await send_with_retry(f"📊 <b>Chart Analysis</b>\n\n{analysis_html}")
    await first_msg.answer("✅ Pushed to channel.")
  except Exception as e:
    log.warning("Could not push chart analysis to channel: %s", e)
    await first_msg.answer("⚠️ Could not push to channel.")


@router.message(F.chat.type == "private", F.photo)
async def handle_chart_photo(msg: Message) -> None:
  """Analyse chart screenshot(s) sent as DM photo(s), reply in DM and push."""
  if not _is_owner(msg):
    return

  user_id = msg.from_user.id
  photo = msg.photo[-1]

  is_leader = user_id not in _photo_buffer
  if is_leader:
    _photo_buffer[user_id] = {
      "photos": [], "first_msg": msg, "thinking": None, "task": None,
    }

  entry = _photo_buffer[user_id]
  entry["photos"].append(photo)

  old_task = entry.get("task")
  if old_task and not old_task.done():
    old_task.cancel()
  entry["task"] = asyncio.create_task(_flush_photo_buffer(user_id))

  if is_leader:
    thinking = await msg.answer("🔍 Collecting charts…")
    if user_id in _photo_buffer:
      _photo_buffer[user_id]["thinking"] = thinking
