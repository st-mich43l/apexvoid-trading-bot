"""Active open-trade exposure gates for new autonomous plans.

When an order is already active:
- opposing direction within ``min_price_separation`` (absolute |Δprice|) is
  blocked (SELL @ 4063 → BUY blocked between 4048 and 4078 when separation is 15)
- same-direction opportunity is allowed at 60% size on a single leg only
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_OPEN_V7_STAGES = frozenset({
  "PartiallyOpen",
  "FullyOpen",
  "Open",  # legacy synonym
  "partially_open",
  "fully_open",
  "managing",
  "partially_closed",
})
_OPEN_V7_GROUP_STAGES = frozenset({
  "partially_open",
  "fully_open",
  "managing",
  "partially_closed",
})


@dataclass(frozen=True)
class ActiveExposure:
  direction: str
  entry_price: float
  source: str
  group_id: str | None = None
  plan_id: str | None = None
  position_id: int | None = None
  remaining_volume: float | None = None


@dataclass(frozen=True)
class ExposureDecision:
  """Result of comparing a candidate against live open exposure."""

  block: bool
  reason_code: str | None = None
  message: str = ""
  same_direction_stack: bool = False
  measured: dict[str, Any] | None = None


def normalize_direction(value: object) -> str | None:
  if value is None:
    return None
  if isinstance(value, int):
    if value == 0:
      return "BUY"
    if value == 1:
      return "SELL"
    return None
  text = str(value).strip().upper()
  if text in {"BUY", "B", "0"}:
    return "BUY"
  if text in {"SELL", "S", "1"}:
    return "SELL"
  return None


def _as_float(value: object) -> float | None:
  try:
    number = float(value)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    return None
  if number != number:  # NaN
    return None
  return number


def _payload_get(payload: dict[str, Any], *keys: str) -> Any:
  """Read snake_case or PascalCase Redis JSON keys."""
  for key in keys:
    if key in payload and payload[key] is not None:
      return payload[key]
  return None


def _payload_entry_price(payload: dict[str, Any]) -> float | None:
  for key in (
    "entry_price",
    "EntryPrice",
    "group_weighted_fill_price",
    "GroupWeightedFillPrice",
    "entry_fill_price",
    "EntryFillPrice",
  ):
    price = _as_float(payload.get(key))
    if price is not None and price > 0:
      return price
  return None


def _payload_remaining(payload: dict[str, Any]) -> float | None:
  for key in (
    "remaining_volume",
    "RemainingVolume",
    "total_filled_volume",
    "TotalFilledVolume",
  ):
    if key not in payload:
      continue
    value = _as_float(payload.get(key))
    if value is not None:
      return value
  return None


def _is_scale_in_child(payload: dict[str, Any]) -> bool:
  parent = _payload_get(payload, "parent_group_id", "ParentGroupId")
  return bool(str(parent or "").strip())


async def load_active_exposures(client: Any) -> list[ActiveExposure]:
  """Load open V6 position states and V7 plan runtimes."""
  exposures: list[ActiveExposure] = []
  exposures.extend(await _load_v6_position_exposures(client))
  exposures.extend(await _load_v7_plan_exposures(client))
  return exposures


async def _load_v6_position_exposures(client: Any) -> list[ActiveExposure]:
  raw_ids = await client.smembers("auto_trade:positions")
  if not raw_ids:
    return []
  out: list[ActiveExposure] = []
  for raw_id in raw_ids:
    token = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
    try:
      position_id = int(token)
    except (TypeError, ValueError):
      continue
    raw = await client.get(f"auto_trade:position:{position_id}")
    if not raw:
      continue
    try:
      payload = json.loads(
        raw.decode() if isinstance(raw, bytes) else str(raw)
      )
    except (TypeError, ValueError, json.JSONDecodeError):
      continue
    if not isinstance(payload, dict) or _is_scale_in_child(payload):
      continue
    remaining = _payload_remaining(payload)
    if remaining is not None and remaining <= 0:
      continue
    direction = normalize_direction(
      _payload_get(payload, "direction", "Direction")
    )
    entry = _payload_entry_price(payload)
    if direction is None or entry is None:
      continue
    out.append(ActiveExposure(
      direction=direction,
      entry_price=entry,
      source="v6_position",
      group_id=str(
        _payload_get(payload, "group_id", "GroupId") or ""
      ) or None,
      position_id=position_id,
      remaining_volume=remaining,
    ))
  return out


async def _load_v7_plan_exposures(client: Any) -> list[ActiveExposure]:
  raw = await client.get("execution:trade_plan_runtime_ids")
  if not raw:
    return []
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  plan_ids = [item for item in text.split(",") if item.strip()]
  out: list[ActiveExposure] = []
  for plan_id in plan_ids:
    state_raw = await client.get(f"execution:plan_runtime:{plan_id}")
    if not state_raw:
      continue
    try:
      payload = json.loads(
        state_raw.decode() if isinstance(state_raw, bytes) else str(state_raw)
      )
    except (TypeError, ValueError, json.JSONDecodeError):
      continue
    if not isinstance(payload, dict):
      continue
    # TradePlanStateJsonContext serializes PascalCase property names
    # (Stage/GroupStage/Direction) with string enums — accept both shapes.
    stage = str(_payload_get(payload, "stage", "Stage") or "")
    group_stage = str(_payload_get(payload, "group_stage", "GroupStage") or "")
    if stage not in _OPEN_V7_STAGES and group_stage not in _OPEN_V7_GROUP_STAGES:
      continue
    remaining = _payload_remaining(payload)
    if remaining is not None and remaining <= 0:
      continue
    # Still-open means at least one filled/remaining lot or submitted legs.
    filled = _as_float(
      _payload_get(payload, "total_filled_volume", "TotalFilledVolume")
    ) or 0.0
    if filled <= 0 and (remaining is None or remaining <= 0):
      continue
    direction = normalize_direction(
      _payload_get(payload, "direction", "Direction")
    )
    entry = (
      _as_float(
        _payload_get(
          payload,
          "group_weighted_fill_price",
          "GroupWeightedFillPrice",
        )
      )
      or _as_float(
        _payload_get(payload, "entry_fill_price", "EntryFillPrice")
      )
    )
    if direction is None or entry is None or entry <= 0:
      continue
    out.append(ActiveExposure(
      direction=direction,
      entry_price=float(entry),
      source="v7_plan",
      plan_id=str(
        _payload_get(payload, "plan_id", "PlanId") or plan_id
      ),
      group_id=str(
        _payload_get(payload, "setup_id", "SetupId") or ""
      ) or None,
      remaining_volume=remaining if remaining is not None else filled,
    ))
  return out


def evaluate_entry_against_exposure(
  *,
  direction: str,
  entry_price: float,
  exposures: list[ActiveExposure],
  min_price_separation: float = 15.0,
  same_direction_size_fraction: float = 0.60,
) -> ExposureDecision:
  """Apply opposing-distance and same-direction stack rules."""
  wanted = normalize_direction(direction)
  if wanted is None or entry_price <= 0:
    return ExposureDecision(block=False)
  opposite = "SELL" if wanted == "BUY" else "BUY"
  separation = max(0.0, float(min_price_separation))

  for active in exposures:
    if active.direction != opposite:
      continue
    distance = abs(float(entry_price) - float(active.entry_price))
    if distance < separation:
      return ExposureDecision(
        block=True,
        reason_code="opposing_active_too_close",
        message=(
          f"{wanted} entry {entry_price:.2f} is only {distance:.2f} from "
          f"active {active.direction} @ {active.entry_price:.2f}; "
          f"require >= {separation:.0f} price separation"
        ),
        measured={
          "active_direction": active.direction,
          "active_entry_price": active.entry_price,
          "candidate_entry_price": entry_price,
          "price_distance": distance,
          "min_price_separation": separation,
          "active_source": active.source,
          "active_plan_id": active.plan_id,
          "active_group_id": active.group_id,
          "active_position_id": active.position_id,
        },
      )

  same = [item for item in exposures if item.direction == wanted]
  if not same:
    return ExposureDecision(block=False)
  primary = same[0]
  fraction = max(0.01, min(1.0, float(same_direction_size_fraction)))
  return ExposureDecision(
    block=False,
    same_direction_stack=True,
    reason_code="same_direction_stack",
    message=(
      f"same-direction stack on active {wanted} @ {primary.entry_price:.2f}: "
      f"size {fraction:.0%} on a single leg"
    ),
    measured={
      "active_direction": primary.direction,
      "active_entry_price": primary.entry_price,
      "same_direction_size_fraction": fraction,
      "active_source": primary.source,
      "active_plan_id": primary.plan_id,
      "active_group_id": primary.group_id,
      "active_position_id": primary.position_id,
      "same_direction_count": len(same),
    },
  )


def apply_same_direction_stack_sizing(
  measured: dict[str, Any],
  *,
  size_fraction: float = 0.60,
) -> dict[str, Any]:
  """Force single-leg entry and reduce risk multiplier for a stack add."""
  out = dict(measured)
  fraction = max(0.01, min(1.0, float(size_fraction)))
  current = float(out.get("effective_risk_multiplier") or 1.0)
  out["effective_risk_multiplier"] = round(current * fraction, 6)
  out["entry_distribution"] = "single"
  out["same_direction_stack"] = True
  out["same_direction_size_fraction"] = fraction
  route = str(out.get("planned_execution_route") or "").strip().lower()
  entry = out.get("planned_entry_price")
  if route in {
    "limit_ladder",
    "market_with_limit_scale",
    "zone_scale",
    "reaction_scale",
  }:
    out["planned_execution_route"] = (
      "market" if route == "market_with_limit_scale" else "single_limit"
    )
  if entry is not None:
    out["planned_leg_entry_prices"] = [entry]
    out["planned_leg_volume_ratios"] = [1.0]
  return out
