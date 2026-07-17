from types import SimpleNamespace

import pandas as pd

from app.scalp_ranges import build_scalp_structure


def _cfg(**overrides):
  values = {
    "range_scalp_lookback": 36,
    "range_scalp_cluster_atr": 0.20,
    "range_scalp_min_touches": 3,
    "range_scalp_min_wick_frac": 0.35,
    "range_scalp_entry_tol_atr": 0.15,
    "range_scalp_min_width_atr": 1.2,
    "range_scalp_max_width_atr": 6.0,
    "range_scalp_min_room_atr": 1.0,
    "range_scalp_break_closes": 2,
    "round_step": 5.0,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def _range_df() -> pd.DataFrame:
  rows = [
    (105, 107, 103, 106, 100),
    (106, 110, 105, 106, 100),
    (105, 107, 103, 105, 100),
    (104, 105, 100, 104, 100),
    (104, 107, 103, 106, 100),
    (106, 110, 105, 106, 100),
    (105, 107, 103, 105, 100),
    (104, 105, 100, 104, 100),
    (104, 107, 103, 106, 100),
    (106, 110, 105, 106, 100),
    (104, 105, 100, 104, 100),
    (106, 111, 105, 106, 100),
  ]
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-07-17", periods=len(rows), freq="5min", tz="UTC"),
  ).astype(float)


def test_builds_two_sided_range_from_separate_wick_touch_episodes():
  df = _range_df()
  atr = pd.Series([2.0] * len(df), index=df.index)

  barriers, scalp_range = build_scalp_structure(df, atr, [], [], None, _cfg())
  lower = min(
    (barrier for barrier in barriers if barrier.side == "support"),
    key=lambda barrier: abs(barrier.level - 100),
  )
  upper = min(
    (barrier for barrier in barriers if barrier.side == "resistance"),
    key=lambda barrier: abs(barrier.level - 110),
  )

  assert lower.level == 100
  assert lower.touches == 3
  assert lower.wick_rejections == 3
  assert upper.level == 110
  assert upper.touches == 3
  assert upper.wick_rejections == 3
  assert scalp_range is not None
  assert scalp_range.lower == lower
  assert scalp_range.upper == upper
  assert scalp_range.eq == 105
  assert scalp_range.width_atr == 5


def test_consecutive_candles_at_same_edge_count_as_one_episode():
  df = _range_df()
  df.iloc[2] = [106, 110, 105, 106, 100]
  atr = pd.Series([2.0] * len(df), index=df.index)

  barriers, _ = build_scalp_structure(df, atr, [], [], None, _cfg())
  upper = min(
    (barrier for barrier in barriers if barrier.side == "resistance"),
    key=lambda barrier: abs(barrier.level - 110),
  )

  assert upper.touches == 3


def test_two_accepted_closes_invalidate_resistance_barrier():
  df = _range_df()
  df.iloc[10] = [110.5, 112, 109, 111, 100]
  df.iloc[11] = [111, 113, 110, 112, 100]
  atr = pd.Series([2.0] * len(df), index=df.index)

  barriers, scalp_range = build_scalp_structure(df, atr, [], [], None, _cfg())

  assert all(
    barrier.side != "resistance" or abs(barrier.level - 110) > 0.5
    for barrier in barriers
  )
  assert scalp_range is None


def test_builder_is_deterministic_and_does_not_mutate_input():
  df = _range_df()
  original = df.copy(deep=True)
  atr = pd.Series([2.0] * len(df), index=df.index)

  first = build_scalp_structure(df, atr, [], [], None, _cfg())
  second = build_scalp_structure(df, atr, [], [], None, _cfg())

  assert first == second
  pd.testing.assert_frame_equal(df, original)
