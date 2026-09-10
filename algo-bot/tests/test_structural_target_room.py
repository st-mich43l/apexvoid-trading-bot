"""Opposing Structure V2 (2026-09 Key Level repair) — evaluate_opposing_structure_v2."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.autotrade.structural_target_room import (
  ZoneOpposingEntry,
  evaluate_opposing_structure_v2,
  evaluate_structural_target_room,
)


pytestmark = pytest.mark.no_database


def test_planned_entry_inside_opposing_zone_blocks_inside():
  entries = [ZoneOpposingEntry("sell", 4100.0, 4105.0, tier="zone", score=8.0)]
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4102.0,
    entries=entries,
    atr=2.0,
    pip_size=0.1,
  )
  assert ev is not None
  assert ev.action == "BLOCK_INSIDE"
  assert ev.reason_code == "opposing_entry_contained"


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


def test_clear_room_beyond_caution_band_is_clear():
  entries = [ZoneOpposingEntry("sell", 4200.0, 4205.0, tier="zone", score=8.0)]
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=entries,
    atr=2.0,
    pip_size=0.1,
    protective_stop_distance=5.0,   # risk = 5.0 price units = 1R
    first_target_r=0.5,
    caution_room_r=2.0,
  )
  assert ev is not None
  # raw_room = 4200 - 4100 = 100 price -> room_r = 100 / 5 = 20R, well clear.
  assert ev.room_r == pytest.approx(20.0)
  assert ev.before_tp1 is False
  assert ev.action == "CLEAR"
  assert ev.reason_code == "opposing_room_clear"
  assert ev.room_pressure_score == pytest.approx(0.0)


def test_room_before_tp1_is_computed_but_not_the_only_block_reason():
  entries = [ZoneOpposingEntry("sell", 4102.0, 4105.0, tier="zone", score=8.0)]
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=entries,
    atr=2.0,
    pip_size=0.1,
    protective_stop_distance=8.0,   # 1R = 8.0 price units
    first_target_r=0.5,             # TP1 = 0.5R = 4.0 price units of room
  )
  assert ev is not None
  # raw_room = 4102 - 4100 = 2.0 price -> room_r = 2.0/8.0 = 0.25R < 0.5R TP1.
  assert ev.room_r == pytest.approx(0.25)
  assert ev.before_tp1 is True
  assert ev.action == "BLOCK_BEFORE_TP1"
  assert ev.reason_code == "opposing_room_before_tp1"


def test_mitigated_zone_is_ignored_mitigated_when_included():
  entries = [
    ZoneOpposingEntry(
      "sell", 4102.0, 4105.0, tier="zone", score=8.0, mitigated=True,
    ),
  ]
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=entries,
    atr=2.0,
    pip_size=0.1,
  )
  assert ev is not None
  assert ev.mitigated is True
  assert ev.strength_score == 0.0
  assert ev.action == "IGNORED_MITIGATED"
  assert ev.reason_code == "opposing_zone_mitigated"


def test_displaced_zone_is_ignored_displaced():
  # A caller/test-only entry that deliberately keeps a displaced flag for
  # telemetry (production callers filter displacement upstream so this
  # never reaches _nearest_opposing there — see filter_displaced_opposing_
  # entries). ZoneOpposingEntry itself has no "displaced" field (by
  # design, see module docstring) since a real one is filtered out before
  # construction — a plain duck-typed stand-in exercises the evaluator's
  # getattr-based read of it instead.
  displaced_entry = SimpleNamespace(
    side="sell", lo=4102.0, hi=4105.0, tier="zone", score=8.0,
    touches=0, mitigated=False, displaced=True,
  )
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=[displaced_entry],
    atr=2.0,
    pip_size=0.1,
  )
  assert ev is not None
  assert ev.displaced is True
  assert ev.strength_score == 0.0
  assert ev.action == "IGNORED_DISPLACED"
  assert ev.reason_code == "opposing_zone_displaced"


def test_missing_stop_distance_leaves_room_r_none_but_keeps_raw_room():
  entries = [ZoneOpposingEntry("sell", 4200.0, 4205.0, tier="zone", score=8.0)]
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=entries,
    atr=2.0,
    pip_size=0.1,
    # No protective_stop_distance supplied — never fabricate a stop.
  )
  assert ev is not None
  assert ev.room_r is None
  assert ev.before_tp1 is None
  assert ev.room_pressure_score is None
  assert ev.opposing_risk_score is None
  assert ev.raw_room_price == pytest.approx(100.0)
  assert ev.raw_room_pips == pytest.approx(1000.0)


def test_strength_score_clamps_at_one_for_a_very_high_zone_score():
  entries = [ZoneOpposingEntry("sell", 4200.0, 4205.0, tier="major", score=99.0)]
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=entries,
    atr=2.0,
    pip_size=0.1,
    strength_score_ceiling=15.0,
  )
  assert ev is not None
  assert ev.strength_score == 1.0


def test_direction_symmetry_buy_into_supply_mirrors_sell_into_demand():
  buy_entries = [ZoneOpposingEntry("sell", 4105.0, 4110.0, tier="zone", score=6.0)]
  buy_ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=buy_entries,
    atr=2.0,
    pip_size=0.1,
  )
  sell_entries = [ZoneOpposingEntry("buy", 4090.0, 4095.0, tier="zone", score=6.0)]
  sell_ev = evaluate_opposing_structure_v2(
    direction="SELL",
    planned_entry_price=4100.0,
    entries=sell_entries,
    atr=2.0,
    pip_size=0.1,
  )
  assert buy_ev is not None and sell_ev is not None
  assert buy_ev.raw_room_price == pytest.approx(sell_ev.raw_room_price)
  assert buy_ev.strength_score == pytest.approx(sell_ev.strength_score)
  assert buy_ev.action == sell_ev.action == "CLEAR"


def test_returns_none_when_no_opposing_entry_on_the_correct_side():
  # Only a same-side (buy) entry exists — nothing opposes a BUY.
  entries = [ZoneOpposingEntry("buy", 4090.0, 4095.0, tier="zone", score=6.0)]
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=entries,
    atr=2.0,
    pip_size=0.1,
  )
  assert ev is None


def test_evidence_to_dict_round_trips_every_field():
  entries = [ZoneOpposingEntry("sell", 4102.0, 4105.0, tier="zone", score=8.0, touches=2)]
  ev = evaluate_opposing_structure_v2(
    direction="BUY",
    planned_entry_price=4100.0,
    entries=entries,
    atr=2.0,
    pip_size=0.1,
    protective_stop_distance=8.0,
    first_target_r=0.5,
  )
  assert ev is not None
  payload = ev.to_dict()
  for key in (
    "direction", "zone_low", "zone_high", "zone_side", "tier", "zone_score",
    "touches", "mitigated", "displaced", "entry_price", "raw_room_price",
    "raw_room_pips", "room_atr", "room_r", "first_target_r", "before_tp1",
    "strength_score", "room_pressure_score", "opposing_risk_score",
    "action", "reason_code",
  ):
    assert key in payload
  assert payload["touches"] == 2
