"""Decimal-safe protective-stop contract shared with the C# executor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


STOP_PLAN_VERSION = 2


class ProtectiveStopError(ValueError):
  """A stop cannot be constructed under the shared execution contract."""


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
    raise ProtectiveStopError("stop_inside_opposing_zone")
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
      "Opposing-zone push is not on the losing side of entry"
    )
  if direction == "SELL" and final_stop_price <= entry:
    raise ProtectiveStopError(
      "Opposing-zone push is not on the losing side of entry"
    )
  final_stop_pips = final_distance / pip
  if final_stop_pips > Decimal(maximum_stop_pips):
    raise ProtectiveStopError("stop_inside_opposing_zone")
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


def stop_bounds_for_strategy(
  *,
  strategy: str,
  pip_size: Any,
  cfg: Any | None,
) -> tuple[int, int]:
  """Use the same candidate family split as C# ``StopPipsBounds``."""
  if str(strategy) == "Range Box Scalp":
    minimum = int(getattr(cfg, "auto_trade_add_min_stop_pips", 30))
    sl_distance = decimal_value(
      getattr(cfg, "auto_trade_sl_distance", 6.5),
      "auto_trade_sl_distance",
    )
    pip = decimal_value(pip_size, "pip_size")
    maximum = int(sl_distance // pip)
    return minimum, maximum
  return (
    int(getattr(cfg, "auto_trade_trend_stop_min_pips", 40)),
    int(getattr(cfg, "auto_trade_trend_stop_max_pips", 65)),
  )
