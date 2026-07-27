"""Canonical executor entry-distance gate shared with the C# engine.

Adaptive strategy drift (observation tolerance) is a separate concept and must
never authorise Algo bot READY. Only this hard cap may.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class EntryDistanceMeasurement:
  executable_quote: Decimal
  zone_low: Decimal
  zone_high: Decimal
  distance: Decimal
  distance_pips: Decimal
  cap_pips: Decimal
  within_cap: bool

  def as_measured(self) -> dict[str, float | bool]:
    return {
      "executor_quote": float(self.executable_quote),
      "zone_low": float(self.zone_low),
      "zone_high": float(self.zone_high),
      "executor_distance_raw": float(self.distance),
      "executor_distance_pips": float(self.distance_pips),
      "executor_limit_pips": float(self.cap_pips),
      "executor_within_cap": self.within_cap,
    }


def _decimal(value: Any, name: str) -> Decimal:
  try:
    result = Decimal(str(value))
  except (InvalidOperation, TypeError, ValueError) as exc:
    raise ValueError(f"{name} is invalid") from exc
  if not result.is_finite():
    raise ValueError(f"{name} is invalid")
  return result


def zone_distance(
  executable_quote: Decimal,
  zone_low: Decimal,
  zone_high: Decimal,
) -> Decimal:
  if executable_quote < zone_low:
    return zone_low - executable_quote
  if executable_quote > zone_high:
    return executable_quote - zone_high
  return Decimal("0")


def measure_entry_distance(
  *,
  direction: str,
  bid: Any,
  ask: Any,
  zone_low: Any,
  zone_high: Any,
  pip_size: Any,
  cap_pips: Any,
  mid_fallback: Any | None = None,
) -> EntryDistanceMeasurement:
  """Mirror AutoTradeEngine live-quote distance: BUY=ask, SELL=bid."""
  side = str(direction or "").strip().upper()
  low = _decimal(zone_low, "zone_low")
  high = _decimal(zone_high, "zone_high")
  if high < low:
    low, high = high, low
  pip = _decimal(pip_size, "pip_size")
  if pip <= 0:
    raise ValueError("pip_size must be positive")
  cap = _decimal(cap_pips, "cap_pips")
  if side == "BUY":
    quote = _decimal(ask if ask is not None else mid_fallback, "ask")
  elif side == "SELL":
    quote = _decimal(bid if bid is not None else mid_fallback, "bid")
  else:
    raise ValueError("direction must be BUY or SELL")
  distance = zone_distance(quote, low, high)
  distance_pips = (distance / pip).quantize(
    Decimal("0.1"), rounding=ROUND_HALF_UP,
  )
  return EntryDistanceMeasurement(
    executable_quote=quote,
    zone_low=low,
    zone_high=high,
    distance=distance,
    distance_pips=distance_pips,
    cap_pips=cap,
    within_cap=distance_pips <= cap,
  )
