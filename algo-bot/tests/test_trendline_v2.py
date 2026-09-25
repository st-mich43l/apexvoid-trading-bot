from __future__ import annotations

import json
from dataclasses import replace

import pandas as pd
import pytest

from app.analysis.engine import AnalysisSettings, _nested_cfg_from_analysis_settings
from app.analysis.trendline_v2 import evaluate_live_interaction
from app.analysis.trendlines import Trendline, trendlines, value_at
from app.analysis.types import Swing


pytestmark = pytest.mark.no_database


def _cfg(**overrides):
  return _nested_cfg_from_analysis_settings(
    AnalysisSettings(tl_version="v2", **overrides),
  )


def _support_df(length: int = 60, *, slope: float = 0.1, base: float = 100.0):
  rows = []
  for index in range(length):
    line = base + slope * index
    rows.append((line + 0.5, line + 1.2, line, line + 0.7, 100))
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-09-01", periods=length, freq="5min", tz="UTC"),
  )


def _resistance_df(length: int = 60, *, slope: float = -0.1, base: float = 110.0):
  rows = []
  for index in range(length):
    line = base + slope * index
    rows.append((line - 0.5, line, line - 1.2, line - 0.7, 100))
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-09-01", periods=length, freq="5min", tz="UTC"),
  )


def _atr(df: pd.DataFrame, value: float = 1.0) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _confirmed_support() -> list[Swing]:
  return [
    Swing(10, "low", 101.0, confirmed_index=12),
    Swing(20, "low", 102.0, confirmed_index=22),
    Swing(40, "low", 104.0, confirmed_index=42),
  ]


def _confirmed_resistance() -> list[Swing]:
  return [
    Swing(10, "high", 109.0, confirmed_index=12),
    Swing(20, "high", 108.0, confirmed_index=22),
    Swing(40, "high", 106.0, confirmed_index=42),
  ]


def _line(lines: list[Trendline], anchors: tuple[int, int]) -> Trendline:
  return next(line for line in lines if line.anchor_idx == anchors)


def test_reference_incident_cannot_retrofit_a_c_line_through_b():
  """The 2026-09-17 loss is rejected by its causal, not visual, geometry."""
  atr = 6.182857142857172
  a_index, a_price = 106, 4258.84
  b_index, b_price = 117, 4266.43
  c_index, c_price = 143, 4281.12
  slope = (b_price - a_price) / (b_index - a_index)
  df = _support_df(151, slope=slope, base=a_price - slope * a_index)
  projected = value_at(
    Trendline("support", (a_index, b_index), slope, a_price - slope * a_index, 2, False, None),
    c_index,
  )
  df.iloc[c_index, df.columns.get_loc("low")] = c_price
  swings = [
    Swing(a_index, "low", a_price, confirmed_index=a_index + 2),
    Swing(b_index, "low", b_price, confirmed_index=b_index + 2),
    Swing(c_index, "low", c_price, confirmed_index=c_index + 2),
  ]

  lines = trendlines(swings, df, _atr(df, atr), _cfg())
  causal = _line(lines, (a_index, b_index))

  assert projected == pytest.approx(4284.37, abs=0.02)
  assert abs(c_price - projected) / atr == pytest.approx(0.526, abs=0.01)
  assert causal.state == "tentative"
  assert causal.validation_touch_count == 0
  assert not any(line.anchor_idx == (a_index, c_index) for line in lines)


@pytest.mark.parametrize(
  ("df_factory", "swings_factory", "kind"),
  [
    (_support_df, _confirmed_support, "support"),
    (_resistance_df, _confirmed_resistance, "resistance"),
  ],
)
def test_clean_forward_validation_confirms_buy_and_sell(
  df_factory,
  swings_factory,
  kind,
):
  df = df_factory()
  line = _line(trendlines(swings_factory(), df, _atr(df), _cfg()), (10, 20))

  assert line.version == "v2"
  assert line.kind == kind
  assert line.state == "confirmed"
  assert line.anchor_count == 2
  assert line.validation_touch_count == 1
  assert line.total_touch_count == 3
  assert line.validation_touches[0].structure_confirmed is True
  assert line.validation_touches[0].approach_direction_valid is True
  assert line.mean_validation_error_atr == pytest.approx(0.0)
  assert json.dumps(line.to_telemetry())


def test_two_anchors_stay_tentative_without_forward_validation():
  df = _support_df()
  line = _line(
    trendlines(_confirmed_support()[:2], df, _atr(df), _cfg()),
    (10, 20),
  )

  assert line.state == "tentative"
  assert line.anchor_count == 2
  assert line.validation_touch_count == 0


def test_v1_shadow_is_metrics_only_when_v2_owns_discovery():
  metrics: list[tuple[str, str, dict[str, str]]] = []

  lines = trendlines(
    _confirmed_support(),
    _support_df(),
    _atr(_support_df()),
    _cfg(tl_shadow_v1=True, tl_min_validation_touches=2),
    symbol="XAU",
    timeframe="M5",
    metric_sink=lambda name, symbol, tags: metrics.append((name, symbol, tags)),
  )

  assert all(line.version == "v2" for line in lines)
  assert any(name == "trendline_v1_shadow_eligible" for name, _, _ in metrics)


def test_validation_waits_for_its_complete_closed_reaction_window():
  df = _support_df(length=39)
  swings = [
    Swing(10, "low", 101.0, confirmed_index=12),
    Swing(20, "low", 102.0, confirmed_index=22),
    Swing(36, "low", 103.6, confirmed_index=38),
  ]

  line = _line(trendlines(swings, df, _atr(df), _cfg()), (10, 20))

  assert line.state == "tentative"
  assert line.validation_touch_count == 0


def test_live_test_is_not_armed_and_failed_close_breaks_support():
  df = _support_df()
  line = Trendline(
    "support",
    (10, 20, 40),
    0.1,
    100.0,
    3,
    False,
    None,
    version="v2",
    state="confirmed",
    anchor_idx=(10, 20),
    validation_touch_count=1,
    total_touch_count=3,
  )
  last = len(df) - 1
  level = value_at(line, last)
  testing = df.copy()
  testing.iloc[last] = (level + 0.05, level + 0.30, level - 0.05, level - 0.05, 100)

  interaction = evaluate_live_interaction(
    line,
    testing,
    1.0,
    _cfg().analysis.trendlines,
  )
  assert interaction.state == "TESTING_SUPPORT"
  assert interaction.rejection_reason == "testing_without_reaction"

  failed = testing.copy()
  failed.iloc[last, failed.columns.get_loc("close")] = level - 0.20
  assert evaluate_live_interaction(
    line,
    failed,
    1.0,
    _cfg().analysis.trendlines,
  ).state == "FAILED_SUPPORT"


def test_wick_probe_can_reclaim_but_unreclaimed_close_breaks_line():
  df = _support_df()
  swings = _confirmed_support()
  line = _line(trendlines(swings, df, _atr(df), _cfg()), (10, 20))
  assert line.close_violations == 0

  broken = df.copy()
  index = len(broken) - 1
  level = value_at(line, index)
  broken.iloc[index] = (level - 0.2, level + 0.1, level - 0.7, level - 0.3, 100)
  lines = trendlines(swings, broken, _atr(broken), _cfg())
  assert _line(lines, (10, 20)).state == "broken"


def test_wick_reclaim_is_telemetry_not_a_close_break_and_repeated_wicks_degrade():
  df = _support_df()
  index = 50
  line_price = 100.0 + 0.1 * index
  df.iloc[index] = (
    line_price + 0.1,
    line_price + 0.3,
    line_price - 0.6,
    line_price + 0.2,
    100,
  )
  line = _line(trendlines(_confirmed_support(), df, _atr(df), _cfg()), (10, 20))
  assert line.state == "confirmed"
  assert line.wick_violations == 1
  assert line.close_violations == 0
  assert line.violation_reclaimed is True

  df.iloc[48] = (
    104.9,
    105.1,
    104.2,
    105.0,
    100,
  )
  degraded = _line(
    trendlines(
      _confirmed_support(),
      df,
      _atr(df),
      _cfg(tl_max_wick_violations=1),
    ),
    (10, 20),
  )
  assert degraded.state == "degraded"


def test_independent_validations_exhaust_a_line_without_refitting_anchors():
  df = _support_df()
  swings = [
    Swing(10, "low", 101.0, confirmed_index=12),
    Swing(20, "low", 102.0, confirmed_index=22),
    Swing(40, "low", 104.0, confirmed_index=42),
    Swing(50, "low", 105.0, confirmed_index=52),
  ]

  line = _line(
    trendlines(
      swings,
      df,
      _atr(df),
      _cfg(tl_exhaustion_validation_touches=2),
    ),
    (10, 20),
  )

  assert line.state == "exhausted"
  assert line.anchor_idx == (10, 20)
  assert line.validation_touch_count == 2


def test_live_wrong_side_approach_cannot_be_reclassified_as_support_reclaim():
  df = _support_df()
  line = Trendline(
    "support", (10, 20, 40), 0.1, 100.0, 3, False, None,
    version="v2", state="confirmed", anchor_idx=(10, 20),
    validation_touch_count=1, total_touch_count=3,
  )
  index = len(df) - 1
  previous_line = value_at(line, index - 1)
  current_line = value_at(line, index)
  df.iloc[index - 1, df.columns.get_loc("close")] = previous_line - 0.2
  df.iloc[index] = (
    current_line - 0.1,
    current_line + 0.2,
    current_line - 0.1,
    current_line + 0.1,
    100,
  )

  interaction = evaluate_live_interaction(line, df, 1.0, _cfg().analysis.trendlines)

  assert interaction.state == "FAILED_SUPPORT"
  assert interaction.rejection_reason == "invalid_approach_direction"
