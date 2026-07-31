"""Pure opposing-structure target-room geometry shared by scanner and V7."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Any, Iterable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuralTargetRoomDecision:
  allowed: bool
  reason_code: str
  message: str
  hard_block: bool
  measured: dict[str, Any]
  opposing_entry: Any | None = None
  fitted_targets_pips: tuple[int, ...] = ()
  effective_target_pips: float | None = None


def _overlap(
  first_low: float,
  first_high: float,
  second_low: float,
  second_high: float,
) -> tuple[float, float]:
  overlap = max(
    0.0,
    min(first_high, second_high) - max(first_low, second_low),
  )
  width = max(0.0, first_high - first_low)
  ratio = (
    overlap / width
    if width > 0
    else 1.0 if overlap > 0 else 0.0
  )
  return overlap, ratio


def filter_displaced_opposing_entries(
  entries: Iterable[Any],
  *,
  direction: str,
  recent_closes: Iterable[float],
) -> list[Any]:
  """Drop opposing-side entries that recent price action has already
  decisively closed beyond, in the candidate's own direction.

  An opposing zone's own classification (e.g. an H1 breaker/flip) can lag
  real price by up to a full HTF bar - _breaker_violation (zones.py)
  requires a confirmed close beyond the zone before relabeling it, which is
  the right caution against flipping on a mere wick, but it means a zone
  can still show up here as an unbroken barrier minutes after the
  candidate's own execution timeframe has already closed decisively
  through it. Applying the exact same confirmed-close standard directly
  against the candidate's own recent closes - not waiting on the barrier's
  own reclassification - recognizes a displacement that has already
  happened instead of treating a barrier as live once it no longer is.
  Only genuinely CLOSED beyond the far edge counts; a wick alone does not.
  """
  side = direction.upper()
  closes = [float(value) for value in recent_closes if math.isfinite(value)]
  opposing_side = "sell" if side == "BUY" else "buy"
  kept: list[Any] = []
  dropped: list[tuple[float, float]] = []
  for entry in entries:
    if str(getattr(entry, "side", "")).casefold() != opposing_side:
      kept.append(entry)
      continue
    low = float(getattr(entry, "lo"))
    high = float(getattr(entry, "hi"))
    displaced = any(
      (side == "BUY" and close > high) or (side == "SELL" and close < low)
      for close in closes
    )
    if displaced:
      dropped.append((low, high))
    else:
      kept.append(entry)
  log_fn = log.info if dropped else log.debug
  log_fn(
    "structural_target_room displacement direction=%s closes=%s "
    "kept=%s dropped=%s dropped_bounds=%s",
    side,
    closes,
    len(kept),
    len(dropped),
    [(round(lo, 6), round(hi, 6)) for lo, hi in dropped],
  )
  return kept


def _nearest_opposing(
  direction: str,
  planned_entry: float,
  candidate_low: float,
  candidate_high: float,
  entries: Iterable[Any],
) -> Any | None:
  opposing_side = "sell" if direction == "BUY" else "buy"
  relevant = []
  for entry in entries:
    if str(getattr(entry, "side", "")).casefold() != opposing_side:
      continue
    low = float(getattr(entry, "lo"))
    high = float(getattr(entry, "hi"))
    overlaps_candidate = min(candidate_high, high) > max(candidate_low, low)
    directionally_relevant = (
      high >= planned_entry
      if direction == "BUY"
      else low <= planned_entry
    )
    if not directionally_relevant and not overlaps_candidate:
      continue
    raw_room = (
      low - planned_entry
      if direction == "BUY"
      else planned_entry - high
    )
    # contains_price means market spot is inside the map entry — not that the
    # planned execution entry is geometrically contained. Ranking / hard-block
    # must only use planned-entry geometry.
    planned_entry_contained = low <= planned_entry <= high
    tier_rank = {
      "major": 0,
      "zone": 1,
      "level": 2,
    }.get(str(getattr(entry, "tier", "")).casefold(), 3)
    relevant.append((
      0.0 if planned_entry_contained or overlaps_candidate else max(0.0, raw_room),
      tier_rank,
      abs(raw_room),
      low,
      entry,
    ))
  return min(relevant, default=None, key=lambda item: item[:4])[-1] if relevant else None


def evaluate_structural_target_room(
  *,
  direction: str,
  planned_entry_price: float,
  candidate_entry_low: float,
  candidate_entry_high: float,
  configured_target_pips: Iterable[int],
  actionable_entries: Iterable[Any],
  atr: float,
  pip_size: float,
  barrier_buffer_atr: float,
  min_capped_target_pips: float = 0.0,
  execution_cost_pips: float = 0.0,
  displacement_state: dict[str, Any] | None = None,
) -> StructuralTargetRoomDecision:
  """Cap reachable target room at the nearest opposing actionable entry.

  Hard-blocks only on structural impossibility: planned entry contained in
  the opposing structure, or raw geometric room <= 0. The ATR barrier buffer
  is preference for TP sizing — it must not invent a hard reject when raw
  room is still positive.

  Market Map ``contains_price`` is telemetry only: it describes whether the
  *current market price* sits inside a map entry, not whether the planned
  execution entry is contained.

  Candidate-band overlap without planned-entry containment is allow-with-
  warning plus optional target cap — never a hard structural reject.

  Callers must apply ``filter_displaced_opposing_entries`` on authoritative
  recent closed bars before passing ``actionable_entries``.

  execution_cost_pips is the hard viability floor for a capped target (spread
  / slippage). A capped target must never exceed usable buffered room and
  must clear this floor. min_capped_target_pips remains preference telemetry
  when room clears the execution-cost floor but sits below the preferred
  minimum.
  """
  side = str(direction).upper()
  planned = float(planned_entry_price)
  low = min(float(candidate_entry_low), float(candidate_entry_high))
  high = max(float(candidate_entry_low), float(candidate_entry_high))
  pip = float(pip_size)
  cost = max(0.0, float(execution_cost_pips))
  preference_floor = max(0.0, float(min_capped_target_pips))
  targets = tuple(sorted({
    int(value) for value in configured_target_pips if int(value) > 0
  }))
  if (
    side not in {"BUY", "SELL"}
    or not all(math.isfinite(value) for value in (planned, low, high, atr, pip))
    or pip <= 0
  ):
    return StructuralTargetRoomDecision(
      False,
      "invalid_target_room_geometry",
      "candidate target-room geometry is invalid",
      True,
      {
        "planned_entry_price": planned,
        "candidate_entry_low": low,
        "candidate_entry_high": high,
      },
    )

  barrier = _nearest_opposing(
    side,
    planned,
    low,
    high,
    actionable_entries,
  )
  base_measured: dict[str, Any] = {
    "planned_entry_price": planned,
    "candidate_entry_low": low,
    "candidate_entry_high": high,
    "configured_target_pips": list(targets),
    "execution_cost_pips": cost,
    "min_capped_target_pips": preference_floor,
  }
  if displacement_state:
    base_measured["displacement_state"] = dict(displacement_state)
  if barrier is None:
    effective = float(max(targets)) if targets else None
    log.debug(
      "structural_target_room allowed=true reason=no_opposing_barrier "
      "direction=%s planned_entry=%s displacement=%s",
      side,
      planned,
      displacement_state,
    )
    return StructuralTargetRoomDecision(
      True,
      "no_opposing_barrier",
      "no opposing actionable structure ahead",
      False,
      {
        **base_measured,
        "effective_target_pips": effective,
      },
      fitted_targets_pips=targets,
      effective_target_pips=effective,
    )

  opposing_low = float(getattr(barrier, "lo"))
  opposing_high = float(getattr(barrier, "hi"))
  overlap_price, overlap_ratio = _overlap(
    low,
    high,
    opposing_low,
    opposing_high,
  )
  raw_room = (
    opposing_low - planned
    if side == "BUY"
    else planned - opposing_high
  )
  buffer_price = max(0.0, float(barrier_buffer_atr)) * max(0.0, float(atr))
  buffered_room = raw_room - buffer_price
  raw_room_pips = raw_room / pip
  room_pips = buffered_room / pip
  room_atr = buffered_room / atr if atr > 0 else 0.0
  planned_entry_contained = opposing_low <= planned <= opposing_high
  market_price_contained = bool(getattr(barrier, "contains_price", False))
  tier = str(getattr(barrier, "tier", "") or "")
  tags = [str(tag) for tag in getattr(barrier, "tags", ()) or ()]
  measured = {
    **base_measured,
    "opposing_low": opposing_low,
    "opposing_high": opposing_high,
    "opposing_tier": tier,
    "opposing_tags": tags,
    "planned_entry_contained": planned_entry_contained,
    "market_price_contained": market_price_contained,
    # Legacy alias — market-map contains_price telemetry only.
    "opposing_contains_price": market_price_contained,
    "entry_overlap_price": round(overlap_price, 6),
    "entry_overlap_ratio": round(overlap_ratio, 6),
    "raw_room_price": round(raw_room, 6),
    "raw_room_pips": round(raw_room_pips, 3),
    "barrier_buffer_price": round(buffer_price, 6),
    "buffered_room_price": round(buffered_room, 6),
    "room_pips": round(room_pips, 3),
    "room_atr": round(room_atr, 4),
  }

  def _log_decision(decision: StructuralTargetRoomDecision) -> StructuralTargetRoomDecision:
    log.info(
      "structural_target_room allowed=%s hard_block=%s reason=%s "
      "direction=%s planned_entry=%s opposing_low=%s opposing_high=%s "
      "planned_entry_contained=%s market_price_contained=%s "
      "overlap_price=%s overlap_ratio=%s raw_room=%s buffered_room=%s "
      "displacement=%s",
      decision.allowed,
      decision.hard_block,
      decision.reason_code,
      side,
      planned,
      opposing_low,
      opposing_high,
      planned_entry_contained,
      market_price_contained,
      round(overlap_price, 6),
      round(overlap_ratio, 6),
      round(raw_room, 6),
      round(buffered_room, 6),
      displacement_state,
    )
    return decision

  if planned_entry_contained:
    # Prefer opposing_entry_overlap when the planned entry sits in the
    # candidate∩opposing intersection; otherwise contained (engulfed).
    overlap_low = max(low, opposing_low)
    overlap_high = min(high, opposing_high)
    planned_in_overlap = (
      overlap_price > 0
      and overlap_low <= planned <= overlap_high
    )
    reason = (
      "opposing_entry_overlap"
      if planned_in_overlap
      else "opposing_entry_contained"
    )
    return _log_decision(StructuralTargetRoomDecision(
      False,
      reason,
      (
        "planned entry sits inside an opposing-structure overlap"
        if reason == "opposing_entry_overlap"
        else "planned entry is inside an opposing actionable structure"
      ),
      True,
      measured,
      opposing_entry=barrier,
    ))
  # Hard structural: no raw geometric room. Buffer must not invent this.
  if raw_room <= 0:
    reason = (
      "opposing_major_no_room"
      if tier.casefold() == "major"
      else "opposing_barrier_no_target"
    )
    return _log_decision(StructuralTargetRoomDecision(
      False,
      reason,
      (
        "opposing major structure leaves no raw target room"
        if reason == "opposing_major_no_room"
        else "opposing structure leaves no positive raw target room"
      ),
      True,
      measured,
      opposing_entry=barrier,
    ))

  # Band overlap without planned-entry containment: allow + optional TP cap.
  if overlap_price > 0:
    measured["band_overlap_without_planned_containment"] = True

  # Usable TP room is buffer-aware but never negative for sizing.
  usable_pips = max(0.0, room_pips)
  if usable_pips < cost:
    return _log_decision(StructuralTargetRoomDecision(
      False,
      "execution_cost_insufficient_room",
      "buffered target room does not clear the execution-cost floor",
      True,
      {
        **measured,
        "usable_room_pips": round(usable_pips, 3),
        "effective_target_pips": None,
      },
      opposing_entry=barrier,
    ))

  fitted = tuple(target for target in targets if target <= usable_pips)
  if fitted:
    effective = float(max(fitted))
    return _log_decision(StructuralTargetRoomDecision(
      True,
      "opposing_barrier_target_capped",
      "configured target ladder capped before opposing structure",
      False,
      {
        **measured,
        "usable_room_pips": round(usable_pips, 3),
        "effective_target_pips": effective,
        "preference_telemetry": True,
      },
      opposing_entry=barrier,
      fitted_targets_pips=fitted,
      effective_target_pips=effective,
    ))

  # Cap to real usable room — never invent pips above room (no max(1.0, …)).
  capped_target = float(math.floor(usable_pips))
  if capped_target < cost or capped_target <= 0:
    return _log_decision(StructuralTargetRoomDecision(
      False,
      "execution_cost_insufficient_room",
      "no integer target clears the execution-cost floor inside usable room",
      True,
      {
        **measured,
        "usable_room_pips": round(usable_pips, 3),
        "effective_target_pips": None,
      },
      opposing_entry=barrier,
    ))
  reason = (
    "opposing_barrier_target_capped_below_ladder"
    if preference_floor > 0 and capped_target < preference_floor
    else "configured_ladder_does_not_fit"
    if targets
    else "opposing_barrier_target_capped_below_ladder"
  )
  return _log_decision(StructuralTargetRoomDecision(
    True,
    reason,
    "no configured target fits; capping to real buffered room",
    False,
    {
      **measured,
      "usable_room_pips": round(usable_pips, 3),
      "effective_target_pips": capped_target,
      "preference_telemetry": True,
    },
    opposing_entry=barrier,
    fitted_targets_pips=(int(capped_target),),
    effective_target_pips=capped_target,
  ))
