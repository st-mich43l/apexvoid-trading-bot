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
  is_retest_only_scalp_strategy,
  is_reaction_strategy,
  is_scalp_strategy,
)

# Legacy/default micro-grid size; production instruments may override it.
SCALP_MICRO_CLIPS = 5

ROUTE_MARKET = "market"
ROUTE_SINGLE_LIMIT = "single_limit"
ROUTE_ZONE_SPLIT = "zone_split"
ROUTE_MARKET_WITH_LIMIT_SCALE = "market_with_limit_scale"
ROUTE_EITHER = "either"

def reaction_market_scale_eligible(*, strategy: str | None = None) -> bool:
  """Key Level / Session Level / Trendline opt into market-with-limit-scale.

  Demand/Supply keep zone_scale -> limit_ladder; each strategy's catalog row
  decides, not a group it belongs to.
  """
  return bool(strategy) and is_reaction_strategy(str(strategy))


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
  # True only when Python has already admitted a trade-direction scalp chase
  # and the old structural zone must not be re-armed by the executor.
  immediate_market: bool = False


def _round_price(value: float, digits: int) -> float:
  quant = Decimal("1").scaleb(-max(0, digits))
  return float(
    Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)
  )


def risk_targeted_entry_price(
  *,
  direction: str,
  structural_stop: float,
  zone_low: float,
  zone_high: float,
  target_risk_pips: float,
  pip_size: float,
  digits: int,
) -> float:
  """Entry price within ``[zone_low, zone_high]`` closest to a real
  ``target_risk_pips`` distance from the actual structural stop.

  2026-09-15 (owner-reported, XAU only): entry price used to come purely
  from zone geometry (the near edge / midpoint), with zero awareness of
  the risk distance that entry would produce against the real structural
  stop - the stop envelope (``execution.reaction.stop_min_pips``/
  ``stop_max_pips``) only ever clamped the STOP afterward. This picks the
  entry instead, so risk lands near the target up front. ``structural_stop``
  must be the real stop (mirrors ``_plan_base_stop``'s raw structural
  stop - structure_swing offset by the ATR buffer, before any wick/
  opposing-zone push), independent of which entry within the zone is
  chosen.

  Best-effort: when the zone's own span can't reach ``target_risk_pips``
  from the stop (too tight or too wide), returns whichever zone edge gets
  closest instead of rejecting - the caller's own stop envelope remains
  the final backstop.
  """
  side = str(direction).upper()
  low = min(zone_low, zone_high)
  high = max(zone_low, zone_high)
  target_distance = float(target_risk_pips) * float(pip_size)
  desired = (
    structural_stop + target_distance
    if side == "BUY"
    else structural_stop - target_distance
  )
  clamped = min(max(desired, low), high)
  return _round_price(clamped, digits)


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


def widen_tight_xau_zone(
  *,
  side: str,
  low: float,
  high: float,
  atr: float,
  zone_fill_min_atr: float,
  stop: float | None,
) -> tuple[float, float]:
  """Give a too-tight XAU entry zone the minimum width a shallow/deep ladder needs.

  XAU orders are a separate-leg ladder (shallow near edge, deep inside the
  zone). A zone narrower than ``zone_fill_min_atr`` ATR cannot be split, so the
  order used to collapse to ONE leg (73-100 % of Confluence Zone, CRT,
  Trendline, Flip Zone and Box/Break & Retest zones are that tight). The zone's
  distal edge is moved toward the stop until the zone is the minimum width, but
  never past halfway to the stop (the Manual Algo rule for a degenerate zone:
  the deep leg is never beyond halfway to it). The near edge never moves; a zone
  already wide enough, or one with no stop to bound it, is returned unchanged.
  """
  if stop is None or atr <= 0 or zone_fill_min_atr <= 0:
    return low, high
  minimum = zone_fill_min_atr * atr
  if high - low >= minimum:
    return low, high
  if side == "BUY":
    if stop >= low:
      return low, high
    room = (high - stop) / 2.0
    return min(low, high - min(minimum, room)), high
  if stop <= high:
    return low, high
  room = (stop - low) / 2.0
  return low, max(high, low + min(minimum, room))


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


def _deeper_second_leg(
  *,
  side: str,
  low: float,
  high: float,
  proximal: float,
  anchor: float,
  atr: float,
  scale_step_atr: float,
  digits: int,
) -> float:
  """Leg 2 price: the DEEPER of the structural-price ladder and the
  quote-safe ladder (lower for BUY, higher for SELL).

  Leg 2 anchors off ``proximal`` so it keeps the better structural price
  (ENTRY_LOGIC_REVIEW_2026-09-17), but once price is already inside the
  zone that structural edge can sit on the wrong side of the market - a BUY
  zone whose near edge is above the current quote gives a "deeper" leg
  ABOVE the quote, i.e. a marketable limit that fills instantly at the same
  price as leg 1 (owner-reported live 2026-09-21: XAU Session Level BUY,
  L1 and L2 both filled at 4346.83). Taking the deeper of the two ladders
  guarantees leg 2 is never closer to the market than the quote-anchored
  ladder would have put it.
  """
  from_proximal = _scale_ladder_legs(
    side=side, low=low, high=high, proximal=proximal,
    atr=atr, scale_step_atr=scale_step_atr, digits=digits,
  )[1]
  from_anchor = _scale_ladder_legs(
    side=side, low=low, high=high, proximal=anchor,
    atr=atr, scale_step_atr=scale_step_atr, digits=digits,
  )[1]
  return min(from_proximal, from_anchor) if side == "BUY" else max(
    from_proximal, from_anchor,
  )


def _manual_xau_entry_legs(
  *,
  side: str,
  low: float,
  high: float,
  stop: float | None,
  digits: int,
) -> tuple[float, float] | None:
  """Return the Manual Algo shallow/deep geometry for an XAU ladder.

  Manual Algo owns this contract: shallow is the near edge (BUY high, SELL
  low), deep is the zone midpoint, and both prices use Decimal/AwayFromZero
  rounding. A degenerate zone needs the stop to derive its deep leg; when it
  is unavailable, the caller keeps the existing route instead of inventing a
  stop.
  """
  if low == high and stop is None:
    return None
  from app.autotrade.xau_ladder import entry_leg_prices

  legs = entry_leg_prices(
    side,
    low,
    high,
    low if stop is None else stop,
  )
  return (
    _round_price(legs.shallow, digits),
    _round_price(legs.deep, digits),
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
  single_entry_market_inside: bool = False,
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
  entry_clips: int = SCALP_MICRO_CLIPS,
  structural_stop: float | None = None,
  target_risk_pips: float | None = None,
  pip_size: float | None = None,
  manual_xau_ladder: bool = False,
) -> ExecutionRoutePlan:
  """Resolve a concrete route mirroring AutoTradeEngine.ResolveExecutionRoute.

  ``structural_stop``/``target_risk_pips``/``pip_size`` are optional and,
  when all three are given, replace the anchor entry (``proximal`` below)
  with ``risk_targeted_entry_price`` - see that function's docstring. Any
  one missing keeps today's pure zone-geometry anchor unchanged. The explicit
  ``manual_xau_ladder`` switch is the only path that adopts Manual Algo's
  shallow/deep geometry; FX callers never set it.
  """
  preference = (order_type_preference or "").strip().lower()
  distribution = (entry_distribution or "").strip().lower()
  side = direction.strip().upper()
  quote = float(executable_quote)
  low = float(min(zone_low, zone_high))
  high = float(max(zone_low, zone_high))
  if manual_xau_ladder and zone_fill_enabled:
    low, high = widen_tight_xau_zone(
      side=side, low=low, high=high, atr=atr,
      zone_fill_min_atr=zone_fill_min_atr, stop=structural_stop,
    )
  split_ok = zone_split_qualifies(
    zone_low=low,
    zone_high=high,
    atr=atr,
    zone_fill_enabled=zone_fill_enabled,
    zone_fill_min_atr=zone_fill_min_atr,
  )
  proximal = high if side == "BUY" else low
  if (
    structural_stop is not None
    and target_risk_pips is not None
    and pip_size is not None
  ):
    proximal = risk_targeted_entry_price(
      direction=side,
      structural_stop=float(structural_stop),
      zone_low=low,
      zone_high=high,
      target_risk_pips=float(target_risk_pips),
      pip_size=float(pip_size),
      digits=digits,
    )
  midpoint = _round_price((low + high) / 2.0, digits)
  first_leg_fraction = min(1.0, max(0.0, scale_first_leg_fraction))
  leg_ratios = (first_leg_fraction, round(1.0 - first_leg_fraction, 6))
  manual_xau_legs = (
    _manual_xau_entry_legs(
      side=side,
      low=low,
      high=high,
      stop=structural_stop,
      digits=digits,
    )
    if manual_xau_ladder and split_ok
    else None
  )
  manual_xau_ratios = (0.80, 0.20)
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
    and reaction_market_scale_eligible(strategy=strategy)
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
    # quote, L2 resting limit one step deeper into the zone. L2 always
    # anchors off `proximal` (the risk-targeted price for XAU, or the
    # zone's own far/better edge otherwise) - never the quote-collapsed
    # `scale_entry_anchor`, which exists only to keep L1 fillable once
    # price is already inside the zone. Owner-reported 2026-09:
    # market_with_limit_scale was silently discarding a correctly-computed
    # risk-targeted/zone-edge price for L2, clustering both legs at the
    # live quote instead - see ENTRY_LOGIC_REVIEW_2026-09-17.md.
    # L1 reference price is the live quote (not a limit); L2 is deeper limit.
    l1_price = _round_price(quote, digits)
    if manual_xau_legs is not None:
      # A confirmed XAU reaction can arrive after price has already crossed
      # the manual shallow edge and original midpoint. Reusing either level
      # creates a marketable L2 that fills with L1; a tiny generic ATR step
      # clusters both entries (production 2026-10-02: SELL L1 4185.51, L2
      # 4185.90 inside a 4183.28-4187.19 zone). Preserve Manual Algo's
      # midpoint principle over the still-untraded part of the zone instead:
      # market L1 now, limit L2 halfway from quote to the far/better edge.
      remaining_far = low if side == "BUY" else high
      l2_price = _round_price((quote + remaining_far) / 2.0, digits)
    else:
      l2_price = _deeper_second_leg(
        side=side, low=low, high=high, proximal=proximal,
        anchor=scale_entry_anchor, atr=atr,
        scale_step_atr=reaction_step, digits=digits,
      )
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
      (
        "execution policy: XAU live-zone midpoint ladder"
        if manual_xau_legs is not None
        else "execution policy: reaction market_with_limit_scale"
      ),
      True,
      planned_leg_volume_ratios=(
        manual_xau_ratios if manual_xau_legs is not None else reaction_ratios
      ),
    )

  if is_scalp_strategy(str(strategy or "")):
    retest_only = is_retest_only_scalp_strategy(str(strategy or ""))
    if retest_only and geometry != "inside":
      return ExecutionRoutePlan(
        ROUTE_MARKET,
        _round_price(quote, digits),
        (),
        geometry,
        "retest-only scalp: market_watch until quote inside the card zone",
        True,
        immediate_market=False,
      )
    # Trade-direction chase: quote already past the proximal edge. A micro-
    # grid into the abandoned zone rests L2–Ln on the wrong side of a
    # continuation (live 2026-08-21 HFS Range Sweep SELL: L1 0.04 filled,
    # L2–L5 cancelled before_tp). Book full market so size rides the move.
    chase_away = (
      (side == "SELL" and geometry == "below")
      or (side == "BUY" and geometry == "above")
    )
    # Non-M1 XAU range scalps still use the Manual Algo entry contract:
    # execute the shallow/current leg and leave a deeper limit working over
    # the untraded part of the zone.  The old scalp short-circuit returned a
    # single market leg before ``manual_xau_ladder`` could be used, which is
    # why production Range Edge plans carried ``leg_ratios`` metadata but an
    # empty ``entry.legs`` array.  M1 chase scalps deliberately remain
    # single-market and are excluded by ``manual_xau_ladder``.
    if manual_xau_ladder and split_ok:
      remaining_far = low if side == "BUY" else high
      l2_price = _round_price(
        (quote + remaining_far) / 2.0,
        digits,
      )
      if side == "BUY":
        l2_price = min(high, max(low, l2_price))
      else:
        l2_price = max(low, min(high, l2_price))
      l1_price = _round_price(quote, digits)
      if l1_price != l2_price:
        return ExecutionRoutePlan(
          ROUTE_MARKET_WITH_LIMIT_SCALE,
          l1_price,
          (l1_price, l2_price),
          geometry,
          "scalp: XAU Manual Algo shallow/deep ladder",
          True,
          planned_leg_volume_ratios=manual_xau_ratios,
          immediate_market=True,
        )
    # Scalp: single-leg market only (no micro-grid) - fast, simple
    # execution matters more than entry-price optimality for a scalp.
    # 2026-09-08 (owner-reported bad technique entries): technique
    # strategies (FVG/OB/IFVG/CRT/supply_demand/…) used to share this
    # single-leg-market restriction too, forcing every technique setup to
    # fill immediately at whatever price confirmation landed on rather
    # than using their own declared zone_scale/limit policy - a 2026-08-26
    # workaround for a GROUP RECOVERY REQUIRED false-positive
    # (v8:a80bf164…, an SL'd leg's deal lookup returning Unknown while a
    # sibling leg was still open) that TradePlanRuntime.ClassifyCloseReason/
    # ExitBeyondProtectiveStop has since fixed generally (ctrader-engine,
    # not entry-type-specific) - technique strategies now fall through to
    # their own policy below instead.
    reason = (
      "scalp chase: full market (micro-grid would rest into abandoned zone)"
      if chase_away
      else "scalp: single-leg market (no micro-grid)"
    )
    return ExecutionRoutePlan(
      ROUTE_MARKET,
      _round_price(quote, digits),
      (),
      geometry,
      reason,
      True,
      immediate_market=True,
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
    # FX's single-best-entry contract is intentionally not a marketable
    # limit at the quote. A true market route fills once against the current
    # bid/ask and cannot leave a second entry leg behind. Outside the zone,
    # the single-limit branch below still waits at the proximal boundary.
    if (
      distribution == "single"
      and single_entry_market_inside
      and geometry == "inside"
    ):
      return ExecutionRoutePlan(
        ROUTE_MARKET,
        _round_price(quote, digits),
        (),
        geometry,
        "execution policy: single best in-zone market",
        True,
      )
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
        if manual_xau_legs is not None:
          return ExecutionRoutePlan(
            ROUTE_ZONE_SPLIT,
            manual_xau_legs[0],
            manual_xau_legs,
            geometry,
            "execution policy: Manual Algo XAU shallow/deep ladder",
            True,
            planned_leg_volume_ratios=manual_xau_ratios,
          )
        # Leg 2's step-basis is `proximal` (the risk-targeted/zone-edge
        # price), not the quote-collapsed `scale_entry_anchor` - passing
        # the quote here (the pre-fix behavior) both discards the better
        # price AND, once geometry is "inside", can hand leg 2 a step
        # computed from the wrong side of the market. Leg 1 stays
        # `scale_entry_anchor` deliberately - it must remain a valid,
        # likely-marketable resting price once price is already inside the
        # zone (see the comment above `scale_entry_anchor`'s definition);
        # `_scale_ladder_legs`'s own first return value (== its `proximal`
        # argument verbatim) is not reused for that reason.
        legs = (
          _round_price(scale_entry_anchor, digits),
          _deeper_second_leg(
            side=side, low=low, high=high, proximal=proximal,
            anchor=scale_entry_anchor, atr=atr,
            scale_step_atr=scale_step_atr, digits=digits,
          ),
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
      legs = manual_xau_legs or (_round_price(proximal, digits), midpoint)
      return ExecutionRoutePlan(
        ROUTE_ZONE_SPLIT,
        legs[0],
        legs,
        geometry,
        "execution policy: zone split",
        True,
        planned_leg_volume_ratios=(
          manual_xau_ratios if manual_xau_legs is not None else ()
        ),
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
      if manual_xau_legs is not None:
        return ExecutionRoutePlan(
          ROUTE_ZONE_SPLIT,
          manual_xau_legs[0],
          manual_xau_legs,
          geometry,
          "resolved either → Manual Algo XAU shallow/deep ladder",
          True,
          planned_leg_volume_ratios=manual_xau_ratios,
        )
      # Same fix as the two zone_scale branches above: leg 2 steps from
      # `proximal`, leg 1 stays the quote-safe `scale_entry_anchor`.
      legs = (
        _round_price(scale_entry_anchor, digits),
        _deeper_second_leg(
          side=side, low=low, high=high, proximal=proximal,
          anchor=scale_entry_anchor, atr=atr,
          scale_step_atr=scale_step_atr, digits=digits,
        ),
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
    legs = manual_xau_legs or (_round_price(proximal, digits), midpoint)
    return ExecutionRoutePlan(
      ROUTE_ZONE_SPLIT,
      legs[0],
      legs,
      geometry,
      "resolved either → zone split",
      True,
      planned_leg_volume_ratios=(
        manual_xau_ratios if manual_xau_legs is not None else leg_ratios
      ),
    )
  return ExecutionRoutePlan(
    ROUTE_MARKET,
    _round_price(quote, digits),
    (),
    geometry,
    "resolved either → market",
    True,
  )
