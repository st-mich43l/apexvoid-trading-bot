"""Typed scanner-owned static eligibility for autonomous execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping


EXECUTION_ELIGIBILITY_VERSION = 1
STATIC_ELIGIBLE = "static_eligible"
ANALYSIS_ONLY = "analysis_only"


@dataclass(frozen=True)
class ExecutionEligibility:
  version: int
  allowed: bool
  state: str
  reason_code: str
  message: str
  hard_block: bool
  direction: str
  entry_low: float
  entry_high: float
  planned_entry_price: float
  fitted_targets_pips: tuple[int, ...] = ()
  effective_target_pips: float | None = None
  reward_risk: float | None = None
  minimum_reward_risk: float | None = None
  opposing_entry: dict[str, Any] | None = None
  opposing_room_pips: float | None = None
  key_level_role: str | None = None
  bias_relationship: str | None = None
  market_map_id: str = ""
  calculated_at: int = 0
  measured: dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)

  @classmethod
  def from_dict(
    cls,
    payload: Mapping[str, Any] | None,
  ) -> ExecutionEligibility | None:
    if not isinstance(payload, Mapping):
      return None
    try:
      result = cls(
        version=int(payload["version"]),
        allowed=bool(payload["allowed"]),
        state=str(payload["state"]),
        reason_code=str(payload.get("reason_code") or ""),
        message=str(payload.get("message") or ""),
        hard_block=bool(payload.get("hard_block")),
        direction=str(payload["direction"]).upper(),
        entry_low=float(payload["entry_low"]),
        entry_high=float(payload["entry_high"]),
        planned_entry_price=float(payload["planned_entry_price"]),
        fitted_targets_pips=tuple(
          int(value) for value in payload.get("fitted_targets_pips", ())
        ),
        effective_target_pips=_optional_float(
          payload.get("effective_target_pips"),
        ),
        reward_risk=_optional_float(payload.get("reward_risk")),
        minimum_reward_risk=_optional_float(
          payload.get("minimum_reward_risk"),
        ),
        opposing_entry=(
          dict(payload["opposing_entry"])
          if isinstance(payload.get("opposing_entry"), Mapping)
          else None
        ),
        opposing_room_pips=_optional_float(
          payload.get("opposing_room_pips"),
        ),
        key_level_role=_optional_text(payload.get("key_level_role")),
        bias_relationship=_optional_text(
          payload.get("bias_relationship"),
        ),
        market_map_id=str(payload.get("market_map_id") or ""),
        calculated_at=int(payload.get("calculated_at") or 0),
        measured=dict(payload.get("measured") or {}),
      )
    except (KeyError, TypeError, ValueError):
      return None
    if (
      result.version != EXECUTION_ELIGIBILITY_VERSION
      or result.direction not in {"BUY", "SELL"}
      or not all(
        math.isfinite(value) and value > 0
        for value in (
          result.entry_low,
          result.entry_high,
          result.planned_entry_price,
        )
      )
      or result.entry_high <= result.entry_low
      or result.state not in {STATIC_ELIGIBLE, ANALYSIS_ONLY}
      or result.allowed == result.hard_block
      or result.allowed != (result.state == STATIC_ELIGIBLE)
    ):
      return None
    return result


def _optional_float(value: Any) -> float | None:
  if value is None:
    return None
  return float(value)


def _optional_text(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None
