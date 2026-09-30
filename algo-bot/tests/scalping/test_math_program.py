"""Unit tests for ATR-normalized scalp math features and strategy gates."""

from __future__ import annotations

import pytest

from app.scalping.math_features import (
  VR_ACTIVE,
  VR_QUIET,
  build_feature_vector,
  candle_geometry,
  classify_session_utc_hour,
  classify_volatility_regime,
  impulse_atr,
  location_scores,
  range_position,
  retracement_ratio,
  room_net_price,
  room_sufficient,
  trigger_quality_buy,
  unified_scalp_score,
  volatility_ratio,
  zone_width_atr,
)


pytestmark = pytest.mark.no_database


def test_range_position_and_location_scores():
  p = range_position(4052.0, 4050.0, 4060.0)
  assert p == pytest.approx(0.2)
  buy, sell = location_scores(p)
  assert buy == pytest.approx(0.8)
  assert sell == pytest.approx(0.2)


def test_atr_normalized_width_impulse_retracement():
  assert zone_width_atr(4060.0, 4050.0, 5.0) == pytest.approx(2.0)
  assert impulse_atr(4060.0, 4050.0, 5.0) == pytest.approx(2.0)
  # Bullish impulse 4050→4060, price 4057 → 30% retracement
  r = retracement_ratio(4057.0, 4060.0, 4050.0)
  assert r == pytest.approx(0.3)


def test_room_net_hard_gate():
  room = room_net_price(3.0, spread=0.5, slippage=0.3, buffer=0.2)
  assert room == pytest.approx(2.0)
  assert room_sufficient(room, 1.5)
  assert not room_sufficient(room, 2.5)


def test_trigger_quality_buy_prefers_lower_wick_and_high_close():
  geom = candle_geometry(open_=100.0, high=101.0, low=98.0, close=100.8)
  q = trigger_quality_buy(geom, reclaim=True)
  assert q > 0.5


def test_volatility_regime_and_session():
  assert classify_volatility_regime(volatility_ratio(3.0, 5.0)) == VR_QUIET
  assert classify_volatility_regime(volatility_ratio(8.0, 5.0)) == VR_ACTIVE
  assert classify_session_utc_hour(3) == "asia"
  assert classify_session_utc_hour(14) == "london_ny_overlap"


def test_unified_score_penalizes_cost_and_exhaustion():
  strong = unified_scalp_score(
    location=0.9, trigger=0.8, momentum=0.7, structure=0.75, room=0.85, cost=0.1, exhaustion=0.1,
  )
  weak = unified_scalp_score(
    location=0.9, trigger=0.8, momentum=0.7, structure=0.75, room=0.85, cost=0.9, exhaustion=0.9,
  )
  assert strong > weak


def test_build_feature_vector_smoke():
  fv = build_feature_vector(
    price=4052.0,
    atr=5.0,
    range_low=4050.0,
    range_high=4060.0,
    zone_low=4050.0,
    zone_high=4051.0,
    direction="BUY",
    barrier=4058.0,
    spread=0.2,
    slippage=0.1,
    open_=4051.0,
    high=4052.5,
    low=4049.5,
    close=4052.0,
    reclaim=True,
    atr_short=4.0,
    atr_long=5.0,
    utc_hour=14,
  )
  assert fv.location_buy == pytest.approx(0.8)
  assert fv.session == "london_ny_overlap"
  assert fv.trigger_quality is not None
