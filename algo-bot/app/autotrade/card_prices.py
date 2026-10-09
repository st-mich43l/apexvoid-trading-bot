"""The card price is the order price, on gold.

An XAU card prints whole numbers (``trade_card.format_price``: a presentation
choice), but the plan used to carry Go's fractional prices, so the card and the
orders disagreed. Production 2026-10-09, Key Level BUY: the card read
``Entry Zone 4,143 - 4,147``, ``SL 4,141 · risk 56 pips`` (4,147 to 4,141 reads
as 60), while the plan held a 4147.06 / 4145.11 ladder, a 4141.46 stop and a
4152.66 first target.

For a gold zone plan the planner now decides the final numbers - the ones the
card prints - and the executor places exactly them: the entry band, every ladder
leg, the stop and the targets are rounded the way the card rounds them. The stop
is rounded away from the entry whenever that stays inside the stop envelope, so
rounding never makes the trade riskier than the envelope allows; a target ladder
that was whole multiples of the risk stays whole multiples of the new risk, so
the card's R labels remain true.

Nothing is snapped when the result would break an ordering the executor relies
on or leave the envelope: the plan then keeps Go's exact prices, as before.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from app.autotrade.strategy_taxonomy import is_m1_scalp_strategy, is_scalp_strategy

_GOLD = {"XAU", "XAUUSD"}
# A ladder is "whole R multiples" when each target is within this of a half-R.
_R_TOLERANCE = 0.03


def _card_round(value: float) -> Decimal:
  """Exactly how the card prints a gold price (trade_card.format_price)."""
  return Decimal(round(value))


def _floats(values: Any) -> list[float] | None:
  try:
    out = [float(value) for value in values]
  except (TypeError, ValueError):
    return None
  return out if all(math.isfinite(value) for value in out) else None


def snap_measured_to_card_prices(
  measured: dict[str, Any],
  *,
  direction: str,
  symbol: str,
  strategy: str,
  pip_size: float,
  targets_pips: tuple[int, ...],
  reaction_stop_max_pips: float | None,
) -> dict[str, Any]:
  """``measured`` with its planned prices rounded to what the card prints.

  Returns ``measured`` untouched when the plan is not a gold zone plan or when
  snapping is unsafe. When it snaps, ``card_price_snap`` records the before/after.
  """
  if (
    str(symbol).upper() not in _GOLD
    or pip_size <= 0
    or direction not in {"BUY", "SELL"}
    or is_m1_scalp_strategy(strategy)
    or is_scalp_strategy(strategy)
    or "planned_stop_price" not in measured
    or "planned_entry_price" not in measured
  ):
    return measured

  buy = direction == "BUY"
  sign = 1.0 if buy else -1.0
  legs = _floats(measured.get("planned_leg_entry_prices") or [])
  if legs is None:
    return measured
  try:
    stop = float(measured["planned_stop_price"])
    entry = float(measured["planned_entry_price"])
    zone_low = float(measured["planned_entry_zone_low"])
    zone_high = float(measured["planned_entry_zone_high"])
  except (KeyError, TypeError, ValueError):
    return measured
  if not all(math.isfinite(value) for value in (stop, entry, zone_low, zone_high)):
    return measured

  snapped_legs = [float(_card_round(price)) for price in legs]
  snapped_entry = float(_card_round(entry))
  snapped_low = float(_card_round(zone_low))
  snapped_high = float(_card_round(zone_high))
  if snapped_low >= snapped_high:
    return measured
  if len(set(snapped_legs)) != len(snapped_legs):
    return measured
  # Legs keep their order: the shallow leg stays the nearest to the stop's far side.
  if [sorted(legs).index(p) for p in legs] != [
    sorted(snapped_legs).index(p) for p in snapped_legs
  ]:
    return measured
  if any(not snapped_low - 1e-9 <= price <= snapped_high + 1e-9 for price in snapped_legs):
    return measured

  if snapped_legs:
    proximal = max(snapped_legs) if buy else min(snapped_legs)
  else:
    proximal = snapped_entry
  floor_pips = measured.get("go_stop_envelope_floor_pips")
  cap_pips = measured.get("go_stop_envelope_cap_pips")
  cap = None
  for candidate in (cap_pips, reaction_stop_max_pips):
    if candidate is not None and math.isfinite(float(candidate)):
      cap = float(candidate) if cap is None else min(cap, float(candidate))
  floor = float(floor_pips) if floor_pips is not None else None

  def risk_pips(stop_price: float) -> float:
    return (proximal - stop_price) * sign / pip_size

  away = math.floor(stop) if buy else math.ceil(stop)
  toward = math.ceil(stop) if buy else math.floor(stop)
  chosen = None
  for candidate in (away, toward):
    pips = risk_pips(float(candidate))
    if pips <= 0:
      continue
    if cap is not None and pips > cap + 1e-9:
      continue
    if floor is not None and pips < floor - 1e-9:
      continue
    chosen = float(candidate)
    break
  if chosen is None:
    return measured
  # Every leg stays on the entry side of the stop.
  if any((price - chosen) * sign <= 0 for price in snapped_legs):
    return measured

  old_risk = abs(entry - stop) / pip_size
  new_risk = risk_pips(chosen)
  prices_changed = {
    "planned_stop_price": chosen,
    "planned_entry_price": snapped_entry,
    "planned_entry_zone_low": snapped_low,
    "planned_entry_zone_high": snapped_high,
    "planned_leg_entry_prices": snapped_legs,
  }

  # Targets: whole R multiples of the old risk stay whole R multiples of the new one,
  # otherwise the same pips from the snapped entry. Either way each target is a whole
  # number on the card.
  new_targets_pips: list[int] = []
  if old_risk > 0 and targets_pips:
    ratios = [pips / old_risk for pips in targets_pips]
    if all(abs(ratio * 2 - round(ratio * 2)) <= _R_TOLERANCE * 2 for ratio in ratios):
      new_targets_pips = [int(round(round(ratio * 2) / 2 * new_risk)) for ratio in ratios]
  if not new_targets_pips:
    new_targets_pips = [int(pips) for pips in targets_pips]
  # A whole-number target: snap the price, then re-derive the pips from it.
  target_prices = [
    float(_card_round(snapped_entry + sign * pips * pip_size)) for pips in new_targets_pips
  ]
  if any((price - snapped_entry) * sign <= 0 for price in target_prices):
    return measured
  if target_prices != sorted(target_prices, reverse=not buy):
    return measured
  new_targets_pips = [
    int(round((price - snapped_entry) * sign / pip_size)) for price in target_prices
  ]

  out = dict(measured)
  out.update(prices_changed)
  out["planned_stop_pips"] = format(Decimal(str(round(new_risk, 3))), "f")
  out["planned_final_stop_pips"] = out["planned_stop_pips"]
  out["planned_stop_distance"] = format(
    Decimal(str(round(new_risk * pip_size, 6))), "f",
  )
  out["planned_stop_clamped"] = bool(measured.get("planned_stop_clamped", False))
  out["card_price_snap_targets_pips"] = new_targets_pips
  out["card_price_snap_target_prices"] = target_prices
  out["card_price_snap"] = {
    "stop": [stop, chosen],
    "entry": [entry, snapped_entry],
    "zone": [[zone_low, zone_high], [snapped_low, snapped_high]],
    "legs": [legs, snapped_legs],
    "risk_pips": [round(old_risk, 2), round(new_risk, 2)],
  }
  return out
