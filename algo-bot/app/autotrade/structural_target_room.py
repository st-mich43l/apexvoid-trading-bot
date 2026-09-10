"""Pure opposing-structure target-room geometry shared by scanner and TradePlan."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Any, Iterable

from app.analysis.types import Zone
from app.core.log_throttle import log_at_most
from app.scalping.math_features import safe_div

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZoneOpposingEntry:
  """Minimal opposing-structure shape this module's own functions read
  (``side``/``lo``/``hi``/``tier``/``tags``/``contains_price``) -- adapts a
  technique-native ``Zone`` (the same displacement/supply-demand detector
  data both the scanner's actionability gate and TradePlan build from)
  instead of Market Map.

  ``touches``/``mitigated`` (2026-09, Opposing Structure V2) are additive —
  every existing caller that builds a ``ZoneOpposingEntry`` without them
  keeps today's behavior (touches=0/mitigated=False are never read by the
  pre-existing tier/containment/zero-room logic in this file).
  """
  side: str
  lo: float
  hi: float
  tier: str = "zone"
  tags: tuple[str, ...] = ()
  contains_price: bool = False
  score: float = 0.0
  touches: int = 0
  mitigated: bool = False


def zone_meets_execution_width(
  zone: Zone,
  *,
  atr: float,
  pip_size: float,
  max_width_atr: float,
  max_width_pips: float,
) -> bool:
  """True when ``zone`` is narrow enough to be a meaningful execution wall.

  Mirrors the M1 HTF veto's own execution-zone width gate so a caller
  feeding this module raw per-timeframe zones (not already filtered, as
  the M1 veto's own zone scan is) gets the same treatment.
  """
  width = float(zone.high - zone.low)
  if not math.isfinite(width) or width <= 0 or pip_size <= 0 or atr <= 0:
    return False
  return not (
    width / atr > max_width_atr or width / pip_size > max_width_pips
  )


def _zone_tier(zone: Zone, *, major_score: float) -> str:
  """Mirrors market_map.py's own tier formula exactly (``_zone_entry``):
  ``"major" if htf and (fresh or score >= major_score) else "zone"``. A
  zone only ever carries the "HTF Zone" score reason when it was scored
  through the full multi-timeframe pipeline (``_apply_mtf_zone_scores``,
  what builds the scanner's own ``per_tf`` analysis) - a caller working
  from a bare single-timeframe zone scan (no MTF scoring applied) will
  therefore always get "zone" tier here, same as before this function
  existed, with no separate code path needed for that caller.
  """
  htf = any(
    str(reason).casefold() == "htf zone"
    for reason in (zone.score_reasons or ())
  )
  fresh = int(zone.touches) == 0
  return "major" if htf and (fresh or zone.score >= major_score) else "zone"


def zone_opposing_entries(
  zones: Iterable[Zone] | None,
  *,
  major_score: float = 12.0,
  include_mitigated: bool = False,
) -> tuple[ZoneOpposingEntry, ...]:
  """Technique-native opposing entries for the target-room check.

  2026-09 (owner: "these technique calculate swing right? so we can
  migrate to scanner, detector and clean"). Tier is derived the same way
  Market Map's own build_map derives it (see ``_zone_tier``) so a
  genuinely major wall still hard-blocks the way it always has -
  "zone"/"level" tier gets the weaker treatment
  (``_WEAK_OPPOSING_TIERS``), "major" does not. ``major_score`` defaults
  to the schema default for ``analysis.market_map.major_score``; pass the
  configured value when available. Unsided round-number/reaction key
  levels are not included: they only ever got the same lenient, near-
  zero-effect treatment Market Map's own "level" tier already had, so
  there's no safety value dropped by leaving them out. Callers that need
  an execution-width gate (the M1 HTF veto's own zone scan already has
  one applied) should pre-filter with ``zone_meets_execution_width``
  before calling this.

  ``include_mitigated`` (2026-09, Opposing Structure V2): every existing
  caller keeps the original mitigated-zone drop (default False) - a
  mitigated zone has never been a real opposing wall here. Opposing
  Structure V2's own evaluator passes True so it can positively label
  ``IGNORED_MITIGATED`` instead of silently seeing "no barrier at all,"
  which telemetry needs to distinguish from "genuinely nothing opposing."
  """
  if not zones:
    return ()
  return tuple(
    ZoneOpposingEntry(
      side="buy" if zone.side == "demand" else "sell",
      lo=zone.low,
      hi=zone.high,
      tier=_zone_tier(zone, major_score=major_score),
      score=zone.score,
      touches=int(zone.touches or 0),
      mitigated=bool(zone.mitigated),
    )
    for zone in zones
    if zone.side in ("demand", "supply")
    and (include_mitigated or not zone.mitigated)
  )


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


# 2026-09 (owner-reported production regression, repaired via Opposing
# Structure V2): PR #493 widened this to {"level", "zone"} so a
# geometrically-nearest "zone"-tier entry (e.g. a minor reclaimed
# breakout-retest demand pocket) would never hard-block or room-starve a
# setup whose own technique read the move as continuing well past it. That
# was correct for room-with-genuine-distance, but it also silently
# unblocked the two *structurally impossible* cases this module exists to
# catch - an entry landing directly inside a real, undisplaced, unmitigated
# directional zone, or zero/negative raw room against one - for every
# ordinary zone, not just the minor ones. Production data isolated to the
# exact PR #493 merge timestamp confirmed the cost: Key Level auto-trades
# went from 68 trades / 56% win rate / +628 net pips to 8 trades / 37.5% /
# -165 net pips in the days after.
#
# Only a genuinely unsided, non-directional "level" (a round-number/generic
# reaction level with no real supply/demand behind it) gets the weak
# treatment now - it never carried directional structural authority to
# begin with. An ordinary directional "zone" is real evidence again: it
# hard-blocks on containment/zero-room exactly like "major" does. What
# "zone" tier does NOT get back is the room-based fixed_rr ladder cap PR
# #493 mentioned - that mechanism (_fixed_rr_adaptive_room_pips) was
# deleted outright by a separate same-day PR (#499) and is out of scope
# here; a "zone" with genuine positive room (the Sept-7 motivating trade)
# still passes through this function exactly as before.
#
# Continuous strength/room-in-R context for a "zone"/"major" barrier that
# does NOT hit one of these two hard-structural branches is computed by
# evaluate_opposing_structure_v2() below and persisted as telemetry
# (OpposingStructureEvidence) - not read by this frozenset or by the
# containment/zero-room branches themselves, which stay pure geometry per
# the owner's own "hard geometry stays separate from quality" instruction.
_WEAK_OPPOSING_TIERS = frozenset({"level"})


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


def _clamp01(value: float) -> float:
  return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class OpposingStructureEvidence:
  """Opposing Structure V2 (2026-09 Key Level repair): continuous
  strength/room-in-R context for the nearest opposing barrier this module
  already found via ``_nearest_opposing`` - computed and persisted as
  telemetry alongside ``evaluate_structural_target_room``'s existing
  hard-block decision, never read BY that decision. Hard geometry
  (containment, zero/negative room) stays pure price-space geometry per
  the owner's own "hard geometry stays separate from quality" instruction
  - nothing here softens or toughens ``_WEAK_OPPOSING_TIERS``/the
  containment/zero-room branches.

  ``room_r``/``before_tp1``/``room_pressure_score``/``opposing_risk_score``
  are all ``None`` when no protective-stop distance was supplied by the
  caller - "persist raw room and defer authoritative R-based gating"
  rather than fabricate a stop. ``action``/``reason_code`` are computed
  for every barrier (including the R-based bands: ``BLOCK_BEFORE_TP1``,
  ``CAUTION``, ``CONFIRMATION_REQUIRED``) but in this pass nothing reads
  them to gate anything - only ``BLOCK_INSIDE``/``BLOCK_ZERO_ROOM`` have a
  live counterpart (the containment/zero-room hard-block branches in
  ``evaluate_structural_target_room``, keyed on geometry directly, not on
  this dataclass). The rest is shadow telemetry for a follow-up PR.
  """
  direction: str
  zone_low: float
  zone_high: float
  zone_side: str
  tier: str
  zone_score: float
  touches: int
  mitigated: bool
  displaced: bool
  entry_price: float
  raw_room_price: float
  raw_room_pips: float
  room_atr: float | None
  room_r: float | None
  first_target_r: float | None
  before_tp1: bool | None
  strength_score: float
  room_pressure_score: float | None
  opposing_risk_score: float | None
  action: str
  reason_code: str

  def to_dict(self) -> dict[str, Any]:
    return {
      "direction": self.direction,
      "zone_low": self.zone_low,
      "zone_high": self.zone_high,
      "zone_side": self.zone_side,
      "tier": self.tier,
      "zone_score": round(float(self.zone_score), 4),
      "touches": self.touches,
      "mitigated": self.mitigated,
      "displaced": self.displaced,
      "entry_price": self.entry_price,
      "raw_room_price": round(float(self.raw_room_price), 6),
      "raw_room_pips": round(float(self.raw_room_pips), 3),
      "room_atr": None if self.room_atr is None else round(float(self.room_atr), 4),
      "room_r": None if self.room_r is None else round(float(self.room_r), 4),
      "first_target_r": self.first_target_r,
      "before_tp1": self.before_tp1,
      "strength_score": round(float(self.strength_score), 4),
      "room_pressure_score": (
        None if self.room_pressure_score is None
        else round(float(self.room_pressure_score), 4)
      ),
      "opposing_risk_score": (
        None if self.opposing_risk_score is None
        else round(float(self.opposing_risk_score), 4)
      ),
      "action": self.action,
      "reason_code": self.reason_code,
    }


def _opposing_structure_evidence_for_barrier(
  *,
  direction: str,
  barrier: Any,
  planned: float,
  raw_room: float,
  raw_room_pips: float,
  room_atr: float | None,
  protective_stop_distance: float | None,
  first_target_r: float | None,
  strength_score_ceiling: float,
  caution_room_r: float,
) -> OpposingStructureEvidence:
  """Shared math for an already-selected barrier - both
  ``evaluate_opposing_structure_v2`` (which selects its own barrier) and
  ``evaluate_structural_target_room`` (which reuses the barrier/room it
  already computed for its own hard-block decision, never re-selecting)
  build the evidence object through this one function so the two never
  drift.
  """
  zone_low = float(getattr(barrier, "lo"))
  zone_high = float(getattr(barrier, "hi"))
  tier = str(getattr(barrier, "tier", "") or "")
  zone_score = float(getattr(barrier, "score", 0.0) or 0.0)
  touches = int(getattr(barrier, "touches", 0) or 0)
  mitigated = bool(getattr(barrier, "mitigated", False))
  # A genuinely displaced entry never reaches _nearest_opposing in
  # production - callers filter displacement upstream via
  # filter_displaced_opposing_entries on authoritative closed bars (this
  # module's own contract, unchanged). ``displaced`` here only fires for a
  # caller/test that deliberately keeps a displaced-flagged entry in for
  # telemetry purposes, mirroring ``include_mitigated``.
  displaced = bool(getattr(barrier, "displaced", False))
  contained = zone_low <= planned <= zone_high

  strength = 0.0 if (mitigated or displaced) else _clamp01(
    zone_score / max(1e-9, float(strength_score_ceiling))
  )

  room_r: float | None = None
  if protective_stop_distance is not None:
    risk = float(protective_stop_distance)
    if math.isfinite(risk) and risk > 0:
      room_r = raw_room / risk

  before_tp1: float | None = None
  if room_r is not None and first_target_r is not None and float(first_target_r) > 0:
    before_tp1 = room_r < float(first_target_r)

  room_pressure: float | None = None
  opposing_risk: float | None = None
  if room_r is not None and float(caution_room_r) > 0:
    room_pressure = _clamp01((float(caution_room_r) - room_r) / float(caution_room_r))
    opposing_risk = strength * room_pressure

  if mitigated:
    action, reason_code = "IGNORED_MITIGATED", "opposing_zone_mitigated"
  elif displaced:
    action, reason_code = "IGNORED_DISPLACED", "opposing_zone_displaced"
  elif contained:
    action, reason_code = "BLOCK_INSIDE", "opposing_entry_contained"
  elif raw_room <= 0:
    action, reason_code = "BLOCK_ZERO_ROOM", "opposing_zero_or_negative_room"
  elif before_tp1:
    action, reason_code = "BLOCK_BEFORE_TP1", "opposing_room_before_tp1"
  elif room_r is not None and room_r < 0.5:
    action, reason_code = "CAUTION", "opposing_room_critical"
  elif room_r is not None and room_r < float(caution_room_r):
    action, reason_code = "CONFIRMATION_REQUIRED", "opposing_room_tight"
  else:
    action, reason_code = "CLEAR", "opposing_room_clear"

  # §47 — one throttled debug line per distinct barrier decision, not a
  # noisy per-cycle INFO log. Keyed on the barrier's own bounds/action so
  # a transition (e.g. clear -> tight as price drifts) gets its own key
  # and isn't swallowed by the throttle window for the prior state.
  log_at_most(
    log,
    f"opp_v2:{direction}:{round(zone_low, 4)}:{round(zone_high, 4)}:{action}",
    "key_level_opposing_structure direction=%s entry=%s zone=%s-%s "
    "tier=%s strength=%.3f room_pips=%.2f room_r=%s tp1_r=%s "
    "before_tp1=%s action=%s reason=%s",
    direction,
    round(planned, 6),
    round(zone_low, 6),
    round(zone_high, 6),
    tier,
    strength,
    raw_room_pips,
    None if room_r is None else round(room_r, 4),
    first_target_r,
    before_tp1,
    action,
    reason_code,
    level=logging.DEBUG,
  )

  return OpposingStructureEvidence(
    direction=str(direction).upper(),
    zone_low=zone_low,
    zone_high=zone_high,
    zone_side=str(getattr(barrier, "side", "")),
    tier=tier,
    zone_score=zone_score,
    touches=touches,
    mitigated=mitigated,
    displaced=displaced,
    entry_price=planned,
    raw_room_price=raw_room,
    raw_room_pips=raw_room_pips,
    room_atr=room_atr,
    room_r=room_r,
    first_target_r=(None if first_target_r is None else float(first_target_r)),
    before_tp1=before_tp1,
    strength_score=strength,
    room_pressure_score=room_pressure,
    opposing_risk_score=opposing_risk,
    action=action,
    reason_code=reason_code,
  )


def evaluate_opposing_structure_v2(
  *,
  direction: str,
  planned_entry_price: float,
  entries: Iterable[Any],
  atr: float,
  pip_size: float,
  protective_stop_distance: float | None = None,
  first_target_r: float | None = None,
  strength_score_ceiling: float = 15.0,
  caution_room_r: float = 2.0,
) -> OpposingStructureEvidence | None:
  """Opposing Structure V2: continuous strength + room-in-R evidence for
  the nearest opposing barrier ahead of ``planned_entry_price``, reusing
  the same proximity-first ``_nearest_opposing`` selection
  ``evaluate_structural_target_room`` uses for its own hard-block
  decision. Returns ``None`` when there's no opposing entry on the correct
  side at all - "nothing to evaluate" is distinct from "evaluated and
  clear."

  ``strength_score`` is deliberately just the zone's own score normalized
  against ``strength_score_ceiling`` (0 when mitigated or displaced) - the
  zone's score already bakes in freshness/HTF/touch-quality/source
  confluence (see app/analysis/zones.py::_score_zone); re-adding separate
  terms for the same evidence would double-count it.

  ``room_r``/``before_tp1``/``room_pressure_score``/``opposing_risk_score``
  need a real ``protective_stop_distance`` (price units, always positive)
  to mean anything - when the caller doesn't have one yet, those fields
  stay ``None`` (never a fabricated stop) and only the raw
  price/pips/ATR room is reported.
  """
  side = str(direction).upper()
  if side not in {"BUY", "SELL"}:
    return None
  planned = float(planned_entry_price)
  pip = float(pip_size)
  if not math.isfinite(planned) or not math.isfinite(pip) or pip <= 0:
    return None
  barrier = _nearest_opposing(side, planned, planned, planned, entries)
  if barrier is None:
    return None

  zone_low = float(getattr(barrier, "lo"))
  zone_high = float(getattr(barrier, "hi"))
  raw_room = zone_low - planned if side == "BUY" else planned - zone_high
  raw_room_pips = safe_div(raw_room, pip, default=0.0) or 0.0
  room_atr = safe_div(raw_room, atr) if atr > 0 else None

  return _opposing_structure_evidence_for_barrier(
    direction=side,
    barrier=barrier,
    planned=planned,
    raw_room=raw_room,
    raw_room_pips=raw_room_pips,
    room_atr=room_atr,
    protective_stop_distance=protective_stop_distance,
    first_target_r=first_target_r,
    strength_score_ceiling=strength_score_ceiling,
    caution_room_r=caution_room_r,
  )


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
  protective_stop_distance: float | None = None,
  first_target_r: float | None = None,
  strength_score_ceiling: float = 15.0,
  caution_room_r: float = 2.0,
) -> StructuralTargetRoomDecision:
  """Measure opposing structure ahead — never invent a tiny TP ladder.

  Hard-blocks only on structural impossibility: planned entry contained in
  the opposing structure, or raw geometric room <= 0.

  Live 2026-08-06 Trendline BUY published a single absolute TP at
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

  ``protective_stop_distance``/``first_target_r``/``strength_score_ceiling``/
  ``caution_room_r`` (2026-09, Opposing Structure V2, all optional) feed
  ``measured["opposing_evidence"]`` (an ``OpposingStructureEvidence.to_dict()``,
  present whenever a barrier was found) - shadow telemetry only. They never
  change ``allowed``/``hard_block`` here; the containment/zero-room hard
  gates below stay pure price-space geometry.
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
  # Opposing Structure V2 (2026-09) — shadow telemetry only, built from the
  # SAME barrier/raw_room this function already selected above (never a
  # second, possibly-divergent _nearest_opposing call). Uses raw (not
  # buffer-adjusted) room throughout, matching OpposingStructureEvidence's
  # own "raw_room_*" naming.
  raw_room_atr = safe_div(raw_room, atr) if atr > 0 else None
  measured["opposing_evidence"] = _opposing_structure_evidence_for_barrier(
    direction=side,
    barrier=barrier,
    planned=planned,
    raw_room=raw_room,
    raw_room_pips=raw_room_pips,
    room_atr=raw_room_atr,
    protective_stop_distance=protective_stop_distance,
    first_target_r=first_target_r,
    strength_score_ceiling=strength_score_ceiling,
    caution_room_r=caution_room_r,
  ).to_dict()

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
    # Weak map "level"/"zone" bands are often stacked noise, or minor
    # reclaimed structure a real technique setup is expected to trade
    # through, next to the real move. Major containment stays a hard
    # structural reject.
    if tier.casefold() in _WEAK_OPPOSING_TIERS:
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
    if tier.casefold() in _WEAK_OPPOSING_TIERS:
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
