from types import SimpleNamespace

import pandas as pd

from app import auto_scale_context as context
from app.auto_scalp_gate import AutoScalpDecision, AutoScalpRail
from app.pa_types import Break, Leg, Swing


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
    lambda *args, **kwargs: [Break("BOS", "up", 4001, 17, index[17])],
  )
  target = AutoScalpRail(
    "resistance", 4003.5, 4004.0, 4003.75, 3, 8, ("M5",), ("M5 high",)
  )
  decision = AutoScalpDecision("candidate", "BUY", target=target)

  result = context.build_auto_scale_context(
    {"M1": frame},
    decision,
    spot_price=4001.0,
    cfg=SimpleNamespace(
      atr_length=14,
      swing_fractal_n=2,
      zigzag_pct=0,
      zigzag_atr_mult=1,
      displacement_atr_mult=1.5,
      momentum_body_frac=0.6,
    ),
  )

  assert result is not None
  assert result.structure_swing == 3998.5
  assert result.displacement_direction == "up"
  assert result.displacement_age_bars == 3
  assert result.bos_direction == "up"
  assert result.bos_ts == int(index[17].timestamp())
  assert result.opposing_level_distance_atr == 1.25
