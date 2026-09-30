"""Phase 4: reading analysis-engine's own stop envelope instead of
protective_stop.stop_bounds_for_reaction_room's per-strategy-family lookup.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.autotrade.protective_stop import stop_bounds_from_go_envelope

pytestmark = pytest.mark.no_database


def _match(
  *,
  floor_pips: float | None = 40.0,
  cap_pips: float | None = 60.0,
  desired_minimum_pips: float | None = 40.0,
  source: str | None = "reaction_room",
) -> SimpleNamespace:
  return SimpleNamespace(
    go_stop_envelope_floor_pips=floor_pips,
    go_stop_envelope_cap_pips=cap_pips,
    go_stop_envelope_desired_minimum_pips=desired_minimum_pips,
    go_stop_envelope_source=source,
  )


def test_returns_none_when_the_match_has_no_go_envelope():
  assert stop_bounds_from_go_envelope(_match(floor_pips=None)) is None
  assert stop_bounds_from_go_envelope(_match(cap_pips=None)) is None
  assert stop_bounds_from_go_envelope(_match(desired_minimum_pips=None)) is None
  assert stop_bounds_from_go_envelope(_match(source=None)) is None


def test_single_leg_pins_to_desired_minimum():
  minimum, maximum, measured = stop_bounds_from_go_envelope(
    _match(floor_pips=40, cap_pips=60, desired_minimum_pips=55, source="reaction_room"),
  )
  assert (minimum, maximum) == (55, 60)
  assert measured["stop_bounds_source"] == "go_reaction_room"
  assert measured["stop_bounds_for_group_stop"] is False
  assert measured["fixed_rr_targeting"] is False


def test_desired_minimum_never_exceeds_the_cap():
  # computeStopEnvelope itself already clamps desired <= cap, but this
  # function must not silently violate that invariant even if it didn't.
  minimum, maximum, _measured = stop_bounds_from_go_envelope(
    _match(floor_pips=40, cap_pips=60, desired_minimum_pips=60, source="scalp_room"),
  )
  assert (minimum, maximum) == (60, 60)


def test_group_stop_pins_to_the_floor_not_the_desired_minimum():
  # Mirrors stop_bounds_for_reaction_room_keeps_band_for_group_stop's own
  # regression: a wide desired minimum must not collapse [min, max] for a
  # multi-leg group stop.
  minimum, maximum, measured = stop_bounds_from_go_envelope(
    _match(floor_pips=40, cap_pips=60, desired_minimum_pips=60, source="reaction_room"),
    for_group_stop=True,
  )
  assert (minimum, maximum) == (40, 60)
  assert measured["stop_bounds_for_group_stop"] is True


def test_fixed_rr_pins_to_the_floor_like_group_stop_does():
  minimum, maximum, measured = stop_bounds_from_go_envelope(
    _match(floor_pips=40, cap_pips=60, desired_minimum_pips=60, source="reaction_room"),
    fixed_rr=True,
  )
  assert (minimum, maximum) == (40, 60)
  assert measured["fixed_rr_targeting"] is True


def test_scalp_stop_envelope_source_is_prefixed_go():
  _minimum, _maximum, measured = stop_bounds_from_go_envelope(
    _match(floor_pips=12, cap_pips=45, desired_minimum_pips=12, source="scalp_stop_envelope"),
  )
  assert measured["stop_bounds_source"] == "go_scalp_stop_envelope"


def test_strategy_default_source_never_pins_below_the_floor():
  # "strategy_default" candidates have desired_minimum_pips == floor_pips
  # by construction (computeStopEnvelope never pins them) - single-leg
  # must still just return the floor as the minimum.
  minimum, maximum, measured = stop_bounds_from_go_envelope(
    _match(floor_pips=40, cap_pips=60, desired_minimum_pips=40, source="strategy_default"),
  )
  assert (minimum, maximum) == (40, 60)
  assert measured["stop_bounds_source"] == "go_strategy_default"
