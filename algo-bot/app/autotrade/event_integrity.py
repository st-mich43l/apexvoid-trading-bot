"""Defensive checks for contradictory terminal broker events."""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.core.symbols import pip_for

_PROTECTIVE_STOP_NEAR_PIPS = 5.0
_BE_CLOSE_RE = re.compile(r"(?i)\bbreak[\s-]?even\b")


def _number(value: object) -> float | None:
  try:
    return float(value) if value is not None else None
  except (TypeError, ValueError):
    return None


def _protective_stop_tolerance(symbol: str) -> float:
  try:
    pip = float(pip_for(symbol))
  except Exception:
    pip = 0.1
  return max(pip * _PROTECTIVE_STOP_NEAR_PIPS, pip * 2.0)


def terminal_loss_at_protective_stop(event: Mapping[str, object]) -> bool:
  """True when a negative terminal fill is at the declared stop."""
  realized = _number(
    event.get("group_realized_pips")
    if event.get("group_realized_pips") is not None
    else event.get("leg_realized_pips")
  )
  exit_price = _number(event.get("price"))
  stop = _number(
    event.get("stop_loss")
    if event.get("stop_loss") is not None
    else event.get("stop_price")
  )
  direction = str(event.get("direction") or "").upper()
  if (
    realized is None
    or realized >= 0
    or exit_price is None
    or stop is None
    or direction not in {"BUY", "SELL"}
  ):
    return False
  tolerance = _protective_stop_tolerance(str(event.get("symbol") or "XAU"))
  if direction == "BUY":
    return exit_price <= stop + tolerance
  return exit_price >= stop - tolerance


def close_at_protective_stop(event: Mapping[str, object]) -> bool:
  """True when exit price sits on the protective stop (win/loss/BE)."""
  exit_price = _number(event.get("price"))
  stop = _number(
    event.get("stop_loss")
    if event.get("stop_loss") is not None
    else event.get("stop_price")
  )
  if exit_price is None or stop is None:
    return False
  tolerance = _protective_stop_tolerance(str(event.get("symbol") or "XAU"))
  return abs(exit_price - stop) <= tolerance


def close_at_breakeven(event: Mapping[str, object], message: str | None = None) -> bool:
  """True when the close message or realized pips indicate a BE stop fill."""
  text = str(message if message is not None else event.get("message") or "")
  if _BE_CLOSE_RE.search(text):
    return True
  if event.get("break_even_applied") is True:
    pips = _number(event.get("group_realized_pips"))
    return pips is not None and abs(pips) <= _PROTECTIVE_STOP_NEAR_PIPS
  return False


def contradictory_archived_tp(
  event: Mapping[str, object],
  message: str | None = None,
) -> bool:
  """Reject a TP archive claim that has no booked partial and closed at SL."""
  text = str(message if message is not None else event.get("message") or "")
  if "highest tp archived" not in text.casefold():
    return False
  if str(event.get("previous_state") or "").casefold() == "partially_closed":
    return False
  return terminal_loss_at_protective_stop(event)
