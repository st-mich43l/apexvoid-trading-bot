"""Owner-only inline callback handlers."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.persistence.store import get_manual_signal
from app.bot.keyboards import build_tp_close_kb, _partial_kb
from app.signals.parsing import _is_owner_cb
from app.core.symbols import symbol_for_channel
from app.signals.trade_ops import do_close, render_result

router = Router(name="callbacks")


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


async def _remaining_fraction(sid: int) -> float | None:
  """Open fraction still un-booked, or None if the signal is not open."""
  signal = await get_manual_signal(sid)
  if signal is None or signal.get("status") != "open":
    return None
  used = sum(float(leg["frac"]) for leg in (signal.get("legs") or []))
  return max(0.0, 1.0 - used)


@router.callback_query(F.data.startswith("c0:"))
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


@router.callback_query(F.data.startswith("cx:"))
async def handle_close_cancel(cb: CallbackQuery) -> None:
  if not _is_owner_cb(cb):
    await cb.answer("⛔ Owner only", show_alert=True)
    return
  _, sid_s, tp_s, pips_s = cb.data.split(":")
  await cb.message.edit_reply_markup(
    reply_markup=build_tp_close_kb(int(sid_s), int(tp_s), int(pips_s))
  )
  await cb.answer("Cancelled")


@router.callback_query(F.data.startswith("c1:"))
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
      f"{base}\n\n✅ <b>Closed</b> · total net <b>{sign}{abs(net)} pips</b>",
      reply_markup=None,
    )
    await cb.answer("Closed")
  else:
    rem_pct = round(row["remaining"] * 100)
    net_so_far = row.get("net")
    net_part = (
      f" · net so far <b>{net_so_far:+d}</b>"
      if isinstance(net_so_far, int)
      else ""
    )
    await cb.message.edit_text(
      f"{base}\n\n📊 Booked <b>{frac_pct}%</b> @ {pips:+d} pips"
      f"{net_part} · remaining <b>{rem_pct}%</b>",
      reply_markup=build_tp_close_kb(sid, tp, pips),
    )
    await cb.answer(f"Booked {frac_pct}%")
