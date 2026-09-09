"""Candle Confirmation V2 — evidence family A: REJECTION (§5/§25-§27/§49)."""

from __future__ import annotations

import pytest

from app.analysis.candle_geometry import candle_geometry
from app.analysis.candle_rejection import (
  evaluate_rejection,
  reclaim_depth_atr,
  sweep_penetration_atr,
)


def _hammer_geo():
  # open=100.0, high=100.2, low=98.0, close=100.1 — large lower wick, tiny
  # body, close near the high: textbook hammer/pin_bar/wick_rejection triple.
  return candle_geometry(open_=100.0, high=100.2, low=98.0, close=100.1, atr=1.0)


def _shooting_star_geo():
  return candle_geometry(open_=100.0, high=102.0, low=99.8, close=99.9, atr=1.0)


def test_hammer_bar_collapses_to_one_score_with_all_labels():
  geo = _hammer_geo()
  ev = evaluate_rejection(
    geo, direction="BUY", atr=1.0, level=99.0, zone_low=98.5, zone_high=100.5,
  )
  assert ev is not None
  assert set(ev.patterns) == {"wick_rejection", "hammer", "pin_bar", "sweep_reclaim"}
  assert ev.score == pytest.approx(0.9588744588744574, rel=1e-6)
  assert 0.0 <= ev.score <= 1.0
  assert ev.sweep is True
  assert ev.reclaim is True


def test_shooting_star_bar_is_direction_mirrored():
  geo = _shooting_star_geo()
  ev = evaluate_rejection(
    geo, direction="SELL", atr=1.0, level=101.0, zone_low=99.5, zone_high=102.5,
  )
  assert ev is not None
  assert set(ev.patterns) == {"wick_rejection", "shooting_star", "pin_bar", "sweep_reclaim"}
  assert 0.0 <= ev.score <= 1.0


def test_same_bar_does_not_qualify_as_hammer_in_the_wrong_direction():
  # The hammer bar is a BUY setup; evaluated as SELL it should not produce
  # a shooting_star label (the wick sits on the wrong side for SELL).
  geo = _hammer_geo()
  ev = evaluate_rejection(
    geo, direction="SELL", atr=1.0, level=99.0, zone_low=98.5, zone_high=100.5,
  )
  assert ev is None or "shooting_star" not in ev.patterns


def test_wick_rejection_only_when_body_too_large_for_hammer_or_pin_bar():
  # body_fraction ~0.4545 exceeds both hammer (0.3) and pin_bar (0.2)
  # body ceilings, but the directional wick still clears the minimum.
  geo = candle_geometry(open_=100.0, high=100.6, low=99.5, close=100.5, atr=1.0)
  ev = evaluate_rejection(
    geo, direction="BUY", atr=1.0, level=101.0, zone_low=99.0, zone_high=101.0,
  )
  assert ev is not None
  assert ev.patterns == ("wick_rejection",)
  assert ev.sweep is True
  assert ev.reclaim is False


def test_returns_none_outside_the_relevant_zone():
  geo = _hammer_geo()
  ev = evaluate_rejection(
    geo, direction="BUY", atr=1.0, level=99.0, zone_low=200.0, zone_high=210.0,
  )
  assert ev is None


def test_returns_none_for_invalid_direction():
  geo = _hammer_geo()
  assert evaluate_rejection(geo, direction="LONG", atr=1.0, level=99.0) is None


def test_returns_none_when_no_pattern_qualifies():
  # A dull, small-range bar with no meaningful directional wick, and a
  # level far enough away that sweep and reclaim never both fire.
  geo = candle_geometry(open_=100.0, high=100.05, low=99.98, close=100.02, atr=1.0)
  ev = evaluate_rejection(
    geo, direction="BUY", atr=1.0, level=200.0, zone_low=99.8, zone_high=100.3,
  )
  assert ev is None


def test_sweep_penetration_atr_requires_level_actually_crossed():
  assert sweep_penetration_atr(
    direction="BUY", bar_low=98.0, bar_high=100.2, level=99.0, atr=1.0,
  ) == pytest.approx(1.0)
  assert sweep_penetration_atr(
    direction="BUY", bar_low=99.5, bar_high=100.2, level=99.0, atr=1.0,
  ) is None


def test_reclaim_depth_atr_requires_close_beyond_level():
  assert reclaim_depth_atr(
    direction="BUY", close=100.1, level=99.0, atr=1.0,
  ) == pytest.approx(1.1)
  assert reclaim_depth_atr(
    direction="SELL", close=100.1, level=99.0, atr=1.0,
  ) is None


def test_zone_touch_defaults_true_when_no_zone_given():
  geo = _hammer_geo()
  ev = evaluate_rejection(geo, direction="BUY", atr=1.0, level=99.0)
  assert ev is not None
