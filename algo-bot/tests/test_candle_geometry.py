"""Candle Confirmation V2 — shared geometry (§4/§49)."""

from __future__ import annotations

import pytest

from app.analysis.candle_geometry import atr_quality, candle_geometry, clamp01, fraction_quality


def test_hammer_shaped_bar_geometry():
  g = candle_geometry(open_=100.0, high=100.2, low=98.5, close=100.1, atr=1.0)
  assert g.range_price == pytest.approx(1.7)
  assert g.body_price == pytest.approx(0.1)
  assert g.lower_wick_price == pytest.approx(1.5)
  assert g.upper_wick_price == pytest.approx(0.1)
  assert g.body_fraction == pytest.approx(0.1 / 1.7)
  assert g.lower_wick_fraction == pytest.approx(1.5 / 1.7)
  assert g.close_location == pytest.approx((100.1 - 98.5) / 1.7)
  assert 0.0 <= g.body_fraction <= 1.0
  assert 0.0 <= g.close_location <= 1.0
  assert g.is_bullish
  assert g.body_atr == pytest.approx(0.1)
  assert g.range_atr == pytest.approx(1.7)


def test_zero_range_candle_is_handled_safely():
  g = candle_geometry(open_=100.0, high=100.0, low=100.0, close=100.0, atr=1.0)
  assert g.is_zero_range
  assert g.range_price == 0.0
  assert g.body_fraction == 0.0
  assert g.upper_wick_fraction == 0.0
  assert g.lower_wick_fraction == 0.0
  assert g.close_location == 0.5  # neutral, not a crash


def test_atr_normalization_is_none_without_atr():
  g = candle_geometry(open_=100.0, high=101.0, low=99.0, close=100.5)
  assert g.body_atr is None
  assert g.range_atr is None


def test_atr_normalization_scales_with_atr():
  g_tight = candle_geometry(open_=100.0, high=101.0, low=99.0, close=100.5, atr=10.0)
  g_wide = candle_geometry(open_=100.0, high=101.0, low=99.0, close=100.5, atr=0.5)
  assert g_tight.range_atr < g_wide.range_atr


def test_clamp01_bounds():
  assert clamp01(-5.0) == 0.0
  assert clamp01(5.0) == 1.0
  assert clamp01(0.5) == 0.5


def test_fraction_quality_scales_linearly_between_minimum_and_strong():
  assert fraction_quality(0.1, minimum=0.3, strong=0.6) == 0.0
  assert fraction_quality(0.6, minimum=0.3, strong=0.6) == 1.0
  assert fraction_quality(0.45, minimum=0.3, strong=0.6) == pytest.approx(0.5)


def test_atr_quality_zero_for_non_positive_values():
  assert atr_quality(None, minimum=0.05) == 0.0
  assert atr_quality(0.0, minimum=0.05) == 0.0
  assert atr_quality(-0.1, minimum=0.05) == 0.0


def test_atr_quality_saturates_at_four_times_minimum():
  assert atr_quality(0.2, minimum=0.05) == 1.0
  assert atr_quality(0.1, minimum=0.05) == pytest.approx(0.5)
