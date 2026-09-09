"""Unit coverage for autotrade Fibonacci + velocity/acceleration math."""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.dealing_range import dealing_range
from app.analysis.detectors import (
  ConfluenceFactors,
  DetectorSettings,
  _confluence_from_factors,
)
from app.analysis.fibonacci import (
  fib_ladder,
  fib_zone_label,
  nearest_fib,
)
from app.analysis.momentum import momentum_state
from app.analysis.types import Swing
from app.autotrade.setup_card import _format_math_line, format_plan_published_root_card
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.scalping.microstructure import detect_impulse_pullback


def _ohlc(closes: list[float], atr: float = 1.0) -> tuple[pd.DataFrame, pd.Series]:
  rows = []
  for i, close in enumerate(closes):
    open_ = close - 0.1
    rows.append({
      "open": open_,
      "high": close + atr * 0.2,
      "low": close - atr * 0.2,
      "close": close,
    })
  df = pd.DataFrame(rows)
  atr_s = pd.Series([atr] * len(df), index=df.index)
  return df, atr_s


def test_fib_ladder_retracements_and_nearest():
  levels = fib_ladder(100.0, 200.0, include_extensions=False)
  ratios = [item.ratio for item in levels]
  assert ratios == [0.236, 0.382, 0.5, 0.618, 0.786]
  # 0.618 retracement from 200 → 100 = 138.2
  hit = nearest_fib(levels, 138.2, atr=1.0, epsilon_atr=0.5)
  assert hit is not None
  assert hit.ratio == 0.618
  miss = nearest_fib(levels, 160.0, atr=1.0, epsilon_atr=0.1)
  assert miss is None


def test_fib_zone_and_dealing_range_deep_bands():
  assert fib_zone_label(0.30) == "deep_discount"
  assert fib_zone_label(0.42) == "discount"
  assert fib_zone_label(0.50) == "eq"
  assert fib_zone_label(0.70) == "deep_premium"

  swings = [Swing(0, "low", 100.0), Swing(1, "high", 200.0)]
  deep = dealing_range(swings, 130.0)  # pos 0.30
  assert deep is not None
  assert deep.zone == "discount"
  assert deep.fib_zone == "deep_discount"
  mid = dealing_range(swings, 170.0)  # pos 0.70
  assert mid is not None
  assert mid.zone == "premium"
  assert mid.fib_zone == "deep_premium"


def test_momentum_state_velocity_and_acceleration_signs():
  # Rising closes → positive velocity; accelerating rise → positive a.
  closes = [100.0 + i * 0.5 for i in range(24)]
  df, atr = _ohlc(closes, atr=1.0)
  state = momentum_state(df, atr, lookback=4, bull_threshold=0.1, bear_threshold=-0.1)
  assert state.velocity > 0
  assert state.state == "bull"
  assert state.acceleration >= 0

  falling = [112.0 - i * 0.5 for i in range(24)]
  df_f, atr_f = _ohlc(falling, atr=1.0)
  bear = momentum_state(df_f, atr_f, lookback=4, bull_threshold=0.1, bear_threshold=-0.1)
  assert bear.velocity < 0
  assert bear.state == "bear"


def test_momentum_acceleration_is_per_bar_not_a_bare_velocity_delta():
  """2026-09: a = (v[t]-v[t-n])/n, a true per-bar second derivative - not the
  v1 `a = v[t]-v[t-n]` velocity delta. Three-segment rate-of-rise closes
  (1/bar, then 2/bar, then 3/bar) with n=4, ATR=1 give known v/v_prev.
  """
  closes = [0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 18.0, 21.0, 24.0]
  df, atr = _ohlc(closes, atr=1.0)
  state = momentum_state(df, atr, lookback=4, bull_threshold=0.1, bear_threshold=-0.1)
  # v[12] = (24-12)/(4*1) = 3.0; v[8] = (12-4)/(4*1) = 2.0
  assert state.velocity == pytest.approx(3.0)
  assert state.acceleration == pytest.approx((3.0 - 2.0) / 4)


def test_confluence_fib_touch_weight():
  base = _confluence_from_factors(ConfluenceFactors(htf_aligned=True))
  boosted = _confluence_from_factors(
    ConfluenceFactors(htf_aligned=True, fib_touch=True),
    DetectorSettings(fibonacci_confluence_weight=2.5),
  )
  assert boosted >= base


def test_hfs_impulse_preferred_defaults_are_fib_band():
  import inspect

  sig = inspect.signature(detect_impulse_pullback)
  assert sig.parameters["preferred_low"].default == 0.382
  assert sig.parameters["preferred_high"].default == 0.618
  assert sig.parameters["min_retracement"].default == 0.25
  assert sig.parameters["max_retracement"].default == 0.75


def test_strategy_match_math_round_trip_and_card_line():
  from app.autotrade.strategy_match import strategy_match_id

  match_id = strategy_match_id(
    "XAU", "M5", "1", "Momentum Ride", "BUY", 1998.0, 2002.0,
  )
  match = StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id,
    symbol="XAU",
    source_tf="M5",
    event_ts="1",
    issued_at=1,
    expires_at=100,
    strategy="Momentum Ride",
    strategy_mode="with_trend",
    direction="BUY",
    key_level=2000.0,
    entry_low=1998.0,
    entry_high=2002.0,
    current_price=2000.0,
    confluence=2,
    reasons=("impulse break",),
    atr=2.0,
    structure_swing=1990.0,
    targets_pips=(30,),
    math_fib_ratio=0.618,
    math_velocity=0.41,
    math_acceleration=0.08,
    math_pd=0.37,
  )
  restored = StrategyMatch.from_json(match.to_json())
  assert restored is not None
  assert restored.math_fib_ratio == 0.618
  assert restored.math_velocity == 0.41
  assert restored.math_acceleration == 0.08
  assert restored.math_pd == 0.37

  line = _format_math_line(match)
  assert line is not None
  assert "fib 0.618" in line
  assert "v=+0.41" in line
  assert "PD 0.37" in line

  card = format_plan_published_root_card(match)
  assert "📐 <b>Math</b>" in card
  assert "fib 0.618" in card
