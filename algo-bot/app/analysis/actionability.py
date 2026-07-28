"""Pure boundary between scanner observations and executable setups."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

from app.analysis.detectors import DetectionResult
from app.analysis.key_level_role import (
  ROLE_AMBIGUOUS,
  ROLE_BROKEN_RESISTANCE,
  ROLE_BROKEN_SUPPORT,
  ROLE_RESISTANCE,
  ROLE_SUPPORT,
  classify_key_level_role,
)
from app.analysis.market_map import MapEntry, MarketMap
from app.analysis.structural_reaction_support import STRUCTURAL_SETUPS
from app.autotrade.structural_target_room import (
  evaluate_structural_target_room,
)


@dataclass(frozen=True)
class ActionabilityDecision:
  allowed: bool
  reason_code: str
  message: str
  hard_block: bool
  measured: dict[str, Any]
  opposing_entry: MapEntry | None = None


@dataclass(frozen=True)
class ActionabilityResolution:
  observed: tuple[DetectionResult, ...]
  actionable: tuple[DetectionResult, ...]
  gated: tuple[tuple[DetectionResult, ActionabilityDecision], ...]
  conflicts: tuple[dict[str, Any], ...]


def _structural(result: DetectionResult) -> bool:
  return bool(result.structural_id) or result.setup in STRUCTURAL_SETUPS


def _zone_overlap_ratio(first: DetectionResult, second: DetectionResult) -> float:
  overlap = (
    min(first.entry_zone.high, second.entry_zone.high)
    - max(first.entry_zone.low, second.entry_zone.low)
  )
  if overlap <= 0:
    return 0.0
  smaller = min(
    first.entry_zone.high - first.entry_zone.low,
    second.entry_zone.high - second.entry_zone.low,
  )
  return overlap / smaller if smaller > 0 else 1.0


def _band_overlap(
  first_low: float,
  first_high: float,
  second_low: float,
  second_high: float,
) -> float:
  return max(
    0.0,
    min(first_high, second_high) - max(first_low, second_low),
  )


def _map_conflict(
  first: DetectionResult,
  second: DetectionResult,
  entries: Sequence[MapEntry],
) -> bool:
  """Whether opposing observations resolve into contradictory map space."""
  first_side = "buy" if first.direction.upper() == "BUY" else "sell"
  second_side = "buy" if second.direction.upper() == "BUY" else "sell"
  first_entries = [
    entry for entry in entries
    if entry.side.casefold() == first_side
    and _band_overlap(
      first.entry_zone.low,
      first.entry_zone.high,
      entry.lo,
      entry.hi,
    ) > 0
  ]
  second_entries = [
    entry for entry in entries
    if entry.side.casefold() == second_side
    and _band_overlap(
      second.entry_zone.low,
      second.entry_zone.high,
      entry.lo,
      entry.hi,
    ) > 0
  ]
  return any(
    _band_overlap(first_entry.lo, first_entry.hi, second_entry.lo, second_entry.hi)
    > 0
    for first_entry in first_entries
    for second_entry in second_entries
  )


def _result_payload(result: DetectionResult) -> dict[str, Any]:
  return {
    "setup": result.setup,
    "direction": result.direction,
    "confluence": result.confluence,
    "entry_low": float(result.entry_zone.low),
    "entry_high": float(result.entry_zone.high),
  }


def _decision(
  reason_code: str,
  message: str,
  measured: dict[str, Any],
  *,
  opposing_entry: MapEntry | None = None,
) -> ActionabilityDecision:
  return ActionabilityDecision(
    False,
    reason_code,
    message,
    True,
    measured,
    opposing_entry,
  )


def _key_level_role(
  result: DetectionResult,
  context: Any,
  cfg: Any,
) -> str | None:
  if result.setup != "Key Level Reaction":
    return None
  if result.key_level_role:
    return result.key_level_role
  frame = getattr(context, "frames", {}).get(
    str(getattr(context, "tf", "M5")).upper()
  )
  if frame is None:
    return ROLE_AMBIGUOUS
  return classify_key_level_role(
    kind=result.structural_kind,
    level_price=float(result.key_level),
    band_low=float(result.entry_zone.low),
    band_high=float(result.entry_zone.high),
    closed_bars=frame,
    breakout_accept_bars=int(getattr(cfg, "breakout_accept_bars", 2)),
  ).role


def resolve_actionability(
  *,
  symbol: str,
  observed_results: Sequence[DetectionResult],
  market_map: MarketMap | None,
  context: Any,
  atr: float,
  pip_size: float,
  cfg: Any,
) -> ActionabilityResolution:
  """Resolve semantic, cross-side, and opposing-room hard geometry."""
  observed = tuple(observed_results)
  entries = () if market_map is None else tuple(market_map.actionable_entries)
  gated: dict[int, ActionabilityDecision] = {}
  conflicts: list[dict[str, Any]] = []

  for index, result in enumerate(observed):
    if _structural(result) and market_map is None:
      gated[index] = _decision(
        "opposing_context_unavailable",
        "current Market Map context is unavailable",
        {"symbol": symbol, "htf_bias": getattr(context, "htf_bias", None)},
      )

  threshold = max(0.0, float(getattr(cfg, "scanner_conflict_overlap", 0.5)))
  margin = max(0.0, float(getattr(cfg, "scanner_conflict_margin", 1.0)))
  for first_index, first in enumerate(observed):
    if first_index in gated:
      continue
    for second_index in range(first_index + 1, len(observed)):
      if first_index in gated:
        break
      if second_index in gated:
        continue
      second = observed[second_index]
      if first.direction.upper() == second.direction.upper():
        continue
      overlap = _zone_overlap_ratio(first, second)
      map_conflict = _map_conflict(first, second, entries)
      if overlap < threshold and not map_conflict:
        continue
      difference = float(first.confluence) - float(second.confluence)
      if abs(difference) < margin:
        measured = {
          "entry_overlap_ratio": overlap,
          "map_structure_conflict": map_conflict,
          "quality_margin": abs(difference),
          "required_margin": margin,
        }
        gated[first_index] = _decision(
          "opposing_conflict_ambiguous",
          "opposing structural observations overlap without a decisive side",
          measured,
        )
        gated[second_index] = gated[first_index]
        stronger, weaker = first, second
        outcome = "both_dropped"
      else:
        winner_index, loser_index = (
          (first_index, second_index)
          if difference > 0
          else (second_index, first_index)
        )
        stronger = observed[winner_index]
        weaker = observed[loser_index]
        gated[loser_index] = _decision(
          "opposing_conflict_weaker",
          "opposing structural observation lost the quality margin",
          {
            "entry_overlap_ratio": overlap,
            "map_structure_conflict": map_conflict,
            "quality_margin": abs(difference),
            "required_margin": margin,
            "winner_direction": stronger.direction,
          },
        )
        outcome = "stronger_kept"
      conflicts.append({
        "outcome": outcome,
        "a": _result_payload(stronger),
        "b": _result_payload(weaker),
      })

  actionable: list[DetectionResult] = []
  for index, original in enumerate(observed):
    if index in gated:
      continue
    if not _structural(original):
      actionable.append(original)
      continue
    result = original
    targets = tuple(result.provisional_targets_pips)
    if targets:
      room = evaluate_structural_target_room(
        direction=result.direction,
        planned_entry_price=(
          result.planned_entry_price
          if result.planned_entry_price is not None
          else result.current_price
        ),
        candidate_entry_low=float(result.entry_zone.low),
        candidate_entry_high=float(result.entry_zone.high),
        configured_target_pips=targets,
        actionable_entries=entries,
        atr=atr,
        pip_size=pip_size,
        barrier_buffer_atr=float(
          getattr(cfg, "auto_trade_opposing_barrier_atr", 0.5)
        ),
      )
      measured = {
        **room.measured,
        "htf_bias": getattr(context, "htf_bias", None),
        "bias_relationship": result.bias_relationship or result.mode,
      }
      if not room.allowed:
        gated[index] = _decision(
          room.reason_code,
          room.message,
          measured,
          opposing_entry=room.opposing_entry,
        )
        continue
      if room.opposing_entry is not None:
        result = replace(
          result,
          target_cap_pips=room.effective_target_pips,
          target_room_measured=measured,
        )

    role = _key_level_role(result, context, cfg)
    if role == ROLE_AMBIGUOUS:
      gated[index] = _decision(
        "key_level_role_ambiguous",
        "generic key level has no accepted support/resistance role",
        {
          "key_level_role": role,
          "key_level": float(result.key_level),
          "htf_bias": getattr(context, "htf_bias", None),
        },
      )
      continue
    if role in {ROLE_BROKEN_SUPPORT, ROLE_BROKEN_RESISTANCE}:
      gated[index] = _decision(
        "key_level_role_flip_requires_retest",
        "accepted level break belongs to Break & Retest",
        {"key_level_role": role, "key_level": float(result.key_level)},
      )
      continue
    if (
      role == ROLE_SUPPORT and result.direction.upper() != "BUY"
      or role == ROLE_RESISTANCE and result.direction.upper() != "SELL"
    ):
      gated[index] = _decision(
        "key_level_role_direction_mismatch",
        "Key Level Reaction direction conflicts with the classified role",
        {
          "key_level_role": role,
          "direction": result.direction.upper(),
        },
      )
      continue
    if (
      str(result.bias_relationship or result.mode).casefold()
      == "counter_bias"
      and not bool(getattr(cfg, "auto_trade_allow_counter_bias", False))
    ):
      gated[index] = _decision(
        "counter_bias_disabled",
        "counter-bias setup is disabled by policy",
        {"htf_bias": getattr(context, "htf_bias", None)},
      )
      continue
    actionable.append(result)

  return ActionabilityResolution(
    observed,
    tuple(actionable),
    tuple((observed[index], gated[index]) for index in sorted(gated)),
    tuple(conflicts),
  )
