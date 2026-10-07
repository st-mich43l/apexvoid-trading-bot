"""XAU shallow/deep entry-leg prices.

The reviewed specification lives in ``contracts/autotrade/xau-ladder-spec.json``. The Auto
Algo's XAU non-scalp zone ladder calls ``entry_leg_prices`` so its broker entry geometry is
identical to Manual Algo's (``ctrader-engine/src/AutoTradeEngine.cs``,
``ManualEntryLegPrices``): shallow at the near edge, deep at the zone midpoint. The executor
owns the leg volumes and the optional XAU risk leg (``TradePlanRuntime.cs``); its C# parity
test pins the same file.

All arithmetic is decimal, matching the C# ``decimal`` behaviour: prices are rounded to the
instrument's digits, midpoints away from zero. Binary floats would round 4101.005 down.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

XAU_DIGITS = 2


def _d(value: float | int | str | Decimal) -> Decimal:
  return value if isinstance(value, Decimal) else Decimal(repr(value) if isinstance(value, float) else str(value))


def round_price(value: float | Decimal, digits: int = XAU_DIGITS) -> float:
  """Round to ``digits`` decimals, midpoints away from zero (C# MidpointRounding.AwayFromZero)."""
  quantum = Decimal(1).scaleb(-digits)
  return float(_d(value).quantize(quantum, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class EntryLegPrices:
  shallow: float
  deep: float


def entry_leg_prices(
  direction: str,
  zone_low: float,
  zone_high: float,
  stop_loss: float,
  *,
  digits: int = XAU_DIGITS,
) -> EntryLegPrices:
  """Shallow (near edge, most likely to fill) and Deep (best price, least likely to fill).

  A real typed range (zone_low != zone_high) places Shallow at the near edge (High for BUY,
  Low for SELL) and Deep at the zone's own midpoint. A degenerate zone has no span to split,
  so Deep is the midpoint between the typed price and the stop: never past halfway to it.
  """
  direction = direction.upper()
  low, high, stop = _d(zone_low), _d(zone_high), _d(stop_loss)
  if low != high:
    shallow = high if direction == "BUY" else low
    deep = (low + high) / 2
  else:
    shallow = low
    deep = shallow + (stop - shallow) / 2
  return EntryLegPrices(shallow=round_price(shallow, digits), deep=round_price(deep, digits))


