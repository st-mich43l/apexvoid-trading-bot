"""Immutable plan-time evidence: what was known, and decided, when a TradePlan was admitted.

The facts a later evaluation needs (the technical and executable geometry, the barrier and the room it left, the route, the cost assumptions, the scoring
rule and the configuration) used to live only in the ``candidate_published`` lifecycle list in Redis, which expires after about six days. This builds the
record that is written once, to PostgreSQL, when the plan reaches the stream (``store.record_plan_snapshot``).

Rules:
  * It is a measurement of the decision, never a new decision: nothing here is read back by admission or execution.
  * A value that was not available is ``None``. Nothing is derived from candles, and nothing is back-filled.
  * ``plan_id`` is the primary key and equals the ``group_id`` of ``auto_trade_fills`` / ``auto_trade_results`` (``v8:go_opp_<h>``); ``opportunity_id``
    (``opp_<h>``) joins to ``analysis_opportunity_events`` for Go's own envelope (exact technical targets, the Go configuration fingerprint).
"""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Iterable

SNAPSHOT_VERSION = 1

# The planning facts the executor is bound to (route, entry, stop before and after widening) and the ladder the stop was priced for.
_ADMISSION_FIELDS = (
  "planned_execution_route", "planned_market_immediate", "planned_entry_price", "planned_leg_entry_prices", "planned_entry_zone_low",
  "planned_entry_zone_high", "entry_distribution", "entry_plan_version", "planned_stop_entry_price", "planned_stop_price", "planned_stop_distance",
  "planned_stop_pips", "planned_stop_raw_price", "planned_stop_clamped", "stop_source", "stop_plan_version", "planned_base_stop_price",
  "planned_base_stop_pips", "planned_final_stop_price", "planned_final_stop_distance", "planned_final_stop_pips", "stop_adjustment",
  "planned_target_r_multiples", "planned_target_prices", "planned_target_pips", "planned_target_close_ratios", "target_policy_mode",
  "target_reward_risk", "target_room_fallback_used", "breakeven_after_r",
)


def _number(value: Any) -> float | None:
  try:
    out = float(value)
  except (TypeError, ValueError):
    return None
  return out if math.isfinite(out) else None


def _jsonable(value: Any) -> Any:
  if isinstance(value, Decimal):
    return format(value, "f")
  if isinstance(value, dict):
    return {str(k): _jsonable(v) for k, v in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_jsonable(v) for v in value]
  if isinstance(value, float) and not math.isfinite(value):
    return None
  return value


def _barrier(target_room: Any, executable_room: dict[str, Any] | None) -> dict[str, Any]:
  entry = getattr(target_room, "opposing_entry", None)
  room = executable_room or {}
  out: dict[str, Any] = {
    "barrier_present": None if target_room is None else entry is not None,
    "barrier_side": None, "barrier_low": None, "barrier_high": None, "barrier_tier": None, "barrier_members": None,
    "raw_room_pips": _number(room.get("raw_room_pips")), "usable_room_pips": _number(room.get("usable_room_pips")),
    "room_r": _number(room.get("room_r")), "room_classification": room.get("classification"),
  }
  if entry is not None:
    out.update({
      "barrier_side": getattr(entry, "side", None), "barrier_low": _number(getattr(entry, "lo", None)),
      "barrier_high": _number(getattr(entry, "hi", None)), "barrier_tier": getattr(entry, "tier", None),
      "barrier_members": _jsonable(list(getattr(entry, "members", ()) or ())) or None,
    })
  return out


def build_plan_snapshot(
  *,
  plan: Any,
  match: Any,
  opportunity_id: str,
  admitted_at: int,
  gate_measured: dict[str, Any],
  target_room: Any = None,
  executable_room: dict[str, Any] | None = None,
  spot: Any = None,
  executable_quote: float | None = None,
  execution_cost_pips: float | None = None,
  pip_size: float | None = None,
  technical_targets_pips: Iterable[Any] | None = None,
) -> dict[str, Any]:
  """The row for ``auto_trade_plan_snapshots``. ``match`` is Go's opportunity as adapted (technical facts); ``plan`` is what was published."""
  base_pips = _number(gate_measured.get("planned_base_stop_pips"))
  final_pips = _number(gate_measured.get("planned_final_stop_pips"))
  components = dict(getattr(match, "quality_components", None) or {})
  version = _number(components.get("quality_model_version"))
  bid, ask = _number(getattr(spot, "bid", None)), _number(getattr(spot, "ask", None))
  spread = None if bid is None or ask is None or not pip_size else round((ask - bid) / pip_size, 3)
  provenance = getattr(plan, "provenance", None)
  entry = getattr(plan, "entry", None)
  targets = list(getattr(plan, "targets", ()) or ())
  row: dict[str, Any] = {
    "plan_id": plan.plan_id,
    "opportunity_id": opportunity_id,
    "match_id": getattr(match, "match_id", None),
    "thesis_id": getattr(plan, "thesis_id", None),
    "symbol": plan.symbol,
    "strategy": getattr(match, "strategy", None),
    "direction": getattr(match, "direction", None),
    "opportunity_created_at": getattr(match, "issued_at", None),
    "admitted_at": int(admitted_at),
    "snapshot_version": SNAPSHOT_VERSION,
    "quality_model_version": None if version is None else int(version),
    "quality_overall": _number(getattr(match, "quality_overall", None)),
    "config_fingerprint": (getattr(provenance, "config_fingerprint", None) or None),
    "analysis_engine_version": (getattr(provenance, "analysis_engine_version", None) or None),
    "technical_entry_low": _number(getattr(match, "entry_low", None)),
    "technical_entry_high": _number(getattr(match, "entry_high", None)),
    "technical_invalidation": _number(getattr(match, "go_invalidation_price", None)),
    "technical_stop_pips": base_pips,
    "technical_targets_pips": _jsonable(list(technical_targets_pips if technical_targets_pips is not None else (getattr(match, "targets_pips", ()) or ()))) or None,
    "entry_route": gate_measured.get("planned_execution_route"),
    "planned_entry_price": _number(gate_measured.get("planned_entry_price")),
    "final_stop_price": _number(getattr(getattr(plan, "stop", None), "price", None)),
    "final_stop_pips": final_pips,
    "stop_widening_ratio": round(final_pips / base_pips, 4) if final_pips is not None and base_pips else None,
    "stop_adjustment": gate_measured.get("stop_adjustment"),
    "executable_quote": _number(executable_quote),
    "spot_bid": bid, "spot_ask": ask, "spot_spread_pips": spread, "spot_ts": getattr(spot, "ts", None),
    "execution_cost_pips": _number(execution_cost_pips),
    "max_spread_ticks": getattr(entry, "max_spread_ticks", None),
    "max_slippage_ticks": getattr(entry, "max_slippage_ticks", None),
    "targets": [{"id": t.target_id, "price": format(t.price, "f"), "close_ratio": format(t.close_ratio, "f")} for t in targets],
  }
  row.update(_barrier(target_room, executable_room))
  row["quality_components"] = _jsonable(components) or None
  row["admission"] = _jsonable({
    **{k: gate_measured[k] for k in _ADMISSION_FIELDS if k in gate_measured},
    "target_room": getattr(target_room, "measured", None),
    "executable_room": executable_room,
  })
  row["plan"] = _jsonable(plan.to_dict())
  return row
