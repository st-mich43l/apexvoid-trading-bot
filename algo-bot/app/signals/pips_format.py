"""Shared pip-accounting conventions and result decorations."""

from __future__ import annotations

from app.core.symbols import pip_for


def rr_entry(sig: dict) -> float:
  """Return the conservative entry edge used for risk and reward."""
  entry = float(sig["entry"])
  entry_end = sig.get("entry_end")
  entry_high = entry if entry_end is None else float(entry_end)
  return entry if sig["action"] == "SELL" else entry_high


def actual_entry(sig: dict) -> float:
  """Prefer broker fill when present; otherwise the advertised zone edge."""
  fill = sig.get("broker_fill_price")
  if fill is not None:
    try:
      value = float(fill)
    except (TypeError, ValueError):
      value = None
    else:
      if value == value:  # not NaN
        return value
  return rr_entry(sig)


def entry_zone_bounds(sig: dict) -> tuple[float, float]:
  entry = float(sig["entry"])
  entry_end = sig.get("entry_end")
  entry_end = entry if entry_end is None else float(entry_end)
  return sorted((entry, entry_end))


def pips_between(sig: dict, price: float) -> int:
  """Absolute pips from the advertised card edge to ``price``.

  Watcher/TP alerts keep using the zone edge so the keyboard matches the
  published card. Booking after a real broker fill must use
  :func:`signed_result_pips` instead.
  """
  pip = pip_for(sig.get("symbol", "XAU"))
  return round(abs(float(price) - rr_entry(sig)) / pip)


def signed_result_pips(sig: dict, exit_price: float) -> int:
  """Signed pips from actual entry (fill or zone edge) to the exit price."""
  entry = actual_entry(sig)
  exit_px = float(exit_price)
  pip = pip_for(sig.get("symbol", "XAU"))
  if pip <= 0:
    return 0
  if sig["action"] == "BUY":
    return round((exit_px - entry) / pip)
  return round((entry - exit_px) / pip)


def sl_result_pips(sig: dict, fill_price: float) -> int:
  """Signed stop/close result pips.

  When a broker fill exists, distance is measured from that fill.
  Without a fill, an exit still inside the advertised entry zone is BE (0).
  """
  fill = float(fill_price)
  if sig.get("broker_fill_price") is not None:
    return signed_result_pips(sig, fill)
  entry_low, entry_high = entry_zone_bounds(sig)
  if entry_low <= fill <= entry_high:
    return 0
  return signed_result_pips(sig, fill)


def legs_net_pips(legs: list[dict]) -> int:
  """Volume-fraction weighted net across booked close legs."""
  if not legs:
    return 0
  return round(sum(float(leg["frac"]) * int(leg["pips"]) for leg in legs))


def wing_icons(pips: int) -> str:
  """Return dollar-wing icons for positive pip wins.

  Old channel rule:
  - 1–100 pips: 1 icon
  - 101–299 pips: 2 icons
  - 300+ pips: 3 icons
  """
  value = abs(int(pips))
  if value <= 0:
    return ""
  if value <= 100:
    return "💸"
  if value < 300:
    return "💸💸"
  return "💸💸💸"
