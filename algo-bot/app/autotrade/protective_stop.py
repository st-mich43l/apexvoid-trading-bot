"""Decimal-safe protective-stop contract shared with the C# executor."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import math
from typing import Any

from app.autotrade.strategy_taxonomy import RANGE_STRATEGIES, REACTION_STRATEGIES


def _default_runtime_cfg() -> Any:
  from app.core.config import runtime_config

  return runtime_config


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
  if cfg is None:
    cfg = _default_runtime_cfg()
  policy_cfg = cfg.execution.policy
  stops_cfg = cfg.execution.stops
  scaling_add_cfg = cfg.execution.scaling.add
  max_width_atr = decimal_value(
    policy_cfg.execution_zone_max_width_atr
    if policy_cfg.execution_zone_max_width_atr is not None
    else 2.0,
    "auto_trade_execution_zone_max_width_atr",
  )
  max_width_pips = decimal_value(
    policy_cfg.execution_zone_max_width_pips
    if policy_cfg.execution_zone_max_width_pips is not None
    else 100.0,
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
      stops_cfg.stop_push_beyond_zone
      if stops_cfg.stop_push_beyond_zone is not None
      else True
    ),
    buffer_atr=decimal_value(
      scaling_add_cfg.stop_buffer_atr
      if scaling_add_cfg.stop_buffer_atr is not None
      else 0.3,
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


# Exact Reaction-family names only (see strategy_taxonomy). Zone / Demand /
# Supply are an independent supply_demand family — they must not share this
# room-synced stop path.
_REACTION_FAMILY_STRATEGIES = REACTION_STRATEGIES

# Taxonomy reaction only: Key / Session / Trendline. SL tracks room-capped TP.
_ROOM_SYNCED_STOP_STRATEGIES = frozenset(REACTION_STRATEGIES)

# Range scalp family: thin room (15/20) tracks TP; independent of reaction.
_SCALP_ROOM_SYNCED_STOP_STRATEGIES = frozenset(RANGE_STRATEGIES)


def uses_reaction_room_stop(strategy: str) -> bool:
  return str(strategy or "") in _ROOM_SYNCED_STOP_STRATEGIES


def uses_scalp_room_stop(strategy: str) -> bool:
  return str(strategy or "") in _SCALP_ROOM_SYNCED_STOP_STRATEGIES


def primary_tp_pips_from_match(match: Any) -> float | None:
  """Largest positive target (or target_cap) used to sync reaction SL."""
  targets = tuple(
    float(value)
    for value in (getattr(match, "targets_pips", None) or ())
    if value is not None
  )
  positive = [value for value in targets if math.isfinite(value) and value > 0]
  if positive:
    return max(positive)
  cap = getattr(match, "target_cap_pips", None)
  if cap is None:
    return None
  try:
    value = float(cap)
  except (TypeError, ValueError):
    return None
  return value if math.isfinite(value) and value > 0 else None


def stop_bounds_for_reaction_room(
  *,
  strategy: str,
  primary_tp_pips: float | int | None,
  pip_size: Any,
  cfg: Any | None,
  for_group_stop: bool = False,
  symbol: str | None = None,
) -> tuple[int, int, dict[str, Any]]:
  """Pin reaction/scalp SL envelope to room-synced primary TP (≈1:1).

  Returns ``(minimum_stop_pips, maximum_stop_pips, measured)``. When primary
  TP is missing, falls back to the strategy envelope. Zone / Demand / Supply
  never use this path (independent family).

  ``for_group_stop`` must be set whenever the caller will size a multi-leg
  absolute group stop (plan_group_protective_stop), not a single-leg one.
  A wide room pushes ``desired`` up near/at ``cap_pips``, and pinning
  ``minimum`` to that same value collapses [min, max] to a single point
  (live 2026-08-06: min=max=60 on live Supply/Demand candidates). A single
  absolute stop price is a different pip distance from every leg by
  construction, so a single-point band makes every multi-leg group stop
  with any real leg spread mathematically unsatisfiable — it always
  rejected as stop_exceeds_envelope_furthest_leg. Group stops keep the
  floor at ``floor_pips`` regardless of ``desired`` so [min, max] always
  has real width to place legs across.
  """
  fallback = stop_bounds_for_strategy(
    strategy=strategy, pip_size=pip_size, cfg=cfg, symbol=symbol,
  )
  is_reaction = uses_reaction_room_stop(strategy)
  is_scalp = uses_scalp_room_stop(strategy)
  if not is_reaction and not is_scalp:
    return (
      fallback[0],
      fallback[1],
      {
        "stop_bounds_source": "strategy_default",
        "primary_tp_pips": None,
        "desired_stop_pips": None,
      },
    )
  try:
    primary = float(primary_tp_pips) if primary_tp_pips is not None else 0.0
  except (TypeError, ValueError):
    primary = 0.0
  if not math.isfinite(primary) or primary <= 0:
    return (
      fallback[0],
      fallback[1],
      {
        "stop_bounds_source": "legacy_envelope",
        "primary_tp_pips": None,
        "desired_stop_pips": None,
      },
    )
  if cfg is None:
    cfg = _default_runtime_cfg()
  execution = cfg.execution
  resolver = getattr(cfg, "for_instrument", None)
  if symbol and callable(resolver):
    execution = resolver(symbol).execution
  if is_scalp:
    min_rr = float(
      execution.range.min_rr
      if execution.range.min_rr is not None
      else 1.0
    ) or 1.0
    floor_pips = int(
      execution.range.room_stop_floor_pips
      if execution.range.room_stop_floor_pips is not None
      else 15
    ) or 15
    # Prefer Range Box max (sl_distance) when present; else trend cap.
    cap_pips = int(fallback[1]) if fallback[1] > 0 else 60
    source = "scalp_room"
  else:
    min_rr = float(
      execution.reaction.room_stop_min_rr
      if execution.reaction.room_stop_min_rr is not None
      else 1.0
    ) or 1.0
    # Owner envelope for reaction families is 40–60 on XAU. FX overrides
    # this slice (12–25) so EURUSD structure can actually plan a stop.
    reaction_min = int(
      execution.reaction.stop_min_pips
      if execution.reaction.stop_min_pips is not None
      else 40
    ) or 40
    room_floor = int(
      execution.stops.reaction.room_floor_pips
      if execution.stops.reaction.room_floor_pips is not None
      else reaction_min
    ) or reaction_min
    floor_pips = max(reaction_min, room_floor)
    reaction_max = int(
      execution.reaction.stop_max_pips
      if execution.reaction.stop_max_pips is not None
      else 60
    ) or 60
    trend_cap = int(
      execution.trend.stop_max_pips
      if execution.trend.stop_max_pips is not None
      else 60
    ) or 60
    cap_pips = max(floor_pips, min(reaction_max, trend_cap))
    source = "reaction_room"
  if not math.isfinite(min_rr) or min_rr <= 0:
    min_rr = 1.0
  floor_pips = max(1, floor_pips)
  cap_pips = max(floor_pips, cap_pips)
  desired = max(floor_pips, int(math.ceil(primary / min_rr)))
  fixed_rr = False
  if symbol:
    from app.core.instrument_geometry import fixed_reward_risk
    fixed_rr = fixed_reward_risk(symbol, cfg) is not None
  if for_group_stop or fixed_rr:
    # Group stops keep [min, max] wide. Fixed-RR instruments plan the stop
    # from structure first, then place TP at the configured R multiple.
    minimum = floor_pips
  else:
    # Single-leg XAU: pin toward desired for a genuine ~1:1 stop against
    # the room-capped target.
    minimum = min(desired, cap_pips)
  maximum = cap_pips
  measured: dict[str, Any] = {
    "stop_bounds_source": source,
    "primary_tp_pips": round(primary, 3),
    "desired_stop_pips": desired,
    "stop_bounds_for_group_stop": for_group_stop,
    "fixed_rr_targeting": fixed_rr,
  }
  if is_scalp:
    measured["range_room_stop_floor_pips"] = floor_pips
  else:
    measured["reaction_room_stop_min_rr"] = min_rr
    measured["reaction_room_stop_floor_pips"] = floor_pips
  return minimum, maximum, measured


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
  broker lots) used as a group reference entry for plan telemetry still.
  The envelope is enforced against every planned leg with a hard floor from
  the nearest leg (never under ``minimum_stop_pips``) and a hard furthest-leg
  cap: if floor geometry leaves furthest distance over ``maximum_stop_pips``,
  reject — never soft-expand past the owner envelope. Volumes must not invent
  an absolute stop from assumed equity/lots.
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
  max_pips = Decimal(maximum_stop_pips)
  min_pips = Decimal(minimum_stop_pips)
  # Hard floor from nearest planned leg; furthest-leg max is always hard —
  # reject if floor geometry overshoots maximum_stop_pips (no soft expand).
  nearest = min(prices) if direction == "BUY" else max(prices)
  furthest = max(prices) if direction == "BUY" else min(prices)
  if direction == "BUY":
    floor_stop = nearest - min_pips * pip
    cap_stop = furthest - max_pips * pip
    stop_price = min(raw_stop, floor_stop)
    if stop_price < cap_stop and (nearest - cap_stop) / pip + Decimal("0.000001") >= min_pips:
      stop_price = cap_stop
  else:
    floor_stop = nearest + min_pips * pip
    cap_stop = furthest + max_pips * pip
    stop_price = max(raw_stop, floor_stop)
    if stop_price > cap_stop and (cap_stop - nearest) / pip + Decimal("0.000001") >= min_pips:
      stop_price = cap_stop
  stop_price = stop_price.quantize(tick, rounding=ROUND_HALF_UP)

  def _leg_pips(stop: Decimal) -> list[Decimal]:
    if direction == "BUY":
      return [(price - stop) / pip for price in prices]
    return [(stop - price) / pip for price in prices]

  leg_pips = _leg_pips(stop_price)
  tightest = min(leg_pips)
  widest = max(leg_pips)
  if tightest + Decimal("0.000001") < min_pips:
    # Quantize drifted through the floor — expand away from entries.
    if direction == "BUY":
      stop_price = (nearest - min_pips * pip).quantize(
        tick, rounding=ROUND_FLOOR,
      )
    else:
      stop_price = (nearest + min_pips * pip).quantize(
        tick, rounding=ROUND_CEILING,
      )
    leg_pips = _leg_pips(stop_price)
    tightest = min(leg_pips)
    widest = max(leg_pips)
  if tightest + Decimal("0.000001") < min_pips:
    raise ProtectiveStopError(
      "stop_cannot_clear_leg_floor",
      measured={
        "stop_reject_detail": "cannot_clear_leg_floor",
        "stop_min_envelope_pips": int(minimum_stop_pips),
        "stop_max_envelope_pips": int(maximum_stop_pips),
        "nearest_leg_stop_pips": format(tightest, "f"),
        "furthest_leg_stop_pips": format(widest, "f"),
        "planned_leg_prices": [format(price, "f") for price in prices],
        "planned_stop_price": format(stop_price, "f"),
      },
    )
  if widest > max_pips + Decimal("0.000001"):
    raise ProtectiveStopError(
      "stop_exceeds_envelope_furthest_leg",
      measured={
        "stop_reject_detail": "furthest_leg_over_max",
        "stop_min_envelope_pips": int(minimum_stop_pips),
        "stop_max_envelope_pips": int(maximum_stop_pips),
        "nearest_leg_stop_pips": format(tightest, "f"),
        "furthest_leg_stop_pips": format(widest, "f"),
        "planned_leg_prices": [format(price, "f") for price in prices],
        "planned_stop_price": format(stop_price, "f"),
      },
    )
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
  # Report worst-leg risk (furthest entry → stop) for RR / sizing telemetry.
  stop_pips = max(leg_pips)
  distance = stop_pips * pip
  clamped = stop_price != raw_stop.quantize(tick, rounding=ROUND_HALF_UP) or (
    stop_pips != raw_pips
  )
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
  pushed_leg_pips = (
    [(price - final_stop_price) / pip for price in prices]
    if direction == "BUY"
    else [(final_stop_price - price) / pip for price in prices]
  )
  if min(pushed_leg_pips) + Decimal("0.000001") < Decimal(minimum_stop_pips):
    raise ProtectiveStopError(
      "stop_cannot_clear_leg_floor",
      measured={
        "stop_reject_detail": "opposing_push_breaks_leg_floor",
        "stop_min_envelope_pips": int(minimum_stop_pips),
        "stop_max_envelope_pips": int(maximum_stop_pips),
        "nearest_leg_stop_pips": format(min(pushed_leg_pips), "f"),
        "furthest_leg_stop_pips": format(max(pushed_leg_pips), "f"),
      },
    )
  # Soft max after opposing push: allow furthest > max when floor must hold.
  return FinalProtectiveStopPlan(
    entry_price=reference,
    base_stop_price=stop_price,
    base_stop_pips=stop_pips,
    final_stop_price=final_stop_price,
    final_stop_distance=max(pushed_leg_pips) * pip,
    final_stop_pips=max(pushed_leg_pips),
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
  symbol: str | None = None,
) -> tuple[int, int]:
  """Owner group envelope 40–60 for trend and zone-scale reaction families.

  Prefer ``stop_bounds_for_reaction_room`` when a room-capped primary TP is
  known — that path pins SL near TP inside the owner reaction envelope
  (floor ``execution.reaction.stop_min_pips``, default 40; cap
  ``execution.reaction.stop_max_pips``, default 60).
  ``AUTO_TRADE_REACTION_STOP_*`` keys remain for compatibility.
  """
  if cfg is None:
    cfg = _default_runtime_cfg()
  execution = cfg.execution
  resolver = getattr(cfg, "for_instrument", None)
  if symbol and callable(resolver):
    execution = resolver(symbol).execution
  scaling_add_cfg = execution.scaling.add
  stops_cfg = execution.stops
  trend_stops_cfg = execution.stops.trend
  if str(strategy) == "Range Box Scalp":
    minimum = int(
      scaling_add_cfg.min_stop_pips
      if scaling_add_cfg.min_stop_pips is not None
      else 30
    )
    sl_distance = decimal_value(
      stops_cfg.sl_distance
      if stops_cfg.sl_distance is not None
      else 6.5,
      "auto_trade_sl_distance",
    )
    pip = decimal_value(pip_size, "pip_size")
    maximum = int(sl_distance // pip)
    return minimum, maximum
  trend_min = int(
    trend_stops_cfg.minimum_pips
    if trend_stops_cfg.minimum_pips is not None
    else 40
  )
  trend_max = int(
    execution.trend.stop_max_pips
    if execution.trend.stop_max_pips is not None
    else 60
  )
  # Reaction taxonomy fallback when room TP is unavailable. Zone / Demand /
  # Supply use the same numeric envelope as an independent family default.
  if str(strategy) in _REACTION_FAMILY_STRATEGIES:
    return trend_min, trend_max
  return trend_min, trend_max
