from dataclasses import replace

import pandas as pd
import pytest

from app.analysis import AnalysisSettings
from app.pa_types import Swing, Zone
from app.trendlines import Trendline, _dedup, trendlines, value_at
from app.zones import TRENDLINE_SCORE, score_zones


def _line_df(length: int = 10) -> pd.DataFrame:
  rows = []
  for index in range(length):
    support = 100 + 0.1 * index
    rows.append((support + 0.5, support + 1.2, support, support + 0.7, 100))
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-07-10", periods=length, freq="5min", tz="UTC"),
  )


def _support_swings(count: int = 3) -> list[Swing]:
  indexes = [1, 4, 7, 9][:count]
  return [Swing(index, "low", 100 + 0.1 * index) for index in indexes]


def test_three_ascending_lows_fit_one_support_line():
  df = _line_df()

  lines = trendlines(
    _support_swings(),
    df,
    pd.Series([1.0] * len(df), index=df.index),
    AnalysisSettings(),
  )

  assert len(lines) == 1
  assert lines[0].kind == "support"
  assert lines[0].touches == 3
  assert lines[0].point_idx == (1, 4, 7)
  assert value_at(lines[0], 9) == pytest.approx(100.9)


def test_mid_span_close_beyond_tolerance_rejects_candidate():
  df = _line_df()
  df.iloc[5, df.columns.get_loc("low")] = 99.0
  df.iloc[5, df.columns.get_loc("close")] = 99.2

  lines = trendlines(
    _support_swings(),
    df,
    1.0,
    AnalysisSettings(),
  )

  assert lines == []


def test_later_close_marks_support_broken_at_exact_bar():
  df = _line_df()
  df.iloc[8, df.columns.get_loc("low")] = 99.8
  df.iloc[8, df.columns.get_loc("close")] = 100.0

  line = trendlines(
    _support_swings(),
    df,
    1.0,
    AnalysisSettings(),
  )[0]

  assert line.broken is True
  assert line.break_index == 8


def test_near_duplicate_lines_keep_most_touched():
  three = Trendline("support", (1, 4, 7), 0.10, 100, 3, False, None)
  four = Trendline("support", (1, 3, 5, 7), 0.11, 100.05, 4, False, None)

  assert _dedup([three, four], last_bar=9, atr=1.0) == [four]


def test_slope_beyond_atr_bound_is_rejected():
  df = _line_df()
  steep = [Swing(index, "low", 100 + 0.2 * index) for index in (1, 4, 7)]

  assert trendlines(steep, df, 1.0, AnalysisSettings()) == []


def test_zone_score_rewards_unbroken_trendline_confluence():
  zone = Zone(100.7, 101.1, "demand", source="supply_demand")
  line = Trendline("support", (1, 4, 7), 0.1, 100, 3, False, None)
  broken = replace(line, broken=True, break_index=8)

  plain = score_zones([zone], [], [], 0)[0]
  scored = score_zones(
    [zone],
    [],
    [],
    0,
    trendlines=[line],
    bar_index=9,
  )[0]
  ignored = score_zones(
    [zone],
    [],
    [],
    0,
    trendlines=[broken],
    bar_index=9,
  )[0]

  assert scored.score == pytest.approx(plain.score + TRENDLINE_SCORE)
  assert "TL confluence" in scored.score_reasons
  assert ignored.score == plain.score
