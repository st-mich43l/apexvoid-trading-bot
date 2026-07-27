"""Pure-Python mirror of C# ``ScaleInPlanner`` raw-lot selection."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ScaleInSizingInputs:
  balance: float
  risk_percent: float
  pip_value_per_lot: float
  add_risk_fraction: float
  add_stop_pips: float
  booked_pnl: float
  stop_pnl: float
  table_lots: float
  open_lots: float
  initial_tranche_lots: float | None
  add_size_ratio: float
  lot_size: int = 10_000
  min_volume: int = 100
  step_volume: int = 100


@dataclass(frozen=True)
class ScaleInSizingDecision:
  allowed: bool
  lots: float
  volume: int
  binding_term: str | None
  reason: str


def _binding_term(
  chosen: float,
  exposure: float,
  risk: float,
  cap: float,
  size_ratio_cap: float,
) -> str:
  if math.isclose(chosen, size_ratio_cap):
    return "size-ratio-bound"
  if math.isclose(chosen, exposure):
    return "exposure-bound"
  if math.isclose(chosen, risk):
    return "risk-bound"
  return "add-cap-bound"


def _volume_for_lots(lots: float, *, lot_size: int, min_volume: int, step: int) -> int:
  if lots <= 0 or lot_size <= 0:
    return 0
  raw = int(lots * lot_size)
  if raw < min_volume:
    return 0
  stepped = (raw // step) * step
  return stepped if stepped >= min_volume else 0


def plan_scale_in_sizing(inputs: ScaleInSizingInputs) -> ScaleInSizingDecision:
  """Return the capped add lots/volume using the same four-term min as C#."""
  if (
    inputs.balance <= 0
    or inputs.risk_percent <= 0
    or inputs.pip_value_per_lot <= 0
    or inputs.add_risk_fraction <= 0
    or inputs.add_risk_fraction > 1
    or inputs.add_stop_pips <= 0
    or inputs.add_size_ratio <= 0
    or inputs.add_size_ratio > 1
  ):
    return ScaleInSizingDecision(
      allowed=False,
      lots=0.0,
      volume=0,
      binding_term=None,
      reason="add sizing inputs are invalid",
    )

  budget = inputs.balance * inputs.risk_percent / 100.0
  current_worst_case = inputs.booked_pnl + inputs.stop_pnl
  headroom_risk = budget + current_worst_case
  if headroom_risk <= 0:
    return ScaleInSizingDecision(
      allowed=False,
      lots=0.0,
      volume=0,
      binding_term=None,
      reason="group loss ceiling exhausted",
    )

  headroom_lots = max(0.0, inputs.table_lots - inputs.open_lots)
  if headroom_lots <= 0:
    return ScaleInSizingDecision(
      allowed=False,
      lots=0.0,
      volume=0,
      binding_term=None,
      reason="exposure ceiling reached; bank a partial first",
    )

  risk_headroom_lots = headroom_risk / (inputs.add_stop_pips * inputs.pip_value_per_lot)
  add_cap_lots = (
    inputs.add_risk_fraction * budget
    / (inputs.add_stop_pips * inputs.pip_value_per_lot)
  )
  size_ratio_cap_lots = (
    inputs.initial_tranche_lots * inputs.add_size_ratio
    if inputs.initial_tranche_lots is not None
    else float("inf")
  )
  raw_lots = min(
    headroom_lots,
    risk_headroom_lots,
    add_cap_lots,
    size_ratio_cap_lots,
  )
  binding = _binding_term(
    raw_lots,
    headroom_lots,
    risk_headroom_lots,
    add_cap_lots,
    size_ratio_cap_lots,
  )
  volume = _volume_for_lots(
    raw_lots,
    lot_size=inputs.lot_size,
    min_volume=inputs.min_volume,
    step=inputs.step_volume,
  )
  if volume <= 0:
    return ScaleInSizingDecision(
      allowed=False,
      lots=0.0,
      volume=0,
      binding_term=binding,
      reason="add sizing is below broker minimum volume",
    )
  lots = volume / inputs.lot_size
  return ScaleInSizingDecision(
    allowed=True,
    lots=lots,
    volume=volume,
    binding_term=binding,
    reason="",
  )
