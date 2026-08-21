"""Deterministic execution-route resolution for strict stop contracts.

Strict autonomous candidates (entry_plan_version >= 1 and stop_plan_version >= 2)
must publish a concrete route: market | single_limit | zone_split. Unresolved
`either` may not carry an exact final-stop contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.autotrade.strategy_taxonomy import (
  REACTION_STRATEGIES,
  is_reaction_strategy,
  is_scalp_strategy,
  is_technique_or_confluence,
)

# AE-style scalp: one structural zone, five equal clips (not one full lot).
SCALP_MICRO_CLIPS = 5

ROUTE_MARKET = "market"
ROUTE_SINGLE_LIMIT = "single_limit"
ROUTE_ZONE_SPLIT = "zone_split"
ROUTE_MARKET_WITH_LIMIT_SCALE = "market_with_limit_scale"
ROUTE_EITHER = "either"

# Key Level / Session / Trendline only — Demand/Supply keep zone_scale → limit_ladder.
REACTION_MARKET_SCALE_STRATEGIES = REACTION_STRATEGIES
REACTION_MARKET_SCALE_FAMILIES = frozenset({
  "key_level",
  "session_level",
  "trendline",
})


def reaction_market_scale_eligible(
  *,
  strategy: str | None = None,
  strategy_family: str | None = None,
) -> bool:
  if strategy and is_reaction_strategy(strategy):
    return True
  if strategy_family and strategy_family in REACTION_MARKET_SCALE_FAMILIES:
    return True
  return False


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


def _unique_prices(prices: list[float]) -> tuple[float, ...]:
  out: list[float] = []
  for price in prices:
    if not out or price != out[-1]:
      out.append(price)
  return tuple(out)


def scalp_micro_grid_legs(
  *,
  side: str,
  low: float,
  high: float,
  quote: float,
  digits: int,
  clips: int = SCALP_MICRO_CLIPS,
) -> tuple[float, ...]:
  """Equal-size DCA clips from live/proximal into the confirmed zone.

  BUY steps down toward demand distal; SELL steps up toward supply distal.
  L1 is the live quote when already inside (marketable); remaining legs rest
  as limits so they only fill if M1 actually travels there.
  """
  count = max(2, int(clips))
  if side == "BUY":
    start = min(quote, high) if quote <= high else high
    far = low
    if start <= far:
      return ()
    step = (start - far) / (count - 1)
    raw = [start - (step * index) for index in range(count)]
  else:
    start = max(quote, low) if quote >= low else low
    far = high
    if start >= far:
      return ()
    step = (far - start) / (count - 1)
    raw = [start + (step * index) for index in range(count)]
  return _unique_prices([_round_price(price, digits) for price in raw])


def _equal_clip_ratios(count: int) -> tuple[float, ...]:
  if count <= 0:
    return ()
  base = round(1.0 / count, 6)
  ratios = [base] * count
  ratios[-1] = round(1.0 - base * (count - 1), 6)
  return tuple(ratios)


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
  scale_first_leg_fraction: float = 0.80,
  scale_step_atr: float = 0.5,
  reaction_scale_enabled: bool = False,
  reaction_market_fraction: float = 0.80,
  reaction_scale_fraction: float = 0.20,
  reaction_scale_step_atr: float | None = None,
  reaction_scale_invalid_policy: str = "single_market",
  strategy: str | None = None,
  strategy_family: str | None = None,
  entry_clips: int = SCALP_MICRO_CLIPS,
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

  reaction_scale_ok = (
    reaction_scale_enabled
    and reaction_market_scale_eligible(
      strategy=strategy, strategy_family=strategy_family,
    )
    and distribution in {"zone_scale", "reaction_scale", "either", ""}
  )
  reaction_step = (
    scale_step_atr if reaction_scale_step_atr is None else reaction_scale_step_atr
  )
  reaction_market_frac = min(1.0, max(0.0, reaction_market_fraction))
  reaction_scale_frac = min(1.0, max(0.0, reaction_scale_fraction))
  if abs(reaction_market_frac + reaction_scale_frac - 1.0) > 1e-6:
    reaction_scale_frac = round(1.0 - reaction_market_frac, 6)
  reaction_ratios = (reaction_market_frac, reaction_scale_frac)

  def _market_with_limit_scale_plan() -> ExecutionRoutePlan | None:
    if not reaction_scale_ok:
      return None
    if not split_ok:
      policy = (reaction_scale_invalid_policy or "single_market").strip().lower()
      if policy == "single_market" or zone_fill_fallback_enabled:
        return ExecutionRoutePlan(
          ROUTE_MARKET,
          _round_price(quote, digits),
          (),
          geometry,
          "reaction scale unqualified; single market fallback",
          True,
        )
      return ExecutionRoutePlan(
        ROUTE_MARKET_WITH_LIMIT_SCALE,
        quote,
        (),
        geometry,
        "reaction scale required but unqualified",
        False,
        "execution policy requires unavailable market_with_limit_scale",
      )
    # Confirmed in-zone (or zone-scale reaction selected): L1 market at live
    # quote, L2 resting limit one step deeper into the zone.
    l2_anchor = scale_entry_anchor if geometry == "inside" else proximal
    legs = _scale_ladder_legs(
      side=side, low=low, high=high, proximal=l2_anchor,
      atr=atr, scale_step_atr=reaction_step, digits=digits,
    )
    # L1 reference price is the live quote (not a limit); L2 is deeper limit.
    l1_price = _round_price(quote, digits)
    l2_price = legs[1]
    if l1_price == l2_price:
      policy = (reaction_scale_invalid_policy or "single_market").strip().lower()
      if policy == "single_market":
        return ExecutionRoutePlan(
          ROUTE_MARKET,
          l1_price,
          (),
          geometry,
          "reaction scale L2 coincides with L1; single market fallback",
          True,
        )
    return ExecutionRoutePlan(
      ROUTE_MARKET_WITH_LIMIT_SCALE,
      l1_price,
      (l1_price, l2_price),
      geometry,
      "execution policy: reaction market_with_limit_scale",
      True,
      planned_leg_volume_ratios=reaction_ratios,
    )

  if is_scalp_strategy(
    str(strategy or ""),
    family=strategy_family,
  ) or is_technique_or_confluence(str(strategy or "")):
    # Trade-direction chase: quote already past the proximal edge. A micro-
    # grid into the abandoned zone rests L2–Ln on the wrong side of a
    # continuation (live 2026-08-21 HFS Range Sweep SELL: L1 0.04 filled,
    # L2–L5 cancelled before_tp). Book full market so size rides the move.
    chase_away = (
      (side == "SELL" and geometry == "below")
      or (side == "BUY" and geometry == "above")
    )
    if chase_away:
      return ExecutionRoutePlan(
        ROUTE_MARKET,
        _round_price(quote, digits),
        (),
        geometry,
        "scalp chase: full market (micro-grid would rest into abandoned zone)",
        True,
      )
    grid = scalp_micro_grid_legs(
      side=side,
      low=low,
      high=high,
      quote=quote,
      digits=digits,
      clips=entry_clips,
    )
    if len(grid) >= 2:
      l1 = grid[0]
      return ExecutionRoutePlan(
        ROUTE_MARKET_WITH_LIMIT_SCALE,
        l1,
        grid,
        geometry,
        "scalp micro-grid: equal clips into the structural zone",
        True,
        planned_leg_volume_ratios=_equal_clip_ratios(len(grid)),
      )

  if preference == "market":
    # In-zone reaction: L1 market + deeper L2 limit (not a resting ladder).
    if reaction_scale_ok and geometry == "inside":
      scaled = _market_with_limit_scale_plan()
      if scaled is not None:
        return scaled
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
    # Only force market_with_limit_scale once price is already inside the
    # zone. Outside approaches keep the resting limit / DCA ladder path.
    if reaction_scale_ok and geometry == "inside":
      scaled = _market_with_limit_scale_plan()
      if scaled is not None:
        return scaled
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
  if reaction_scale_ok and geometry == "inside":
    scaled = _market_with_limit_scale_plan()
    if scaled is not None:
      return scaled
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
