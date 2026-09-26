"""Shared XAU shallow/deep entry-ladder and risk-leg calculator.

The single reviewed specification lives in ``contracts/autotrade/xau-ladder-spec.json`` and is
held identical by three implementations, each pinned to the same hand-computed cases:

- Manual Algo: ``ctrader-engine/src/AutoTradeEngine.cs`` (``ManualEntryLegPrices``,
  ``ManualAlgoRiskLegPrice``, ``ManualAlgoRiskLegVolume``, ``ManualEntryLegRatios``);
- the Auto Algo executor's injected risk leg: ``ctrader-engine/src/TradePlanRuntime.cs``
  (``ReactionRiskLeg*``, the same constants declared a second time);
- this module.

What this is **not**: the Auto Algo *entry* ladder (``execution_route._scale_ladder_legs`` /
``_deeper_second_leg``: leg 2 one ATR step deeper, capped at the far edge, ratios from the
equity table) is a different owner-defined rule. Nothing here unifies the two; a test pins
where they differ so any future change is deliberate. This module is still not wired into any
execution path, and the risk leg is not activated for Go-origin plans (see
``analysis.technical_authority.go_origin_risk_leg_enabled``).

All arithmetic is decimal, matching the C# ``decimal`` behaviour: prices are rounded to the
instrument's digits, midpoints away from zero. Binary floats would round 4101.005 down.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Verified against ctrader-engine/src/AutoTradeEngine.cs and pinned by the shared spec.
ENTRY_LEG_RATIOS: tuple[float, float] = (0.8, 0.2)
RISK_LEG_LOTS_DEFAULT = 0.05
RISK_LEG_LOTS_BELOW_EQUITY_FLOOR = 0.02
RISK_LEG_EQUITY_FLOOR = 1_000.0
RISK_LEG_PIPS_FROM_STOP = 15.0
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


def risk_leg_price(direction: str, stop_loss: float, pip_size: float, *, digits: int = XAU_DIGITS) -> float:
  """Rests RISK_LEG_PIPS_FROM_STOP pips from the stop, on the entry side."""
  offset = _d(RISK_LEG_PIPS_FROM_STOP) * _d(pip_size)
  stop = _d(stop_loss)
  return round_price(stop + offset if direction.upper() == "BUY" else stop - offset, digits)


def risk_leg_volume(equity: float) -> float:
  """Equity-tiered (live account equity, not balance) fixed lot size, never scaled from the main
  ladder's own sizing."""
  return RISK_LEG_LOTS_BELOW_EQUITY_FLOOR if equity < RISK_LEG_EQUITY_FLOOR else RISK_LEG_LOTS_DEFAULT


def volume_for_lots(lots: float, *, lot_size: int, min_volume: int, step_volume: int, max_volume: int) -> int:
  """Broker volume for a lot size: floor to the step, 0 when below the minimum or above the
  maximum. Ports ``VolumePlanner.VolumeForLots`` exactly."""
  lots_d = _d(lots)
  if lots_d <= 0 or lot_size <= 0 or min_volume <= 0 or step_volume <= 0 or max_volume < min_volume:
    return 0
  raw = int((lots_d * lot_size).to_integral_value(rounding="ROUND_FLOOR"))
  if raw > max_volume:
    return 0
  stepped = raw // step_volume * step_volume
  return stepped if stepped >= min_volume else 0


def equity_table_lots(equity: float) -> float:
  """The owner's equity-table lots for non-FX instruments, ports ``VolumePlanner.LotsForEquity``
  (bands with deliberate discontinuities; below $200 no trade). Pinned to the same hand-computed
  cases as the C# original in the shared spec."""
  e = _d(equity)
  if e < 200:
    lots = Decimal(0)
  elif e >= 5_000:
    lots = Decimal("0.30")
  elif e >= 3_000:
    lots = Decimal("0.25") + (e - 3_000) * Decimal("0.05") / 2_000
  elif e >= 2_000:
    lots = Decimal("0.15")
  elif e > 1_000:
    lots = Decimal("0.12")
  elif e >= 600:
    lots = Decimal("0.10")
  else:
    lots = Decimal("0.02") + (e - 200) * Decimal("0.04") / 700
  return float(lots.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class LadderLeg:
  price: float
  volume: float                 # lots
  is_risk_leg: bool = False


def build_ladder(
  direction: str,
  zone_low: float,
  zone_high: float,
  stop_loss: float,
  *,
  pip_size: float,
  total_entry_volume: float,
  equity: float,
  include_risk_leg: bool = True,
) -> tuple[LadderLeg, ...]:
  """The full leg set (Shallow, Deep, optionally the risk leg) for one XAU group. Pure
  computation: no broker-minimum collapse, no order submission."""
  prices = entry_leg_prices(direction, zone_low, zone_high, stop_loss)
  shallow_ratio, deep_ratio = ENTRY_LEG_RATIOS
  legs = [
    LadderLeg(price=prices.shallow, volume=total_entry_volume * shallow_ratio),
    LadderLeg(price=prices.deep, volume=total_entry_volume * deep_ratio),
  ]
  if include_risk_leg:
    legs.append(LadderLeg(
      price=risk_leg_price(direction, stop_loss, pip_size),
      volume=risk_leg_volume(equity),
      is_risk_leg=True,
    ))
  return tuple(legs)


def worst_case_group_risk(legs: tuple[LadderLeg, ...], stop_loss: float) -> float:
  """Sum of every leg's own volume * its own distance to the stop: the loss (in lot-price units)
  if every resting leg fills and the group then stops out, including the risk leg."""
  return sum(leg.volume * abs(leg.price - stop_loss) for leg in legs)


def worst_case_group_loss(
  legs: tuple[LadderLeg, ...], stop_loss: float, *, pip_size: float, pip_value_per_lot: float,
) -> float:
  """The same worst case in account currency: lots x pips-to-stop x pip value per lot per leg."""
  return sum(
    float(_d(leg.volume) * (abs(_d(leg.price) - _d(stop_loss)) / _d(pip_size)) * _d(pip_value_per_lot))
    for leg in legs
  )
