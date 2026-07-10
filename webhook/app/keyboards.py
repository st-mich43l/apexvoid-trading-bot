"""Inline keyboard builders shared by watcher, trade ops, and callbacks."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


_PARTIAL_STEPS = (25, 50, 75, 90)


def build_tp_close_kb(sid: int, tp: int, pips: int) -> InlineKeyboardMarkup:
  """Single Close button for a fresh (or partially-booked) VIP TP alert."""
  return InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(
      text="🔒 Close", callback_data=f"c0:{sid}:{tp}:{pips}"
    ),
  ]])


def build_close_kb(sid: int, tp: int, pips: int) -> InlineKeyboardMarkup:
  return build_tp_close_kb(sid, tp, pips)


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
