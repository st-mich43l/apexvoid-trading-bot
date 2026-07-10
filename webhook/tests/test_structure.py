import pandas as pd

from app import structure


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


def test_fvg_sweep_and_retest_fire_on_crafted_window():
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
  assert structure.liquidity_sweep(df, 102) == "buy"

  retest = structure.find_retest(df, 102)
  assert retest is not None
  assert retest.kind == "retest_support"


def test_flat_window_has_no_gap_sweep_or_retest():
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 99, 100),
    (100, 101, 99, 100),
    (100, 101, 99, 100),
  ])

  assert structure.fvg(df) == []
  assert structure.liquidity_sweep(df, 100) is None
  assert structure.find_retest(df, 100) is None
