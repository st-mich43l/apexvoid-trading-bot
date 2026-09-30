"""Equivalence tests for label-only analysis path."""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.engine import AnalysisSettings, analyze, analysis_labels


pytestmark = pytest.mark.no_database


def _trending_ohlc(*, bars: int, direction: str, start: float = 2000.0) -> pd.DataFrame:
  rows = []
  index = []
  price = start
  step = 2.0 if direction == "up" else -2.0
  for i in range(bars):
    o = price
    c = price + step
    h = max(o, c) + 0.5
    low = min(o, c) - 0.5
    rows.append({"open": o, "high": h, "low": low, "close": c, "volume": 1.0})
    index.append(pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=i))
    price = c
  return pd.DataFrame(rows, index=index)


def _ranging_ohlc(*, bars: int = 55, center: float = 2000.0, amp: float = 8.0) -> pd.DataFrame:
  rows = []
  index = []
  for i in range(bars):
    offset = amp if i % 2 == 0 else -amp
    c = center + offset
    o = c - 1.0
    h = c + 1.0
    low = c - 2.0
    rows.append({"open": o, "high": h, "low": low, "close": c, "volume": 1.0})
    index.append(pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=i))
  return pd.DataFrame(rows, index=index)


def _assert_labels_match_analyze(frames: dict[str, pd.DataFrame], pip_size: float = 0.1):
  settings = AnalysisSettings(pip_size=pip_size)
  htf_order = ["H1", "M15"]
  ctx = analyze(frames, settings, htf_order=htf_order)
  expected_regime = ctx.regime.kind if ctx.regime is not None else "unknown"
  m5 = ctx.per_tf.get("M5")
  expected_structure = str(m5.structure) if m5 is not None else "unknown"
  assert analysis_labels(frames, settings, htf_order=htf_order) == (
    ctx.htf_bias,
    expected_structure,
    expected_regime,
  )


def test_analysis_labels_matches_analyze_trending_up():
  h1 = _trending_ohlc(bars=55, direction="up")
  frames = {
    "H1": h1,
    "M15": h1.iloc[-40:].copy(),
    "M5": h1.iloc[-25:].copy(),
  }
  _assert_labels_match_analyze(frames)


def test_analysis_labels_matches_analyze_trending_down():
  h1 = _trending_ohlc(bars=55, direction="down", start=2500.0)
  frames = {
    "H1": h1,
    "M15": h1.iloc[-40:].copy(),
    "M5": h1.iloc[-25:].copy(),
  }
  _assert_labels_match_analyze(frames)


def test_analysis_labels_matches_analyze_ranging():
  h1 = _ranging_ohlc(bars=55)
  frames = {
    "H1": h1,
    "M15": h1.iloc[-40:].copy(),
    "M5": h1.iloc[-25:].copy(),
  }
  _assert_labels_match_analyze(frames)


def test_analysis_labels_empty_frames():
  assert analysis_labels({}, AnalysisSettings(pip_size=0.1)) == (
    "unknown",
    "unknown",
    "unknown",
  )


def test_analysis_labels_h1_only():
  h1 = _trending_ohlc(bars=55, direction="up")
  settings = AnalysisSettings(pip_size=0.1)
  htf_bias, structure, regime_kind = analysis_labels(
    {"H1": h1},
    settings,
    htf_order=["H1", "M15"],
  )
  assert htf_bias in {"up", "down", "range"}
  assert structure
  assert regime_kind
  ctx = analyze({"H1": h1}, settings, htf_order=["H1", "M15"])
  assert (htf_bias, structure, regime_kind) == (
    ctx.htf_bias,
    ctx.per_tf["H1"].structure,
    ctx.regime.kind if ctx.regime is not None else "unknown",
  )
