"""Active open-trade exposure gates for new autonomous plans.

When an order is already active **on the same instrument**:
- opposing direction within ``min_price_separation`` (absolute |Δprice|) is
  blocked (SELL @ 4063 → BUY blocked between 4048 and 4078 when separation is 15)
- same-direction non-scalp adds are allowed only after every open same-dir
  plan has **booked** TP2 (``HighestBookedTargetIndex >= 1``) **and** the new
  candidate is Tier A; size stays ``same_direction_size_fraction`` (default 60%)
  on a single leg
- same-direction scalp adds may stack at that fraction without waiting for TP2
  / Tier A when ``allow_same_direction_stack`` is true

Live 2026-08-17: GBPJPY SELL @ 215.91 blocked EURUSD SELL, and VIP XAU SELL
@ 4414.11 blocked GBPJPY, because this module compared direction/price with
no symbol. Engine ``HasBlockingSameDirectionLivePlan`` already filters by
symbol; Python must match that.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Target ladder indexes: TP1=0, TP2=1. Non-scalp same-dir unlock requires a
# real booked close at/after TP2 — not merely NextTargetIndex after a deferral.
SAME_DIRECTION_UNLOCK_BOOKED_TARGET_INDEX = 1

# Broker aliases that must not leak exposure across instruments.
_SYMBOL_ALIASES = {
  "XAUUSD": "XAU",
  "GOLD": "XAU",
}

_OPEN_TRADE_PLAN_STAGES = frozenset({
  "PartiallyOpen",
  "FullyOpen",
  "Open",  # legacy synonym
  "partially_open",
  "fully_open",
  "managing",
  "partially_closed",
})
_OPEN_TRADE_PLAN_GROUP_STAGES = frozenset({
  "partially_open",
  "fully_open",
  "managing",
  "partially_closed",
})
# Pending/submitted plans already occupy the symbol. Live 2026-08-17 GBPJPY
# duplicated two Key Level sells because only FullyOpen fills counted.
_PENDING_TRADE_PLAN_STAGES = frozenset({
  "Received",
  "Submitting",
  "Submitted",
  "received",
  "submitting",
  "submitted",
})
_PENDING_TRADE_PLAN_GROUP_STAGES = frozenset({
  "received",
  "submitting",
  "submitted",
})


@dataclass(frozen=True)
class ActiveExposure:
  direction: str
  entry_price: float
  source: str
  symbol: str | None = None
  group_id: str | None = None
  plan_id: str | None = None
  position_id: int | None = None
  remaining_volume: float | None = None
  # None when unknown (V6 / pre-schema-3). Treated as not-yet-TP2 for unlock.
  highest_booked_target_index: int | None = None


@dataclass(frozen=True)
class ExposureDecision:
  """Result of comparing a candidate against live open exposure."""

  block: bool
  reason_code: str | None = None
  message: str = ""
  same_direction_stack: bool = False
  measured: dict[str, Any] | None = None


def normalize_symbol(value: object) -> str | None:
  """Canonical instrument key so XAUUSD and XAU occupy the same book."""
  text = str(value or "").strip().upper()
  if not text:
    return None
  return _SYMBOL_ALIASES.get(text, text)


def same_instrument(left: object, right: object) -> bool:
  a = normalize_symbol(left)
  b = normalize_symbol(right)
  return a is not None and a == b


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


def _as_int(value: object) -> int | None:
  try:
    number = int(value)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    return None
  return number


def same_direction_tp2_booked(exposure: ActiveExposure) -> bool:
  """True when a real TP2 (or later) partial was booked on this exposure."""
  booked = exposure.highest_booked_target_index
  return (
    booked is not None
    and booked >= SAME_DIRECTION_UNLOCK_BOOKED_TARGET_INDEX
  )


def same_direction_stack_unlocked(exposures: list[ActiveExposure]) -> bool:
  """Unlock non-scalp stack only when every same-dir open has booked TP2."""
  if not exposures:
    return True
  return all(same_direction_tp2_booked(item) for item in exposures)


def _payload_get(payload: dict[str, Any], *keys: str) -> Any:
  """Read snake_case or PascalCase Redis JSON keys."""
  for key in keys:
    if key in payload and payload[key] is not None:
      return payload[key]
  return None


def _payload_symbol(payload: dict[str, Any]) -> str | None:
  return normalize_symbol(_payload_get(payload, "symbol", "Symbol"))


def _payload_entry_price(payload: dict[str, Any]) -> float | None:
  for key in (
    "entry_price",
    "EntryPrice",
    "group_weighted_fill_price",
    "GroupWeightedFillPrice",
    "entry_fill_price",
    "EntryFillPrice",
    "intended_entry_price",
    "IntendedEntryPrice",
  ):
    price = _as_float(payload.get(key))
    if price is not None and price > 0:
      return price
  legs = _payload_get(payload, "legs", "Legs")
  if isinstance(legs, list):
    for leg in legs:
      if not isinstance(leg, dict):
        continue
      price = _as_float(
        _payload_get(leg, "intended_price", "IntendedPrice")
      )
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


async def _mget_or_get(client: Any, keys: list[str]) -> list[Any]:
  """Read a Redis key batch in one round-trip when the client supports it.

  Production uses ``redis.asyncio.Redis.mget``.  The small fallback keeps
  lightweight test doubles and alternate Redis adapters compatible without
  making the production hot path N+1 again.
  """
  if not keys:
    return []
  mget = getattr(client, "mget", None)
  if callable(mget):
    values = await mget(keys)
    return list(values or ())
  return [await client.get(key) for key in keys]


async def load_active_exposures(
  client: Any,
  *,
  symbol: str | None = None,
) -> list[ActiveExposure]:
  """Load open V6 positions plus live V8 plans, including pending/submitted.

  Received/Submitted plans occupy the symbol before the first fill. Omitting
  them let two GBPJPY Key Level sells publish 5s apart (2026-08-17).

  Pass ``symbol`` to keep only that instrument's book (HFS reconcile / any
  per-symbol caller). Omit only when the caller will filter next.
  """
  exposures: list[ActiveExposure] = []
  exposures.extend(await _load_v6_position_exposures(client))
  exposures.extend(await _load_trade_plan_exposures(client))
  return filter_exposures_for_symbol(exposures, symbol)


def filter_exposures_for_symbol(
  exposures: list[ActiveExposure],
  symbol: str | None,
) -> list[ActiveExposure]:
  """Keep same-instrument rows only when ``symbol`` is set.

  Missing-symbol rows are dropped in that mode so a GBPJPY fill cannot lock
  EURUSD/XAU (and a VIP gold pending cannot lock GBPJPY). When the caller
  omits ``symbol``, the full list is returned.
  """
  wanted = normalize_symbol(symbol)
  if wanted is None:
    return list(exposures)
  out: list[ActiveExposure] = []
  for item in exposures:
    active = normalize_symbol(item.symbol)
    if active is None or active != wanted:
      continue
    out.append(item)
  return out


async def _load_v6_position_exposures(client: Any) -> list[ActiveExposure]:
  raw_ids = await client.smembers("auto_trade:positions")
  if not raw_ids:
    return []
  position_ids: list[int] = []
  for raw_id in raw_ids:
    token = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
    try:
      position_ids.append(int(token))
    except (TypeError, ValueError):
      continue
  raw_positions = await _mget_or_get(
    client,
    [f"auto_trade:position:{position_id}" for position_id in position_ids],
  )
  out: list[ActiveExposure] = []
  for position_id, raw in zip(position_ids, raw_positions, strict=False):
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
      symbol=_payload_symbol(payload),
      group_id=str(
        _payload_get(payload, "group_id", "GroupId") or ""
      ) or None,
      position_id=position_id,
      remaining_volume=remaining,
    ))
  return out


async def _load_trade_plan_exposures(client: Any) -> list[ActiveExposure]:
  raw = await client.get("execution:trade_plan_runtime_ids")
  if not raw:
    return []
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  plan_ids = [item for item in text.split(",") if item.strip()]
  raw_states = await _mget_or_get(
    client,
    [f"execution:plan_runtime:{plan_id}" for plan_id in plan_ids],
  )
  out: list[ActiveExposure] = []
  for plan_id, state_raw in zip(plan_ids, raw_states, strict=False):
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
    is_pending = (
      stage in _PENDING_TRADE_PLAN_STAGES
      or group_stage in _PENDING_TRADE_PLAN_GROUP_STAGES
    )
    is_open = (
      stage in _OPEN_TRADE_PLAN_STAGES
      or group_stage in _OPEN_TRADE_PLAN_GROUP_STAGES
    )
    if not is_pending and not is_open:
      continue
    remaining = _payload_remaining(payload)
    filled = _as_float(
      _payload_get(payload, "total_filled_volume", "TotalFilledVolume")
    ) or 0.0
    if not is_pending:
      if remaining is not None and remaining <= 0:
        continue
      # Still-open means at least one filled/remaining lot or submitted legs.
      if filled <= 0 and (remaining is None or remaining <= 0):
        continue
    direction = normalize_direction(
      _payload_get(payload, "direction", "Direction")
    )
    entry = _payload_entry_price(payload)
    if direction is None or entry is None or entry <= 0:
      continue
    booked_raw = _payload_get(
      payload,
      "highest_booked_target_index",
      "HighestBookedTargetIndex",
    )
    booked = _as_int(booked_raw)
    # Schema/default -1 means nothing booked yet.
    if booked is not None and booked < 0:
      booked = None
    out.append(ActiveExposure(
      direction=direction,
      entry_price=float(entry),
      source="v8_plan",
      symbol=_payload_symbol(payload),
      plan_id=str(
        _payload_get(payload, "plan_id", "PlanId") or plan_id
      ),
      group_id=str(
        _payload_get(payload, "setup_id", "SetupId") or ""
      ) or None,
      remaining_volume=remaining if remaining is not None else filled,
      highest_booked_target_index=booked,
    ))
  return out


def _exposures_for_candidate(
  exposures: list[ActiveExposure],
  candidate_symbol: str | None,
) -> list[ActiveExposure]:
  return filter_exposures_for_symbol(exposures, candidate_symbol)


def evaluate_entry_against_exposure(
  *,
  direction: str,
  entry_price: float,
  exposures: list[ActiveExposure],
  min_price_separation: float = 15.0,
  same_direction_size_fraction: float = 0.60,
  ignore_opposing_active: bool = False,
  allow_same_direction_stack: bool = False,
  candidate_tier: str | None = None,
  candidate_symbol: str | None = None,
) -> ExposureDecision:
  """Apply opposing-distance and same-direction rules.

  ``candidate_symbol``: only exposures on that instrument count. Omit only
  in tests that model a single-book.

  ``ignore_opposing_active``: scalp with fitted native min room may open
  even while an opposite position is already activated — opposing price
  separation is soft telemetry then, not a hard block.

  ``allow_same_direction_stack``: scalps pass True to stack without waiting
  for TP2 / Tier A. Non-scalp (False) may stack at
  ``same_direction_size_fraction`` only after every open same-dir plan has
  booked TP2 and the candidate is Tier A; otherwise blocked.
  """
  wanted = normalize_direction(direction)
  if wanted is None or entry_price <= 0:
    return ExposureDecision(block=False)
  opposite = "SELL" if wanted == "BUY" else "BUY"
  separation = max(0.0, float(min_price_separation))
  tier = str(candidate_tier or "").strip().upper() or None
  exposures = _exposures_for_candidate(exposures, candidate_symbol)

  for active in exposures:
    if active.direction != opposite:
      continue
    distance = abs(float(entry_price) - float(active.entry_price))
    if distance < separation:
      measured = {
        "active_direction": active.direction,
        "active_entry_price": active.entry_price,
        "active_symbol": active.symbol,
        "candidate_entry_price": entry_price,
        "candidate_symbol": normalize_symbol(candidate_symbol),
        "price_distance": distance,
        "min_price_separation": separation,
        "active_source": active.source,
        "active_plan_id": active.plan_id,
        "active_group_id": active.group_id,
        "active_position_id": active.position_id,
      }
      if ignore_opposing_active:
        return ExposureDecision(
          block=False,
          reason_code="opposing_active_too_close_ignored_scalp",
          message=(
            f"{wanted} scalp entry {entry_price:.2f} is {distance:.2f} from "
            f"active {active.direction} @ {active.entry_price:.2f}; "
            "fitted native room ignores opposing-active separation"
          ),
          measured={
            **measured,
            "ignore_opposing_active": True,
            "preference_telemetry": True,
          },
        )
      return ExposureDecision(
        block=True,
        reason_code="opposing_active_too_close",
        message=(
          f"{wanted} entry {entry_price:.2f} is only {distance:.2f} from "
          f"active {active.direction} @ {active.entry_price:.2f}; "
          f"require >= {separation:.0f} price separation"
        ),
        measured=measured,
      )

  same = [item for item in exposures if item.direction == wanted]
  if not same:
    return ExposureDecision(block=False)
  primary = same[0]
  fraction = max(0.01, min(1.0, float(same_direction_size_fraction)))
  unlocked = same_direction_stack_unlocked(same)
  tier_ok = tier == "A"
  measured = {
    "active_direction": primary.direction,
    "active_entry_price": primary.entry_price,
    "active_symbol": primary.symbol,
    "candidate_symbol": normalize_symbol(candidate_symbol),
    "same_direction_size_fraction": fraction,
    "active_source": primary.source,
    "active_plan_id": primary.plan_id,
    "active_group_id": primary.group_id,
    "active_position_id": primary.position_id,
    "same_direction_count": len(same),
    "same_direction_tp2_booked": unlocked,
    "unlock_booked_target_index": SAME_DIRECTION_UNLOCK_BOOKED_TARGET_INDEX,
    "active_highest_booked_target_indexes": [
      item.highest_booked_target_index for item in same
    ],
    "candidate_tier": tier,
    "same_direction_requires_tier_a": not allow_same_direction_stack,
  }
  # Scalps stack freely; non-scalp needs booked TP2 + Tier A candidate.
  if allow_same_direction_stack:
    return ExposureDecision(
      block=False,
      same_direction_stack=True,
      reason_code="same_direction_stack",
      message=(
        f"same-direction stack on active {wanted} @ {primary.entry_price:.2f}: "
        f"size {fraction:.0%} on a single leg"
      ),
      measured=measured,
    )
  if not unlocked:
    return ExposureDecision(
      block=True,
      reason_code="same_direction_active_before_tp2",
      message=(
        f"same-direction {wanted} already active @ {primary.entry_price:.2f}; "
        "wait until each open plan has booked TP2 before adding"
      ),
      measured=measured,
    )
  if not tier_ok:
    return ExposureDecision(
      block=True,
      reason_code="same_direction_stack_requires_tier_a",
      message=(
        f"same-direction {wanted} already active @ {primary.entry_price:.2f} "
        f"with TP2 booked, but candidate tier={tier or 'missing'} "
        "(require Tier A quality to stack)"
      ),
      measured=measured,
    )
  return ExposureDecision(
    block=False,
    same_direction_stack=True,
    reason_code="same_direction_stack",
    message=(
      f"same-direction stack on active {wanted} @ {primary.entry_price:.2f}: "
      f"size {fraction:.0%} on a single leg "
      "(unlocked after booked TP2 + Tier A)"
    ),
    measured=measured,
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
