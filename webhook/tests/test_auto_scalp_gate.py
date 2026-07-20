import pandas as pd
import pytest

from app import auto_scalp_gate as gate


def _frame(
  periods: int,
  freq: str,
  *,
  base: float = 100.5,
) -> pd.DataFrame:
  index = pd.date_range("2026-07-20", periods=periods, freq=freq, tz="UTC")
  wave = [0.15 if index % 2 == 0 else -0.15 for index in range(periods)]
  close = [base + value for value in wave]
  return pd.DataFrame({
    "open": [value - 0.05 for value in close],
    "high": [value + 0.35 for value in close],
    "low": [value - 0.35 for value in close],
    "close": close,
    "volume": [1.0] * periods,
  }, index=index)


def _frames(last: dict[str, float] | None = None) -> dict[str, pd.DataFrame]:
  m1 = _frame(24, "1min")
  if last:
    for field, value in last.items():
      m1.loc[m1.index[-1], field] = value
  return {
    "M1": m1,
    "M5": _frame(30, "5min"),
    "M15": _frame(20, "15min"),
  }


def _rail(
  role: str,
  level: float,
  *,
  touches: int = 2,
  tf: tuple[str, ...] = ("M5",),
) -> gate.AutoScalpRail:
  return gate.AutoScalpRail(
    role,
    level - 0.1,
    level + 0.1,
    level,
    touches,
    float(touches + 2),
    tf,
    (f"M5 {role}",),
  )


def test_support_rejection_creates_buy_with_30_plus_pips_room(monkeypatch):
  rails = [
    _rail("support", 100.0, touches=3),
    _rail("support", 101.0),
    _rail("resistance", 104.0),
  ]
  monkeypatch.setattr(gate, "build_auto_scalp_rails", lambda m5, m15: rails)
  frames = _frames({
    "open": 100.25,
    "high": 100.75,
    "low": 99.85,
    "close": 100.60,
  })

  decision = gate.evaluate_auto_scalp_gate(
    frames,
    symbol="XAU",
    spot_price=100.60,
  )

  assert decision.state == "candidate"
  assert decision.direction == "BUY"
  assert decision.trigger == "range_rejection"
  assert decision.rail == rails[0]
  assert decision.target == rails[2]
  assert decision.target_room_pips == pytest.approx(33.0)


def test_same_role_micro_level_does_not_block_opposite_target(monkeypatch):
  support = _rail("support", 100.0, touches=3)
  same_role_above = _rail("support", 101.0, touches=4)
  resistance = _rail("resistance", 104.0)
  monkeypatch.setattr(
    gate,
    "build_auto_scalp_rails",
    lambda m5, m15: [support, same_role_above, resistance],
  )
  frames = _frames({
    "open": 100.30,
    "high": 100.75,
    "low": 99.90,
    "close": 100.55,
  })

  decision = gate.evaluate_auto_scalp_gate(
    frames,
    symbol="XAU",
    spot_price=100.55,
  )

  assert decision.state == "candidate"
  assert decision.target is resistance


def test_target_below_30_pips_is_blocked(monkeypatch):
  support = _rail("support", 100.0, touches=3)
  resistance = _rail("resistance", 102.5)
  monkeypatch.setattr(
    gate,
    "build_auto_scalp_rails",
    lambda m5, m15: [support, resistance],
  )
  frames = _frames({
    "open": 100.25,
    "high": 100.70,
    "low": 99.85,
    "close": 100.55,
  })

  decision = gate.evaluate_auto_scalp_gate(
    frames,
    symbol="XAU",
    spot_price=100.55,
  )

  assert decision.state == "target_blocked"
  assert decision.target_room_pips == pytest.approx(18.5)


def test_resistance_rejection_creates_sell(monkeypatch):
  support = _rail("support", 96.0)
  resistance = _rail("resistance", 100.0, touches=3)
  monkeypatch.setattr(
    gate,
    "build_auto_scalp_rails",
    lambda m5, m15: [support, resistance],
  )
  frames = _frames({
    "open": 99.75,
    "high": 100.15,
    "low": 99.30,
    "close": 99.45,
  })

  decision = gate.evaluate_auto_scalp_gate(
    frames,
    symbol="XAU",
    spot_price=99.45,
  )

  assert decision.state == "candidate"
  assert decision.direction == "SELL"
  assert decision.target == support


def test_raw_current_rail_breakout_waits_for_retest(monkeypatch):
  resistance = _rail("resistance", 100.0, touches=3)
  monkeypatch.setattr(
    gate,
    "build_auto_scalp_rails",
    lambda m5, m15: [resistance],
  )
  frames = _frames({
    "open": 99.50,
    "high": 100.85,
    "low": 99.40,
    "close": 100.75,
  })

  decision = gate.evaluate_auto_scalp_gate(
    frames,
    symbol="XAU",
    spot_price=100.75,
  )

  assert decision.state in {"waiting_for_touch", "waiting_rejection"}
  assert decision.direction is None


def test_m5_trend_blocks_only_countertrend_direction():
  m5 = _frame(20, "5min")
  m5["close"] = [100 + index * 0.5 for index in range(len(m5))]
  m5["open"] = m5["close"] - 0.2
  m5["high"] = m5["close"] + 0.3
  m5["low"] = m5["open"] - 0.3
  atr = gate._atr(m5)

  assert gate._m5_countertrend_blocks(m5, "SELL", atr) is True
  assert gate._m5_countertrend_blocks(m5, "BUY", atr) is False


def test_high_volatility_cannot_widen_execution_rail_beyond_16_pips():
  points = [
    ("support", 100.0, 1, 2.0, "M5 swing-low"),
    ("support", 100.4, 2, 2.0, "M15 swing-low"),
  ]

  rails = gate._cluster_rails(points, "support", atr=20.0)

  assert len(rails) == 1
  assert rails[0].high - rails[0].low <= 1.6 + 1e-9


def test_gate_has_no_forming_signal_or_market_map_dependency():
  forbidden = {
    "app.analysis",
    "app.detectors",
    "app.market_map",
  }
  module_globals = {
    getattr(value, "__module__", "")
    for value in gate.__dict__.values()
  }
  assert not forbidden & module_globals
  source = open(gate.__file__, encoding="utf-8").read()
  assert "app.detectors" not in source
  assert "app.analysis" not in source
  assert "app.market_map" not in source
  assert "forming" in gate.evaluate_auto_scalp_gate.__doc__
