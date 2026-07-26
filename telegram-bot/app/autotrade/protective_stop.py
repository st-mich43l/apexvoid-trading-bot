"""Decimal-safe protective-stop contract shared with the C# executor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


STOP_PLAN_VERSION = 1


class ProtectiveStopError(ValueError):
  """A stop cannot be constructed under the shared execution contract."""


@dataclass(frozen=True)
class ProtectiveStopPlan:
  stop_price: Decimal
  distance: Decimal
  stop_pips: Decimal
  raw_stop_price: Decimal
  clamped: bool
  source: str
  version: int = STOP_PLAN_VERSION

  def candidate_fields(self, *, entry_price: Decimal) -> dict[str, str | bool | int]:
    # Strings preserve the Decimal contract across Python JSON encoding.
    # C# explicitly accepts number strings for these decimal fields.
    return {
      "planned_stop_entry_price": format(entry_price, "f"),
      "planned_stop_price": format(self.stop_price, "f"),
      "planned_stop_distance": format(self.distance, "f"),
      "planned_stop_pips": format(self.stop_pips, "f"),
      "planned_stop_raw_price": format(self.raw_stop_price, "f"),
      "planned_stop_clamped": self.clamped,
      "stop_source": self.source,
      "stop_plan_version": self.version,
    }


def decimal_value(value: Any, name: str) -> Decimal:
  try:
    result = Decimal(str(value))
  except (InvalidOperation, TypeError, ValueError) as exc:
    raise ProtectiveStopError(f"{name} is invalid") from exc
  if not result.is_finite():
    raise ProtectiveStopError(f"{name} is invalid")
  return result


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
) -> ProtectiveStopPlan:
  """Mirror ``StructureStopPlanner.Plan`` operation-for-operation."""
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
  raw_stop = (
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
  return ProtectiveStopPlan(
    stop_price=stop_price,
    distance=distance,
    stop_pips=stop_pips,
    raw_stop_price=raw_stop,
    clamped=stop_pips != raw_pips,
    source=source,
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
