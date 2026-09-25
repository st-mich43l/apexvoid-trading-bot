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
from app.scalping.math_strategies import (
  evaluate_impulse_pullback_continuation,
  evaluate_liquidity_sweep_reversal,
  evaluate_range_edge_mean_reversion,
)
from app.scalping.replay import calibration_report, split_dataset
from app.scalping.rollout import ControlledLivePolicy, evaluate_math_shadow


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


def test_liquidity_sweep_reversal_buy_gates():
  ok = evaluate_liquidity_sweep_reversal(
    direction="BUY",
    price=4050.5,
    liquidity_level=4050.0,
    bar_low=4049.5,
    bar_high=4051.0,
    bar_close=4050.6,
    bar_open=4050.2,
    atr=5.0,
    range_low=4048.0,
    range_high=4060.0,
    barrier=4058.0,
    target_min_price=1.0,
  )
  assert ok.allowed
  assert ok.reason_code == "sweep_reclaim_location_room_ok"

  blocked = evaluate_liquidity_sweep_reversal(
    direction="BUY",
    price=4056.0,
    liquidity_level=4050.0,
    bar_low=4049.5,
    bar_high=4057.0,
    bar_close=4056.5,
    bar_open=4055.0,
    atr=5.0,
    range_low=4048.0,
    range_high=4060.0,
    barrier=4058.0,
    target_min_price=1.0,
  )
  assert blocked.hard_block
  assert blocked.reason_code == "location_outside_edge"


def test_impulse_pullback_rejects_chase_at_extreme():
  blocked = evaluate_impulse_pullback_continuation(
    direction="BUY",
    price=4059.8,
    atr=5.0,
    impulse_origin=4050.0,
    impulse_extreme=4060.0,
    barrier=4070.0,
    target_min_price=1.0,
    continuation_trigger=True,
  )
  assert blocked.hard_block
  # retracement ~0.02 is caught by the dedicated chase-at-extreme check,
  # which now runs before the generic band check regardless of
  # retracement_min - previously always shadowed by retracement_outside_band
  # under the default retracement_min=0.25, making this branch dead code.
  assert blocked.reason_code == "chasing_extreme"

  ok = evaluate_impulse_pullback_continuation(
    direction="BUY",
    price=4057.0,
    atr=5.0,
    impulse_origin=4050.0,
    impulse_extreme=4060.0,
    barrier=4070.0,
    target_min_price=1.0,
    continuation_trigger=True,
  )
  assert ok.allowed


def test_impulse_pullback_measures_original_leg_not_current_retraced_price():
  """2026-09 owner spec: origin 4500, extreme 4512, current 4506, ATR 10 ->
  impulse 1.2 ATR, retracement 0.5. Must not be rejected as a 0.6 ATR impulse
  (the bug: measuring |current-origin|/ATR instead of |extreme-origin|/ATR).
  """
  result = evaluate_impulse_pullback_continuation(
    direction="BUY",
    price=4506.0,
    atr=10.0,
    impulse_origin=4500.0,
    impulse_extreme=4512.0,
    barrier=4530.0,
    target_min_price=1.0,
    continuation_trigger=True,
  )
  assert result.features["impulse_atr"] == pytest.approx(1.2)
  assert result.features["retracement"] == pytest.approx(0.5)
  assert result.allowed
  assert result.reason_code == "impulse_pullback_continuation_ok"


def test_range_edge_dead_zone():
  blocked = evaluate_range_edge_mean_reversion(
    direction="BUY",
    price=4055.0,
    atr=5.0,
    range_low=4050.0,
    range_high=4060.0,
    barrier=4059.0,
    target_min_price=0.5,
  )
  assert blocked.reason_code == "equilibrium_dead_zone"

  ok = evaluate_range_edge_mean_reversion(
    direction="BUY",
    price=4051.0,
    atr=5.0,
    range_low=4050.0,
    range_high=4060.0,
    barrier=4059.0,
    target_min_price=0.5,
  )
  assert ok.allowed


def test_split_dataset_and_calibration_holdout_discipline():
  rows = [{"timestamp": i, "outcome": "target" if i % 2 == 0 else "stop", "net_r": 1.0 if i % 2 == 0 else -1.0, "mfe_pips": 2.0, "mae_pips": -1.0} for i in range(100)]
  splits = split_dataset(rows)
  assert len(splits["development"]) == 60
  assert len(splits["validation"]) == 20
  assert len(splits["holdout"]) == 20
  report = calibration_report(rows)
  assert report["discipline"]["rule"] == "never_tune_thresholds_on_holdout"
  assert "expectancy_r" in report["holdout"]


def test_math_shadow_never_executes_in_shadow_mode():
  shadow = evaluate_math_shadow(
    mode="shadow",
    direction="BUY",
    price=4051.0,
    atr=5.0,
    range_low=4050.0,
    range_high=4060.0,
    liquidity_level=4050.0,
    barrier=4059.0,
    bar_open=4050.2,
    bar_high=4051.2,
    bar_low=4049.4,
    bar_close=4050.8,
    target_min_price=0.5,
  )
  assert shadow.would_execute is False
  assert shadow.mode == "shadow"


def test_controlled_live_requires_explicit_enable():
  shadow = evaluate_math_shadow(
    mode="live",
    direction="BUY",
    price=4051.0,
    atr=5.0,
    range_low=4050.0,
    range_high=4060.0,
    liquidity_level=4050.0,
    barrier=4059.0,
    bar_open=4050.2,
    bar_high=4051.2,
    bar_low=4049.4,
    bar_close=4050.8,
    target_min_price=0.5,
    policy=ControlledLivePolicy(enabled=False),
  )
  assert shadow.would_execute is False
