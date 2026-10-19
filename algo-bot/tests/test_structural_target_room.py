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


# --- why a barrier blocked: the zones behind it --------------------------------

from app.autotrade.structural_target_room import (  # noqa: E402
  ZoneOpposingEntry as _Entry,
  barrier_log_summary,
  describe_blocking_barrier,
  evaluate_structural_target_room as _evaluate,
)


def _wall():
  return _Entry(side="sell", lo=100.0, hi=112.0, tier="zone", score=0.9, touches=1, members=(
    {"id": "z-low", "timeframe": "M15", "low": 100.0, "high": 104.0, "state": "partially_mitigated", "touches": 3, "strength": 0.5},
    {"id": "z-mid", "timeframe": "M5", "low": 103.0, "high": 108.0, "state": "touched", "touches": 1, "strength": 0.7},
    {"id": "z-top", "timeframe": "H1", "low": 108.0, "high": 112.0, "state": "fresh", "touches": 0, "strength": 0.9},
  ))


def test_an_entry_inside_a_merged_wall_names_the_member_that_holds_it():
  described = describe_blocking_barrier(
    _wall(), direction="BUY", planned_entry=109.0, candidate_entry_low=108.5, candidate_entry_high=110.0,
    reason_code="opposing_entry_contained", measured={"room_pips": -3.0},
  )
  assert described["member_count"] == 3 and described["member_ids"] == ["z-low", "z-mid", "z-top"]
  assert described["barrier_type"] == "supply" and described["barrier_low"] == 100.0 and described["barrier_high"] == 112.0
  assert described["decisive_member"]["id"] == "z-top" and described["decisive_member"]["state"] == "fresh"
  assert described["room_pips"] == -3.0
  assert "decisive=z-top" in barrier_log_summary(described)


def test_a_room_block_names_the_member_that_owns_the_nearest_edge():
  buy = describe_blocking_barrier(_wall(), direction="BUY", planned_entry=95.0, candidate_entry_low=94.0, candidate_entry_high=96.0, reason_code="opposing_barrier_room_below_cost")
  assert buy["decisive_member"]["id"] == "z-low"
  demand = _Entry(side="buy", lo=80.0, hi=90.0, members=(
    {"id": "d1", "low": 80.0, "high": 85.0}, {"id": "d2", "low": 84.0, "high": 90.0},
  ))
  sell = describe_blocking_barrier(demand, direction="SELL", planned_entry=92.0, candidate_entry_low=91.0, candidate_entry_high=93.0, reason_code="opposing_barrier_no_target")
  assert sell["decisive_member"]["id"] == "d2"


def test_a_barrier_published_without_members_gets_no_invented_decisive_member():
  described = describe_blocking_barrier(
    _Entry(side="sell", lo=100.0, hi=112.0), direction="BUY", planned_entry=109.0, candidate_entry_low=108.0, candidate_entry_high=110.0,
    reason_code="opposing_entry_overlap",
  )
  assert described["members_published"] is False and described["member_count"] == 0 and described["decisive_member"] is None
  assert "members=unpublished" in barrier_log_summary(described)


def test_provenance_does_not_change_the_target_room_decision():
  common = dict(
    direction="BUY", planned_entry_price=109.0, candidate_entry_low=108.5, candidate_entry_high=110.0,
    configured_target_pips=[20, 40], atr=2.0, pip_size=0.1, barrier_buffer_atr=0.5, min_capped_target_pips=15.0,
    execution_cost_pips=1.0,
  )
  with_members = _evaluate(actionable_entries=[_wall()], **common)
  without = _evaluate(actionable_entries=[_Entry(side="sell", lo=100.0, hi=112.0, tier="zone", score=0.9, touches=1)], **common)
  assert (with_members.allowed, with_members.reason_code, with_members.hard_block, with_members.measured) == (
    without.allowed, without.reason_code, without.hard_block, without.measured)


# --- a barrier behind the entry is not in the way ---------------------------------

_COMMON = dict(atr=2.0, pip_size=0.1, barrier_buffer_atr=0.5, min_capped_target_pips=15.0, execution_cost_pips=1.0, allow_same_wall_overlap=False)


def _buy(entries, *, executable, planned=110.0, targets=(30, 60)):
  return _evaluate(
    direction="BUY", planned_entry_price=planned, candidate_entry_low=100.0, candidate_entry_high=110.0,
    configured_target_pips=list(targets), actionable_entries=entries, executable_entry_price=executable, **_COMMON,
  )


def test_a_supply_wall_behind_both_the_structural_reference_and_the_price_no_longer_reads_as_no_target_room():
  """The candidate demand band 100-110 holds an old supply zone 103-108. Price is at 112, above it, and the reference is the band's
  proximal edge 110: the wall is behind the entry. It used to win the nearest-barrier ranking (a band overlap is distance zero), have
  negative raw room and hard-block as ``opposing_barrier_no_target`` for a wall that is not in the way."""
  behind = _Entry(side="sell", lo=103.0, hi=108.0, tier="zone", score=0.8, touches=2)
  decision = _buy([behind], executable=112.0)
  assert decision.allowed and decision.reason_code == "no_opposing_barrier"
  assert decision.measured["shared_boundary_state"]["behind_entry_excluded"] == 1


def test_the_real_barrier_ahead_is_found_once_the_wall_behind_is_set_aside():
  behind = _Entry(side="sell", lo=103.0, hi=108.0)
  ahead_close = _Entry(side="sell", lo=110.6, hi=115.0)        # 6 price units = 6 pips away in 0.1 pips: thin
  ahead_far = _Entry(side="sell", lo=140.0, hi=145.0)
  blocked = _buy([behind, ahead_close], executable=112.0)
  assert not blocked.allowed and blocked.reason_code == "opposing_barrier_room_below_cost" and blocked.opposing_entry is ahead_close
  admitted = _buy([behind, ahead_far], executable=112.0)
  assert admitted.allowed and admitted.opposing_entry is ahead_far


def test_a_wall_the_price_still_has_to_pass_or_is_inside_keeps_blocking():
  wall = _Entry(side="sell", lo=103.0, hi=108.0)
  # price at 101: the wall is ahead of the executable price although behind the proximal reference
  ahead_of_price = _buy([wall], executable=101.0)
  assert not ahead_of_price.allowed and ahead_of_price.reason_code == "opposing_barrier_no_target"
  # price inside the wall
  inside = _buy([wall], executable=105.0)
  assert not inside.allowed and inside.reason_code == "opposing_barrier_no_target"
  # without an executable price nothing can be proven behind the entry
  unknown = _evaluate(direction="BUY", planned_entry_price=110.0, candidate_entry_low=100.0, candidate_entry_high=110.0, configured_target_pips=[30, 60],
                      actionable_entries=[wall], executable_entry_price=None, **_COMMON)
  assert not unknown.allowed and unknown.reason_code == "opposing_barrier_no_target"


def test_the_mirror_for_a_sell():
  demand_behind = _Entry(side="buy", lo=102.0, hi=107.0)
  common = dict(direction="SELL", planned_entry_price=100.0, candidate_entry_low=100.0, candidate_entry_high=110.0, configured_target_pips=[30, 60],
                actionable_entries=[demand_behind], **_COMMON)
  assert _evaluate(executable_entry_price=98.0, **common).allowed                     # price below the demand zone: it is behind
  held = _evaluate(executable_entry_price=109.0, **common)                              # price above it: still ahead of the fill
  assert not held.allowed and held.reason_code == "opposing_barrier_no_target"


def test_a_barrier_that_holds_the_planned_entry_is_never_set_aside():
  holding = _Entry(side="sell", lo=105.0, hi=115.0)
  decision = _buy([holding], executable=112.0)
  assert not decision.allowed and decision.reason_code in {"opposing_entry_contained", "opposing_entry_overlap"}
