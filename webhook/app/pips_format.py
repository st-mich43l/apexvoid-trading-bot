"""Shared pip-accounting conventions and result decorations."""

from app.symbols import pip_for


def rr_entry(sig: dict) -> float:
  """Return the conservative entry edge used for risk and reward."""
  entry = float(sig["entry"])
  entry_end = sig.get("entry_end")
  entry_high = entry if entry_end is None else float(entry_end)
  return entry if sig["action"] == "SELL" else entry_high


def pips_between(sig: dict, price: float) -> int:
  """Measure absolute pips from the same edge advertised on the card."""
  pip = pip_for(sig.get("symbol", "XAU"))
  return round(abs(float(price) - rr_entry(sig)) / pip)


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
