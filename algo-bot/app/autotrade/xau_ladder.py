"""Shared XAU shallow/deep entry-ladder and risk-leg calculator.

Phase S12 (Unified Manual & Auto Algo Trading Experience) asks for one
shared XAU ladder/risk-leg mechanism instead of the two independent
implementations that exist today, both in C#:

- Manual Algo: ``ctrader-engine/src/AutoTradeEngine.cs``
  (``ManualEntryLegPrices``, ``ManualAlgoRiskLegPrice``,
  ``ManualAlgoRiskLegVolume``, ``ManualEntryLegRatios``).
- Auto Algo (TradePlan V8): a second, independently-declared copy of the
  identical risk-leg constants in ``ctrader-engine/src/TradePlanRuntime.cs``
  (``ReactionRiskLeg*``), injected at runtime - Python's own ``TradePlan``
  contract never declares this leg at all, so it is invisible to any
  group-risk accounting.

This module is the Python-side single source of truth those two should
eventually both defer to, ported line-for-line from the C# formulas above
(verified against the source, not re-derived) so it produces identical
numbers to what Manual Algo already places live. It is deliberately NOT
wired into any execution path yet: neither ``manual_execution.py`` nor
``trade_plan_builder.py`` calls this module. Using it to actually decide
what gets submitted to the broker is a separate, later step that needs a
shadow-comparison window against real Manual Algo fills first, the same
discipline this codebase already applied to the Go analysis-engine
rollout - a silently-wrong shared formula here would affect two live
order-placement paths at once instead of one.
"""

from __future__ import annotations

from dataclasses import dataclass

# Verified against ctrader-engine/src/AutoTradeEngine.cs (ManualEntryLegRatios,
# ManualAlgoRiskLegLotsDefault/LotsBelowEquityFloor/EquityFloor/PipsFromStop).
ENTRY_LEG_RATIOS: tuple[float, float] = (0.8, 0.2)
RISK_LEG_LOTS_DEFAULT = 0.05
RISK_LEG_LOTS_BELOW_EQUITY_FLOOR = 0.02
RISK_LEG_EQUITY_FLOOR = 1_000.0
RISK_LEG_PIPS_FROM_STOP = 15.0


@dataclass(frozen=True)
class EntryLegPrices:
  shallow: float
  deep: float


def entry_leg_prices(
  direction: str,
  zone_low: float,
  zone_high: float,
  stop_loss: float,
) -> EntryLegPrices:
  """Shallow (near edge, most likely to fill) and Deep (best price, least
  likely to fill) - ports ManualEntryLegPrices exactly.

  A real typed range (zone_low != zone_high) places Shallow at the near
  edge (High for BUY, Low for SELL) and Deep at the zone's own midpoint.
  A degenerate/single-price zone has no span to split, so Deep is the
  midpoint between the typed price and the stop - this never places a leg
  past the halfway point to the stop.
  """
  direction = direction.upper()
  if zone_low != zone_high:
    shallow = zone_high if direction == "BUY" else zone_low
    deep = (zone_low + zone_high) / 2.0
  else:
    shallow = zone_low
    deep = shallow + (stop_loss - shallow) / 2.0
  return EntryLegPrices(shallow=shallow, deep=deep)


def risk_leg_price(direction: str, stop_loss: float, pip_size: float) -> float:
  """Rests RISK_LEG_PIPS_FROM_STOP pips from the stop, on the entry side -
  ports ManualAlgoRiskLegPrice exactly. A deliberate "trade off" spot: if
  price nearly invalidates the setup before reversing, this leg still
  catches a much deeper (better) fill than Shallow/Deep ever would; if it
  keeps going instead, the small fixed size (see risk_leg_volume) caps the
  extra loss.
  """
  offset = RISK_LEG_PIPS_FROM_STOP * pip_size
  return stop_loss + offset if direction.upper() == "BUY" else stop_loss - offset


def risk_leg_volume(equity: float) -> float:
  """Equity-tiered (live account equity, not balance) fixed lot size, never
  scaled from the main ladder's own sizing - ports ManualAlgoRiskLegVolume
  exactly. A large account books the same small fixed size here as a
  smaller one above the floor.
  """
  return (
    RISK_LEG_LOTS_BELOW_EQUITY_FLOOR
    if equity < RISK_LEG_EQUITY_FLOOR
    else RISK_LEG_LOTS_DEFAULT
  )


@dataclass(frozen=True)
class LadderLeg:
  price: float
  volume: float
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
  """The full leg set (Shallow, Deep, optionally the risk leg) for one XAU
  group - the shape both Manual and Auto Algo place today via their own
  separate C# implementations. Pure computation only: no broker-minimum-
  volume collapse, no order submission. A caller enforcing a broker
  minimum still needs its own fallback to a single-entry order, exactly
  as VolumePlanner.SplitEntryVolume does today in C#.
  """
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
  """Sum of every leg's own volume * its own distance to the stop, i.e.
  the loss if every resting leg fills and the group then stops out -
  including the risk leg. Neither AutoTradeEngine.cs (excludes the risk
  leg from its own worst-case report) nor TradePlanRuntime.cs (never
  computes this at all - TradePlan.risk.max_group_risk_percent is
  deserialized but never read) does this full computation today; this is
  the piece Phase S12's "critical risk requirement" (spec S12 prompt §9)
  asks for before any group-risk gate can be real.
  """
  return sum(leg.volume * abs(leg.price - stop_loss) for leg in legs)
