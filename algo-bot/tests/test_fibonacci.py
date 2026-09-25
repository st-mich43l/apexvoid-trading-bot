"""Fibonacci ladder retracement/extension formula tests."""

from __future__ import annotations

import pytest

from app.analysis.fibonacci import (
  EXTENSION_RATIOS,
  RETRACEMENT_RATIOS,
  fib_ladder,
  fib_zone_label,
  nearest_fib,
)


def test_retracements_measure_down_from_high():
  levels = fib_ladder(100.0, 110.0, include_extensions=False)
  by_ratio = {level.ratio: level.price for level in levels}
  assert set(by_ratio) == set(RETRACEMENT_RATIOS)
  assert by_ratio[0.5] == pytest.approx(105.0)
  assert by_ratio[0.618] == pytest.approx(103.82)
  assert all(level.kind == "retracement" for level in levels)


def test_extensions_are_anchored_at_low_not_high():
  """low=100, high=110 -> 1.272 ext = 112.72, 1.618 ext = 116.18 (owner spec)."""
  levels = fib_ladder(100.0, 110.0, include_extensions=True)
  extensions = {
    level.ratio: level.price for level in levels if level.kind == "extension"
  }
  assert set(extensions) == set(EXTENSION_RATIOS)
  assert extensions[1.0] == pytest.approx(110.0)
  assert extensions[1.272] == pytest.approx(112.72)
  assert extensions[1.618] == pytest.approx(116.18)


def test_zero_or_negative_span_returns_no_levels():
  assert fib_ladder(100.0, 100.0) == []
  assert fib_ladder(110.0, 100.0) == []


def test_nearest_fib_defaults_to_retracement_only():
  levels = fib_ladder(100.0, 110.0)
  nearest = nearest_fib(levels, price=105.1, atr=1.0, epsilon_atr=0.5)
  assert nearest is not None
  assert nearest.kind == "retracement"
  assert nearest.ratio == 0.5


def test_nearest_fib_can_include_extensions():
  levels = fib_ladder(100.0, 110.0)
  nearest = nearest_fib(
    levels, price=112.7, atr=1.0, epsilon_atr=0.5,
    kinds=("retracement", "extension"),
  )
  assert nearest is not None
  assert nearest.kind == "extension"
  assert nearest.ratio == 1.272


def test_fib_zone_label_bands():
  assert fib_zone_label(0.5) == "eq"
  assert fib_zone_label(0.2) == "deep_discount"
  assert fib_zone_label(0.42) == "discount"
  assert fib_zone_label(0.58) == "premium"
  assert fib_zone_label(0.8) == "deep_premium"
