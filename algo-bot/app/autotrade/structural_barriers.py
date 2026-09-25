"""StructuralBarrierBook — canonical multi-timeframe opposing-structure pool.

2026-09 Key Level structural repair (Phase 1). The 2026-09-07 "Market Map
purge" (#500/#502) replaced the old ``MarketMap.actionable_entries`` pool
(built by ``build_map()`` pooling every timeframe's candidates, then
merging same-side overlaps and reconciling cross-side conflicts) with two
independent, single-timeframe (M15-only) zone reads that never gained an
equivalent merge/reconciliation step. This module restores that missing
structural-pool behavior directly against ``analysis.per_tf`` zones,
without reviving Market Map itself as a trading dependency:
``market_map.py``/``map_strategy.py`` are still fully intact (never
deleted, just unwired) and this module's merge/reconciliation logic is
adapted from their still-readable ``_merge_display_entries``/
``_resolve_cross_side_overlaps`` — ported against the current
``Zone``/``ZoneOpposingEntry`` shapes, not copied verbatim, and
deliberately dropping their display-only half (label formatting, per-side
display caps).

Enforced by ``tests/test_structural_barriers_dependency.py``: this module
must never import ``market_map``, ``map_strategy``, ``MarketMap``,
``MapEntry``, or any ``market_map_key``/cache read.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from app.analysis.trendlines import value_at
from app.analysis.types import Zone
from app.autotrade.structural_target_room import (
  ZoneOpposingEntry,
  _zone_tier,
  zone_meets_execution_width,
)

DEFAULT_STRUCTURAL_TIMEFRAMES: tuple[str, ...] = ("M5", "M15", "H1")

# Same circuit-breaker philosophy as market_map.py's own cross-side
# reconciliation (learned from the 22 Jul zone-reconcile regression): never
# let one pass strip more than a third of the pool, fail open (return input
# unchanged) instead of risking a runaway cascade emptying a side of the book.
_CROSS_SIDE_OVERLAP_RATIO = 0.5
_CROSS_SIDE_MAX_DROP_FRACTION = 0.34

_TIER_RANK = {"level": 1, "zone": 2, "major": 3}

# Session-level band width, price units = this * ATR. Mirrors market_map.py's
# own SESSION_BAND_ATR constant exactly (duplicated, not imported - this
# module must never import market_map, see the dependency test).
_SESSION_BAND_ATR = 0.1
_MAJOR_SESSION_LEVELS = {"PDH", "PDL", "PWH", "PWL"}


@dataclass(frozen=True)
class StructuralBarrier:
  """One canonical opposing-structure wall, merged across whichever
  timeframes independently detected it.

  ``side`` uses the same "buy"/"sell" convention as
  ``structural_target_room.ZoneOpposingEntry`` and market_map.py's
  ``MapEntry`` — "buy" is a demand/support wall below price, "sell" a
  supply/resistance wall above. ``source_timeframes`` is the set of
  timeframes whose zones were merged into this one barrier (a single
  timeframe when nothing else overlapped it).
  """
  side: str
  low: float
  high: float
  tier: str
  score: float
  touches: int
  mitigated: bool
  source_timeframes: tuple[str, ...]


def _tf_rank(tf: str) -> int:
  """Mirrors market_map.py's own ``_tf_rank`` (higher timeframe -> larger
  rank) — used only to order ``source_timeframes`` deterministically."""
  tf = tf.upper()
  if len(tf) < 2 or not tf[1:].isdigit():
    return 0
  value = int(tf[1:])
  if tf.startswith("M"):
    return value
  if tf.startswith("H"):
    return value * 60
  return 0


def _bands_overlap(first_lo: float, first_hi: float, lo: float, hi: float) -> bool:
  return min(first_hi, hi) >= max(first_lo, lo)


def _barrier_from_zone(
  zone: Zone, *, tf: str, major_score: float,
) -> StructuralBarrier:
  return StructuralBarrier(
    side="buy" if zone.side == "demand" else "sell",
    low=float(zone.low),
    high=float(zone.high),
    tier=_zone_tier(zone, major_score=major_score),
    score=float(zone.score),
    touches=int(zone.touches or 0),
    mitigated=bool(zone.mitigated),
    source_timeframes=(tf,),
  )


def _timeframe_atr(analysis: Any) -> float:
  atr_series = getattr(analysis, "atr", None)
  if atr_series is None or len(atr_series) == 0:
    return 0.0
  try:
    last = float(atr_series.iloc[-1])
  except (TypeError, ValueError, IndexError, AttributeError):
    return 0.0
  return last if last == last else 0.0  # NaN check without importing math


def _barriers_from_timeframe(
  zones: Sequence[Zone],
  *,
  tf: str,
  major_score: float,
  atr: float,
  pip_size: float,
  max_width_atr: float,
  max_width_pips: float,
) -> list[StructuralBarrier]:
  out: list[StructuralBarrier] = []
  for zone in zones:
    if zone.side not in ("demand", "supply") or zone.mitigated:
      continue
    if not zone_meets_execution_width(
      zone,
      atr=atr,
      pip_size=pip_size,
      max_width_atr=max_width_atr,
      max_width_pips=max_width_pips,
    ):
      continue
    out.append(_barrier_from_zone(zone, tf=tf, major_score=major_score))
  return out


def _merge_same_side(barriers: list[StructuralBarrier]) -> list[StructuralBarrier]:
  """Port of market_map.py's ``_merge_display_entries`` GEOMETRY core: sort
  same-side candidates, union-merge overlapping bands (promoting tier and
  score to the stronger of the two on merge), carrying forward every
  contributing timeframe. Deliberately omits that function's DISPLAY-only
  half (per-side band-width capping, label formatting) — a barrier's real
  geometric extent must not be narrowed for card-label purposes.
  """
  resolved: list[StructuralBarrier] = []
  for side in ("buy", "sell"):
    candidates = sorted(
      (barrier for barrier in barriers if barrier.side == side),
      key=lambda barrier: (barrier.low, barrier.high),
    )
    merged: list[StructuralBarrier] = []
    for barrier in candidates:
      if not merged or not _bands_overlap(
        merged[-1].low, merged[-1].high, barrier.low, barrier.high,
      ):
        merged.append(barrier)
        continue
      previous = merged[-1]
      tier = max(
        (previous.tier, barrier.tier), key=lambda tier_name: _TIER_RANK[tier_name],
      )
      merged[-1] = StructuralBarrier(
        side=side,
        low=min(previous.low, barrier.low),
        high=max(previous.high, barrier.high),
        tier=tier,
        score=max(previous.score, barrier.score),
        touches=min(previous.touches, barrier.touches),
        mitigated=previous.mitigated or barrier.mitigated,
        source_timeframes=tuple(sorted(
          set(previous.source_timeframes) | set(barrier.source_timeframes),
          key=_tf_rank,
        )),
      )
    resolved.extend(merged)
  return resolved


def _resolve_cross_side_overlaps(
  barriers: list[StructuralBarrier],
) -> list[StructuralBarrier]:
  """Port of market_map.py's ``_resolve_cross_side_overlaps``: drop the
  weaker of two opposing-side barriers that substantially overlap the same
  price band — a demand wall and a supply wall a few points apart are
  contradictory noise, not two independently real barriers. This is the
  reconciliation step ``zone_opposing_entries()`` never gained after the
  Market Map purge (#500/#502) replaced the old pooled structural pool.
  """
  drop: set[int] = set()
  for i, first in enumerate(barriers):
    if i in drop:
      continue
    for j in range(i + 1, len(barriers)):
      if j in drop or barriers[j].side == first.side:
        continue
      second = barriers[j]
      overlap = max(
        0.0, min(first.high, second.high) - max(first.low, second.low),
      )
      first_width = max(0.0, first.high - first.low)
      second_width = max(0.0, second.high - second.low)
      ratio = max(
        (overlap / first_width) if first_width > 0 else (1.0 if overlap > 0 else 0.0),
        (overlap / second_width) if second_width > 0 else (1.0 if overlap > 0 else 0.0),
      )
      if ratio < _CROSS_SIDE_OVERLAP_RATIO:
        continue
      first_key = (_TIER_RANK[first.tier], first.score)
      second_key = (_TIER_RANK[second.tier], second.score)
      drop.add(j if first_key >= second_key else i)
      if i in drop:
        break
  if not barriers or len(drop) / len(barriers) > _CROSS_SIDE_MAX_DROP_FRACTION:
    return barriers
  return [barrier for index, barrier in enumerate(barriers) if index not in drop]


def _confluence_candidates(
  per_tf: Mapping[str, Any],
  *,
  timeframes: tuple[str, ...],
  major_score: float,
  proximal_band_atr: float,
) -> list[tuple[str, float, float, float]]:
  """(side, low, high, score) reference bands from key levels, session
  levels, and unbroken trendlines — mirrors the reference pools
  market_map.py's ``build_map()`` fed into ``_attach_confluence``
  (``key_levels()``/session levels/``trendline_candidates``). A candidate
  here can never become an independent barrier — every one of these is
  ``_level_entry``'s default ``tier="level"`` in the old code, and
  ``_is_structural_actionable`` never accepted "level" tier — so this only
  ever boosts an already-real barrier's score, restoring
  ``_attach_confluence``'s one effect that actually reached a trading
  decision (see structural repair Phase 2/3 notes on
  ``build_structural_barrier_book``).

  Deliberately price-position-agnostic: old market_map.py sided a level by
  comparing it to a live price snapshot at map-build time. Here a
  candidate is offered to both sides and only survives via genuine band
  overlap against an already-correctly-sided barrier below, which makes a
  live price parameter unnecessary — a level far on the wrong side of a
  barrier simply never overlaps it.
  """
  out: list[tuple[str, float, float, float]] = []
  for tf in timeframes:
    analysis = per_tf.get(tf)
    if analysis is None:
      continue
    atr = _timeframe_atr(analysis)
    if atr <= 0:
      continue
    band = max(0.0, proximal_band_atr) * atr
    for level in getattr(analysis, "key_levels", None) or ():
      value = float(level.price)
      score = float(getattr(level, "strength", None) or getattr(level, "touches", 1))
      out.append(("buy", value - band, value + band, score))
      out.append(("sell", value - band, value + band, score))
    session_band = _SESSION_BAND_ATR * atr
    for session in getattr(analysis, "session_levels", None) or ():
      value = float(session.price)
      score = (
        major_score if str(session.name) in _MAJOR_SESSION_LEVELS else 4.0
      )
      out.append(("buy", value - session_band, value + session_band, score))
      out.append(("sell", value - session_band, value + session_band, score))
    df = getattr(analysis, "df", None)
    current_bar = max(0, len(df) - 1) if df is not None else 0
    for line in getattr(analysis, "trendlines", None) or ():
      if line.broken:
        continue
      side = (
        "buy" if line.kind == "support"
        else "sell" if line.kind == "resistance"
        else None
      )
      if side is None:
        continue
      value = value_at(line, current_bar)
      out.append((side, value - band, value + band, float(line.touches)))
  return out


def _apply_confluence_boost(
  barriers: list[StructuralBarrier],
  candidates: list[tuple[str, float, float, float]],
) -> list[StructuralBarrier]:
  """Port of market_map.py's ``_attach_confluence`` — but only its one
  effect that ever reached a trading decision: a barrier overlapping a
  same-side reference (key level, session level, or trendline) gets its
  score bumped to the stronger of the two. Tier is deliberately never
  touched — ``_attach_confluence``'s own ``max(tier)`` could never promote
  a real zone's tier past itself either, since every reference is always
  the weakest ("level") tier rank in the old code (see
  ``_confluence_candidates``), so reproducing that isn't a simplification,
  it's the actual old behavior.
  """
  boosted: list[StructuralBarrier] = []
  for barrier in barriers:
    best_score = barrier.score
    for side, low, high, score in candidates:
      if side != barrier.side or score <= best_score:
        continue
      if _bands_overlap(barrier.low, barrier.high, low, high):
        best_score = score
    boosted.append(
      barrier if best_score == barrier.score
      else replace(barrier, score=best_score)
    )
  return boosted


def build_structural_barrier_book(
  per_tf: Mapping[str, Any],
  *,
  major_score: float,
  pip_size: float,
  max_width_atr: float,
  max_width_pips: float,
  timeframes: tuple[str, ...] = DEFAULT_STRUCTURAL_TIMEFRAMES,
  proximal_band_atr: float = 0.5,
) -> tuple[StructuralBarrier, ...]:
  """The canonical, Market-Map-independent opposing-structure pool.

  Pools every requested timeframe's own ``analysis.per_tf[tf].zones``
  (each already carrying HTF-confluence-boosted scores from
  ``engine.py::_apply_mtf_zone_scores``), tiers each zone the same way
  ``market_map.py``'s ``build_map()`` always did (``_zone_tier``,
  unchanged — reused directly from ``structural_target_room.py``, not
  re-derived a third time), then applies the structural-pool operations
  the purge dropped: same-side merging (``_merge_same_side``), a
  confluence score boost from overlapping key levels/session levels/
  trendlines (``_apply_confluence_boost`` — ``_attach_confluence``'s one
  effect that ever reached a trading decision, see that function's
  docstring), and cross-side reconciliation (``_resolve_cross_side_
  overlaps``, run last so the boosted score is what its tie-break sees,
  matching old ``build_map``'s own ordering).

  This is the single shared source every consumer (scanner actionability,
  Key Level direction resolution, worker/TradePlan room evaluation,
  activation-time revalidation) should build its opposing-structure view
  from, instead of each maintaining its own independent M15/M5 zone read.
  """
  pooled: list[StructuralBarrier] = []
  for tf in timeframes:
    analysis = per_tf.get(tf)
    if analysis is None:
      continue
    zones = list(getattr(analysis, "zones", None) or ())
    if not zones:
      continue
    pooled.extend(_barriers_from_timeframe(
      zones,
      tf=tf,
      major_score=major_score,
      atr=_timeframe_atr(analysis),
      pip_size=pip_size,
      max_width_atr=max_width_atr,
      max_width_pips=max_width_pips,
    ))
  merged = _merge_same_side(pooled)
  candidates = _confluence_candidates(
    per_tf,
    timeframes=timeframes,
    major_score=major_score,
    proximal_band_atr=proximal_band_atr,
  )
  boosted = _apply_confluence_boost(merged, candidates)
  return tuple(_resolve_cross_side_overlaps(boosted))


def to_opposing_entries(
  barriers: Sequence[StructuralBarrier],
) -> tuple[ZoneOpposingEntry, ...]:
  """Thin adapter into ``structural_target_room.py``'s existing, unchanged
  hard-gate machinery (``evaluate_structural_target_room``/
  ``_nearest_opposing``) — the book replaces zone-*gathering*, never the
  gate itself, which stays exactly as PR #517 left it.
  """
  return tuple(
    ZoneOpposingEntry(
      side=barrier.side,
      lo=barrier.low,
      hi=barrier.high,
      tier=barrier.tier,
      score=barrier.score,
      touches=barrier.touches,
      mitigated=barrier.mitigated,
    )
    for barrier in barriers
  )
