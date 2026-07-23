import pandas as pd
import pytest

from app.autotrade import gate


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
  m1 = _frame(80, "1min")
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


def _box(
  support: gate.AutoScalpRail,
  resistance: gate.AutoScalpRail,
) -> gate.AutoScalpBox:
  return gate.AutoScalpBox(
    "xau-test-box",
    support,
    resistance,
    (resistance.low - support.high) / 0.1,
    inside_ratio=0.95,
    efficiency=0.1,
  )


def test_support_rejection_creates_buy_with_full_50_pip_target(monkeypatch):
  rails = [
    _rail("support", 100.0, touches=3),
    _rail("support", 101.0),
    _rail("resistance", 106.3),
  ]
  monkeypatch.setattr(
    gate,
    "_m1_consolidation_box",
    lambda m1, atr, symbol: _box(rails[0], rails[2]),
  )
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
  assert decision.target_room_pips == pytest.approx(56.0)
  assert decision.full_tp_pips == 50
  assert decision.box is not None
  assert decision.sweep_low == pytest.approx(99.85)
  assert decision.sweep_high is None


def test_support_rejection_uses_70_pips_when_box_has_room(monkeypatch):
  support = _rail("support", 100.0, touches=3)
  resistance = _rail("resistance", 108.3)
  monkeypatch.setattr(
    gate,
    "_m1_consolidation_box",
    lambda m1, atr, symbol: _box(support, resistance),
  )
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
  assert decision.target_room_pips == pytest.approx(76.0)
  assert decision.full_tp_pips == 70


def test_same_role_micro_level_does_not_block_opposite_target(monkeypatch):
  support = _rail("support", 100.0, touches=3)
  same_role_above = _rail("support", 101.0, touches=4)
  resistance = _rail("resistance", 106.3)
  monkeypatch.setattr(
    gate,
    "_m1_consolidation_box",
    lambda m1, atr, symbol: _box(support, resistance),
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


def test_target_below_50_pips_plus_buffer_is_blocked(monkeypatch):
  support = _rail("support", 100.0, touches=3)
  resistance = _rail("resistance", 105.7)
  monkeypatch.setattr(
    gate,
    "_m1_consolidation_box",
    lambda m1, atr, symbol: _box(support, resistance),
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
  assert decision.target_room_pips == pytest.approx(50.5)
  assert decision.full_tp_pips is None


def test_resistance_rejection_creates_sell(monkeypatch):
  support = _rail("support", 91.6)
  resistance = _rail("resistance", 100.0, touches=3)
  monkeypatch.setattr(
    gate,
    "_m1_consolidation_box",
    lambda m1, atr, symbol: _box(support, resistance),
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
  assert decision.full_tp_pips == 70
  assert decision.sweep_low is None
  assert decision.sweep_high == pytest.approx(100.15)


def test_single_rail_cannot_form_a_box(monkeypatch):
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

  assert decision.state == "waiting_for_box"
  assert decision.direction is None


def test_valid_box_allows_sell_at_top_even_when_m5_is_rising(monkeypatch):
  support = _rail("support", 91.6)
  resistance = _rail("resistance", 100.0, touches=3)
  monkeypatch.setattr(
    gate,
    "_m1_consolidation_box",
    lambda m1, atr, symbol: _box(support, resistance),
  )
  m5 = _frame(20, "5min")
  m5["close"] = [90 + index * 0.5 for index in range(len(m5))]
  m5["open"] = m5["close"] - 0.2
  m5["high"] = m5["close"] + 0.3
  m5["low"] = m5["open"] - 0.3
  frames = _frames({
    "open": 99.75,
    "high": 100.15,
    "low": 99.30,
    "close": 99.45,
  })
  frames["M5"] = m5

  decision = gate.evaluate_auto_scalp_gate(
    frames,
    symbol="XAU",
    spot_price=99.45,
  )

  assert decision.state == "candidate"
  assert decision.direction == "SELL"


def test_two_m1_closes_outside_retires_the_box(monkeypatch):
  support = _rail("support", 100.0)
  resistance = _rail("resistance", 106.3)
  monkeypatch.setattr(
    gate,
    "_m1_consolidation_box",
    lambda m1, atr, symbol: _box(support, resistance),
  )
  frames = _frames()
  for index, close in zip(frames["M1"].index[-2:], (106.9, 107.0)):
    frames["M1"].loc[index, ["open", "high", "low", "close"]] = [
      close - 0.2,
      close + 0.2,
      close - 0.3,
      close,
    ]

  decision = gate.evaluate_auto_scalp_gate(frames, symbol="XAU")

  assert decision.state == "box_broken"
  assert decision.box is not None


def test_one_m5_close_outside_retires_the_box(monkeypatch):
  support = _rail("support", 100.0)
  resistance = _rail("resistance", 106.3)
  monkeypatch.setattr(
    gate,
    "_m1_consolidation_box",
    lambda m1, atr, symbol: _box(support, resistance),
  )
  frames = _frames()
  frames["M5"].loc[
    frames["M5"].index[-1],
    ["open", "high", "low", "close"],
  ] = [106.5, 107.2, 106.4, 107.0]

  decision = gate.evaluate_auto_scalp_gate(frames, symbol="XAU")

  assert decision.state == "box_broken"


def test_m1_consolidation_box_requires_repeated_two_edge_auctions():
  cycle = [100.4, 102.0, 104.0, 106.0, 107.6, 105.5]
  close = (cycle * 11)[:61]
  frame = pd.DataFrame({
    "open": [value - 0.1 for value in close],
    "high": [value + 0.4 for value in close],
    "low": [value - 0.4 for value in close],
    "close": close,
  }, index=pd.date_range(
    "2026-07-20",
    periods=len(close),
    freq="1min",
    tz="UTC",
  ))

  box = gate._m1_consolidation_box(
    frame,
    gate._atr(frame),
    "XAU",
  )

  assert box is not None
  assert 55 <= box.width_pips <= 120
  assert box.lower.touches >= 2
  assert box.upper.touches >= 2
  assert box.inside_ratio >= 0.82


def test_missing_and_insufficient_frame_states_carry_reasons():
  assert gate.evaluate_auto_scalp_gate(
    {}, symbol="XAU", spot_price=100.0,
  ).reasons
  assert gate.evaluate_auto_scalp_gate(
    {"M1": _frame(5, "1min"), "M5": _frame(5, "5min"), "M15": _frame(5, "15min")},
    symbol="XAU",
    spot_price=100.0,
  ).reasons


def test_invalid_atr_state_carries_reasons():
  flat_index = pd.date_range("2026-07-20", periods=80, freq="1min", tz="UTC")
  flat = pd.DataFrame({
    "open": [100.0] * 80,
    "high": [100.0] * 80,
    "low": [100.0] * 80,
    "close": [100.0] * 80,
  }, index=flat_index)
  decision = gate.evaluate_auto_scalp_gate(
    {"M1": flat, "M5": flat, "M15": flat},
    symbol="XAU",
    spot_price=100.0,
  )
  assert decision.state == "invalid_atr"
  assert decision.reasons


def test_waiting_for_box_state_carries_reasons():
  frames = _frames({
    "open": 99.50, "high": 100.85, "low": 99.40, "close": 100.75,
  })
  decision = gate.evaluate_auto_scalp_gate(frames, symbol="XAU", spot_price=100.75)
  assert decision.state == "waiting_for_box"
  assert decision.reasons


def test_target_blocked_state_carries_reasons(monkeypatch):
  support = _rail("support", 100.0, touches=3)
  resistance = _rail("resistance", 105.7)
  monkeypatch.setattr(
    gate, "_m1_consolidation_box", lambda m1, atr, symbol: _box(support, resistance),
  )
  frames = _frames({
    "open": 100.25, "high": 100.70, "low": 99.85, "close": 100.55,
  })
  decision = gate.evaluate_auto_scalp_gate(frames, symbol="XAU", spot_price=100.55)
  assert decision.state == "target_blocked"
  assert decision.reasons


def test_entry_moved_state_carries_reasons(monkeypatch):
  support = _rail("support", 100.0, touches=3)
  resistance = _rail("resistance", 106.3)
  monkeypatch.setattr(
    gate, "_m1_consolidation_box", lambda m1, atr, symbol: _box(support, resistance),
  )
  frames = _frames({
    "open": 100.25, "high": 100.75, "low": 99.85, "close": 100.60,
  })
  decision = gate.evaluate_auto_scalp_gate(frames, symbol="XAU", spot_price=105.0)
  assert decision.state == "entry_moved"
  assert decision.reasons


def test_waiting_rejection_and_waiting_for_touch_states_carry_reasons(monkeypatch):
  support = _rail("support", 100.0, touches=3)
  resistance = _rail("resistance", 106.3)
  monkeypatch.setattr(
    gate, "_m1_consolidation_box", lambda m1, atr, symbol: _box(support, resistance),
  )
  frames = _frames()  # no rejection candle at the last bar -> no trigger
  decision = gate.evaluate_auto_scalp_gate(frames, symbol="XAU", spot_price=100.5)
  assert decision.state in ("waiting_rejection", "waiting_for_touch")
  assert decision.reasons


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
