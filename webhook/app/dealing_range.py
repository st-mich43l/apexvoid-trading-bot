"""Premium/discount dealing range from confirmed swings."""

from __future__ import annotations

from app.pa_types import DealingRange, Swing


def dealing_range(
  swings: list[Swing],
  price: float,
  eq_band: float = 0.10,
) -> DealingRange | None:
  pair = _bracketing_pair(swings, price) or _last_opposing_pair(swings)
  if pair is None:
    return None
  low, high = pair
  if high <= low:
    return None
  position = min(1.0, max(0.0, (float(price) - low) / (high - low)))
  eq = (high + low) / 2
  half_band = max(0.0, float(eq_band)) / 2
  if abs(position - 0.5) <= half_band:
    zone = "eq"
  elif position < 0.5:
    zone = "discount"
  else:
    zone = "premium"
  return DealingRange(high=high, low=low, eq=eq, position=position, zone=zone)


def _bracketing_pair(
  swings: list[Swing],
  price: float,
) -> tuple[float, float] | None:
  for i in range(len(swings) - 1, -1, -1):
    first = swings[i]
    for j in range(i - 1, -1, -1):
      second = swings[j]
      if first.kind == second.kind:
        continue
      low = min(float(first.price), float(second.price))
      high = max(float(first.price), float(second.price))
      if low <= price <= high:
        return low, high
  return None


def _last_opposing_pair(swings: list[Swing]) -> tuple[float, float] | None:
  if len(swings) < 2:
    return None
  last = swings[-1]
  for item in reversed(swings[:-1]):
    if item.kind == last.kind:
      continue
    return (
      min(float(last.price), float(item.price)),
      max(float(last.price), float(item.price)),
    )
  return None
