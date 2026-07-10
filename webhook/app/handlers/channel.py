"""Anchored channel command handlers."""

from aiogram import F, Router
from aiogram.types import Message

from app.parsing import (
  _ACTIVE_RE,
  _CANCEL_RE,
  _CLOSE_RE,
  _CLOSEBE_RE,
  _NOTE_RE,
  _REOPEN_RE,
  _SL_RE,
  _TAG_RE,
  _parse_close,
  _resolve_any_sid,
  _resolve_sid,
)
from app.symbols import symbol_for_channel, tier_for_channel
from app.tg_core import delete_message, send_with_retry
from app.trade_ops import (
  do_active,
  do_cancel,
  do_close,
  do_note,
  do_reopen,
  do_sl,
  do_tag,
  post_result,
  render_result,
)

router = Router(name="channel")


async def _delete_command(msg: Message) -> None:
  try:
    await delete_message(msg.chat.id, msg.message_id)
  except Exception:
    pass


def _channel_symbol(msg: Message) -> str | None:
  if tier_for_channel(msg.chat.id) != "vip":
    return None
  return symbol_for_channel(msg.chat.id)


@router.channel_post(F.text.regexp(_ACTIVE_RE), F.reply_to_message)
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


@router.channel_post(
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


@router.channel_post(F.text.regexp(_CANCEL_RE), F.reply_to_message)
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


@router.channel_post(F.text.regexp(_SL_RE), F.reply_to_message)
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


@router.channel_post(F.text.regexp(_REOPEN_RE), F.reply_to_message)
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
    await send_with_retry(
      render_result(result, symbol, "vip"),
      reply_to=reply_to,
      chat_id=msg.chat.id,
    )
    await _delete_command(msg)
    return
  await post_result(result, symbol)
  await _delete_command(msg)


@router.channel_post(F.text.regexp(_TAG_RE), F.reply_to_message)
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


@router.channel_post(F.text.regexp(_NOTE_RE), F.reply_to_message)
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
