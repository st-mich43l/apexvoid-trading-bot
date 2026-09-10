"""Candle Confirmation V2 — evidence family C: SEQUENCE (§10-§15/§49)."""

from __future__ import annotations

import pytest

from app.analysis.candle_geometry import candle_geometry
from app.analysis.candle_sequences import (
  detect_compression_break,
  detect_morning_evening_star,
  detect_sweep_indecision_displacement,
  evaluate_indecision,
  evaluate_sequence,
)


def _morning_star_bars():
  bar1 = candle_geometry(open_=101.0, high=101.05, low=99.95, close=100.0, atr=1.0)
  bar2 = candle_geometry(open_=100.0, high=100.2, low=99.9, close=100.05, atr=1.0)
  bar3 = candle_geometry(open_=100.05, high=100.85, low=100.0, close=100.8, atr=1.0)
  return [bar1, bar2, bar3]


def _evening_star_bars():
  bar1 = candle_geometry(open_=99.0, high=100.05, low=98.95, close=100.0, atr=1.0)
  bar2 = candle_geometry(open_=100.0, high=100.1, low=99.8, close=99.95, atr=1.0)
  bar3 = candle_geometry(open_=99.95, high=100.0, low=99.15, close=99.2, atr=1.0)
  return [bar1, bar2, bar3]


def _compression_break_buy_bars():
  compression = [
    candle_geometry(open_=100.0, high=100.15, low=99.9, close=100.05, atr=1.0),
    candle_geometry(open_=100.05, high=100.2, low=99.95, close=100.1, atr=1.0),
    candle_geometry(open_=100.1, high=100.2, low=99.95, close=100.0, atr=1.0),
  ]
  breakout = candle_geometry(open_=100.0, high=100.7, low=99.98, close=100.65, atr=1.0)
  return [*compression, breakout]


def _sweep_indecision_displacement_bars():
  sweep_bar = candle_geometry(open_=100.0, high=100.1, low=98.9, close=99.95, atr=1.0)
  indecision_bar = candle_geometry(open_=99.95, high=100.05, low=99.85, close=99.98, atr=1.0)
  displacement_bar = candle_geometry(open_=99.98, high=100.8, low=99.9, close=100.7, atr=1.0)
  return [sweep_bar, indecision_bar, displacement_bar]


def test_morning_star_detected_from_pressure_indecision_recovery():
  bars = _morning_star_bars()
  patterns = detect_morning_evening_star(bars, direction="BUY")
  assert patterns == ("morning_star",)


def test_evening_star_is_direction_mirrored():
  bars = _evening_star_bars()
  patterns = detect_morning_evening_star(bars, direction="SELL")
  assert patterns == ("evening_star",)


def test_morning_star_requires_exactly_three_bars():
  bars = _morning_star_bars()
  assert detect_morning_evening_star(bars[:2], direction="BUY") is None
  assert detect_morning_evening_star([*bars, bars[-1]], direction="BUY") is None


def test_morning_star_rejects_insufficient_recovery():
  bar1 = candle_geometry(open_=101.0, high=101.05, low=99.95, close=100.0, atr=1.0)
  bar2 = candle_geometry(open_=100.0, high=100.2, low=99.9, close=100.05, atr=1.0)
  # Only ~10% recovery of bar1's move — well under the 0.50 minimum ratio.
  bar3 = candle_geometry(open_=100.05, high=100.15, low=100.0, close=100.1, atr=1.0)
  assert detect_morning_evening_star([bar1, bar2, bar3], direction="BUY") is None


def test_compression_break_detects_breakout_beyond_consolidation():
  bars = _compression_break_buy_bars()
  patterns = detect_compression_break(bars, direction="BUY", atr=1.0)
  assert patterns == ("compression_break",)


def test_compression_break_none_when_breakout_does_not_clear_range():
  compression = _compression_break_buy_bars()[:-1]
  weak_breakout = candle_geometry(open_=100.0, high=100.15, low=99.95, close=100.1, atr=1.0)
  assert detect_compression_break(
    [*compression, weak_breakout], direction="BUY", atr=1.0,
  ) is None


def test_sweep_indecision_displacement_detects_full_sequence():
  bars = _sweep_indecision_displacement_bars()
  patterns = detect_sweep_indecision_displacement(
    bars, direction="BUY", level=99.0, atr=1.0,
  )
  assert patterns == ("sweep_indecision_displacement",)


def test_sweep_indecision_displacement_none_without_sweep():
  bars = _sweep_indecision_displacement_bars()
  # Level far below the sweep bar's low — never actually swept.
  assert detect_sweep_indecision_displacement(
    bars, direction="BUY", level=50.0, atr=1.0,
  ) is None


def test_evaluate_sequence_returns_bounded_score_for_morning_star():
  bars = _morning_star_bars()
  ev = evaluate_sequence(bars, direction="BUY", atr=1.0)
  assert ev is not None
  assert "morning_star" in ev.patterns
  assert ev.bars == 3
  assert 0.0 <= ev.score <= 1.0
  assert ev.sequence_name == "morning_star"


def test_evaluate_sequence_dedupes_when_multiple_detectors_fire():
  bars = _sweep_indecision_displacement_bars()
  ev = evaluate_sequence(bars, direction="BUY", atr=1.0, level=99.0)
  assert ev is not None
  assert len(ev.patterns) == len(set(ev.patterns))


def test_evaluate_sequence_none_for_flat_uninteresting_bars():
  flat = [
    candle_geometry(open_=100.0, high=100.02, low=99.99, close=100.01, atr=1.0)
    for _ in range(3)
  ]
  assert evaluate_sequence(flat, direction="BUY", atr=1.0) is None


def test_evaluate_sequence_rejects_invalid_direction_and_too_few_bars():
  bars = _morning_star_bars()
  assert evaluate_sequence(bars, direction="UP", atr=1.0) is None
  assert evaluate_sequence(bars[:1], direction="BUY", atr=1.0) is None


def test_indecision_doji_and_spinning_top_are_contextual_only():
  doji_geo = candle_geometry(open_=100.0, high=100.2, low=99.8, close=100.01, atr=1.0)
  ev = evaluate_indecision(doji_geo)
  assert ev.doji is True
  assert ev.spinning_top is False  # doji takes precedence

  spinning_geo = candle_geometry(open_=100.0, high=100.3, low=99.7, close=100.1, atr=1.0)
  ev2 = evaluate_indecision(spinning_geo)
  assert ev2.doji is False
  assert ev2.spinning_top is True


def test_indecision_inside_bar_requires_prior_bar_range_containment():
  prior = candle_geometry(open_=100.0, high=101.0, low=99.0, close=100.5, atr=1.0)
  inside = candle_geometry(open_=100.2, high=100.6, low=99.8, close=100.3, atr=1.0)
  ev = evaluate_indecision(inside, prior)
  assert ev.inside_bar is True

  outside = candle_geometry(open_=100.2, high=101.5, low=99.8, close=100.3, atr=1.0)
  ev2 = evaluate_indecision(outside, prior)
  assert ev2.inside_bar is False
