"""Shared trade lifecycle executors used by DM and channel adapters."""

import time
from html import escape

from app.core.config import settings
from app.persistence.store import (
  cancel_manual_signal,
  close_leg,
  delete_manual_signal,
  get_manual_signal,
  get_open_signals,
  get_signal_cluster,
  mark_filled,
  set_note,
  signal_root,
  store_manual_signal,
  store_pips,
  undo_last_close_leg,
  update_setup,
  update_sl,
)
from app.signals.broadcast import broadcast_entry, delete_posts, fanout_update
from app.bot.keyboards import build_tp_close_kb
from app.signals.pips_format import wing_icons
from app.persistence.redis_state import clear_sl_alert, mark_tp_alert
from app.core.symbols import SYMBOLS, channel_for_symbol


def _display_seq(row: dict) -> int:
  return row.get("daily_seq") or row["id"]


def _price(value: float, symbol: str) -> str:
  digits = int(SYMBOLS[symbol]["digits"])
  return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def _win_wings(pips: int) -> str:
  icons = wing_icons(pips)
  return f" {icons}" if icons else ""


async def do_active(ctx: dict) -> dict:
  row = await mark_filled(ctx["sid"])
  if row is None:
    return {"action": "active", "ok": False, "error": "not_pending"}
  return {
    "action": "active",
    "ok": True,
    "row": row,
    "reply_to": row.get("channel_message_id") or ctx.get("reply_to"),
  }


async def _execute_close(
  sid: int,
  symbol: str,
  pips: int,
  frac: float | None,
  reply_to: int | None = None,
  tp_number: int | None = None,
) -> dict:
  """The actual Postgres booking for a close - shared by the direct
  (non-algo, or algo-not-yet-filled) path and the broker-confirmed algo
  path (app.signals.manual_execution, once the real close/SL/TP fires).

  ``tp_number``, when the broker-confirmed close is attributable to a
  specific configured target (app.signals.manual_execution._handle_take_
  profit already resolves this from the fill's target_pips), is surfaced
  in the result so render_result can label the channel card the same way
  a watcher-detected TP does, instead of a bare "booked X%".
  """
  row = await close_leg(sid, pips, frac)
  if row is None:
    return {"action": "close", "ok": False, "error": "not_open"}
  result = {
    "action": "close",
    "ok": True,
    "row": row,
    "pips": pips,
    "reply_to": row.get("channel_message_id") or reply_to,
    "tp_number": tp_number,
  }
  if row.get("closed"):
    net = row["net"]
    await store_pips(
      "+" if net >= 0 else "-",
      abs(net),
      message_id=row.get("channel_message_id"),
      chat_id=channel_for_symbol(symbol),
      signal_id=sid,
    )
  return result


async def _maybe_defer_close_to_broker(
  sid: int,
  frac: float | None,
  reply_to: int | None,
) -> dict | None:
  """Route to the real broker instead of Postgres when this signal is an
  algo-armed/filled manual /algo signal - the entire reason this feature
  exists (today /trade_close only ever mutated Postgres/Telegram, never a
  real position). Returns None to fall through to the direct path
  unchanged for every non-algo (or not-yet-filled) signal.
  """
  full = await get_manual_signal(sid)
  if full is None or full.get("execution_mode") != "algo":
    return None
  if full.get("execution_status") != "filled":
    return None
  position_id = full.get("broker_position_id")
  if position_id is None:
    return None
  from app.signals import manual_execution
  await manual_execution.request_close(sid, int(position_id), frac=frac)
  return {
    "action": "close",
    "ok": True,
    "pending": True,
    "row": full,
    "reply_to": full.get("channel_message_id") or reply_to,
  }


async def do_close(ctx: dict) -> dict:
  pips = 0 if ctx.get("frac") == "be" else int(ctx["pips"])
  frac = None if ctx.get("frac") == "be" else ctx.get("frac")
  pending = await _maybe_defer_close_to_broker(
    ctx["sid"], frac, ctx.get("reply_to"),
  )
  if pending is not None:
    return pending
  return await _execute_close(ctx["sid"], ctx["symbol"], pips, frac, ctx.get("reply_to"))


async def do_uncclose(ctx: dict) -> dict:
  signal = await get_manual_signal(ctx["sid"])
  if (
    signal is None
    or signal.get("symbol", "XAU") != ctx["symbol"]
  ):
    return {"action": "uncclose", "ok": False, "error": "not_found"}
  row = await undo_last_close_leg(ctx["sid"])
  if row is None:
    return {"action": "uncclose", "ok": False, "error": "not_restorable"}
  return {
    "action": "uncclose",
    "ok": True,
    "row": row,
    "sid": row["id"],
    "remaining": row["remaining"],
    "reply_to": row.get("channel_message_id") or ctx.get("reply_to"),
  }


async def _execute_sl(
  sid: int,
  price: float,
  is_be: bool,
  reply_to: int | None = None,
) -> dict:
  """The actual Postgres SL move - shared by the direct (non-algo, or
  algo-not-yet-filled) path and the broker-confirmed algo path
  (app.signals.manual_execution, once AmendPositionStopLossAsync confirms).
  """
  row = await update_sl(sid, price)
  if row is None:
    return {"action": "sl", "ok": False, "error": "not_open"}
  await clear_sl_alert(sid)
  return {
    "action": "sl",
    "ok": True,
    "row": row,
    "price": price,
    "is_be": is_be,
    "reply_to": row.get("channel_message_id") or reply_to,
  }


async def _maybe_defer_sl_to_broker(
  sid: int,
  price: float,
  is_be: bool,
  reply_to: int | None,
) -> dict | None:
  full = await get_manual_signal(sid)
  if full is None or full.get("execution_mode") != "algo":
    return None
  if full.get("execution_status") != "filled":
    return None
  position_id = full.get("broker_position_id")
  if position_id is None:
    return None
  from app.signals import manual_execution
  await manual_execution.request_move_sl(sid, int(position_id), price)
  return {
    "action": "sl",
    "ok": True,
    "pending": True,
    "row": full,
    "price": price,
    "is_be": is_be,
    "reply_to": full.get("channel_message_id") or reply_to,
  }


async def do_sl(ctx: dict) -> dict:
  signals = await get_open_signals(ctx["symbol"])
  signal = next(
    (row for row in signals if row["id"] == ctx["sid"]),
    None,
  )
  if signal is None:
    return {"action": "sl", "ok": False, "error": "not_open"}
  target = str(ctx["sl"])
  is_be = target.lower() == "be"
  if is_be:
    entry_end = signal.get("entry_end")
    if entry_end is None:
      entry_end = signal["entry"]
    price = (signal["entry"] + entry_end) / 2
  else:
    price = float(target)
  pending = await _maybe_defer_sl_to_broker(
    ctx["sid"], price, is_be, ctx.get("reply_to"),
  )
  if pending is not None:
    return pending
  return await _execute_sl(ctx["sid"], price, is_be, ctx.get("reply_to"))


async def _execute_cancel(sid: int, reply_to: int | None = None) -> dict:
  """The actual Postgres cancel - shared by the direct (non-algo) path and
  the broker-confirmed algo path (app.signals.manual_execution, once
  CancelPendingOrderAsync confirms).
  """
  row = await cancel_manual_signal(sid)
  if row is None:
    return {"action": "cancel", "ok": False, "error": "not_open"}
  return {
    "action": "cancel",
    "ok": True,
    "row": row,
    "reply_to": row.get("channel_message_id") or reply_to,
  }


async def _maybe_defer_cancel_to_broker(
  sid: int,
  reply_to: int | None,
) -> dict | None:
  """Only an ARMED (not yet filled) manual algo signal defers here - once
  filled, /trade_cancel is not a broker verb (the position is open; the
  owner wants /trade_close instead), so a filled/error/cancelled signal
  falls through unchanged.
  """
  full = await get_manual_signal(sid)
  if full is None or full.get("execution_mode") != "algo":
    return None
  if full.get("execution_status") != "armed":
    return None
  intent_id = full.get("execution_intent_id")
  if not intent_id:
    return None
  from app.signals import manual_execution
  await manual_execution.request_cancel(intent_id)
  return {
    "action": "cancel",
    "ok": True,
    "pending": True,
    "row": full,
    "reply_to": full.get("channel_message_id") or reply_to,
  }


async def do_cancel(ctx: dict) -> dict:
  pending = await _maybe_defer_cancel_to_broker(ctx["sid"], ctx.get("reply_to"))
  if pending is not None:
    return pending
  return await _execute_cancel(ctx["sid"], ctx.get("reply_to"))


async def do_delete(ctx: dict) -> dict:
  signal = await get_manual_signal(ctx["sid"])
  if signal is None or signal.get("symbol", "XAU") != ctx["symbol"]:
    return {"action": "delete", "ok": False, "error": "not_found"}
  result = await delete_manual_signal(ctx["sid"])
  if result is None:
    return {"action": "delete", "ok": False, "error": "not_found"}
  if result.get("error") == "has_rounds":
    return {"action": "delete", "ok": False, "error": "has_rounds"}
  await delete_posts(result.get("posts") or [])
  return {
    "action": "delete",
    "ok": True,
    "row": result,
    "seq": _display_seq(result),
  }


async def do_reopen(ctx: dict) -> dict:
  source = await get_manual_signal(ctx["sid"])
  if source is None or source.get("symbol", "XAU") != ctx["symbol"]:
    return {"action": "reopen", "ok": False, "error": "not_found"}
  # Re-entry is for a round that already ended: reopening a still-open signal
  # would run two live trades in the same zone (both tracked by the watcher).
  if source.get("status") == "open":
    return {
      "action": "reopen", "ok": False, "error": "still_open", "source": source,
    }
  cluster = await get_signal_cluster(ctx["sid"])
  entry = source["entry"]
  entry_end = source.get("entry_end")
  override = ctx.get("entry_override")
  if override:
    entry, entry_end = sorted(override)
  if entry_end is None:
    entry_end = entry
  # Inherit the ORIGINAL stop, not a moved one (e.g. after TP1 → SL to BE),
  # otherwise the reopened round starts with its stop inside the entry zone.
  original_sl = (
    source["original_sl"]
    if source.get("original_sl") is not None
    else source["sl"]
  )
  rec = await store_manual_signal(
    ts=int(time.time()),
    action=source["action"],
    entry=entry,
    entry_end=entry_end,
    sl=original_sl,
    tps=source["tps"],
    parent_id=signal_root(source),
    setup_type=source.get("setup_type"),
    confluence=source.get("confluence"),
    symbol=ctx["symbol"],
    visibility=source.get("visibility", "both"),
    # Bug fix: without this the reopened round always silently reverted to
    # 'notify' even when the parent round was armed 'algo' - a reopen keeps
    # the parent's execution mode, it does not need the owner to re-suffix
    # / algo by hand.
    execution_mode=source.get("execution_mode", "notify"),
  )
  return {
    "action": "reopen",
    "ok": True,
    "source": source,
    "record": rec,
    "entry": entry,
    "entry_end": entry_end,
    "round": len(cluster) + 1,
    "reply_to": None,
  }


async def do_tag(ctx: dict) -> dict:
  if not await update_setup(ctx["sid"], ctx["setup"], ctx.get("stars")):
    return {"action": "tag", "ok": False, "error": "not_found"}
  return {
    "action": "tag",
    "ok": True,
    "seq": ctx["seq"],
    "sid": ctx["sid"],
    "setup": ctx["setup"],
    "stars": ctx.get("stars"),
    "reply_to": ctx.get("reply_to"),
  }


async def do_note(ctx: dict) -> dict:
  if not await set_note(ctx["sid"], ctx["text"]):
    return {"action": "note", "ok": False, "error": "not_found"}
  return {
    "action": "note",
    "ok": True,
    "seq": ctx["seq"],
    "sid": ctx["sid"],
    "reply_to": ctx.get("reply_to"),
  }


async def do_tp(ctx: dict) -> dict:
  """Build a notify-only TP event without changing trade accounting."""
  signal = await get_manual_signal(ctx["sid"])
  tp_number = int(ctx["tp_number"])
  if (
    signal is None
    or signal.get("status") != "open"
    or signal.get("symbol", "XAU") != ctx["symbol"]
  ):
    return {"action": "tp", "ok": False, "error": "not_open"}
  if tp_number < 1 or tp_number > len(signal.get("tps") or []):
    return {"action": "tp", "ok": False, "error": "invalid_tp"}
  await mark_tp_alert(signal["id"], tp_number, int(ctx["pips"]))
  return {
    "action": "tp",
    "ok": True,
    "sid": signal["id"],
    "seq": _display_seq(signal),
    "tp_number": tp_number,
    "pips": int(ctx["pips"]),
  }


def render_result(
  result: dict,
  symbol: str,
  tier: str = "vip",
) -> str:
  action = result["action"]
  if not result.get("ok"):
    if result.get("error") == "has_rounds":
      return (
        "⚠️ Has re-entry rounds — cancel it, or delete the later rounds first."
      )
    if result.get("error") == "still_open":
      seq = _display_seq(result["source"])
      return (
        f"⚠️ #{seq} still open — close or cancel it before reopening"
      )
    return "⚠️ Signal not found or action is no longer valid."
  if action == "active":
    seq = f"#{_display_seq(result['row'])} " if tier == "vip" else ""
    return f"🟢 {seq}active — order filled"
  if action == "cancel":
    seq = f"#{_display_seq(result['row'])} " if tier == "vip" else ""
    if result.get("pending"):
      return f"⏳ {seq}cancel requested — awaiting broker confirmation"
    return f"❌ {seq}cancelled"
  if action == "delete":
    seq = f"#{result['seq']} " if tier == "vip" else ""
    return f"🗑 {seq}deleted"
  if action == "uncclose":
    seq = f"#{_display_seq(result['row'])} " if tier == "vip" else ""
    remaining = int(round(float(result["remaining"]) * 100))
    suffix = f" · remaining {remaining}%" if remaining < 100 else ""
    return f"♻️ {seq}restored — trade still running{suffix}"
  if action == "tp":
    seq = f"#{result['seq']} " if tier == "vip" else ""
    if tier == "public" and not settings.public_show_pips:
      return f"🎯 TP{result['tp_number']} hit"
    return (
      f"🎯 {seq}TP{result['tp_number']} "
      f"+{result['pips']} pips{_win_wings(result['pips'])}"
    )
  if action == "sl":
    seq = f"#{_display_seq(result['row'])} " if tier == "vip" else ""
    if result.get("pending"):
      return f"⏳ {seq}stop-loss move requested — awaiting broker confirmation"
    suffix = " (BE)" if result["is_be"] else ""
    return (
      f"🛡 {seq}move SL to "
      f"{_price(result['price'], symbol)}{suffix}"
    )
  if action == "close":
    row = result["row"]
    seq = (
      f"#{row.get('daily_seq') or '?'} "
      if tier == "vip"
      else ""
    )
    if result.get("pending"):
      return f"⏳ {seq}close requested — awaiting broker confirmation"
    if row.get("error") == "exceeds_remaining":
      remaining = int(round(row["remaining"] * 100))
      return f"⚠️ {seq}only has {remaining}% remaining to close"
    tp_number = result.get("tp_number")
    tp_label = f"TP{tp_number} " if tp_number else ""
    if row["closed"]:
      net = row["net"]
      if tier == "public":
        if net > 0:
          detail = (
            f"+{net} pips win{_win_wings(net)}"
            if settings.public_show_pips
            else "win"
          )
          return f"✅ {tp_label}closed — {detail}"
        if net < 0:
          detail = (
            f"{net} pips loss"
            if settings.public_show_pips
            else "loss"
          )
          return f"🛑 {tp_label}closed — {detail}"
        return f"➖ {tp_label}closed — breakeven"
      icon = "✅" if net >= 0 else "🛑"
      sign = "+" if net >= 0 else ""
      suffix = _win_wings(net) if net > 0 else ""
      return f"{icon} {seq}{tp_label}closed — net {sign}{net} pips{suffix}"
    if tier == "public" and not settings.public_show_pips:
      return f"🎯 {tp_label}partial booked"
    booked = int(round(row["frac"] * 100))
    remaining = int(round(row["remaining"] * 100))
    return (
      f"🎯 {seq}{tp_label}booked {booked}% · {result['pips']:+d} pips"
      f"{_win_wings(result['pips']) if result['pips'] > 0 else ''} · "
      f"remaining {remaining}%"
    )
  if action == "reopen":
    source = result["source"]
    rec = result["record"]
    tps = "/".join(_price(tp, symbol) for tp in source["tps"])
    seq = f"#{rec['daily_seq']} · " if tier == "vip" else ""
    source_seq = (
      f" from #{_display_seq(source)}"
      if tier == "vip"
      else ""
    )
    return (
      f"♻️ <b>{seq}round {result['round']}{source_seq}</b> — "
      f"{source['action']} "
      f"{_price(result['entry'], symbol)}–"
      f"{_price(result['entry_end'], symbol)} / "
      f"🛡 {_price(source['sl'], symbol)} / TP {tps}"
    )
  if action == "tag":
    stars = f" {'⭐' * result['stars']}" if result.get("stars") else ""
    seq = f"#{result['seq']} " if tier == "vip" else ""
    return (
      f"🏷 {seq}tagged "
      f"{escape(result['setup'])}{stars}"
    )
  seq = f"#{result['seq']} " if tier == "vip" else ""
  return f"📝 {seq}note saved"


async def post_result(result: dict, symbol: str) -> str:
  """Render and deliver one result through persisted fan-out paths."""
  text = render_result(result, symbol, "vip")
  if not result.get("ok"):
    return text
  if result["action"] in {"note", "tag", "delete"}:
    return text
  if result["action"] == "reopen":
    sig = await get_manual_signal(result["record"]["id"])
    await broadcast_entry(
      sig,
      lambda tier: render_result(result, symbol, tier),
    )
    return text
  signal_id = (
    result["row"]["id"]
    if "row" in result
    else result["sid"]
  )
  sig = await get_manual_signal(signal_id)
  if (
    result["action"] == "close"
    and result.get("row", {}).get("error")
  ):
    await fanout_update(
      sig,
      lambda tier: text if tier == "vip" else None,
    )
    return text
  markup_fn = None
  if result["action"] == "tp":
    # Same owner-only Close button the watcher attaches to auto TP alerts.
    sid_, tp_, pips_ = result["sid"], result["tp_number"], result["pips"]
    markup_fn = (
      lambda tier: build_tp_close_kb(sid_, tp_, pips_) if tier == "vip" else None
    )
  await fanout_update(
    sig,
    lambda tier: render_result(result, symbol, tier),
    markup_fn=markup_fn,
  )
  return text
