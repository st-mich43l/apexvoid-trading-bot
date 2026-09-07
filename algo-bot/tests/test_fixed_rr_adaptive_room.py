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

from app.autotrade.worker import _fixed_rr_adaptive_room_pips, _technique_swing_room_pips


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


# --- technique_swing_room: the technique's own structural_swing math ------
#
# 2026-09 (owner-reported): "the room logic must depend on technique zone
# instead of decide opposing low/high" - a credible structural_swing
# distance (the swing point the technique itself measured its reaction
# from) can widen room past what a major wall alone would allow. It can
# only ever widen, never narrow - if there's no major wall, room is already
# unconstrained and a swing number would only make things stricter.


def test_technique_swing_room_requires_all_inputs():
  assert _technique_swing_room_pips(
    planned_entry_price=None, structural_swing=4350.0, pip_size=0.1, atr=5.0,
  ) is None
  assert _technique_swing_room_pips(
    planned_entry_price=4411.43, structural_swing=None, pip_size=0.1, atr=5.0,
  ) is None
  assert _technique_swing_room_pips(
    planned_entry_price=4411.43, structural_swing=4350.0, pip_size=None, atr=5.0,
  ) is None
  assert _technique_swing_room_pips(
    planned_entry_price=4411.43, structural_swing=4350.0, pip_size=0.1, atr=None,
  ) is None


def test_technique_swing_room_below_atr_floor_is_not_credible():
  # Swing only ~2.4 price units away with atr=5.0 -> below the 2x floor.
  room = _technique_swing_room_pips(
    planned_entry_price=4411.43, structural_swing=4409.0, pip_size=0.1, atr=5.0,
  )
  assert room is None


def test_technique_swing_room_clearing_atr_floor_is_credible():
  room = _technique_swing_room_pips(
    planned_entry_price=4411.43, structural_swing=4350.0, pip_size=0.1, atr=5.0,
  )
  assert room == pytest.approx((4411.43 - 4350.0) / 0.1)


def test_technique_swing_room_widens_a_major_wall():
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=True,
    target_room_measured={"opposing_tier": "major", "usable_room_pips": 18.7},
    planned_entry_price=4411.43,
    structural_swing=4350.0,
    pip_size=0.1,
    atr=5.0,
  )
  assert room == pytest.approx((4411.43 - 4350.0) / 0.1)
  assert room > 18.7


def test_noisy_swing_does_not_widen_a_major_wall():
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=True,
    target_room_measured={"opposing_tier": "major", "usable_room_pips": 18.7},
    planned_entry_price=4411.43,
    structural_swing=4409.0,
    pip_size=0.1,
    atr=5.0,
  )
  assert room == pytest.approx(18.7)


def test_credible_swing_never_introduces_a_cap_when_no_major_wall():
  # No major wall at all -> already unconstrained. A swing number here would
  # only make things stricter, never the intent - must stay None.
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=False,
    target_room_measured={},
    planned_entry_price=4411.43,
    structural_swing=4350.0,
    pip_size=0.1,
    atr=5.0,
  )
  assert room is None


def test_technique_swing_room_widens_a_major_wall_on_fx_scale():
  # Same mechanism, FX price/pip/atr scale (EURUSD) - confirms this isn't
  # XAU-specific; nothing here is gated by symbol.
  room = _fixed_rr_adaptive_room_pips(
    fixed_rr_target=True,
    has_opposing_entry=True,
    target_room_measured={"opposing_tier": "major", "usable_room_pips": 8.0},
    planned_entry_price=1.16173,
    structural_swing=1.1550,
    pip_size=0.0001,
    atr=0.0015,
  )
  assert room == pytest.approx((1.16173 - 1.1550) / 0.0001)
  assert room > 8.0
