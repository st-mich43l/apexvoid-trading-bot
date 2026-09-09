import pandas as pd

from app.analysis import structure


def _df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=index,
  ).assign(volume=100)


def test_swings_and_market_structure_label_hh_hl_uptrend():
  df = _df([
    (100, 101, 99, 100),
    (101, 103, 100, 102),
    (102, 106, 102, 105),
    (104, 104, 101, 102),
    (101, 102, 98, 100),
    (104, 107, 103, 106),
    (106, 111, 105, 110),
    (107, 108, 104, 105),
    (104, 106, 101, 103),
    (109, 112, 106, 111),
    (111, 116, 108, 115),
    (112, 113, 107, 109),
    (113, 114, 109, 113),
  ])

  pivots = structure.swings(df, left=1, right=1)

  assert [s.label for s in pivots if s.kind == "high"][-2:] == ["HH", "HH"]
  assert [s.label for s in pivots if s.kind == "low"][-2:] == ["HL", "HL"]
  assert structure.market_structure(pivots) == "up"


def test_fvg_and_retest_fire_on_crafted_window():
  df = _df([
    (99, 100, 98, 99),
    (100, 101, 99, 100),
    (102, 103, 101.5, 102.5),
    (101, 101.5, 100, 101),
    (101, 104, 101.3, 103.5),
    (103.5, 105, 103, 104),
    (102.5, 103, 101.95, 102.1),
  ])

  gaps = structure.fvg(df)
  assert any(zone.kind == "bullish_fvg" for zone in gaps)

  retest = structure.find_retest(df, 102)
  assert retest is not None
  assert retest.kind == "retest_support"


def test_flat_window_has_no_gap_or_retest():
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 99, 100),
    (100, 101, 99, 100),
    (100, 101, 99, 100),
  ])

  assert structure.fvg(df) == []
  assert structure.find_retest(df, 100) is None


def test_last_consecutive_break_index_requires_the_full_run():
  # A single close beyond the level is not, by itself, a structural break
  # (see key_level_role.classify_key_level_role, which uses the exact same
  # breakout_accept_bars requirement) - a run must actually reach the
  # required length before it counts.
  closes = [98.0, 98.0, 103.0, 97.0, 96.0]

  assert structure._last_consecutive_break_index(
    closes, 100.0, 1, above=True,
  ) == 2
  assert structure._last_consecutive_break_index(
    closes, 100.0, 2, above=True,
  ) is None


def test_last_consecutive_break_index_finds_the_completion_bar():
  closes = [98.0, 98.0, 103.0, 104.0, 105.0]

  # required=1: the run reaches length 1 at the first close above price.
  assert structure._last_consecutive_break_index(
    closes, 100.0, 1, above=True,
  ) == 2
  # required=2: the run reaches length 2 one bar later.
  assert structure._last_consecutive_break_index(
    closes, 100.0, 2, above=True,
  ) == 3


def test_last_consecutive_break_index_tracks_the_most_recent_run():
  # Two separate above-price runs - the function reports where the later
  # (most recent) one reached the required length, not the first.
  closes = [103.0, 104.0, 98.0, 105.0, 106.0]

  assert structure._last_consecutive_break_index(
    closes, 100.0, 2, above=True,
  ) == 4
