"""Opposing Structure V2 (2026-09 Key Level repair) — evaluate_opposing_structure_v2."""

from __future__ import annotations


import pytest

from app.autotrade.structural_target_room import (
  ZoneOpposingEntry,
  evaluate_structural_target_room,
)


pytestmark = pytest.mark.no_database


def test_zero_or_negative_raw_room_blocks_zero_room():
  # For a bare point candidate, "raw_room <= 0" and "contained" collapse to
  # the same condition (both reduce to zone_low <= planned <= zone_high for
  # a BUY) — BLOCK_INSIDE always wins there, correctly (see
  # test_planned_entry_inside_opposing_zone_blocks_inside above). The
  # "negative room but NOT contained" case only exists when the caller has
  # a real candidate *band* (not just a point) that overlaps the opposing
  # zone while the specific planned-entry point sits past its far edge —
  # exactly evaluate_structural_target_room's own richer geometry, which
  # is what production actually calls. Exercise that path directly, mirror
  # of test_ordinary_zone_zero_raw_room_hard_blocks's fixture in
  # test_scanner_actionability.py.
  entries = [ZoneOpposingEntry("sell", 4046.0, 4055.0, tier="zone", score=8.0)]
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4056.0,
    candidate_entry_low=4054.0,
    candidate_entry_high=4057.0,
    configured_target_pips=(30, 60, 90),
    actionable_entries=entries,
    atr=2.0,
    pip_size=0.1,
    barrier_buffer_atr=0.0,
    protective_stop_distance=8.0,
    # Isolate the containment/zero-room branch from the separate (already
    # covered elsewhere) same-wall-overlap pre-filter, which would
    # otherwise drop this deliberately heavily-overlapping fixture first.
    allow_same_wall_overlap=False,
  )
  assert decision.hard_block is True
  assert decision.reason_code == "opposing_barrier_no_target"
  assert decision.measured["raw_room_price"] < 0
  ev = decision.measured["opposing_evidence"]
  assert ev["action"] == "BLOCK_ZERO_ROOM"
  assert ev["reason_code"] == "opposing_zero_or_negative_room"


# ----------------------------------------- technique-room widening (PR #494) ---


# ------------------------------------ Sept-7 motivating case (spec S35/36) ---


def test_sept_7_motivating_case_minor_weak_zone_with_real_room_still_allows():
  """The original owner complaint that led to PR #493: a manual Key Level
  SELL around 4421-4424 with a minor demand pocket near 4410 incorrectly
  got blocked by treating every opposing zone as an absolute wall. The
  repair must NOT re-revert to that blind veto - a weak/minor zone with
  real room past it must still allow the setup."""
  planned_entry = 4422.0
  minor_demand = ZoneOpposingEntry(
    "buy", 4408.0, 4412.0, tier="zone", score=3.0,  # weak: no HTF confluence
  )
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=planned_entry,
    candidate_entry_low=4421.0,
    candidate_entry_high=4424.0,
    configured_target_pips=(30, 60, 90),
    actionable_entries=[minor_demand],
    atr=3.0,
    pip_size=0.1,
    barrier_buffer_atr=0.0,
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_entry_directly_inside_real_opposing_structure_still_rejects():
  """Companion to the Sept-7 case (spec S36): the repair must not swing so
  far the other way that it stops catching a genuinely bad entry. A SELL
  planned directly inside a real, unmitigated demand zone must still hard-
  block, regardless of tier - this is PR #517's own repair, re-asserted
  here as the second half of the Sept-7 regression's acceptance test."""
  decision = evaluate_structural_target_room(
    direction="SELL",
    planned_entry_price=4410.0,
    candidate_entry_low=4409.5,
    candidate_entry_high=4410.5,
    configured_target_pips=(30, 60, 90),
    actionable_entries=[
      ZoneOpposingEntry("buy", 4408.0, 4412.0, tier="zone", score=3.0),
    ],
    atr=3.0,
    pip_size=0.1,
    barrier_buffer_atr=0.0,
    # Isolates the containment check from the separate same-wall-overlap
    # pre-filter (a deliberately heavily-overlapping fixture would
    # otherwise be dropped by that filter first - see
    # test_zero_or_negative_raw_room_blocks_zero_room's own note).
    allow_same_wall_overlap=False,
  )
  assert decision.hard_block is True
  assert decision.reason_code in {"opposing_entry_contained", "opposing_entry_overlap"}
