"""Opposing Structure V2 (2026-09 Key Level repair) — evaluate_opposing_structure_v2."""

from __future__ import annotations


import pytest

from app.autotrade.structural_target_room import (
  ZoneOpposingEntry,
  assess_executable_room,
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


# ---- Q5.2: no counter-bias label can buy back a structural impossibility ------------------------------------------------

_SOFT_CODES = {"opposing_entry_overlap", "opposing_entry_contained", "opposing_major_no_room"}


@pytest.mark.parametrize("symbol,pip,atr,buffer_atr", [
  ("EURUSD", 0.0001, 0.0010, 0.5),
  ("USDJPY", 0.01, 0.10, 0.5),
  ("XAU", 0.1, 4.0, 0.15),
])
def test_the_codes_a_counter_bias_exception_once_waived_never_have_usable_room(symbol, pip, atr, buffer_atr):
  """The worker used to keep a counter-bias setup past these three codes when ``usable_room_pips or room_pips`` was at least the
  15-pip preference floor. Over every geometry the gate can produce (direction, tier, barrier offset and width, candidate band,
  executable price present or not) each of those codes has non-positive room, so the exception could never fire; it is removed.
  This pins why: a rejection for one of them is geometry (the entry inside, or on, the opposing zone) and no room is ever
  measured in front of it."""
  entry = 1000.0 * pip
  seen = set()
  for direction in ("BUY", "SELL"):
    for tier in ("zone", "major"):
      for offset in range(-60, 61, 3):
        for width in (1, 6, 30, 90):
          for band in (0, 4, 25):
            for executable in (None, entry):
              if direction == "BUY":
                low = entry + offset * pip
                zone = ZoneOpposingEntry("sell", low, low + width * pip, tier=tier)
              else:
                high = entry + offset * pip
                zone = ZoneOpposingEntry("buy", high - width * pip, high, tier=tier)
              decision = evaluate_structural_target_room(
                direction=direction, planned_entry_price=entry,
                candidate_entry_low=entry - band * pip, candidate_entry_high=entry + band * pip,
                configured_target_pips=(10, 20), actionable_entries=[zone], atr=atr, pip_size=pip,
                barrier_buffer_atr=buffer_atr, min_capped_target_pips=15.0, execution_cost_pips=1.0,
                executable_entry_price=executable, allow_same_wall_overlap=False,
              )
              if decision.reason_code in _SOFT_CODES:
                seen.add(decision.reason_code)
                measured = decision.measured
                room = float(measured.get("usable_room_pips") or measured.get("room_pips") or 0.0)
                assert decision.hard_block and not decision.allowed
                assert room <= 0.0, (symbol, direction, tier, offset, width, band, executable, decision.reason_code, room)
  assert {"opposing_entry_overlap"} <= seen


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_an_entry_inside_the_opposing_zone_blocks_with_or_without_a_higher_timeframe_agreeing(direction):
  """Supply opposes a BUY and demand opposes a SELL, and the entry inside it is a hard rejection that does not depend on bias."""
  entry = 1.1000
  zone = ZoneOpposingEntry("sell" if direction == "BUY" else "buy", entry - 0.0010, entry + 0.0010, tier="zone", score=0.8, touches=1)
  decision = evaluate_structural_target_room(
    direction=direction, planned_entry_price=entry, candidate_entry_low=entry - 0.0002, candidate_entry_high=entry + 0.0002,
    configured_target_pips=(20, 40), actionable_entries=[zone], atr=0.0010, pip_size=0.0001, barrier_buffer_atr=0.5,
    execution_cost_pips=1.0, executable_entry_price=entry, allow_same_wall_overlap=False,
  )
  assert decision.hard_block and decision.reason_code in {"opposing_entry_overlap", "opposing_entry_contained"}


def test_a_zone_on_the_wrong_side_never_opposes():
  """A demand zone ahead of a BUY (or supply ahead of a SELL) is not an opposing barrier whatever its width or score."""
  entry = 1.1000
  for direction, same_side in (("BUY", "buy"), ("SELL", "sell")):
    zone = ZoneOpposingEntry(same_side, entry - 0.0010, entry + 0.0010, tier="major", score=1.0)
    decision = evaluate_structural_target_room(
      direction=direction, planned_entry_price=entry, candidate_entry_low=entry, candidate_entry_high=entry,
      configured_target_pips=(20,), actionable_entries=[zone], atr=0.0010, pip_size=0.0001, barrier_buffer_atr=0.5,
      execution_cost_pips=1.0, executable_entry_price=entry,
    )
    assert decision.allowed and decision.reason_code == "no_opposing_barrier"


# ---- Q5.2: the room in R of the stop the executor will use ---------------------------------------------------------------

def _admitted(room_pips, *, pip=0.0001, atr=0.0010, buffer_atr=0.0, targets=(40,)):
  entry = 1.1000
  barrier = ZoneOpposingEntry("sell", entry + room_pips * pip, entry + (room_pips + 10) * pip, tier="zone", score=0.8, touches=1)
  decision = evaluate_structural_target_room(
    direction="BUY", planned_entry_price=entry, candidate_entry_low=entry, candidate_entry_high=entry,
    configured_target_pips=targets, actionable_entries=[barrier], atr=atr, pip_size=pip, barrier_buffer_atr=buffer_atr,
    execution_cost_pips=1.0, executable_entry_price=entry,
  )
  assert decision.allowed and decision.opposing_entry is not None
  return decision


def test_no_barrier_means_the_whole_ladder_is_in_the_clear():
  decision = evaluate_structural_target_room(
    direction="BUY", planned_entry_price=1.1, candidate_entry_low=1.1, candidate_entry_high=1.1, configured_target_pips=(20,),
    actionable_entries=[], atr=0.001, pip_size=0.0001, barrier_buffer_atr=0.5, execution_cost_pips=1.0, executable_entry_price=1.1)
  out = assess_executable_room(decision, executable_stop_pips=15, technical_stop_pips=5, target_pips=["15", "30"])
  assert out["classification"] == "no_opposing_barrier" and out["barrier_present"] is False
  assert out["targets_within_room"] == 2 and out["stop_widening_ratio"] == 3.0 and out["enforcement"] == "advisory"


def test_room_is_measured_in_r_of_the_executable_stop_not_the_technical_one():
  out = assess_executable_room(_admitted(40), executable_stop_pips="15", technical_stop_pips="5", target_pips=["15", "30"])
  assert out["usable_room_pips"] == pytest.approx(40.0)
  assert out["room_r"] == pytest.approx(40 / 15, abs=1e-3) and out["classification"] == "ladder_within_room"
  # against the technical stop the same room would read three times larger and mislead the reader
  assert out["room_r"] < 40 / 5


@pytest.mark.parametrize("room,targets,expected,fits", [
  (40, ["15", "30"], "ladder_within_room", 2),
  (20, ["15", "30"], "ladder_exceeds_room", 1),
  (8, ["15", "30"], "first_target_beyond_room", 0),
])
def test_the_ladder_is_classified_against_the_barrier_without_shrinking_it(room, targets, expected, fits):
  decision = _admitted(room)
  out = assess_executable_room(decision, executable_stop_pips=15, technical_stop_pips=15, target_pips=targets)
  assert out["classification"] == expected and out["targets_within_room"] == fits
  # a classification is never an admission verdict: the decision that was taken is untouched
  assert decision.allowed and tuple(decision.fitted_targets_pips) == (40,)


def test_the_buffer_comes_off_the_room_before_it_is_read_in_r():
  out = assess_executable_room(_admitted(40, buffer_atr=0.5), executable_stop_pips=15, technical_stop_pips=15, target_pips=["15", "30"])
  # 40 pips raw, 0.5 ATR (5 pips) buffer
  assert out["raw_room_pips"] == pytest.approx(40.0) and out["usable_room_pips"] == pytest.approx(35.0)
  assert out["raw_room_r"] > out["room_r"]


def test_a_missing_or_non_finite_stop_or_target_is_unmeasured_not_invented():
  out = assess_executable_room(_admitted(40), executable_stop_pips=None, technical_stop_pips="nan", target_pips=["x", "30"])
  assert out["room_r"] is None and out["stop_widening_ratio"] is None and out["target_pips"] == [30.0]
