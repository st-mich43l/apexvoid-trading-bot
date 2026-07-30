"""Deterministic execution-route resolution for strict stop contracts.

Strict autonomous candidates (entry_plan_version >= 1 and stop_plan_version >= 2)
must publish a concrete route: market | single_limit | zone_split. Unresolved
`either` may not carry an exact final-stop contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

ROUTE_MARKET = "market"
ROUTE_SINGLE_LIMIT = "single_limit"
ROUTE_ZONE_SPLIT = "zone_split"
ROUTE_EITHER = "either"


@dataclass(frozen=True)
class ExecutionRoutePlan:
  route: str
  planned_entry_price: float
  planned_leg_entry_prices: tuple[float, ...]
  entry_geometry: str
  routing_reason: str
  valid: bool
  reject_reason: str | None = None
  # DCA-into-zone scale ladder (owner spec): first leg fills at the
  # proximal edge with the larger share; the remaining share only fills at
  # a further, momentum-confirmed price deeper into the zone. Empty for any
  # non-scaled route (market/single_limit) - trade_plan_builder falls back
  # to an equal split across whatever legs it does have in that case.
  planned_leg_volume_ratios: tuple[float, ...] = ()


def _round_price(value: float, digits: int) -> float:
  quant = Decimal("1").scaleb(-max(0, digits))
  return float(
    Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
  )


def zone_split_qualifies(
  *,
  zone_low: float,
  zone_high: float,
  atr: float,
  zone_fill_enabled: bool,
  zone_fill_min_atr: float,
) -> bool:
  if not zone_fill_enabled or atr <= 0:
    return False
  width = abs(zone_high - zone_low)
  return width >= max(0.0, zone_fill_min_atr) * atr


def _scale_ladder_legs(
  *,
  side: str,
  low: float,
  high: float,
  proximal: float,
  atr: float,
  scale_step_atr: float,
  digits: int,
) -> tuple[float, float]:
  """DCA-into-zone ladder (owner spec): leg 2 sits one momentum-confirmed
  step deeper into the zone than the proximal edge, capped at the far edge
  so it never falls outside the confirmed zone. A resting limit order at
  this price only fills if price actually travels there - "momentum
  confirmed" falls naturally out of it being a real limit order, no
  separate live momentum check is needed.
  """
  far = low if side == "BUY" else high
  step_price = max(0.0, scale_step_atr) * max(0.0, atr)
  if side == "BUY":
    second_leg = max(far, proximal - step_price)
  else:
    second_leg = min(far, proximal + step_price)
  return (
    _round_price(proximal, digits),
    _round_price(second_leg, digits),
  )


def resolve_execution_route_plan(
  *,
  direction: str,
  order_type_preference: str,
  entry_distribution: str,
  executable_quote: float,
  zone_low: float,
  zone_high: float,
  atr: float,
  zone_fill_enabled: bool = False,
  zone_fill_min_atr: float = 0.5,
  inside_zone_market_entry_enabled: bool = True,
  zone_fill_fallback_enabled: bool = True,
  digits: int = 2,
  allow_either: bool = False,
  scale_first_leg_fraction: float = 0.70,
  scale_step_atr: float = 0.5,
) -> ExecutionRoutePlan:
  """Resolve a concrete route mirroring AutoTradeEngine.ResolveExecutionRoute."""
  preference = (order_type_preference or "").strip().lower()
  distribution = (entry_distribution or "").strip().lower()
  side = direction.strip().upper()
  quote = float(executable_quote)
  low = float(min(zone_low, zone_high))
  high = float(max(zone_low, zone_high))
  split_ok = zone_split_qualifies(
    zone_low=low,
    zone_high=high,
    atr=atr,
    zone_fill_enabled=zone_fill_enabled,
    zone_fill_min_atr=zone_fill_min_atr,
  )
  proximal = high if side == "BUY" else low
  midpoint = _round_price((low + high) / 2.0, digits)
  first_leg_fraction = min(1.0, max(0.0, scale_first_leg_fraction))
  leg_ratios = (first_leg_fraction, round(1.0 - first_leg_fraction, 6))
  geometry = (
    "inside"
    if low <= quote <= high
    else "below" if quote < low else "above"
  )
  # A resting limit at the raw zone edge only makes sense while price has
  # not reached it yet - once price has already traded through the near
  # edge (geometry inside/overshot), that price sits on the wrong side of
  # the market for a limit order to rest there (a SELL limit below the
  # current quote, or a BUY limit above it, is not a valid resting order).
  # Snap the anchor to the current quote in that case so leg 1 fills as an
  # immediate/marketable entry instead of a stuck, unplaceable order.
  if inside_zone_market_entry_enabled:
    scale_entry_anchor = (
      (min(quote, high) if geometry != "above" else proximal)
      if side == "BUY"
      else (max(quote, low) if geometry != "below" else proximal)
    )
  else:
    scale_entry_anchor = proximal

  if preference == "market":
    if distribution in {"zone_split", "zone_scale"}:
      return ExecutionRoutePlan(
        ROUTE_ZONE_SPLIT,
        quote,
        (),
        geometry,
        f"market cannot use {distribution}",
        False,
        f"market order cannot use {distribution} entry distribution",
      )
    return ExecutionRoutePlan(
      ROUTE_MARKET,
      _round_price(quote, digits),
      (),
      geometry,
      "execution policy: market",
      True,
    )

  if preference == "limit":
    if distribution in {"zone_split", "zone_scale"} or (
      distribution == "either" and split_ok
    ):
      if not split_ok:
        if zone_fill_fallback_enabled:
          return ExecutionRoutePlan(
            ROUTE_MARKET,
            _round_price(quote, digits),
            (),
            geometry,
            "zone-fill unavailable; single-entry fallback",
            True,
          )
        return ExecutionRoutePlan(
          ROUTE_ZONE_SPLIT,
          quote,
          (),
          geometry,
          "zone_split required but unqualified",
          False,
          "execution policy requires unavailable zone_split limit capability",
        )
      if distribution == "zone_scale":
        legs = _scale_ladder_legs(
          side=side, low=low, high=high, proximal=scale_entry_anchor,
          atr=atr, scale_step_atr=scale_step_atr, digits=digits,
        )
        return ExecutionRoutePlan(
          ROUTE_ZONE_SPLIT,
          legs[0],
          legs,
          geometry,
          "execution policy: DCA zone scale",
          True,
          planned_leg_volume_ratios=leg_ratios,
        )
      legs = (_round_price(proximal, digits), midpoint)
      return ExecutionRoutePlan(
        ROUTE_ZONE_SPLIT,
        legs[0],
        legs,
        geometry,
        "execution policy: zone split",
        True,
      )
    # single limit
    if side == "BUY":
      limit = min(quote, high) if geometry != "above" else proximal
      if quote > high and not inside_zone_market_entry_enabled:
        limit = high
    else:
      limit = max(quote, low) if geometry != "below" else proximal
      if quote < low and not inside_zone_market_entry_enabled:
        limit = low
    price = _round_price(float(limit), digits)
    return ExecutionRoutePlan(
      ROUTE_SINGLE_LIMIT,
      price,
      (price,),
      geometry,
      "execution policy: single limit",
      True,
    )

  # preference == either (or unknown)
  if allow_either:
    return ExecutionRoutePlan(
      ROUTE_EITHER,
      _round_price(quote, digits),
      (),
      geometry,
      "legacy uncommitted either",
      True,
    )
  if split_ok and distribution in {"zone_split", "zone_scale", "either", ""}:
    if distribution == "zone_scale":
      legs = _scale_ladder_legs(
        side=side, low=low, high=high, proximal=scale_entry_anchor, atr=atr,
        scale_step_atr=scale_step_atr, digits=digits,
      )
      return ExecutionRoutePlan(
        ROUTE_ZONE_SPLIT,
        legs[0],
        legs,
        geometry,
        "resolved either → DCA zone scale",
        True,
        planned_leg_volume_ratios=leg_ratios,
      )
    legs = (_round_price(proximal, digits), midpoint)
    return ExecutionRoutePlan(
      ROUTE_ZONE_SPLIT,
      legs[0],
      legs,
      geometry,
      "resolved either → zone split",
      True,
      planned_leg_volume_ratios=leg_ratios,
    )
  return ExecutionRoutePlan(
    ROUTE_MARKET,
    _round_price(quote, digits),
    (),
    geometry,
    "resolved either → market",
    True,
  )
