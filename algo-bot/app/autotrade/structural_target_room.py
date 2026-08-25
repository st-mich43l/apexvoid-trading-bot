"""Pure opposing-structure target-room geometry shared by scanner and V7."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Any, Iterable

from app.core.log_throttle import log_at_most

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
  log.debug(
    "structural_target_room displacement direction=%s closes=%s "
    "kept=%s dropped=%s dropped_bounds=%s",
    side,
    closes,
    len(kept),
    len(dropped),
    [(round(lo, 6), round(hi, 6)) for lo, hi in dropped],
  )
  return kept


def shared_boundary_epsilon(*, pip_size: float, atr: float) -> float:
  """Tick-scale glue tolerance for Market Map walls that share a proximal edge."""
  pip = max(0.0, float(pip_size))
  atr_value = max(0.0, float(atr))
  return max(pip, 0.05 * atr_value)


def _glued_to_ref(
  *,
  side: str,
  entry_low: float,
  entry_high: float,
  ref: float,
  epsilon: float,
) -> bool:
  if not math.isfinite(ref):
    return False
  if side == "SELL":
    return abs(entry_high - ref) <= epsilon and entry_high <= ref + epsilon
  return abs(entry_low - ref) <= epsilon and entry_low >= ref - epsilon


def filter_shared_boundary_opposing_entries(
  entries: Iterable[Any],
  *,
  direction: str,
  candidate_entry_low: float,
  candidate_entry_high: float,
  pip_size: float,
  atr: float,
  planned_entry: float | None = None,
) -> tuple[list[Any], dict[str, Any]]:
  """V8: drop opposing map entries glued to the candidate proximal wall.

  Market Map often stacks demand under supply (or supply over demand) so the
  opposing far edge equals the candidate proximal edge *or* the live/planned
  price sitting on that wall. Measuring room from that price yields
  raw_room≈0 — those shared walls are not structure ahead. Deep penetration
  past the wall stays and still hard-blocks.
  """
  side = str(direction).upper()
  low = min(float(candidate_entry_low), float(candidate_entry_high))
  high = max(float(candidate_entry_low), float(candidate_entry_high))
  epsilon = shared_boundary_epsilon(pip_size=pip_size, atr=atr)
  opposing_side = "sell" if side == "BUY" else "buy"
  kept: list[Any] = []
  dropped: list[tuple[float, float]] = []
  if side not in {"BUY", "SELL"} or not math.isfinite(epsilon):
    return list(entries), {
      "applied": False,
      "reason": "invalid_shared_boundary_geometry",
      "epsilon": epsilon,
    }
  refs = [low] if side == "SELL" else [high]
  if planned_entry is not None:
    refs.append(float(planned_entry))
  for entry in entries:
    if str(getattr(entry, "side", "")).casefold() != opposing_side:
      kept.append(entry)
      continue
    entry_low = float(getattr(entry, "lo"))
    entry_high = float(getattr(entry, "hi"))
    glued = any(
      _glued_to_ref(
        side=side,
        entry_low=entry_low,
        entry_high=entry_high,
        ref=ref,
        epsilon=epsilon,
      )
      for ref in refs
    )
    if glued:
      dropped.append((entry_low, entry_high))
    else:
      kept.append(entry)
  state = {
    "applied": True,
    "contract": "v8",
    "epsilon": round(epsilon, 6),
    "entries_before": len(kept) + len(dropped),
    "entries_after": len(kept),
    "shared_boundary_excluded": len(dropped),
    "dropped_bounds": [(round(lo, 6), round(hi, 6)) for lo, hi in dropped],
  }
  log.debug(
    "structural_target_room v8 shared_boundary direction=%s "
    "epsilon=%s kept=%s dropped=%s dropped_bounds=%s",
    side,
    round(epsilon, 6),
    len(kept),
    len(dropped),
    state["dropped_bounds"],
  )
  return kept, state


def overlap_exclusion_threshold(*, pip_size: float, atr: float) -> float:
  """Minimum price overlap to treat an opposing map band as the same wall."""
  pip = max(0.0, float(pip_size))
  atr_value = max(0.0, float(atr))
  return max(2.0 * pip, 0.10 * atr_value)


def filter_overlapping_opposing_entries(
  entries: Iterable[Any],
  *,
  direction: str,
  candidate_entry_low: float,
  candidate_entry_high: float,
  pip_size: float,
  atr: float,
) -> tuple[list[Any], dict[str, Any]]:
  """Drop opposing map entries that substantially overlap the candidate band.

  Technique / confluence geometry often stacks on top of Market Map
  demand/supply so the opposing entry *is* the candidate's own wall.
  Shared-boundary glue only catches edge-touch cases; overlap catches the
  band-inside-band case. Barriers beyond the candidate (no meaningful
  overlap) stay and still hard-block.
  """
  side = str(direction).upper()
  low = min(float(candidate_entry_low), float(candidate_entry_high))
  high = max(float(candidate_entry_low), float(candidate_entry_high))
  threshold = overlap_exclusion_threshold(pip_size=pip_size, atr=atr)
  opposing_side = "sell" if side == "BUY" else "buy"
  kept: list[Any] = []
  dropped: list[tuple[float, float]] = []
  if side not in {"BUY", "SELL"} or not math.isfinite(threshold):
    return list(entries), {
      "applied": False,
      "reason": "invalid_overlap_geometry",
      "threshold": threshold,
    }
  for entry in entries:
    if str(getattr(entry, "side", "")).casefold() != opposing_side:
      kept.append(entry)
      continue
    entry_low = float(getattr(entry, "lo"))
    entry_high = float(getattr(entry, "hi"))
    overlap_width, _ratio = _overlap(low, high, entry_low, entry_high)
    if overlap_width >= threshold:
      dropped.append((entry_low, entry_high))
    else:
      kept.append(entry)
  state = {
    "applied": True,
    "contract": "v8_overlap",
    "threshold": round(threshold, 6),
    "entries_before": len(kept) + len(dropped),
    "entries_after": len(kept),
    "overlap_excluded": len(dropped),
    "dropped_bounds": [(round(lo, 6), round(hi, 6)) for lo, hi in dropped],
  }
  log.debug(
    "structural_target_room v8 overlap direction=%s "
    "threshold=%s kept=%s dropped=%s dropped_bounds=%s",
    side,
    round(threshold, 6),
    len(kept),
    len(dropped),
    state["dropped_bounds"],
  )
  return kept, state


def zone_proximal_room_reference(
  *,
  direction: str,
  spot_price: float,
  candidate_entry_low: float,
  candidate_entry_high: float,
  pip_size: float,
  atr: float,
) -> tuple[float, str]:
  """V8: when spot is in/near the candidate zone, measure room from proximal.

  Returns (room_reference_price, room_reference_source). Order routing still
  uses executable spot; this value is only for structural target-room geometry.
  """
  side = str(direction).upper()
  spot = float(spot_price)
  low = min(float(candidate_entry_low), float(candidate_entry_high))
  high = max(float(candidate_entry_low), float(candidate_entry_high))
  epsilon = shared_boundary_epsilon(pip_size=pip_size, atr=atr)
  in_or_near = (low - epsilon) <= spot <= (high + epsilon)
  if side == "SELL" and in_or_near and math.isfinite(spot):
    return low, "v8_zone_proximal"
  if side == "BUY" and in_or_near and math.isfinite(spot):
    return high, "v8_zone_proximal"
  return spot, "executable_spot"


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
  room_reference_source: str | None = None,
  executable_entry_price: float | None = None,
  shared_boundary_state: dict[str, Any] | None = None,
  allow_same_wall_overlap: bool = True,
) -> StructuralTargetRoomDecision:
  """Measure opposing structure ahead — never invent a tiny TP ladder.

  Hard-blocks only on structural impossibility: planned entry contained in
  the opposing structure, or raw geometric room <= 0.

  Live 2026-08-06 Trendline Reaction BUY published a single absolute TP at
  4255.49 with close_ratio=1.0 (~9 pips from fill) because this function
  used to replace the owner ladder with ``floor(usable_room)``. That is
  scalping theatre. Owner directive: reaction/swing setups always keep the
  configured partial ladder (30/60/90/120/200) — barrier room is telemetry
  only, not a TP calculator.

  Market Map ``contains_price`` is telemetry only. Candidate-band overlap
  without planned-entry containment is allow-with-warning — never a hard
  structural reject and never a reason to shrink ``fitted_targets_pips``.

  Callers must apply ``filter_displaced_opposing_entries`` on authoritative
  recent closed bars before passing ``actionable_entries``.
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

  room_entries, internal_shared = filter_shared_boundary_opposing_entries(
    actionable_entries,
    direction=side,
    candidate_entry_low=low,
    candidate_entry_high=high,
    pip_size=pip,
    atr=atr,
    planned_entry=planned,
  )
  if internal_shared.get("applied"):
    prior = dict(shared_boundary_state or {})
    extra_dropped = list(internal_shared.get("dropped_bounds") or [])
    shared_boundary_state = {
      **prior,
      **internal_shared,
      "shared_boundary_excluded": (
        int(prior.get("shared_boundary_excluded") or 0)
        + int(internal_shared.get("shared_boundary_excluded") or 0)
      ),
      "dropped_bounds": list(prior.get("dropped_bounds") or []) + extra_dropped,
    }

  internal_overlap: dict[str, Any] = {
    "applied": False,
    "reason": "same_wall_overlap_not_allowed",
  }
  if allow_same_wall_overlap:
    room_entries, internal_overlap = filter_overlapping_opposing_entries(
      room_entries,
      direction=side,
      candidate_entry_low=low,
      candidate_entry_high=high,
      pip_size=pip,
      atr=atr,
    )
  if internal_overlap.get("applied"):
    prior = dict(shared_boundary_state or {})
    extra_dropped = list(internal_overlap.get("dropped_bounds") or [])
    shared_boundary_state = {
      **prior,
      "overlap_state": internal_overlap,
      "overlap_excluded": int(internal_overlap.get("overlap_excluded") or 0),
      "dropped_bounds": list(prior.get("dropped_bounds") or []) + extra_dropped,
    }

  barrier = _nearest_opposing(
    side,
    planned,
    low,
    high,
    room_entries,
  )
  base_measured: dict[str, Any] = {
    "planned_entry_price": planned,
    "candidate_entry_low": low,
    "candidate_entry_high": high,
    "configured_target_pips": list(targets),
    "execution_cost_pips": cost,
    "min_capped_target_pips": preference_floor,
  }
  if room_reference_source:
    base_measured["room_reference_source"] = str(room_reference_source)
    base_measured["room_reference_price"] = planned
  if executable_entry_price is not None and math.isfinite(
    float(executable_entry_price)
  ):
    base_measured["executable_entry_price"] = float(executable_entry_price)
  if displacement_state:
    base_measured["displacement_state"] = dict(displacement_state)
  if shared_boundary_state:
    base_measured["shared_boundary_state"] = dict(shared_boundary_state)
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
    msg = (
      "structural_target_room allowed=%s hard_block=%s reason=%s "
      "direction=%s planned_entry=%s opposing_low=%s opposing_high=%s "
      "planned_entry_contained=%s market_price_contained=%s "
      "overlap_price=%s overlap_ratio=%s raw_room=%s buffered_room=%s "
      "displacement=%s"
    )
    args = (
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
    if decision.allowed and not decision.hard_block:
      log.debug(msg, *args)
    else:
      entry_key = round(float(planned), 4) if math.isfinite(float(planned)) else planned
      log_at_most(
        log,
        f"str_room:{decision.reason_code}:{side}:{entry_key}",
        msg,
        *args,
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
    # Weak map "level" bands are often stacked noise next to a real zone —
    # they silenced HFS/technique analysis while discretionary charts still
    # had a trade. Major/zone containment stays a hard structural reject.
    if tier.casefold() == "level":
      measured["weak_opposing_level_ignored"] = True
      measured["weak_opposing_level_reason"] = reason
      log.debug(
        "structural_target_room ignoring weak opposing level "
        "reason=%s direction=%s planned_entry=%s opposing=%s-%s",
        reason,
        side,
        planned,
        opposing_low,
        opposing_high,
      )
    else:
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
  if raw_room <= 0 and not measured.get("weak_opposing_level_ignored"):
    reason = (
      "opposing_major_no_room"
      if tier.casefold() == "major"
      else "opposing_barrier_no_target"
    )
    if tier.casefold() == "level":
      measured["weak_opposing_level_ignored"] = True
      measured["weak_opposing_level_reason"] = reason
      log.debug(
        "structural_target_room ignoring weak opposing level "
        "reason=%s direction=%s planned_entry=%s opposing=%s-%s",
        reason,
        side,
        planned,
        opposing_low,
        opposing_high,
      )
    else:
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
  elif raw_room <= 0 and measured.get("weak_opposing_level_ignored"):
    pass  # fall through — treat weak level as non-barrier below

  if measured.get("weak_opposing_level_ignored"):
    effective = float(max(targets)) if targets else None
    return _log_decision(StructuralTargetRoomDecision(
      True,
      "weak_opposing_level_ignored",
      "weak map level opposing ignored; analysis continues",
      False,
      {
        **measured,
        "usable_room_pips": round(max(0.0, room_pips), 3),
        "effective_target_pips": effective,
        "preference_telemetry": True,
      },
      opposing_entry=barrier,
      fitted_targets_pips=targets,
      effective_target_pips=effective,
    ))

  # Band overlap without planned-entry containment: allow + telemetry.
  if overlap_price > 0:
    measured["band_overlap_without_planned_containment"] = True

  usable_pips = max(0.0, room_pips)
  effective = float(max(targets)) if targets else None
  would_fit = tuple(target for target in targets if target <= usable_pips)
  barrier_would_cap = bool(targets) and len(would_fit) < len(targets)
  below_cost = usable_pips < cost
  # Owner 2026-08-06 (revised same day): buffered usable room below the
  # execution-cost floor is a hard structural kill — publishing a full
  # 30/60/90/120/200 ladder into ~0 pip of barrier room put live Trendline
  # / Key Level SELs next to demand (fe023dd8 @ 4268 with opposing high
  # 4267.8). Positive usable room still never invents floor(usable) as a
  # solo TP and never trims the configured partial ladder.
  if below_cost:
    return _log_decision(StructuralTargetRoomDecision(
      False,
      "opposing_barrier_room_below_cost",
      (
        "buffered target room sits below execution-cost floor; "
        "opposing barrier leaves no tradable TP room"
      ),
      True,
      {
        **measured,
        "usable_room_pips": round(usable_pips, 3),
        "effective_target_pips": None,
        "barrier_would_cap_ladder": barrier_would_cap,
        "barrier_usable_room_below_cost": True,
      },
      opposing_entry=barrier,
    ))
  if not targets:
    reason = "opposing_barrier_no_configured_targets"
    message = "opposing structure present but no configured targets to publish"
  elif barrier_would_cap:
    reason = "opposing_barrier_room_ignored_full_ladder"
    message = (
      "opposing structure would have truncated the ladder; "
      "configured partial ladder published unchanged"
    )
  else:
    reason = "opposing_barrier_full_ladder_fits"
    message = (
      "opposing structure present but configured ladder fits within buffered room"
    )
  return _log_decision(StructuralTargetRoomDecision(
    True,
    reason,
    message,
    False,
    {
      **measured,
      "usable_room_pips": round(usable_pips, 3),
      "effective_target_pips": effective,
      "barrier_would_cap_ladder": barrier_would_cap,
      "barrier_usable_room_below_cost": False,
      "preference_telemetry": True,
    },
    opposing_entry=barrier,
    fitted_targets_pips=targets,
    effective_target_pips=effective,
  ))
