"""Directional override for chop/trend regime classification."""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from app.analysis import engine as engine_module
from app.analysis.engine import AnalysisSettings, regime
from app.analysis.swings import find_swings
from app.analysis.types import DealingRange, Swing


def _df(
  rows: list[tuple[float, float, float, float]],
) -> pd.DataFrame:
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=pd.date_range(
      "2026-07-20",
      periods=len(rows),
      freq="5min",
      tz="UTC",
    ),
  ).assign(volume=100)


def _atr(df: pd.DataFrame, value: float) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _settings(**overrides) -> AnalysisSettings:
  values = {
    "chop_range_atr": 4.0,
    "chop_lookback": 24,
    "regime_direction_enabled": True,
    "regime_direction_lookback": 120,
    "regime_min_directional_swings": 3,
    "regime_min_displacement_atr": 4.0,
  }
  values.update(overrides)
  return AnalysisSettings(**values)


def _staircase_decline(
  *,
  atr: float = 1.0,
  bars: int = 120,
  net_atr: float = -6.2,
  height_atr: float = 2.1,
) -> tuple[pd.DataFrame, list[Swing], DealingRange]:
  """Build a declining staircase with four LH/LL pairs inside a tight box."""
  start = 100.0 + abs(net_atr) * atr
  end = start + net_atr * atr
  step = (end - start) / max(1, bars - 1)
  half_height = (height_atr * atr) / 2.0
  rows: list[tuple[float, float, float, float]] = []
  for index in range(bars):
    close = start + step * index
    high = close + half_height * 0.4
    low = close - half_height * 0.4
    rows.append((close, high, low, close))
  df = _df(rows)
  # Four bearish pairs: LH, LL, LH, LL, LH, LL, LH, LL
  # Prices step down so labels stay LH/LL under _label semantics.
  swings = [
    Swing(10, "high", start - 0.2 * atr, "LH"),
    Swing(20, "low", start - 0.8 * atr, "LL"),
    Swing(35, "high", start - 1.5 * atr, "LH"),
    Swing(50, "low", start - 2.2 * atr, "LL"),
    Swing(65, "high", start - 3.0 * atr, "LH"),
    Swing(80, "low", start - 3.8 * atr, "LL"),
    Swing(95, "high", start - 4.6 * atr, "LH"),
    Swing(110, "low", start - 5.4 * atr, "LL"),
  ]
  # Current dealing range is one narrow step (height 2.1 ATR).
  last = float(df["close"].iloc[-1])
  range_ = DealingRange(
    high=last + half_height,
    low=last - half_height,
    eq=last,
    position=0.5,
    zone="eq",
  )
  return df, swings, range_


def test_swing_detection_comes_from_analysis_swings():
  source = inspect.getsource(engine_module)
  assert "from app.analysis.swings import find_swings" in source
  assert "def find_swings" not in source
  assert callable(find_swings)


def test_staircase_decline_overrides_chop_to_trend():
  df, swings, range_ = _staircase_decline()
  result = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(),
  )

  assert result.legacy_kind == "chop"
  assert result.new_kind == "trend"
  assert result.kind == "trend"
  assert result.reasons[0].startswith("trend (directional override):")
  assert "4 consecutive LH/LL" in result.reasons[0]
  assert "net -6.2 ATR" in result.reasons[0]
  assert any("would have said chop" in reason for reason in result.reasons)
  assert "4 LH/LL, net -6.2 ATR" in result.directional_detail


def test_genuine_range_stays_chop_on_both_paths():
  closes = [104, 106, 105, 103, 107, 106] * 20
  rows = [(105, 108, 102, close) for close in closes]
  df = _df(rows)
  range_ = DealingRange(110, 100, 105, 0.5, "eq")
  swings = [
    Swing(5, "high", 108.0, "HH"),
    Swing(15, "low", 102.0, "HL"),
    Swing(25, "high", 107.5, "LH"),
    Swing(35, "low", 102.5, "HL"),
    Swing(45, "high", 108.2, "HH"),
    Swing(55, "low", 101.8, "LL"),
  ]

  result = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(),
  )

  assert result.legacy_kind == "chop"
  assert result.new_kind == "chop"
  assert result.kind == "chop"
  assert result.directional_detail == ""


def test_two_directional_pairs_below_threshold_keeps_chop():
  df, swings, range_ = _staircase_decline()
  swings = swings[:4]  # two LH/LL pairs only

  result = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(),
  )

  assert result.kind == "chop"
  assert result.new_kind == "chop"


def test_four_pairs_but_insufficient_displacement_keeps_chop():
  df, swings, range_ = _staircase_decline(net_atr=-2.0)

  result = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(),
  )

  assert result.legacy_kind == "chop"
  assert result.new_kind == "chop"
  assert result.kind == "chop"


def test_one_counter_pair_still_allows_trend_override():
  df, swings, range_ = _staircase_decline()
  # Insert one bullish HH/HL pair amid the bearish staircase.
  swings = [
    *swings[:4],
    Swing(55, "high", 97.0, "HH"),
    Swing(58, "low", 96.5, "HL"),
    *swings[4:],
  ]

  result = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(),
  )

  assert result.new_kind == "trend"
  assert result.kind == "trend"
  assert "4 consecutive LH/LL" in result.reasons[0]


def test_two_counter_pairs_block_override():
  df, swings, range_ = _staircase_decline()
  swings = [
    *swings[:2],
    Swing(25, "high", 98.5, "HH"),
    Swing(28, "low", 98.0, "HL"),
    *swings[2:4],
    Swing(55, "high", 96.0, "HH"),
    Swing(58, "low", 95.5, "HL"),
    *swings[4:],
  ]

  result = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(),
  )

  assert result.new_kind == "chop"
  assert result.kind == "chop"


def test_flag_off_keeps_legacy_classification_byte_identical():
  df, swings, range_ = _staircase_decline()
  enabled = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(regime_direction_enabled=True),
  )
  disabled = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(regime_direction_enabled=False),
  )

  assert enabled.kind == "trend"
  assert disabled.kind == "chop"
  assert disabled.legacy_kind == "chop"
  assert disabled.new_kind == "trend"
  assert disabled.reasons == [
    reason for reason in disabled.reasons
    if "directional override" not in reason
  ]
  assert any(reason.startswith("range height") for reason in disabled.reasons)
  # Counterfactual still recorded for regime_compare while flag is off.
  assert disabled.directional_detail == enabled.directional_detail


def test_flag_off_still_populates_new_kind_for_regime_compare():
  df, swings, range_ = _staircase_decline()
  result = regime(
    df,
    _atr(df, 1.0),
    swings,
    "range",
    range_,
    _settings(regime_direction_enabled=False),
  )

  assert result.kind == "chop"
  assert result.legacy_kind == "chop"
  assert result.new_kind == "trend"
  assert result.directional_detail.startswith("4 LH/LL")
  # Shipped reasons stay legacy so behaviour is byte-identical.
  assert all("directional override" not in reason for reason in result.reasons)
  compare_key = f"{result.legacy_kind}:{result.new_kind}"
  assert compare_key == "chop:trend"