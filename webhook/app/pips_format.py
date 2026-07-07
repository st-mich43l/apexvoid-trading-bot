"""Shared formatting helpers for pip-result decorations."""


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
