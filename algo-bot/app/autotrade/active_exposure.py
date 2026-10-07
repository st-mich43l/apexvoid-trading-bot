"""Active open-trade exposure gates for new autonomous plans.

When an order is already active **on the same instrument**:
- opposite direction is decided by ``evaluate_opposite_exposure`` from the
  instrument-owned ``exposure.opposite_position`` policy (config/
  instruments.yml): FX never allows it, XAU allows it only at >= 150 pips
  from every existing opposite group. Strategy, family and scalp status are
  never consulted.
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
from decimal import Decimal
from typing import Any

from app.runtime.instruments import OppositePositionPolicy

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
# Live 2026-08-26: V8 left Stage=FullyOpen + GroupStage=recovery_required after
# unknown_leg_close while broker position keys were already gone. Python still
# counted FullyOpen as live exposure → scalping scalp_max_concurrent_positions
# blocked every XAU discovery for hours. Recovery is not a manageable open book.
_NON_LIVE_TRADE_PLAN_GROUP_STAGES = frozenset({
  "recovery_required",
  "closed",
  "cancelled",
  "expired",
  "failed",
})
_NON_LIVE_TERMINAL_REASONS = frozenset({
  "unknown_leg_close",
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
  # None when unknown (pre-schema-3). Treated as not-yet-TP2 for unlock.
  highest_booked_target_index: int | None = None


@dataclass(frozen=True)
class ExposureDecision:
  """Result of comparing a candidate against live open exposure."""

  block: bool
  reason_code: str | None = None
  message: str = ""
  same_direction_stack: bool = False
  measured: dict[str, Any] | None = None


def _normalize_stage_token(value: str | None) -> str:
  return str(value or "").strip().lower()


def _is_non_live_trade_plan(
  *,
  stage: str | None,
  group_stage: str | None,
  terminal_reason: str | None = None,
) -> bool:
  """True when a V8 runtime must not occupy the symbol concurrent book.

  ``Stage=FullyOpen`` alone is not enough — recovery_required / unknown close
  can leave FullyOpen sticky after broker positions are gone.
  """
  group = _normalize_stage_token(group_stage)
  if group in _NON_LIVE_TRADE_PLAN_GROUP_STAGES:
    return True
  reason = _normalize_stage_token(terminal_reason)
  # Sticky FullyOpen + unknown_leg_close must not lock scalping concurrent forever.
  if reason in _NON_LIVE_TERMINAL_REASONS and _normalize_stage_token(stage) in {
    "fullyopen",
    "fully_open",
    "open",
  }:
    return True
  return False


def normalize_symbol(value: object) -> str | None:
  """Canonical instrument key so XAUUSD and XAU occupy the same book."""
  text = str(value or "").strip().upper()
  if not text:
    return None
  return _SYMBOL_ALIASES.get(text, text)


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
  """Load live TradePlan runtime exposure, including pending/submitted plans.

  Received/Submitted plans occupy the symbol before the first fill. Omitting
  them let two GBPJPY Key Level sells publish 5s apart (2026-08-17).

  Pass ``symbol`` to keep only that instrument's book (scalping reconcile / any
  per-symbol caller). Omit only when the caller will filter next.
  """
  exposures = await _load_trade_plan_exposures(client)
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
    terminal_reason = str(
      _payload_get(payload, "terminal_reason", "TerminalReason") or ""
    )
    if _is_non_live_trade_plan(
      stage=stage,
      group_stage=group_stage,
      terminal_reason=terminal_reason,
    ):
      continue
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


FX_OPPOSITE_NOT_ALLOWED = "fx_opposite_position_not_allowed"
XAU_OPPOSITE_TOO_CLOSE = "xau_opposite_position_too_close"
XAU_OPPOSITE_SEPARATION_SATISFIED = "xau_opposite_position_separation_satisfied"


@dataclass(frozen=True)
class OppositeExposureDecision:
  """Outcome of the instrument-owned opposite-direction exposure rule."""

  allowed: bool
  reason_code: str | None = None
  message: str = ""
  measured: dict[str, Any] | None = None


def _pips_between(entry_a: float, entry_b: float, pip_size: float) -> tuple[float, float]:
  """Return (price distance, pip distance); Decimal keeps 150.0 exact."""
  price = abs(Decimal(str(entry_a)) - Decimal(str(entry_b)))
  pips = price / Decimal(str(pip_size))
  return float(price), float(pips)


def evaluate_opposite_exposure(
  symbol: str,
  incoming_direction: str,
  incoming_entry_reference: float,
  active_exposures: list[ActiveExposure],
  instrument_policy: OppositePositionPolicy,
) -> OppositeExposureDecision:
  """Decide whether an autonomous opposite-direction group may be opened.

  ``instrument_policy`` is the only classifier: ``allowed=False`` (FX) blocks
  any opposite exposure at any distance; ``allowed=True`` (XAU) requires the
  incoming entry to be at least ``minimum_separation_pips`` (inclusive) from
  EVERY opposite group. The incoming reference is the executable quote; each
  existing exposure contributes its recorded/broker fill price once filled,
  or its planned entry reference while pending (see ``_payload_entry_price``).
  """
  wanted = normalize_direction(incoming_direction)
  entry = _as_float(incoming_entry_reference)
  canonical = normalize_symbol(symbol)
  if wanted is None or entry is None or entry <= 0 or canonical is None:
    return OppositeExposureDecision(allowed=True)
  opposite = "SELL" if wanted == "BUY" else "BUY"
  opposing = [
    item
    for item in filter_exposures_for_symbol(active_exposures, canonical)
    if item.direction == opposite
  ]
  if not opposing:
    return OppositeExposureDecision(allowed=True)

  def _measured(active: ActiveExposure) -> dict[str, Any]:
    price, pips = _pips_between(entry, active.entry_price, instrument_policy.pip_size)
    return {
      "symbol": canonical,
      "incoming_direction": wanted,
      "incoming_entry": entry,
      "existing_direction": active.direction,
      "existing_entry": active.entry_price,
      "distance_price": price,
      "distance_pips": pips,
      "minimum_separation_pips": instrument_policy.minimum_separation_pips,
      "existing_plan_id": active.plan_id,
      "existing_group_id": active.group_id,
      "existing_position_id": active.position_id,
      "existing_source": active.source,
      "policy": "allowed" if instrument_policy.allowed else "blocked",
    }

  if not instrument_policy.allowed:
    # Nearest group makes the evidence most useful; distance never decides.
    nearest = min(
      opposing,
      key=lambda item: abs(Decimal(str(entry)) - Decimal(str(item.entry_price))),
    )
    measured = _measured(nearest)
    return OppositeExposureDecision(
      allowed=False,
      reason_code=FX_OPPOSITE_NOT_ALLOWED,
      message=(
        f"{canonical} {wanted} blocked: active {nearest.direction} exposure "
        "exists and this instrument never allows opposite positions"
      ),
      measured=measured,
    )

  minimum = float(instrument_policy.minimum_separation_pips or 0.0)
  nearest = min(
    opposing,
    key=lambda item: abs(Decimal(str(entry)) - Decimal(str(item.entry_price))),
  )
  measured = _measured(nearest)
  if Decimal(str(measured["distance_pips"])) < Decimal(str(minimum)):
    return OppositeExposureDecision(
      allowed=False,
      reason_code=XAU_OPPOSITE_TOO_CLOSE,
      message=(
        f"{canonical} {wanted} entry {entry:.2f} is "
        f"{measured['distance_pips']:.1f} pips from active {nearest.direction} "
        f"@ {nearest.entry_price:.2f}; require >= {minimum:.0f} pips"
      ),
      measured=measured,
    )
  return OppositeExposureDecision(
    allowed=True,
    reason_code=XAU_OPPOSITE_SEPARATION_SATISFIED,
    message=(
      f"{canonical} {wanted} entry is {measured['distance_pips']:.1f} pips from "
      f"every opposite group (min {minimum:.0f})"
    ),
    measured=measured,
  )


def evaluate_entry_against_exposure(
  *,
  direction: str,
  entry_price: float,
  exposures: list[ActiveExposure],
  same_direction_size_fraction: float = 0.60,
  allow_same_direction_stack: bool = False,
  candidate_tier: str | None = None,
  candidate_symbol: str | None = None,
) -> ExposureDecision:
  """Apply the same-direction stacking rules.

  Opposite-direction exposure is NOT decided here; call
  ``evaluate_opposite_exposure`` first (instrument-policy driven).

  ``candidate_symbol``: only exposures on that instrument count. Omit only
  in tests that model a single-book.

  ``allow_same_direction_stack``: scalps pass True to stack without waiting
  for TP2 / Tier A. Non-scalp (False) may stack at
  ``same_direction_size_fraction`` only after every open same-dir plan has
  booked TP2 and the candidate is Tier A; otherwise blocked.
  """
  wanted = normalize_direction(direction)
  if wanted is None or entry_price <= 0:
    return ExposureDecision(block=False)
  tier = str(candidate_tier or "").strip().upper() or None
  exposures = _exposures_for_candidate(exposures, candidate_symbol)

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
  # Always collapse to a single limit at planned_entry_price. Converting
  # market_with_limit_scale → market_watch left validate() checking targets
  # against the full zone (furthest = zone_low for SELL). Short scalping targets
  # computed from the proximal planned entry then fail with
  # "SELL targets must all be below the entry zone" (live 2026-08-20
  # XAU 49ffb74 under same-direction stack) and TradePlanError bypassed
  # claim release. Single-leg at the planned price matches the stack
  # contract ("60% on a single leg") and keeps targets/entry coherent.
  if route in {
    "limit_ladder",
    "market_with_limit_scale",
    "zone_scale",
    "reaction_scale",
    "market",
  }:
    out["planned_execution_route"] = "single_limit"
    out["planned_market_immediate"] = False
  if entry is not None:
    out["planned_leg_entry_prices"] = [entry]
    out["planned_leg_volume_ratios"] = [1.0]
  return out
