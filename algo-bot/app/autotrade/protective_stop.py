"""Decimal-safe protective-stop contract shared with the C# executor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.autotrade.strategy_taxonomy import REACTION_STRATEGIES


STOP_PLAN_VERSION = 2


class ProtectiveStopError(ValueError):
  """A stop cannot be constructed under the shared execution contract.

  Optional ``measured`` carries diagnostic fields for reject telemetry
  (lifecycle / forming-card evidence) without changing the error message
  string that C# parity and callers match on.
  """

  def __init__(
    self,
    message: str,
    *,
    measured: dict[str, Any] | None = None,
  ) -> None:
    super().__init__(message)
    self.measured: dict[str, Any] = dict(measured or {})


@dataclass(frozen=True)
class OpposingZoneStopContext:
  zone_id: str | None
  low: Decimal
  high: Decimal
  execution_grade: bool
  push_beyond_zone: bool
  buffer_atr: Decimal


@dataclass(frozen=True)
class FinalProtectiveStopPlan:
  entry_price: Decimal
  base_stop_price: Decimal
  base_stop_pips: Decimal
  final_stop_price: Decimal
  final_stop_distance: Decimal
  final_stop_pips: Decimal
  raw_stop_price: Decimal
  clamped: bool
  source: str
  adjustment: str  # "none" | "opposing_zone_push"
  adjustment_zone_id: str | None
  adjustment_zone_low: Decimal | None
  adjustment_zone_high: Decimal | None
  version: int = STOP_PLAN_VERSION

  # Backward-compatible aliases for callers still using v1 field names.
  @property
  def stop_price(self) -> Decimal:
    return self.final_stop_price

  @property
  def distance(self) -> Decimal:
    return self.final_stop_distance

  @property
  def stop_pips(self) -> Decimal:
    return self.final_stop_pips

  def candidate_fields(self, *, entry_price: Decimal) -> dict[str, str | bool | int]:
    final_clamped = self.clamped or self.adjustment == "opposing_zone_push"
    fields: dict[str, str | bool | int] = {
      "planned_stop_entry_price": format(entry_price, "f"),
      "planned_stop_price": format(self.final_stop_price, "f"),
      "planned_stop_distance": format(self.final_stop_distance, "f"),
      "planned_stop_pips": format(self.final_stop_pips, "f"),
      "planned_stop_raw_price": format(self.raw_stop_price, "f"),
      "planned_stop_clamped": final_clamped,
      "stop_source": self.source,
      "stop_plan_version": self.version,
      "planned_base_stop_price": format(self.base_stop_price, "f"),
      "planned_base_stop_pips": format(self.base_stop_pips, "f"),
      "planned_final_stop_price": format(self.final_stop_price, "f"),
      "planned_final_stop_distance": format(self.final_stop_distance, "f"),
      "planned_final_stop_pips": format(self.final_stop_pips, "f"),
      "stop_adjustment": self.adjustment,
    }
    if self.adjustment_zone_id is not None:
      fields["stop_adjustment_zone_id"] = self.adjustment_zone_id
    if self.adjustment_zone_low is not None:
      fields["stop_adjustment_zone_low"] = format(
        self.adjustment_zone_low, "f",
      )
    if self.adjustment_zone_high is not None:
      fields["stop_adjustment_zone_high"] = format(
        self.adjustment_zone_high, "f",
      )
    return fields


ProtectiveStopPlan = FinalProtectiveStopPlan


def decimal_value(value: Any, name: str) -> Decimal:
  try:
    result = Decimal(str(value))
  except (InvalidOperation, TypeError, ValueError) as exc:
    raise ProtectiveStopError(f"{name} is invalid") from exc
  if not result.is_finite():
    raise ProtectiveStopError(f"{name} is invalid")
  return result


def opposing_zone_fingerprint(
  *,
  symbol: str,
  timeframe: str,
  side: str,
  low: Any,
  high: Any,
  created_bar_ts: Any,
  source: str | None,
) -> str:
  """Deterministic identity for a zone that carries no stored id.

  Two zones with identical edges but different origins must never be treated
  as the same zone, so provenance (timeframe, side, creation bar and detector
  source) is part of the identity, not just the geometry.
  """
  low_value = decimal_value(low, "opposing_zone_low")
  high_value = decimal_value(high, "opposing_zone_high")
  return "|".join((
    (symbol or "").strip().upper() or "unknown",
    (timeframe or "").strip().upper() or "unknown",
    (side or "").strip().lower() or "unknown",
    format(low_value.quantize(Decimal("0.00001")).normalize(), "f"),
    format(high_value.quantize(Decimal("0.00001")).normalize(), "f"),
    str(created_bar_ts if created_bar_ts is not None else 0),
    (source or "").strip().lower() or "unknown",
  ))


def opposing_zone_context_from_values(
  *,
  opposing_zone_low: Any | None,
  opposing_zone_high: Any | None,
  opposing_zone_id: str | None,
  direction: str,
  atr: Any,
  pip_size: Any,
  cfg: Any | None,
) -> OpposingZoneStopContext | None:
  if opposing_zone_low is None or opposing_zone_high is None:
    return None
  low = decimal_value(opposing_zone_low, "opposing_zone_low")
  high = decimal_value(opposing_zone_high, "opposing_zone_high")
  if low <= 0 or high <= 0 or low >= high:
    return None
  atr_value = decimal_value(atr, "atr")
  pip = decimal_value(pip_size, "pip_size")
  width = high - low
  width_atr = width / atr_value if atr_value > 0 else Decimal("Infinity")
  width_pips = width / pip if pip > 0 else Decimal("Infinity")
  max_width_atr = decimal_value(
    getattr(cfg, "auto_trade_execution_zone_max_width_atr", 2.0),
    "auto_trade_execution_zone_max_width_atr",
  )
  max_width_pips = decimal_value(
    getattr(cfg, "auto_trade_execution_zone_max_width_pips", 100.0),
    "auto_trade_execution_zone_max_width_pips",
  )
  execution_grade = (
    width > 0
    and width_atr <= max_width_atr
    and width_pips <= max_width_pips
  )
  return OpposingZoneStopContext(
    zone_id=opposing_zone_id,
    low=low,
    high=high,
    execution_grade=execution_grade,
    push_beyond_zone=bool(
      getattr(cfg, "auto_trade_stop_push_beyond_zone", True),
    ),
    buffer_atr=decimal_value(
      getattr(cfg, "auto_trade_add_stop_buffer_atr", 0.3),
      "auto_trade_add_stop_buffer_atr",
    ),
  )


def _plan_base_stop(
  *,
  direction: str,
  entry: Decimal,
  structure_swing: Decimal,
  atr_value: Decimal,
  structure_buffer: Decimal,
  sweep_extreme: Any | None,
  wick_buffer: Decimal,
  minimum_stop_pips: int,
  maximum_stop_pips: int,
  pip: Decimal,
  digits: int,
) -> tuple[Decimal, Decimal, Decimal, Decimal, bool, str]:
  """Mirror ``StructureStopPlanner.Plan`` base stop before opposing-zone push."""
  raw_stop = (
    structure_swing - structure_buffer * atr_value
    if direction == "BUY"
    else structure_swing + structure_buffer * atr_value
  )
  source = "structure"
  if sweep_extreme is not None:
    sweep = decimal_value(sweep_extreme, "sweep_extreme")
    if sweep <= 0:
      raise ProtectiveStopError("Sweep extreme is invalid")
    wick_stop = (
      sweep - wick_buffer * atr_value
      if direction == "BUY"
      else sweep + wick_buffer * atr_value
    )
    wick_distance = (
      entry - wick_stop
      if direction == "BUY"
      else wick_stop - entry
    )
    if wick_distance <= 0:
      raise ProtectiveStopError(
        "Sweep invalidation is not on the losing side of entry"
      )
    if wick_distance / pip > Decimal(maximum_stop_pips):
      raise ProtectiveStopError("stop_exceeds_envelope_after_wick")
    selected = min(raw_stop, wick_stop) if direction == "BUY" else max(
      raw_stop, wick_stop,
    )
    if selected == wick_stop and selected != raw_stop:
      source = "wick"
    elif selected == wick_stop:
      source = "structure_and_wick"
    raw_stop = selected
  raw_distance = (
    entry - raw_stop
    if direction == "BUY"
    else raw_stop - entry
  )
  if raw_distance <= 0:
    raise ProtectiveStopError(
      "Structure invalidation is not on the losing side of entry"
    )
  raw_pips = raw_distance / pip
  # V6 / PlanBase parity with StructureStopPlanner: clamp into the
  # [min, max] envelope. Zone-scale group stops use plan_group_protective_stop
  # which rejects over-max instead of pulling the stop inward.
  stop_pips = min(
    max(raw_pips, Decimal(minimum_stop_pips)),
    Decimal(maximum_stop_pips),
  )
  distance = stop_pips * pip
  stop_price = entry - distance if direction == "BUY" else entry + distance
  quantum = Decimal(1).scaleb(-digits)
  stop_price = stop_price.quantize(quantum, rounding=ROUND_HALF_UP)
  distance = abs(entry - stop_price)
  stop_pips = distance / pip
  clamped = stop_pips != raw_pips
  return stop_price, distance, stop_pips, raw_stop, clamped, source


def opposing_zone_context_measured(
  opposing_zone: OpposingZoneStopContext | None,
) -> dict[str, Any]:
  """Stop-side opposing zone fields (distinct from target-room opposing_*)."""
  if opposing_zone is None:
    return {
      "stop_side_opposing_zone_id": None,
      "stop_side_opposing_zone_low": None,
      "stop_side_opposing_zone_high": None,
      "stop_side_opposing_execution_grade": None,
      "stop_side_opposing_push_beyond_zone": None,
      "stop_side_opposing_buffer_atr": None,
    }
  return {
    "stop_side_opposing_zone_id": opposing_zone.zone_id,
    "stop_side_opposing_zone_low": float(opposing_zone.low),
    "stop_side_opposing_zone_high": float(opposing_zone.high),
    "stop_side_opposing_execution_grade": bool(opposing_zone.execution_grade),
    "stop_side_opposing_push_beyond_zone": bool(
      opposing_zone.push_beyond_zone
    ),
    "stop_side_opposing_buffer_atr": float(opposing_zone.buffer_atr),
  }


def _stop_inside_opposing_zone_error(
  *,
  detail: str,
  direction: str,
  entry: Decimal,
  base_stop_price: Decimal,
  base_stop_pips: Decimal,
  opposing_zone: OpposingZoneStopContext,
  atr_value: Decimal,
  maximum_stop_pips: int,
  pip: Decimal,
  digits: int,
  pushed_stop_price: Decimal | None = None,
  pushed_stop_pips: Decimal | None = None,
) -> ProtectiveStopError:
  buffer = opposing_zone.buffer_atr * atr_value
  quantum = Decimal(1).scaleb(-digits)
  measured: dict[str, Any] = {
    **opposing_zone_context_measured(opposing_zone),
    "planned_stop_error": "stop_inside_opposing_zone",
    "stop_reject_detail": detail,
    "planned_stop_entry_price": format(entry, "f"),
    "planned_base_stop_price": format(base_stop_price, "f"),
    "planned_base_stop_pips": format(base_stop_pips, "f"),
    "stop_max_envelope_pips": int(maximum_stop_pips),
    "stop_push_buffer_price": float(buffer),
    "atr": float(atr_value),
    "direction": direction,
  }
  if pushed_stop_price is not None:
    measured["planned_pushed_stop_price"] = format(
      pushed_stop_price.quantize(quantum, rounding=ROUND_HALF_UP), "f",
    )
  if pushed_stop_pips is not None:
    measured["planned_pushed_stop_pips"] = format(pushed_stop_pips, "f")
    measured["pushed_over_envelope_pips"] = format(
      pushed_stop_pips - Decimal(maximum_stop_pips), "f",
    )
  # How far base stop sits inside the zone (toward the far edge).
  if direction == "BUY":
    inside = base_stop_price - opposing_zone.low
  else:
    inside = opposing_zone.high - base_stop_price
  measured["base_stop_inside_zone_price"] = float(inside)
  measured["base_stop_inside_zone_pips"] = float(inside / pip) if pip > 0 else None
  return ProtectiveStopError("stop_inside_opposing_zone", measured=measured)


def _apply_opposing_zone_push(
  *,
  direction: str,
  entry: Decimal,
  base_stop_price: Decimal,
  base_stop_pips: Decimal,
  opposing_zone: OpposingZoneStopContext,
  atr_value: Decimal,
  maximum_stop_pips: int,
  pip: Decimal,
  digits: int,
) -> tuple[
  Decimal,
  Decimal,
  Decimal,
  str,
  str | None,
  Decimal | None,
  Decimal | None,
]:
  if not opposing_zone.execution_grade:
    return (
      base_stop_price,
      abs(entry - base_stop_price),
      base_stop_pips,
      "none",
      None,
      None,
      None,
    )
  if (
    base_stop_price < opposing_zone.low
    or base_stop_price > opposing_zone.high
  ):
    return (
      base_stop_price,
      abs(entry - base_stop_price),
      base_stop_pips,
      "none",
      None,
      None,
      None,
    )
  if not opposing_zone.push_beyond_zone:
    raise _stop_inside_opposing_zone_error(
      detail="push_disabled",
      direction=direction,
      entry=entry,
      base_stop_price=base_stop_price,
      base_stop_pips=base_stop_pips,
      opposing_zone=opposing_zone,
      atr_value=atr_value,
      maximum_stop_pips=maximum_stop_pips,
      pip=pip,
      digits=digits,
    )
  buffer = opposing_zone.buffer_atr * atr_value
  pushed_stop = (
    opposing_zone.low - buffer
    if direction == "BUY"
    else opposing_zone.high + buffer
  )
  quantum = Decimal(1).scaleb(-digits)
  final_stop_price = pushed_stop.quantize(quantum, rounding=ROUND_HALF_UP)
  final_distance = abs(entry - final_stop_price)
  if direction == "BUY" and final_stop_price >= entry:
    raise ProtectiveStopError(
      "Opposing-zone push is not on the losing side of entry",
      measured={
        **opposing_zone_context_measured(opposing_zone),
        "planned_base_stop_price": format(base_stop_price, "f"),
        "planned_pushed_stop_price": format(final_stop_price, "f"),
        "stop_reject_detail": "pushed_not_losing_side",
      },
    )
  if direction == "SELL" and final_stop_price <= entry:
    raise ProtectiveStopError(
      "Opposing-zone push is not on the losing side of entry",
      measured={
        **opposing_zone_context_measured(opposing_zone),
        "planned_base_stop_price": format(base_stop_price, "f"),
        "planned_pushed_stop_price": format(final_stop_price, "f"),
        "stop_reject_detail": "pushed_not_losing_side",
      },
    )
  final_stop_pips = final_distance / pip
  if final_stop_pips > Decimal(maximum_stop_pips):
    raise _stop_inside_opposing_zone_error(
      detail="pushed_exceeds_max_envelope",
      direction=direction,
      entry=entry,
      base_stop_price=base_stop_price,
      base_stop_pips=base_stop_pips,
      opposing_zone=opposing_zone,
      atr_value=atr_value,
      maximum_stop_pips=maximum_stop_pips,
      pip=pip,
      digits=digits,
      pushed_stop_price=final_stop_price,
      pushed_stop_pips=final_stop_pips,
    )
  return (
    final_stop_price,
    final_distance,
    final_stop_pips,
    "opposing_zone_push",
    opposing_zone.zone_id,
    opposing_zone.low,
    opposing_zone.high,
  )


def plan_protective_stop(
  *,
  direction: str,
  entry_price: Any,
  structure_swing: Any,
  atr: Any,
  structure_buffer_atr: Any,
  sweep_extreme: Any | None,
  wick_buffer_atr: Any,
  minimum_stop_pips: int,
  maximum_stop_pips: int,
  pip_size: Any,
  digits: int,
  opposing_zone: OpposingZoneStopContext | None = None,
) -> FinalProtectiveStopPlan:
  """Mirror ``StructureStopPlanner.PlanFinal`` operation-for-operation."""
  direction = str(direction).upper()
  entry = decimal_value(entry_price, "entry_price")
  swing = decimal_value(structure_swing, "structure_swing")
  atr_value = decimal_value(atr, "atr")
  structure_buffer = decimal_value(
    structure_buffer_atr, "structure_buffer_atr",
  )
  wick_buffer = decimal_value(wick_buffer_atr, "wick_buffer_atr")
  pip = decimal_value(pip_size, "pip_size")
  if (
    direction not in {"BUY", "SELL"}
    or entry <= 0
    or swing <= 0
    or atr_value <= 0
    or structure_buffer < 0
    or wick_buffer < 0
    or minimum_stop_pips <= 0
    or maximum_stop_pips < minimum_stop_pips
    or pip <= 0
    or digits < 0
  ):
    raise ProtectiveStopError("Structure-stop inputs are invalid")
  (
    base_stop_price,
    base_distance,
    base_stop_pips,
    raw_stop,
    clamped,
    source,
  ) = _plan_base_stop(
    direction=direction,
    entry=entry,
    structure_swing=swing,
    atr_value=atr_value,
    structure_buffer=structure_buffer,
    sweep_extreme=sweep_extreme,
    wick_buffer=wick_buffer,
    minimum_stop_pips=minimum_stop_pips,
    maximum_stop_pips=maximum_stop_pips,
    pip=pip,
    digits=digits,
  )
  if opposing_zone is None:
    return FinalProtectiveStopPlan(
      entry_price=entry,
      base_stop_price=base_stop_price,
      base_stop_pips=base_stop_pips,
      final_stop_price=base_stop_price,
      final_stop_distance=base_distance,
      final_stop_pips=base_stop_pips,
      raw_stop_price=raw_stop,
      clamped=clamped,
      source=source,
      adjustment="none",
      adjustment_zone_id=None,
      adjustment_zone_low=None,
      adjustment_zone_high=None,
    )
  (
    final_stop_price,
    final_distance,
    final_stop_pips,
    adjustment,
    adjustment_zone_id,
    adjustment_zone_low,
    adjustment_zone_high,
  ) = _apply_opposing_zone_push(
    direction=direction,
    entry=entry,
    base_stop_price=base_stop_price,
    base_stop_pips=base_stop_pips,
    opposing_zone=opposing_zone,
    atr_value=atr_value,
    maximum_stop_pips=maximum_stop_pips,
    pip=pip,
    digits=digits,
  )
  return FinalProtectiveStopPlan(
    entry_price=entry,
    base_stop_price=base_stop_price,
    base_stop_pips=base_stop_pips,
    final_stop_price=final_stop_price,
    final_stop_distance=final_distance,
    final_stop_pips=final_stop_pips,
    raw_stop_price=raw_stop,
    clamped=clamped,
    source=source,
    adjustment=adjustment,
    adjustment_zone_id=adjustment_zone_id,
    adjustment_zone_low=adjustment_zone_low,
    adjustment_zone_high=adjustment_zone_high,
  )


# Exact Reaction-family names only (see strategy_taxonomy). Demand/Supply
# Zone Reaction stay Zone-family and share the trend 40–60 envelope via the
# default path below — they must not be grouped as Reaction here.
_REACTION_FAMILY_STRATEGIES = REACTION_STRATEGIES


def volume_weighted_reference_entry(
  leg_prices: Any,
  leg_volumes: Any,
) -> Decimal:
  """Group reference entry from planned prices and resolved leg volumes.

  Uses actual broker-step volumes (e.g. 0.08/0.03 → 8/11 and 3/11), not the
  ideal declared ratios before rounding.
  """
  prices = [decimal_value(price, "planned_leg_price") for price in leg_prices]
  volumes = [
    decimal_value(volume, "resolved_leg_volume") for volume in leg_volumes
  ]
  if not prices or len(prices) != len(volumes):
    raise ProtectiveStopError(
      "weighted reference requires matching leg prices and volumes",
    )
  if any(volume <= 0 for volume in volumes):
    raise ProtectiveStopError("resolved leg volumes must be positive")
  total = sum(volumes)
  weighted = sum(price * volume for price, volume in zip(prices, volumes))
  return weighted / total


def resolve_entry_leg_lots(
  total_lots: Any,
  ratios: Any,
  *,
  step_lots: Any = Decimal("0.01"),
) -> tuple[Decimal, ...]:
  """Mirror C# ``VolumePlanner.SplitEntryVolume`` in lot space.

  AwayFromZero 2dp round on the first N-1 legs, remainder on the last — so
  0.11 at 70/30 resolves to 0.08 + 0.03 (weights 8/11, 3/11).
  """
  total = decimal_value(total_lots, "total_lots")
  step = decimal_value(step_lots, "step_lots")
  ratio_values = [decimal_value(ratio, "leg_ratio") for ratio in ratios]
  if total <= 0 or step <= 0 or not ratio_values:
    raise ProtectiveStopError("entry lot split inputs are invalid")
  if any(ratio <= 0 for ratio in ratio_values):
    raise ProtectiveStopError("entry ratios must all be positive")
  ratio_sum = sum(ratio_values)
  if abs(ratio_sum - Decimal("1")) > Decimal("0.0001"):
    raise ProtectiveStopError("entry ratios must sum to 1.0")
  if len(ratio_values) == 1:
    return (total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),)

  lot_quantum = Decimal("0.01")
  slices: list[Decimal] = []
  allocated = Decimal("0")
  for ratio in ratio_values[:-1]:
    # C#: Round(totalLots * ratio, 2, AwayFromZero); with step 0.01 that
    # lot-cent value is already broker-step aligned.
    ideal = (total * ratio).quantize(lot_quantum, rounding=ROUND_HALF_UP)
    slices.append(ideal)
    allocated += ideal
  slices.append(total - allocated)
  if any(slice_lots < step for slice_lots in slices):
    raise ProtectiveStopError(
      "resolved entry legs fall below broker step",
    )
  if sum(slices) != total:
    raise ProtectiveStopError("resolved entry legs do not sum to total lots")
  return tuple(slices)


def plan_group_protective_stop(
  *,
  direction: str,
  entry_zone_low: Any,
  entry_zone_high: Any,
  planned_leg_prices: Any,
  resolved_leg_volumes: Any,
  structure_swing: Any,
  atr: Any,
  structure_buffer_atr: Any,
  sweep_extreme: Any | None,
  wick_buffer_atr: Any,
  minimum_stop_pips: int,
  maximum_stop_pips: int,
  pip_size: Any,
  digits: int,
  opposing_zone: OpposingZoneStopContext | None = None,
) -> FinalProtectiveStopPlan:
  """One absolute group stop from structure/zone geometry.

  The stop price itself is absolute (one shared level for every entry leg) —
  e.g. entries 4108 / 4105 share SL 4103. It must clear zone edge, every
  planned entry, structural swing and sweep extreme, and must never remain
  inside the source entry zone.

  ``resolved_leg_volumes`` are relative weights only (declared ratios or
  broker lots) used to measure the 40–60 pip envelope from a group reference
  entry. They must not invent an absolute stop from assumed equity/lots.
  """
  direction = str(direction).upper()
  zone_low = decimal_value(entry_zone_low, "entry_zone_low")
  zone_high = decimal_value(entry_zone_high, "entry_zone_high")
  if zone_low <= 0 or zone_high <= 0 or zone_low >= zone_high:
    raise ProtectiveStopError("entry zone geometry is invalid")
  prices = [
    decimal_value(price, "planned_leg_price") for price in planned_leg_prices
  ]
  if not prices:
    raise ProtectiveStopError("group stop requires planned leg prices")
  reference = volume_weighted_reference_entry(prices, resolved_leg_volumes)
  swing = decimal_value(structure_swing, "structure_swing")
  atr_value = decimal_value(atr, "atr")
  structure_buffer = decimal_value(
    structure_buffer_atr, "structure_buffer_atr",
  )
  wick_buffer = decimal_value(wick_buffer_atr, "wick_buffer_atr")
  pip = decimal_value(pip_size, "pip_size")
  if (
    direction not in {"BUY", "SELL"}
    or reference <= 0
    or swing <= 0
    or atr_value <= 0
    or structure_buffer < 0
    or wick_buffer < 0
    or minimum_stop_pips <= 0
    or maximum_stop_pips < minimum_stop_pips
    or pip <= 0
    or digits < 0
  ):
    raise ProtectiveStopError("Structure-stop inputs are invalid")

  structural = (
    swing - structure_buffer * atr_value
    if direction == "BUY"
    else swing + structure_buffer * atr_value
  )
  source = "structure"
  if sweep_extreme is not None:
    sweep = decimal_value(sweep_extreme, "sweep_extreme")
    if sweep <= 0:
      raise ProtectiveStopError("Sweep extreme is invalid")
    wick_stop = (
      sweep - wick_buffer * atr_value
      if direction == "BUY"
      else sweep + wick_buffer * atr_value
    )
    wick_distance = (
      reference - wick_stop
      if direction == "BUY"
      else wick_stop - reference
    )
    if wick_distance <= 0:
      raise ProtectiveStopError(
        "Sweep invalidation is not on the losing side of entry"
      )
    if wick_distance / pip > Decimal(maximum_stop_pips):
      raise ProtectiveStopError("stop_exceeds_envelope_after_wick")
    selected = min(structural, wick_stop) if direction == "BUY" else max(
      structural, wick_stop,
    )
    if selected == wick_stop and selected != structural:
      source = "wick"
    elif selected == wick_stop:
      source = "structure_and_wick"
    structural = selected

  tick = Decimal(1).scaleb(-digits)
  # Clearance floors: stop must sit strictly outside the source zone and
  # beyond every planned entry, in addition to structural/wick invalidation.
  if direction == "BUY":
    clearance_edge = min(zone_low, min(prices)) - tick
    raw_stop = min(structural, clearance_edge)
  else:
    clearance_edge = max(zone_high, max(prices)) + tick
    raw_stop = max(structural, clearance_edge)

  raw_distance = (
    reference - raw_stop if direction == "BUY" else raw_stop - reference
  )
  if raw_distance <= 0:
    raise ProtectiveStopError(
      "Structure invalidation is not on the losing side of entry"
    )
  raw_pips = raw_distance / pip
  if raw_pips > Decimal(maximum_stop_pips):
    raise ProtectiveStopError("stop_exceeds_max_envelope")
  stop_pips = max(raw_pips, Decimal(minimum_stop_pips))
  distance = stop_pips * pip
  stop_price = (
    reference - distance if direction == "BUY" else reference + distance
  )
  stop_price = stop_price.quantize(tick, rounding=ROUND_HALF_UP)
  # Expanding to the floor must still clear zone + entries.
  if direction == "BUY":
    if stop_price >= zone_low:
      raise ProtectiveStopError("stop_inside_entry_zone")
    if any(stop_price >= price for price in prices):
      raise ProtectiveStopError("stop_not_beyond_planned_entries")
  else:
    if stop_price <= zone_high:
      raise ProtectiveStopError("stop_inside_entry_zone")
    if any(stop_price <= price for price in prices):
      raise ProtectiveStopError("stop_not_beyond_planned_entries")
  distance = abs(reference - stop_price)
  stop_pips = distance / pip
  if stop_pips > Decimal(maximum_stop_pips):
    raise ProtectiveStopError("stop_exceeds_max_envelope")
  clamped = stop_pips != raw_pips
  base_plan = FinalProtectiveStopPlan(
    entry_price=reference,
    base_stop_price=stop_price,
    base_stop_pips=stop_pips,
    final_stop_price=stop_price,
    final_stop_distance=distance,
    final_stop_pips=stop_pips,
    raw_stop_price=raw_stop,
    clamped=clamped,
    source=source,
    adjustment="none",
    adjustment_zone_id=None,
    adjustment_zone_low=None,
    adjustment_zone_high=None,
  )
  if opposing_zone is None:
    return base_plan
  (
    final_stop_price,
    final_distance,
    final_stop_pips,
    adjustment,
    adjustment_zone_id,
    adjustment_zone_low,
    adjustment_zone_high,
  ) = _apply_opposing_zone_push(
    direction=direction,
    entry=reference,
    base_stop_price=stop_price,
    base_stop_pips=stop_pips,
    opposing_zone=opposing_zone,
    atr_value=atr_value,
    maximum_stop_pips=maximum_stop_pips,
    pip=pip,
    digits=digits,
  )
  if direction == "BUY" and final_stop_price >= zone_low:
    raise ProtectiveStopError("stop_inside_entry_zone")
  if direction == "SELL" and final_stop_price <= zone_high:
    raise ProtectiveStopError("stop_inside_entry_zone")
  return FinalProtectiveStopPlan(
    entry_price=reference,
    base_stop_price=stop_price,
    base_stop_pips=stop_pips,
    final_stop_price=final_stop_price,
    final_stop_distance=final_distance,
    final_stop_pips=final_stop_pips,
    raw_stop_price=raw_stop,
    clamped=clamped,
    source=source,
    adjustment=adjustment,
    adjustment_zone_id=adjustment_zone_id,
    adjustment_zone_low=adjustment_zone_low,
    adjustment_zone_high=adjustment_zone_high,
  )


def stop_bounds_for_strategy(
  *,
  strategy: str,
  pip_size: Any,
  cfg: Any | None,
) -> tuple[int, int]:
  """Owner group envelope 40–60 for trend and zone-scale reaction families.

  ``AUTO_TRADE_REACTION_STOP_*`` keys remain for compatibility but are unused
  on the zone-scale path — reaction families share the trend 40–60 envelope
  (max 60, not the legacy 65).
  """
  if str(strategy) == "Range Box Scalp":
    minimum = int(getattr(cfg, "auto_trade_add_min_stop_pips", 30))
    sl_distance = decimal_value(
      getattr(cfg, "auto_trade_sl_distance", 6.5),
      "auto_trade_sl_distance",
    )
    pip = decimal_value(pip_size, "pip_size")
    maximum = int(sl_distance // pip)
    return minimum, maximum
  # Reaction families previously used a tighter 20–65 band; the owner group
  # stop contract applies the same 40–60 envelope as trend families.
  if str(strategy) in _REACTION_FAMILY_STRATEGIES:
    return (
      int(getattr(cfg, "auto_trade_trend_stop_min_pips", 40)),
      int(getattr(cfg, "auto_trade_trend_stop_max_pips", 60)),
    )
  return (
    int(getattr(cfg, "auto_trade_trend_stop_min_pips", 40)),
    int(getattr(cfg, "auto_trade_trend_stop_max_pips", 60)),
  )
