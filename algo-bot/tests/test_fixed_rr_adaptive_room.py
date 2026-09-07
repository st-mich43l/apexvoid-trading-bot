"""fixed_rr adaptive-room fallback: only a "major" wall may cap it.

2026-09 (owner-reported): a manual key-level SELL from 4421-24 was legitimate
per the owner's own read (real target well past a minor 4410 demand pocket),
but the equivalent auto candidate got rejected with fixed_rr_room_insufficient
because a "zone" tier opposing entry with positive-but-small raw room was
still capping the room fallback - structural_target_room's own
weak_opposing_level_ignored flag only covers the contained/zero-room hard-
block branches, never the "positive room, just not enough for the full
ladder" case _fixed_rr_adaptive_room_pips guards here.
"""

from __future__ import annotations

import pytest

from app.autotrade.worker import _fixed_rr_adaptive_room_pips


pytestmark = pytest.mark.no_database


def test_major_tier_room_still_caps_the_fallback():
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=True,
    target_room_measured={"opposing_tier": "major", "usable_room_pips": 18.7},
  )
  assert room == pytest.approx(18.7)


@pytest.mark.parametrize("tier", ["zone", "level", "", "unknown"])
def test_non_major_tier_room_never_caps_the_fallback(tier):
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=True,
    target_room_measured={"opposing_tier": tier, "usable_room_pips": 18.7},
  )
  assert room is None


def test_weak_opposing_level_ignored_flag_also_clears_room_for_major():
  # Belt-and-suspenders: even if a caller somehow set both the flag and
  # tier=major, the flag wins - it means structural_target_room itself
  # already decided this barrier isn't real.
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=True,
    target_room_measured={
      "opposing_tier": "major",
      "usable_room_pips": 18.7,
      "weak_opposing_level_ignored": True,
    },
  )
  assert room is None


def test_no_opposing_entry_means_no_room_cap():
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=False,
    target_room_measured={"opposing_tier": "major", "usable_room_pips": 18.7},
  )
  assert room is None


def test_non_fixed_rr_instrument_never_computes_room():
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=False,
    has_opposing_entry=True,
    target_room_measured={"opposing_tier": "major", "usable_room_pips": 18.7},
  )
  assert room is None


def test_missing_usable_room_pips_returns_none():
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=True,
    target_room_measured={"opposing_tier": "major"},
  )
  assert room is None
