"""XAU shallow/deep entry-leg prices and the risk leg.

The reviewed specification lives in ``contracts/autotrade/xau-ladder-spec.json``. The Auto
Algo's XAU non-scalp zone ladder calls ``entry_leg_prices`` so its broker entry geometry is
identical to Manual Algo's (``ctrader-engine/src/AutoTradeEngine.cs``,
``ManualEntryLegPrices``): shallow at the near edge, deep at the zone midpoint. The planner
declares the optional XAU risk leg (``risk_leg.py``) from ``risk_leg_price``/``risk_leg_lots``
below; the executor places exactly that leg (``TradePlanRuntime.cs``) and its C# parity test
pins the same file.

All arithmetic is decimal, matching the C# ``decimal`` behaviour: prices are rounded to the
instrument's digits, midpoints away from zero. Binary floats would round 4101.005 down.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, ROUND_UP, Decimal

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


# Owner decision 2026-11-06: the XAU entry ladder (Auto Algo and Manual /algo) divides the volume 60% / 20% / 20% over three prices.
# ZL-1: the prices are distinct and meaningfully apart. The default is the original ladder, one price unit (the minimum spacing) between legs from the shallow edge (the
# live quote when it is inside the zone); ``spread_across_zone`` (off) would instead spread toward the zone's far
# edge (for a single-price manual zone, halfway to the stop): L1 near the proximal edge, L2 deeper, L3 deeper again, closer to the structural invalidation. Every
# gap between neighbouring prices is at least ``min_spacing_pips`` (10 pips = 1.00 on gold), measured AFTER rounding to the broker's digits. Where three prices do
# not fit the depth, two (60/40) do; where not even two fit, the ladder is unavailable (the Auto path rejects the plan: ``ladder_geometry_unavailable``; it never
# duplicates a price, never widens the zone, never turns a ladder into one entry).
LADDER_RATIOS: dict[int, tuple[float, ...]] = {3: (0.6, 0.2, 0.2), 2: (0.6, 0.4), 1: (1.0,)}
LADDER_UNAVAILABLE = "ladder_geometry_unavailable"
DEFAULT_MIN_SPACING_PIPS = Decimal("10")
XAU_PIP_SIZE = Decimal("0.1")


class LadderGeometryUnavailable(ValueError):
  """No valid price ladder fits the zone: fewer than two distinct, sufficiently spaced prices."""

  code = LADDER_UNAVAILABLE


@dataclass(frozen=True)
class LadderSettings:
  """The configured ladder rules (``execution.xau_ladder`` in config/execution.yml)."""

  min_spacing_pips: Decimal = DEFAULT_MIN_SPACING_PIPS
  spread_across_zone: bool = False

  def min_spacing_price(self, pip_size: float | Decimal = XAU_PIP_SIZE) -> Decimal:
    return _d(self.min_spacing_pips) * _d(pip_size)


def ladder_settings(cfg: object | None) -> LadderSettings:
  """The ladder rules from the runtime config; the defaults (10 pips, one price unit per leg) when the section is absent."""
  section = getattr(getattr(cfg, "execution", None), "xau_ladder", None)
  if section is None:
    return LadderSettings()
  spacing = getattr(section, "min_leg_spacing_pips", None)
  spread = getattr(section, "spread_across_zone", None)
  return LadderSettings(
    min_spacing_pips=DEFAULT_MIN_SPACING_PIPS if spacing is None else _d(spacing),
    spread_across_zone=False if spread is None else bool(spread),
  )


def price_ladder(
  direction: str,
  start: float | Decimal,
  far: float | Decimal,
  *,
  digits: int = XAU_DIGITS,
  pip_size: float | Decimal = XAU_PIP_SIZE,
  settings: LadderSettings | None = None,
  strict: bool = False,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
  """Entry prices and volume ratios of the XAU ladder from ``start`` toward ``far`` (BUY steps down, SELL steps up).

  Prices are rounded to ``digits``; the spacing rule is checked on the ROUNDED prices. A depth that cannot hold two prices ``min_spacing`` apart yields the single
  price ``start`` (a manual single-price intent), or raises ``LadderGeometryUnavailable`` when ``strict`` (the Auto path: a non-scalp ladder is never one entry).
  """
  settings = settings or LadderSettings()
  sign = -1 if direction.upper() == "BUY" else 1
  quantum = Decimal(1).scaleb(-digits)
  start_price = Decimal(str(round_price(_d(start), digits)))
  far_price = Decimal(str(round_price(_d(far), digits)))
  depth = abs(start_price - far_price)  # whole ticks: the legs must fit inside the zone as the broker will see it
  minimum = settings.min_spacing_price(pip_size)
  # The step is rounded UP to the tick, so rounding a leg can never bring two neighbours closer than the minimum.
  minimum_step = (minimum / quantum).to_integral_value(rounding=ROUND_UP) * quantum

  def on_tick(value: Decimal, rounding: str) -> Decimal:
    return (value / quantum).to_integral_value(rounding=rounding) * quantum

  candidates: list[tuple[Decimal, ...]] = []
  if depth >= minimum_step * 2:
    step = on_tick(depth / 2, ROUND_FLOOR) if settings.spread_across_zone else minimum_step
    step = max(minimum_step, step)
    candidates.append((start_price, start_price + sign * step, start_price + sign * 2 * step))
  if depth >= minimum_step:
    reach = on_tick(depth, ROUND_FLOOR) if settings.spread_across_zone else minimum_step
    candidates.append((start_price, start_price + sign * max(minimum_step, reach)))
  for prices in candidates:
    rounded = tuple(round_price(price, digits) for price in prices)
    inside = all(abs(Decimal(str(price)) - start_price) <= depth for price in rounded)
    if inside and ladder_spacing_ok(rounded, minimum):
      return rounded, LADDER_RATIOS[len(rounded)]
  if strict:
    raise LadderGeometryUnavailable(
      f"no {direction.upper()} ladder fits depth {depth} with {settings.min_spacing_pips} pips between prices",
    )
  return (float(start_price),), LADDER_RATIOS[1]


def ladder_spacing_ok(prices: tuple[float, ...] | list[float], min_spacing: float | Decimal) -> bool:
  """True when every neighbouring pair of ``prices`` is at least ``min_spacing`` apart (exact decimal comparison, on the prices as given)."""
  ordered = [_d(price) for price in prices]
  floor = _d(min_spacing)
  return all(abs(a - b) >= floor for a, b in zip(ordered, ordered[1:]))


def ladder_geometry_problem(
  direction: str,
  prices: tuple[float | Decimal, ...] | list[float | Decimal],
  *,
  min_spacing: float | Decimal | None = None,
) -> str | None:
  """What is wrong with planned leg prices, or None when they are a valid ladder: a repeated price, a price not deeper than the one before (BUY steps down, SELL
  up), or (when ``min_spacing`` is given) a gap smaller than it. One definition for the planner, the plan contract and the tests."""
  ordered = [_d(price) for price in prices]
  buy = direction.upper() == "BUY"
  for earlier, later in zip(ordered, ordered[1:]):
    if later == earlier:
      return "duplicate_leg_price"
    if (later > earlier) if buy else (later < earlier):
      return "leg_prices_not_ordered"
    if min_spacing is not None and abs(later - earlier) < _d(min_spacing):
      return "leg_spacing_below_minimum"
  return None


def risk_leg_price(
  direction: str,
  stop_loss: float | Decimal,
  *,
  pips_from_stop: float | Decimal,
  pip_size: float | Decimal,
  digits: int = XAU_DIGITS,
) -> float:
  """The risk leg's resting price: ``pips_from_stop`` inside the stop.

  BUY rests above the stop, SELL below it. Rounded to ``digits``, midpoints away from zero,
  exactly as the executor's decimal arithmetic does.
  """
  offset = _d(pips_from_stop) * _d(pip_size)
  stop = _d(stop_loss)
  price = stop + offset if direction.upper() == "BUY" else stop - offset
  return round_price(price, digits)


def risk_leg_lots(
  equity: float | Decimal | None,
  *,
  lots: float | Decimal | str,
  lots_below_equity_floor: float | Decimal | str,
  equity_floor: float | Decimal | str,
) -> Decimal:
  """Fixed risk-leg size by account equity tier.

  Unknown equity takes the smaller tier: the planner never guesses a larger exposure.
  """
  if equity is None or _d(equity) < _d(equity_floor):
    return _d(lots_below_equity_floor)
  return _d(lots)
