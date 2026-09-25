"""Candle Confirmation V2 — evidence family B: DISPLACEMENT (§7-§9/§49)."""

from __future__ import annotations

import pytest

from app.analysis.candle_displacement import engulfing_quality, evaluate_displacement
from app.analysis.candle_geometry import candle_geometry


def _strong_bull_body_geo():
  # Big body, close near the high, ample range — textbook displacement.
  return candle_geometry(open_=100.0, high=101.8, low=99.9, close=101.7, atr=1.0)


def _weak_prior_geo():
  # Small opposite-direction body, fully inside the strong bull bar's body
  # range [100.0, 101.7], for the engulfing prior-bar check.
  return candle_geometry(open_=100.5, high=100.6, low=100.1, close=100.2, atr=1.0)


def test_strong_bull_body_produces_body_close_and_strong_close_and_displacement_candle():
  geo = _strong_bull_body_geo()
  ev = evaluate_displacement(geo, None, direction="BUY", atr=1.0)
  assert ev is not None
  assert "strong_close" in ev.patterns
  assert "body_close" in ev.patterns
  assert "displacement_candle" in ev.patterns
  assert 0.0 <= ev.score <= 1.0
  assert ev.body_dominance > 0.55


def test_direction_mirrors_for_sell():
  geo = candle_geometry(open_=100.0, high=100.1, low=98.2, close=98.3, atr=1.0)
  ev = evaluate_displacement(geo, None, direction="SELL", atr=1.0)
  assert ev is not None
  assert "strong_close" in ev.patterns
  assert "body_close" in ev.patterns


def test_engulfing_requires_body_containment_and_direction_and_range_floor():
  current = _strong_bull_body_geo()
  prior = _weak_prior_geo()
  is_engulfing, quality = engulfing_quality(current, prior, direction="BUY")
  assert is_engulfing is True
  assert quality is not None
  assert 0.0 <= quality <= 1.0


def test_engulfing_false_when_prior_body_not_contained():
  current = _weak_prior_geo()
  prior = _strong_bull_body_geo()
  is_engulfing, quality = engulfing_quality(current, prior, direction="BUY")
  assert is_engulfing is False
  assert quality is None


def test_engulfing_false_when_no_prior_bar():
  current = _strong_bull_body_geo()
  is_engulfing, quality = engulfing_quality(current, None, direction="BUY")
  assert is_engulfing is False
  assert quality is None


def test_evaluate_displacement_includes_engulfing_label_when_prior_given():
  current = _strong_bull_body_geo()
  prior = _weak_prior_geo()
  ev = evaluate_displacement(current, prior, direction="BUY", atr=1.0)
  assert ev is not None
  assert "engulfing" in ev.patterns
  assert ev.engulfing is True
  assert ev.engulfing_quality is not None


def test_strong_reclaim_label_requires_close_beyond_level():
  geo = _strong_bull_body_geo()
  ev = evaluate_displacement(geo, None, direction="BUY", level=100.5, atr=1.0)
  assert ev is not None
  assert "strong_reclaim" in ev.patterns
  assert ev.reclaim is True
  assert ev.reclaim_depth_atr == pytest.approx(1.2)


def test_no_reclaim_label_when_close_does_not_clear_level():
  geo = _strong_bull_body_geo()
  ev = evaluate_displacement(geo, None, direction="BUY", level=200.0, atr=1.0)
  assert ev is not None
  assert "strong_reclaim" not in ev.patterns
  assert ev.reclaim is False


def test_returns_none_for_invalid_direction():
  geo = _strong_bull_body_geo()
  assert evaluate_displacement(geo, None, direction="LONG", atr=1.0) is None


def test_returns_none_for_small_indecisive_body():
  # Tiny body, small range relative to ATR — no directional control at all.
  geo = candle_geometry(open_=100.0, high=100.08, low=99.95, close=100.02, atr=1.0)
  ev = evaluate_displacement(geo, None, direction="BUY", atr=1.0)
  assert ev is None


def test_wrong_direction_bearish_body_produces_no_buy_evidence():
  geo = candle_geometry(open_=100.0, high=100.1, low=98.2, close=98.3, atr=1.0)
  ev = evaluate_displacement(geo, None, direction="BUY", atr=1.0)
  assert ev is None
