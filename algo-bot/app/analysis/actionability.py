"""Pure boundary between scanner observations and executable setups."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
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
  filter_displaced_opposing_entries,
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
  decisions: tuple[tuple[DetectionResult, ActionabilityDecision], ...]
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


def _zone_gap(first: DetectionResult, second: DetectionResult) -> float:
  """Distance between the nearest edges of two bands - 0.0 if they overlap
  (including merely touching, eg. one band's high == the other's low).
  """
  overlap = (
    min(first.entry_zone.high, second.entry_zone.high)
    - max(first.entry_zone.low, second.entry_zone.low)
  )
  if overlap >= 0:
    return 0.0
  return (
    second.entry_zone.low - first.entry_zone.high
    if second.entry_zone.low > first.entry_zone.high
    else first.entry_zone.low - second.entry_zone.high
  )


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


def _entries_excluding_displaced_barriers(
  entries: Sequence[MapEntry],
  *,
  result: DetectionResult,
  context: Any,
  cfg: Any,
) -> Sequence[MapEntry]:
  """See structural_target_room.filter_displaced_opposing_entries: excludes
  an opposing barrier the candidate's own recent execution-tf closes have
  already closed decisively beyond, rather than hard-blocking on a barrier
  price has already broken while its own (possibly slower/HTF)
  classification hasn't caught up.
  """
  lookback = max(
    0, int(getattr(cfg, "auto_trade_displacement_override_lookback_bars", 0)),
  )
  if lookback <= 0:
    return entries
  frames = getattr(context, "frames", None)
  tf = getattr(context, "tf", None)
  frame = frames.get(tf) if isinstance(frames, dict) and tf else None
  if frame is None or frame.empty or "close" not in frame.columns:
    return entries
  recent_closes = tuple(float(value) for value in frame["close"].tail(lookback))
  return filter_displaced_opposing_entries(
    entries, direction=result.direction, recent_closes=recent_closes,
  )


def _decision(
  reason_code: str,
  message: str,
  measured: dict[str, Any],
  *,
  hard_block: bool = True,
  opposing_entry: MapEntry | None = None,
) -> ActionabilityDecision:
  return ActionabilityDecision(
    not hard_block,
    reason_code,
    message,
    hard_block,
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
  decisions: dict[int, list[ActionabilityDecision]] = {}
  conflicts: list[dict[str, Any]] = []

  def record(index: int, decision: ActionabilityDecision) -> None:
    decisions.setdefault(index, []).append(decision)
    if decision.hard_block:
      gated[index] = decision

  def price(value: object) -> float:
    try:
      return float(value)
    except (TypeError, ValueError):
      return float("nan")

  for index, result in enumerate(observed):
    planned_entry = (
      result.planned_entry_price
      if result.planned_entry_price is not None
      else result.current_price
    )
    values = {
      "entry_low": price(result.entry_zone.low),
      "entry_high": price(result.entry_zone.high),
      "planned_entry_price": price(planned_entry),
      "current_price": price(result.current_price),
      "key_level": price(result.key_level),
    }
    if (
      result.direction.upper() not in {"BUY", "SELL"}
      or not all(math.isfinite(value) and value > 0 for value in values.values())
      or values["entry_high"] <= values["entry_low"]
    ):
      record(index, _decision(
        "invalid_geometry",
        "setup has invalid direction or non-positive/non-finite price geometry",
        {
          "direction": result.direction,
          **{
            name: value if math.isfinite(value) else None
            for name, value in values.items()
          },
        },
      ))

  for index, result in enumerate(observed):
    if _structural(result) and market_map is None:
      record(index, _decision(
        "opposing_context_unavailable",
        "current Market Map context is unavailable",
        {"symbol": symbol, "htf_bias": getattr(context, "htf_bias", None)},
      ))

  # P0 zone/M1 simplification: a contested corridor is one simple
  # structural rule, not a confluence-margin tiebreak. BUY and SELL bands
  # that overlap at all, or whose nearest edges sit within
  # contested_corridor_gap_atr ATRs of each other, are never independent
  # opportunities and never resolved by picking whichever scored higher -
  # both are always suppressed until price action itself resolves the
  # corridor (a range validates into distinct edges, price accepts
  # outside it, one side is structurally invalidated, or a fresh
  # liquidity sweep + reclaim creates a directional setup elsewhere).
  gap_threshold = max(0.0, float(getattr(cfg, "contested_corridor_gap_atr", 0.5))) * max(0.0, atr)
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
      gap = _zone_gap(first, second)
      if gap > gap_threshold:
        continue
      map_conflict = _map_conflict(first, second, entries)
      measured = {
        "entry_overlap_ratio": _zone_overlap_ratio(first, second),
        "nearest_gap": gap,
        "gap_threshold": gap_threshold,
        "map_structure_conflict": map_conflict,
      }
      conflict_decision = _decision(
        "contested_corridor",
        "opposing structural bands form one contested corridor",
        measured,
      )
      record(first_index, conflict_decision)
      record(second_index, conflict_decision)
      conflicts.append({
        "outcome": "contested_corridor",
        "a": _result_payload(first),
        "b": _result_payload(second),
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
      room_entries = _entries_excluding_displaced_barriers(
        entries, result=result, context=context, cfg=cfg,
      )
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
        actionable_entries=room_entries,
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
        decision = _decision(
          room.reason_code,
          room.message,
          measured,
          opposing_entry=room.opposing_entry,
        )
        record(index, decision)
        if decision.hard_block:
          continue
      if room.opposing_entry is not None:
        result = replace(
          result,
          target_cap_pips=room.effective_target_pips,
          target_room_measured=measured,
        )

    role = _key_level_role(result, context, cfg)
    if role == ROLE_AMBIGUOUS:
      # P0 zone/M1 simplification: this used to hard-block every ambiguous-
      # role Key Level Reaction outright. key_level_reaction() (detectors.py)
      # no longer emits a genuinely-undecided result for an ambiguous role -
      # it deterministically resolves to exactly one direction (price
      # below/above the level, or whichever single side actually confirms a
      # reaction when price sits inside the level's own band) and discards
      # the level entirely if that resolution fails. A result reaching this
      # point already represents a principled decision, not a coin flip -
      # hard-blocking it here duplicated work already done upstream and
      # rejected every one of those decisions regardless. Record for
      # telemetry only; never send to Telegram as a bare "ambiguous" reason
      # per the spec, but never hard-block either.
      record(index, _decision(
        "key_level_role_ambiguous",
        "generic key level has no explicit support/resistance role "
        "(direction already resolved deterministically upstream)",
        {
          "key_level_role": role,
          "key_level": float(result.key_level),
          "htf_bias": getattr(context, "htf_bias", None),
        },
        hard_block=False,
      ))
    if role in {ROLE_BROKEN_SUPPORT, ROLE_BROKEN_RESISTANCE}:
      decision = _decision(
        "key_level_role_flip_requires_retest",
        "accepted level break belongs to Break & Retest",
        {"key_level_role": role, "key_level": float(result.key_level)},
      )
      record(index, decision)
      if decision.hard_block:
        continue
    if (
      role == ROLE_SUPPORT and result.direction.upper() != "BUY"
      or role == ROLE_RESISTANCE and result.direction.upper() != "SELL"
    ):
      decision = _decision(
        "key_level_role_direction_mismatch",
        "Key Level Reaction direction conflicts with the classified role",
        {
          "key_level_role": role,
          "direction": result.direction.upper(),
        },
      )
      record(index, decision)
      if decision.hard_block:
        continue
    if (
      str(result.bias_relationship or result.mode).casefold()
      == "counter_bias"
      and not bool(getattr(cfg, "auto_trade_allow_counter_bias", False))
    ):
      decision = _decision(
        "counter_bias_disabled",
        "counter-bias setup is disabled by policy",
        {"htf_bias": getattr(context, "htf_bias", None)},
      )
      record(index, decision)
      if decision.hard_block:
        continue
    actionable.append(result)

  return ActionabilityResolution(
    observed,
    tuple(actionable),
    tuple((observed[index], gated[index]) for index in sorted(gated)),
    tuple(
      (observed[index], decision)
      for index in sorted(decisions)
      for decision in decisions[index]
    ),
    tuple(conflicts),
  )
