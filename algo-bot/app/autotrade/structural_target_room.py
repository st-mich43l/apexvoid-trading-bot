"""Pure opposing-structure target-room geometry shared by scanner and V7."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable


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
    if not displaced:
      kept.append(entry)
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
    contains = (
      low <= planned_entry <= high
      or bool(getattr(entry, "contains_price", False))
    )
    tier_rank = {
      "major": 0,
      "zone": 1,
      "level": 2,
    }.get(str(getattr(entry, "tier", "")).casefold(), 3)
    relevant.append((
      0.0 if contains or overlaps_candidate else max(0.0, raw_room),
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
) -> StructuralTargetRoomDecision:
  """Cap reachable target room at the nearest opposing actionable entry."""
  side = str(direction).upper()
  planned = float(planned_entry_price)
  low = min(float(candidate_entry_low), float(candidate_entry_high))
  high = max(float(candidate_entry_low), float(candidate_entry_high))
  pip = float(pip_size)
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
  }
  if barrier is None:
    effective = float(max(targets)) if targets else None
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
  room_pips = buffered_room / pip
  room_atr = buffered_room / atr if atr > 0 else 0.0
  contained = (
    opposing_low <= planned <= opposing_high
    or bool(getattr(barrier, "contains_price", False))
  )
  tier = str(getattr(barrier, "tier", "") or "")
  tags = [str(tag) for tag in getattr(barrier, "tags", ()) or ()]
  measured = {
    **base_measured,
    "opposing_low": opposing_low,
    "opposing_high": opposing_high,
    "opposing_tier": tier,
    "opposing_tags": tags,
    "opposing_contains_price": bool(
      getattr(barrier, "contains_price", False)
    ),
    "entry_overlap_price": round(overlap_price, 6),
    "entry_overlap_ratio": round(overlap_ratio, 6),
    "raw_room_price": round(raw_room, 6),
    "barrier_buffer_price": round(buffer_price, 6),
    "buffered_room_price": round(buffered_room, 6),
    "room_pips": round(room_pips, 3),
    "room_atr": round(room_atr, 4),
  }
  if contained:
    return StructuralTargetRoomDecision(
      False,
      "opposing_entry_contained",
      "planned entry is inside an opposing actionable structure",
      True,
      measured,
      opposing_entry=barrier,
    )
  if tier.casefold() == "major" and buffered_room <= 0:
    return StructuralTargetRoomDecision(
      False,
      "opposing_major_no_room",
      "opposing major structure leaves no buffered target room",
      True,
      measured,
      opposing_entry=barrier,
    )
  if overlap_price > 0:
    return StructuralTargetRoomDecision(
      False,
      "opposing_entry_overlap",
      "candidate entry band overlaps an opposing actionable structure",
      True,
      measured,
      opposing_entry=barrier,
    )
  if buffered_room <= 0:
    return StructuralTargetRoomDecision(
      False,
      "opposing_barrier_no_target",
      "opposing structure leaves no positive buffered target room",
      True,
      measured,
      opposing_entry=barrier,
    )
  fitted = tuple(target for target in targets if target <= room_pips)
  if not fitted:
    return StructuralTargetRoomDecision(
      False,
      "opposing_barrier_no_target",
      "no configured target fits before the opposing structure",
      True,
      {
        **measured,
        "effective_target_pips": None,
      },
      opposing_entry=barrier,
    )
  effective = float(max(fitted))
  return StructuralTargetRoomDecision(
    True,
    "opposing_barrier_target_capped",
    "configured target ladder capped before opposing structure",
    False,
    {
      **measured,
      "effective_target_pips": effective,
    },
    opposing_entry=barrier,
    fitted_targets_pips=fitted,
    effective_target_pips=effective,
  )
