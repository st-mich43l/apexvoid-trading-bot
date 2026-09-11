"""StructuralBarrierBook (2026-09 Key Level structural repair, Phase 1).

Parity fixtures for the two structural-pool operations the 2026-09-07
Market Map purge (#500/#502) dropped when it replaced
``MarketMap.actionable_entries`` with an unreconciled single-timeframe
zone read: same-side merging and cross-side reconciliation. Each fixture
below is checked against both the still-intact old ``market_map.py``
functions and the new ``StructuralBarrierBook`` on identical inputs —
true historical replay against real production candidates is not possible
(no per-timeframe analysis snapshot was ever persisted for autonomous
trades, and live OHLC retention does not reach back to the purge date;
see the engineering report), so deterministic fixtures are the only form
of parity evidence available.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.analysis.market_map import _merge_display_entries, _resolve_cross_side_overlaps
from app.analysis.types import Zone
from app.autotrade.structural_barriers import (
  StructuralBarrier,
  build_structural_barrier_book,
  to_opposing_entries,
)
from app.autotrade.structural_target_room import ZoneOpposingEntry

pytestmark = pytest.mark.no_database


def _tf(zones: list[Zone], atr: float = 1.0) -> SimpleNamespace:
  return SimpleNamespace(zones=zones, atr=pd.Series([atr]))


def _demand(low: float, high: float, **kwargs) -> Zone:
  return Zone(bottom=low, top=high, side="demand", **kwargs)


def _supply(low: float, high: float, **kwargs) -> Zone:
  return Zone(bottom=low, top=high, side="supply", **kwargs)


def test_single_timeframe_zone_becomes_one_barrier():
  per_tf = {"M15": _tf([_demand(4494.0, 4497.0, score=15.0)])}
  book = build_structural_barrier_book(
    per_tf, major_score=12.0, pip_size=0.1, max_width_atr=5.0, max_width_pips=100.0,
  )
  assert len(book) == 1
  barrier = book[0]
  assert barrier.side == "buy"
  assert (barrier.low, barrier.high) == (4494.0, 4497.0)
  assert barrier.source_timeframes == ("M15",)


def test_zero_width_or_wide_zone_is_excluded_by_width_gate():
  # Width gate mirrors zone_meets_execution_width - a zone wider than the
  # configured ATR/pip ceiling is not a meaningful execution wall.
  per_tf = {"M15": _tf([_demand(4400.0, 4500.0, score=15.0)], atr=1.0)}
  book = build_structural_barrier_book(
    per_tf, major_score=12.0, pip_size=0.1, max_width_atr=5.0, max_width_pips=100.0,
  )
  assert book == ()


def test_mitigated_zone_is_excluded():
  per_tf = {"M15": _tf([_demand(4494.0, 4497.0, score=15.0, mitigated=True)])}
  book = build_structural_barrier_book(
    per_tf, major_score=12.0, pip_size=0.1, max_width_atr=5.0, max_width_pips=100.0,
  )
  assert book == ()


def test_overlapping_same_side_zones_from_different_timeframes_merge():
  # Genuine multi-timeframe merging: an M15 zone and an overlapping H1 zone
  # on the SAME side combine into one barrier carrying both timeframes -
  # the exact "weak M15 wall but a real merged H1+M15 barrier" scenario
  # the old build_map() pool would have seen as one wall via pooling +
  # _merge_display_entries, and the current unreconciled M15-only read
  # cannot see at all.
  per_tf = {
    "M15": _tf([_demand(4494.0, 4497.0, score=8.0, touches=1)]),
    "H1": _tf([_demand(4495.5, 4499.0, score=18.0, score_reasons=["HTF Zone"])]),
  }
  book = build_structural_barrier_book(
    per_tf,
    major_score=12.0,
    pip_size=0.1,
    max_width_atr=5.0,
    max_width_pips=100.0,
    timeframes=("M15", "H1"),
  )
  assert len(book) == 1
  merged = book[0]
  assert (merged.low, merged.high) == (4494.0, 4499.0)
  # Tier and score promote to the stronger contributor (H1's major tier).
  assert merged.tier == "major"
  assert merged.score == 18.0
  assert set(merged.source_timeframes) == {"M15", "H1"}


def test_non_overlapping_same_side_zones_stay_separate():
  per_tf = {"M15": _tf([
    _demand(4494.0, 4497.0, score=10.0),
    _demand(4470.0, 4472.0, score=6.0),
  ])}
  book = build_structural_barrier_book(
    per_tf, major_score=12.0, pip_size=0.1, max_width_atr=5.0, max_width_pips=100.0,
  )
  assert len(book) == 2


def test_substantially_overlapping_cross_side_zones_keep_only_the_stronger():
  # The reconciliation zone_opposing_entries() never gained after the
  # purge: a demand zone and a supply zone a few points apart are
  # contradictory noise, not two independently live barriers. Padded with
  # unrelated, non-conflicting barriers so the one real drop stays well
  # under the 34% fail-open circuit breaker (a 2-entry pool would make a
  # single drop look like a runaway cascade and correctly no-op instead).
  per_tf = {"M15": _tf([
    _demand(4494.0, 4497.0, score=8.0),
    _supply(4495.0, 4498.0, score=20.0, score_reasons=["HTF Zone"]),
    _demand(4470.0, 4472.0, score=6.0),
    _demand(4460.0, 4462.0, score=6.0),
    _supply(4520.0, 4522.0, score=6.0),
    _supply(4530.0, 4532.0, score=6.0),
  ])}
  book = build_structural_barrier_book(
    per_tf, major_score=12.0, pip_size=0.1, max_width_atr=5.0, max_width_pips=100.0,
  )
  assert len(book) == 5
  contested = [b for b in book if b.low in (4494.0, 4495.0)]
  assert len(contested) == 1
  assert contested[0].side == "sell"
  assert contested[0].tier == "major"


def test_barely_overlapping_cross_side_zones_both_survive():
  # Below the 0.5 overlap-ratio threshold - not a real contradiction. Both
  # zones stay within the width gate (<= 5.0 price units at atr=1.0).
  per_tf = {"M15": _tf([
    _demand(4493.0, 4497.0, score=8.0),
    _supply(4496.5, 4500.0, score=8.0),
  ])}
  book = build_structural_barrier_book(
    per_tf, major_score=12.0, pip_size=0.1, max_width_atr=5.0, max_width_pips=100.0,
  )
  assert len(book) == 2


def test_to_opposing_entries_adapts_into_the_existing_shape():
  per_tf = {"M15": _tf([_demand(4494.0, 4497.0, score=15.0, touches=2)])}
  book = build_structural_barrier_book(
    per_tf, major_score=12.0, pip_size=0.1, max_width_atr=5.0, max_width_pips=100.0,
  )
  entries = to_opposing_entries(book)
  assert entries == (
    ZoneOpposingEntry(
      side="buy", lo=4494.0, hi=4497.0, tier="zone", score=15.0, touches=2,
      mitigated=False,
    ),
  )


# --------------------------------------------------------------- parity ---


def _as_map_entries(barriers, tier_rank={"level": 1, "zone": 2, "major": 3}):
  """Minimal shim reusing market_map.py's own MapEntry-shaped functions
  against StructuralBarrier's fields, for direct old-vs-new comparison."""
  from app.analysis.market_map import MapEntry

  return [
    MapEntry(
      side=barrier.side,
      lo=barrier.low,
      hi=barrier.high,
      label_lo=barrier.low,
      label_hi=barrier.high,
      tier=barrier.tier,
      tags=[],
      score=barrier.score,
    )
    for barrier in barriers
  ]


def test_cross_side_reconciliation_matches_market_map_on_identical_geometry():
  """Direct parity check against the still-intact old function: same
  input geometry must produce the same keep/drop outcome. Padded with
  unrelated barriers so the one real drop stays under the 34% fail-open
  circuit breaker both implementations share."""
  contested = [
    StructuralBarrier("buy", 4494.0, 4497.0, "zone", 8.0, 0, False, ("M15",)),
    StructuralBarrier("sell", 4495.0, 4498.0, "major", 20.0, 0, False, ("M15",)),
  ]
  padding = [
    StructuralBarrier("buy", 4470.0, 4472.0, "zone", 6.0, 0, False, ("M15",)),
    StructuralBarrier("buy", 4460.0, 4462.0, "zone", 6.0, 0, False, ("M15",)),
    StructuralBarrier("sell", 4520.0, 4522.0, "zone", 6.0, 0, False, ("M15",)),
    StructuralBarrier("sell", 4530.0, 4532.0, "zone", 6.0, 0, False, ("M15",)),
  ]

  from app.autotrade.structural_barriers import _resolve_cross_side_overlaps as new_resolve

  new_resolved = new_resolve([*contested, *padding])
  new_sides = sorted(barrier.side for barrier in new_resolved if barrier.low in (4494.0, 4495.0))

  old_resolved = _resolve_cross_side_overlaps(_as_map_entries([*contested, *padding]))
  old_sides = sorted(
    entry.side for entry in old_resolved if entry.lo in (4494.0, 4495.0)
  )

  assert new_sides == old_sides == ["sell"]
