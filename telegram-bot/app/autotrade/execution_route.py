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
  geometry = (
    "inside"
    if low <= quote <= high
    else "below" if quote < low else "above"
  )

  if preference == "market":
    if distribution == "zone_split":
      return ExecutionRoutePlan(
        ROUTE_ZONE_SPLIT,
        quote,
        (),
        geometry,
        "market cannot use zone_split",
        False,
        "market order cannot use zone_split entry distribution",
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
    if distribution == "zone_split" or (distribution == "either" and split_ok):
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
  if split_ok and distribution in {"zone_split", "either", ""}:
    legs = (_round_price(proximal, digits), midpoint)
    return ExecutionRoutePlan(
      ROUTE_ZONE_SPLIT,
      legs[0],
      legs,
      geometry,
      "resolved either → zone split",
      True,
    )
  return ExecutionRoutePlan(
    ROUTE_MARKET,
    _round_price(quote, digits),
    (),
    geometry,
    "resolved either → market",
    True,
  )


def prefer_market_for_reaction_family(family: str) -> bool:
  return family in {
    "key_level",
    "trendline",
    "mapped_zone_reaction",
    "session_level",
  }
