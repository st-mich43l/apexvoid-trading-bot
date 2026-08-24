"""Defensive checks for contradictory terminal broker events."""

from __future__ import annotations

from collections.abc import Mapping

from app.core.symbols import pip_for


def _number(value: object) -> float | None:
  try:
    return float(value) if value is not None else None
  except (TypeError, ValueError):
    return None


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
  try:
    tolerance = 2.0 * pip_for(str(event.get("symbol") or "XAU"))
  except Exception:
    tolerance = 0.0
  if direction == "BUY":
    return exit_price <= stop + tolerance
  return exit_price >= stop - tolerance


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
