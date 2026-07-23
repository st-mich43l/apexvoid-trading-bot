from types import SimpleNamespace

import pandas as pd

from app.autotrade import scale_context as context
from app.analysis.types import Break, Leg, Swing


def test_builds_closed_bar_structure_context_from_shared_primitives(monkeypatch):
  index = pd.date_range("2026-07-21", periods=20, freq="1min", tz="UTC")
  frame = pd.DataFrame({
    "open": [4000.0] * 20,
    "high": [4001.0] * 20,
    "low": [3999.0] * 20,
    "close": [4000.5] * 20,
  }, index=index)
  monkeypatch.setattr(
    context,
    "find_swings",
    lambda *args, **kwargs: [Swing(12, "low", 3998.5, ts=index[12])],
  )
  monkeypatch.setattr(
    context,
    "displacement",
    lambda *args, **kwargs: [Leg(16, 18, "up", 3.0)],
  )
  monkeypatch.setattr(
    context,
    "structure_breaks",
    lambda *args, **kwargs: [
      Break("BOS", "down", 3998, 10, index[10]),
      Break("BOS", "up", 4001, 17, index[17]),
    ],
  )

  result = context.build_auto_scale_context(
    {"M1": frame},
    "BUY",
    spot_price=4001.0,
    cfg=SimpleNamespace(
      atr_length=14,
      swing_fractal_n=2,
      zigzag_pct=0,
      zigzag_atr_mult=1,
      displacement_atr_mult=1.5,
      momentum_body_frac=0.6,
    ),
    target_low=4003.5,
    target_high=4004.0,
  )

  assert result is not None
  assert result.structure_swing == 3998.5
  assert result.displacement_direction == "up"
  assert result.displacement_age_bars == 3
  assert result.bos_direction == "up"
  assert result.bos_ts == int(index[17].timestamp())
  assert result.opposing_level_distance_atr == 1.25
  # Counter-direction BOS (down) since this is a BUY - mirrors bos_ts but
  # for the opposite direction, feeding ScaleInTriggerPlanner's P1 gate.
  assert result.counter_bos_ts == int(index[10].timestamp())
  # Every bar has the same high (4001.0) - idxmax picks the first occurrence.
  assert result.extreme_price == 4001.0
  assert result.extreme_ts == int(index[0].timestamp())
  # Last closed bar: close 4000.5, low 3999.0, high 4001.0, range 2.0 ->
  # ATR(14) of a constant-range series is 2.0, minimum = max(0.3, 0.4) =
  # 0.4; close-low=1.5 >= 0.4 and close(4000.5) >= high-0.6*range (3799.8).
  assert result.rejection_confirmed is True


def test_counter_bos_ts_is_none_without_a_counter_direction_break(monkeypatch):
  index = pd.date_range("2026-07-21", periods=20, freq="1min", tz="UTC")
  frame = pd.DataFrame({
    "open": [4000.0] * 20,
    "high": [4001.0] * 20,
    "low": [3999.0] * 20,
    "close": [4000.5] * 20,
  }, index=index)
  monkeypatch.setattr(
    context, "find_swings", lambda *args, **kwargs: [Swing(12, "low", 3998.5)],
  )
  monkeypatch.setattr(context, "displacement", lambda *args, **kwargs: [])
  monkeypatch.setattr(
    context,
    "structure_breaks",
    lambda *args, **kwargs: [Break("BOS", "up", 4001, 17, index[17])],
  )

  result = context.build_auto_scale_context(
    {"M1": frame},
    "BUY",
    spot_price=4001.0,
    cfg=SimpleNamespace(
      atr_length=14, swing_fractal_n=2, zigzag_pct=0, zigzag_atr_mult=1,
      displacement_atr_mult=1.5, momentum_body_frac=0.6,
    ),
  )

  assert result is not None
  assert result.counter_bos_ts is None


def test_extreme_uses_lowest_low_for_a_sell(monkeypatch):
  index = pd.date_range("2026-07-21", periods=10, freq="1min", tz="UTC")
  lows = [3999.0, 3998.0, 3990.0, 3992.0, 3999.5, 3999.0, 3998.0, 3997.0, 3999.0, 3999.5]
  frame = pd.DataFrame({
    "open": [4000.0] * 10,
    "high": [4001.0] * 10,
    "low": lows,
    "close": [4000.0] * 10,
  }, index=index)
  monkeypatch.setattr(
    context, "find_swings", lambda *args, **kwargs: [Swing(2, "high", 4001.0)],
  )
  monkeypatch.setattr(context, "displacement", lambda *args, **kwargs: [])
  monkeypatch.setattr(context, "structure_breaks", lambda *args, **kwargs: [])

  result = context.build_auto_scale_context(
    {"M1": frame},
    "SELL",
    spot_price=3999.0,
    cfg=SimpleNamespace(
      atr_length=5, swing_fractal_n=1, zigzag_pct=0, zigzag_atr_mult=0,
      displacement_atr_mult=1.5, momentum_body_frac=0.6,
    ),
  )

  assert result is not None
  assert result.extreme_price == 3990.0
  assert result.extreme_ts == int(index[2].timestamp())
