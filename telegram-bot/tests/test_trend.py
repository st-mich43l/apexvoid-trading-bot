import numpy as np
import pandas as pd
import pytest

from app.autotrade import trend
from app.autotrade.gate import AutoScalpBox, AutoScalpDecision, AutoScalpRail
from app.analysis.types import Leg, SessionLevel, Swing, Zone
from app.core.config import settings


def _flat_frame(
  periods: int,
  freq: str,
  *,
  base: float = 4000.0,
) -> pd.DataFrame:
  index = pd.date_range("2026-07-20", periods=periods, freq=freq, tz="UTC")
  return pd.DataFrame({
    "open": [base] * periods,
    "high": [base + 0.3] * periods,
    "low": [base - 0.3] * periods,
    "close": [base] * periods,
  }, index=index)


def _flat_m1(periods: int = 61, base: float = 4000.0) -> pd.DataFrame:
  return _flat_frame(periods, "1min", base=base)


def _staircase(pivots: list[float], seg_len: int = 6) -> list[float]:
  closes: list[float] = []
  for start, end in zip(pivots, pivots[1:]):
    closes.extend(np.linspace(start, end, seg_len, endpoint=False).tolist())
  closes.append(pivots[-1])
  return closes


def _pivots_to_m1(pivots: list[float], seg_len: int = 6) -> pd.DataFrame:
  closes = _staircase(pivots, seg_len)
  index = pd.date_range(
    "2026-07-20", periods=len(closes), freq="1min", tz="UTC",
  )
  close = pd.Series(closes, index=index)
  return pd.DataFrame({
    "open": close - 0.05,
    "high": close + 0.15,
    "low": close - 0.15,
    "close": close,
  }, index=index)


def _uptrend_m1(seg_len: int = 6, legs: int = 6) -> pd.DataFrame:
  """A clean staircase uptrend with *accelerating* leg amplitude:
  alternating higher-highs/higher-lows whose swing size grows leg over
  leg, so both the structural BOS count and the ATR itself genuinely
  expand toward the tail (real, non-monkeypatched ``find_swings``/
  ``structure_breaks``/ATR output - this is what should classify as
  "trend").
  """
  pivots = [4000.0]
  up, down = 6.0, 3.0
  for _ in range(legs):
    pivots.append(pivots[-1] + up)
    pivots.append(pivots[-1] - down)
    up += 1.8
    down += 0.9
  return _pivots_to_m1(pivots, seg_len)


def _uniform_uptrend_m1(seg_len: int = 6, legs: int = 6) -> pd.DataFrame:
  """Same staircase shape/direction as ``_uptrend_m1`` (real BOS output,
  same swing structure) but with identical-size legs throughout, so ATR
  fully stabilizes flat by the tail window instead of expanding.
  """
  pivots = [4000.0]
  for _ in range(legs):
    pivots.append(pivots[-1] + 6.0)
    pivots.append(pivots[-1] - 3.0)
  return _pivots_to_m1(pivots, seg_len)


def _rail(role: str, low: float, high: float, level: float) -> AutoScalpRail:
  return AutoScalpRail(role, low, high, level, 3, 5.0, ("M1",), ("m1",))


def _box_breakout_replay_frame(
  *,
  obstacle: float = 4122.24,
  valid_retest: bool = True,
  data_gap: bool = False,
) -> pd.DataFrame:
  frame = _flat_m1(65, base=4118.0)
  frame.iloc[-8] = [4119.0, obstacle, 4118.7, 4119.2]
  frame.iloc[-7] = [4119.2, 4120.2, 4118.8, 4119.6]
  frame.iloc[-6] = [4119.6, 4120.3, 4119.4, 4120.0]
  frame.iloc[-2] = [4120.84, 4121.95, 4120.52, 4121.76]
  if valid_retest:
    frame.iloc[-1] = [4121.05, 4121.65, 4120.75, 4121.50]
  else:
    # Incident candle: only a tiny lower wick and a large upper wick.
    frame.iloc[-1] = [4121.18, 4122.28, 4121.06, 4121.46]
  if data_gap:
    index = frame.index.to_list()
    index[-1] += pd.Timedelta(minutes=1)
    frame.index = pd.DatetimeIndex(index)
  return frame


def _box_breakout_context() -> tuple[AutoScalpDecision, trend.RegimeInfo]:
  lower = _rail("support", 4113.73, 4113.73, 4113.73)
  upper = _rail("resistance", 4120.80, 4120.80, 4120.80)
  box = AutoScalpBox("xau-4113-4121", lower, upper, 70.7, 0.9, 0.2)
  return (
    AutoScalpDecision("box_broken", box=box),
    trend.RegimeInfo("breakout", "up", 0, 1.1, True, 1, ()),
  )


def _mirrored_sell_breakout_replay() -> tuple[
  pd.DataFrame,
  AutoScalpDecision,
  trend.RegimeInfo,
]:
  pivot = 4121.50
  source = _box_breakout_replay_frame()
  frame = pd.DataFrame(index=source.index)
  frame["open"] = 2 * pivot - source["open"]
  frame["high"] = 2 * pivot - source["low"]
  frame["low"] = 2 * pivot - source["high"]
  frame["close"] = 2 * pivot - source["close"]
  lower = _rail("support", 4122.20, 4122.20, 4122.20)
  upper = _rail("resistance", 4129.27, 4129.27, 4129.27)
  box = AutoScalpBox("xau-4122-4129", lower, upper, 70.7, 0.9, 0.2)
  return (
    frame,
    AutoScalpDecision("box_broken", box=box),
    trend.RegimeInfo("breakout", "down", 0, 1.1, True, 1, ()),
  )


class _NoOpCfg:
  """Delegates every attribute lookup to the real settings object, so tests
  can flip one field without needing to hand-roll every duck-typed cfg
  attribute the analysis toolkit expects (session hours, atr_length, ...).
  """

  def __getattr__(self, name):
    return getattr(settings, name)


# ---------------------------------------------------------------------------
# classify_regime
# ---------------------------------------------------------------------------


def test_clear_uptrend_with_expanding_atr_classifies_as_trend():
  m1 = _uptrend_m1()
  frames = {
    "M1": m1,
    "M5": _flat_frame(30, "5min"),
    "M15": _flat_frame(20, "15min"),
  }
  regime = trend.classify_regime(frames, AutoScalpDecision("waiting_for_box"), settings)

  assert regime.state == "trend"
  assert regime.direction == "up"
  assert regime.bos_count >= settings.trend_min_bos
  assert regime.htf_aligned is True


def test_same_shape_with_flat_atr_does_not_classify_as_trend(monkeypatch):
  monkeypatch.setattr(settings, "trend_atr_expansion", 1.15)
  m1 = _uniform_uptrend_m1()
  frames = {
    "M1": m1,
    "M5": _flat_frame(30, "5min"),
    "M15": _flat_frame(20, "15min"),
  }
  regime = trend.classify_regime(frames, AutoScalpDecision("waiting_for_box"), settings)

  assert regime.state != "trend"
  assert regime.atr_ratio < settings.trend_atr_expansion


def test_accepted_box_break_classifies_as_breakout_even_with_narrow_window():
  periods = 65
  opens = [4000.0] * periods
  highs = [4000.3] * periods
  lows = [3999.7] * periods
  closes = [4000.0] * periods
  # Displacement-grade breakout candle on the final bar.
  opens[-1] = 4003.0
  closes[-1] = 4004.5
  highs[-1] = 4004.6
  lows[-1] = 4002.9
  index = pd.date_range("2026-07-20", periods=periods, freq="1min", tz="UTC")
  m1 = pd.DataFrame(
    {"open": opens, "high": highs, "low": lows, "close": closes}, index=index,
  )
  frames = {
    "M1": m1,
    "M5": _flat_frame(30, "5min"),
    "M15": _flat_frame(20, "15min"),
  }
  lower = _rail("support", 3996.9, 3997.1, 3997.0)
  upper = _rail("resistance", 4002.9, 4003.1, 4003.0)
  box = AutoScalpBox("xau-test", lower, upper, 60.0, 0.9, 0.2)
  decision = AutoScalpDecision("box_broken", box=box)

  regime = trend.classify_regime(frames, decision, settings)

  # The box is far too narrow to independently qualify via the trend
  # height check - this proves breakout classification doesn't get stuck
  # behind that unrelated criterion (the "anti-stuck" case).
  assert regime.state == "breakout"
  assert regime.direction == "up"
  assert regime.box_break_age_bars == 0


def test_incident_replay_rejects_upper_wick_chase():
  m1 = _box_breakout_replay_frame(valid_retest=False)
  box_decision, regime = _box_breakout_context()

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    box_decision,
    symbol="XAU",
    spot_price=4121.55,
    cfg=settings,
  )

  assert decision.state == "retest_rejected"
  assert "wick rejection" in decision.reasons[0]


def test_incident_replay_rejects_missing_m1_bar():
  m1 = _box_breakout_replay_frame(data_gap=True)
  box_decision, regime = _box_breakout_context()

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    box_decision,
    symbol="XAU",
    spot_price=4121.55,
    cfg=settings,
  )

  assert decision.state == "data_gap"
  assert "missing M1 bar" in decision.reasons[0]


def test_incident_replay_rejects_nearby_prebreak_barrier(monkeypatch):
  m1 = _box_breakout_replay_frame()
  box_decision, regime = _box_breakout_context()
  monkeypatch.setattr(settings, "trend_breakout_min_room_pips", 35)

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    box_decision,
    symbol="XAU",
    spot_price=4121.55,
    cfg=settings,
  )

  assert decision.state == "target_blocked"
  assert "6.9 pips room" in decision.reasons[0]
  assert "4122.24" in decision.reasons[0]


def test_box_breakout_retest_with_room_can_trade(monkeypatch):
  m1 = _box_breakout_replay_frame(obstacle=4126.05)
  box_decision, regime = _box_breakout_context()
  monkeypatch.setattr(settings, "trend_breakout_min_room_pips", 35)
  monkeypatch.setattr(trend, "session_levels", lambda df, cfg: [])
  monkeypatch.setattr(trend, "previous_week_levels", lambda df: [])
  monkeypatch.setattr(trend, "displacement", lambda *args, **kwargs: [])

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    box_decision,
    symbol="XAU",
    spot_price=4121.55,
    cfg=settings,
  )

  assert decision.state == "candidate"
  assert decision.mode == "box_breakout"
  assert 45 in decision.targets_pips
  assert len(decision.targets_pips) == len(trend._FALLBACK_TP_PIPS)


def test_sell_breakout_has_symmetric_prior_barrier_gate(monkeypatch):
  m1, box_decision, regime = _mirrored_sell_breakout_replay()
  monkeypatch.setattr(settings, "trend_breakout_min_room_pips", 35)

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    box_decision,
    symbol="XAU",
    spot_price=4121.45,
    cfg=settings,
  )

  assert decision.state == "target_blocked"
  assert "6.9 pips room" in decision.reasons[0]
  assert "4120.76" in decision.reasons[0]


# ---------------------------------------------------------------------------
# Mode A: trend pullback
# ---------------------------------------------------------------------------


def _mode_a_frame(rejection: bool = True) -> pd.DataFrame:
  periods = 61
  opens = [4000.0] * periods
  highs = [4000.3] * periods
  lows = [3999.7] * periods
  closes = [4000.0] * periods
  opens[0], highs[0], lows[0], closes[0] = 3990.0, 3990.2, 3989.8, 3990.0
  if rejection:
    opens[-1], closes[-1], highs[-1], lows[-1] = 3999.6, 3999.9, 4000.0, 3999.0
  else:
    opens[-1], closes[-1], highs[-1], lows[-1] = 3999.9, 3999.2, 4000.0, 3999.0
  index = pd.date_range("2026-07-20", periods=periods, freq="1min", tz="UTC")
  return pd.DataFrame(
    {"open": opens, "high": highs, "low": lows, "close": closes}, index=index,
  )


def _patch_mode_a_primitives(monkeypatch, leg: Leg, zone: Zone) -> None:
  monkeypatch.setattr(trend, "displacement", lambda *a, **k: [leg])
  monkeypatch.setattr(trend, "supply_demand", lambda *a, **k: [zone])
  monkeypatch.setattr(trend, "session_levels", lambda df, cfg: [])
  monkeypatch.setattr(trend, "previous_week_levels", lambda df: [])
  monkeypatch.setattr(trend, "find_swings", lambda *a, **k: [])


def test_mode_a_pullback_with_rejection_candle_fires_candidate(monkeypatch):
  m1 = _mode_a_frame(rejection=True)
  leg = Leg(start=0, end=30, direction="up", size=20.0)
  zone = Zone(bottom=3999.0, top=4000.0, side="demand", origin_index=0)
  _patch_mode_a_primitives(monkeypatch, leg, zone)

  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())
  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    AutoScalpDecision("waiting_for_box"),
    symbol="XAU",
    spot_price=None,
    cfg=settings,
  )

  assert decision.state == "candidate"
  assert decision.mode == "pullback"
  assert decision.direction == "BUY"
  assert decision.entry_zone == (3999.0, 4000.0)
  # Origin-of-leg swing extreme, passed through with no modification.
  assert decision.structure_swing == 3989.8


def test_mode_a_without_rejection_candle_is_no_setup(monkeypatch):
  m1 = _mode_a_frame(rejection=False)
  leg = Leg(start=0, end=30, direction="up", size=20.0)
  zone = Zone(bottom=3999.0, top=4000.0, side="demand", origin_index=0)
  _patch_mode_a_primitives(monkeypatch, leg, zone)

  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())
  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    AutoScalpDecision("waiting_for_box"),
    symbol="XAU",
    spot_price=None,
    cfg=settings,
  )

  assert decision.state == "no_setup"


def test_mode_a_stop_context_passes_through_without_pip_floor_clamp(monkeypatch):
  """A tiny (<40 pip) implied stop distance must reach TrendDecision
  untouched - any floor/clamp is StructureStopPlanner's job on the C#
  side, not Python's.
  """
  m1 = _mode_a_frame(rejection=True)
  # Origin bar low sits just 5 pips (0.5) below the zone - well under a
  # typical 40-pip floor.
  m1 = m1.copy()
  m1.iloc[0, m1.columns.get_loc("low")] = 3998.5
  leg = Leg(start=0, end=30, direction="up", size=20.0)
  zone = Zone(bottom=3999.0, top=4000.0, side="demand", origin_index=0)
  _patch_mode_a_primitives(monkeypatch, leg, zone)

  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())
  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    AutoScalpDecision("waiting_for_box"),
    symbol="XAU",
    spot_price=None,
    cfg=settings,
  )

  assert decision.state == "candidate"
  # Exactly the raw origin-bar low - no widening applied in Python.
  assert decision.structure_swing == 3998.5
  assert decision.atr is not None


# ---------------------------------------------------------------------------
# Mode B: displacement break / breakout-continuation
# ---------------------------------------------------------------------------


def _mode_b_frame(*, accepted: bool = True) -> pd.DataFrame:
  periods = 61
  opens = [4000.0] * periods
  highs = [4000.3] * periods
  lows = [3999.7] * periods
  closes = [4000.0] * periods
  if accepted:
    opens[-1], closes[-1], highs[-1], lows[-1] = 4004.0, 4006.5, 4006.6, 4003.9
  else:
    opens[-1], closes[-1], highs[-1], lows[-1] = 4005.0, 4005.1, 4005.2, 4004.9
  index = pd.date_range("2026-07-20", periods=periods, freq="1min", tz="UTC")
  return pd.DataFrame(
    {"open": opens, "high": highs, "low": lows, "close": closes}, index=index,
  )


def _patch_mode_b_primitives(monkeypatch, swings: list[Swing]) -> None:
  monkeypatch.setattr(trend, "find_swings", lambda *a, **k: swings)
  monkeypatch.setattr(trend, "displacement", lambda *a, **k: [])
  monkeypatch.setattr(trend, "supply_demand", lambda *a, **k: [])
  monkeypatch.setattr(trend, "session_levels", lambda df, cfg: [])
  monkeypatch.setattr(trend, "previous_week_levels", lambda df: [])


def _mode_b_swings() -> list[Swing]:
  return [
    Swing(index=20, kind="low", price=3995.0),
    Swing(index=50, kind="high", price=4005.0),
  ]


def test_mode_b_accepted_break_fires_candidate(monkeypatch):
  m1 = _mode_b_frame(accepted=True)
  _patch_mode_b_primitives(monkeypatch, _mode_b_swings())
  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    AutoScalpDecision("waiting_for_box"),
    symbol="XAU",
    spot_price=4005.1,
    cfg=settings,
  )

  assert decision.state == "candidate"
  assert decision.mode == "breakout_continuation"
  assert decision.direction == "BUY"
  assert decision.structure_swing == 3995.0


def test_mode_b_break_without_displacement_acceptance_does_not_fire(monkeypatch):
  m1 = _mode_b_frame(accepted=False)
  _patch_mode_b_primitives(monkeypatch, _mode_b_swings())
  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    AutoScalpDecision("waiting_for_box"),
    symbol="XAU",
    spot_price=4005.1,
    cfg=settings,
  )

  assert decision.state == "no_setup"


def test_mode_b_opposing_major_level_inside_buffer_blocks_entry(monkeypatch):
  m1 = _mode_b_frame(accepted=True)
  _patch_mode_b_primitives(monkeypatch, _mode_b_swings())
  opposing = SessionLevel("PDH", 4005.5, m1.index[0], False)
  monkeypatch.setattr(trend, "session_levels", lambda df, cfg: [opposing])
  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    AutoScalpDecision("waiting_for_box"),
    symbol="XAU",
    spot_price=4005.1,
    cfg=settings,
  )

  assert decision.state == "no_setup"
  assert any("opposing" in reason for reason in decision.reasons)


def test_mode_b_chase_disabled_with_no_pullback_does_not_fire(monkeypatch):
  m1 = _mode_b_frame(accepted=True)
  _patch_mode_b_primitives(monkeypatch, _mode_b_swings())
  monkeypatch.setattr(settings, "trend_allow_chase", False)
  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    AutoScalpDecision("waiting_for_box"),
    symbol="XAU",
    # Spot equals the acceptance close - i.e. no pullback has happened yet.
    spot_price=4006.5,
    cfg=settings,
  )

  assert decision.state == "no_setup"
  assert any("chase disabled" in reason for reason in decision.reasons)


# ---------------------------------------------------------------------------
# build_trend_targets
# ---------------------------------------------------------------------------


def test_build_trend_targets_orders_tp1_tp2_measured_move_and_tp4(monkeypatch):
  m1 = _flat_m1()
  levels = [
    SessionLevel("PDH", 4010.0, m1.index[0], False),
    SessionLevel("PWH", 4012.0, m1.index[0], False),
    SessionLevel("ASIA_H", 4030.0, m1.index[0], False),
  ]
  monkeypatch.setattr(trend, "session_levels", lambda df, cfg: levels)
  monkeypatch.setattr(trend, "previous_week_levels", lambda df: [])
  monkeypatch.setattr(trend, "displacement", lambda *a, **k: [])
  monkeypatch.setattr(trend, "supply_demand", lambda *a, **k: [])

  targets = trend.build_trend_targets(
    "BUY", 4000.0, 1.0, m1, {"M1": m1}, settings,
    leg_size=15.0, stop_distance=5.0,
  )

  assert targets == [4010.0, 4012.0, 4015.0, 4030.0]


def test_build_trend_targets_drops_clustered_candidates(monkeypatch):
  m1 = _flat_m1()
  levels = [
    SessionLevel("A", 4005.0, m1.index[0], False),
    SessionLevel("B", 4005.2, m1.index[0], False),  # inside min-spacing of A
    SessionLevel("C", 4020.0, m1.index[0], False),
  ]
  monkeypatch.setattr(trend, "session_levels", lambda df, cfg: levels)
  monkeypatch.setattr(trend, "previous_week_levels", lambda df: [])
  monkeypatch.setattr(trend, "displacement", lambda *a, **k: [])
  monkeypatch.setattr(trend, "supply_demand", lambda *a, **k: [])

  targets = trend.build_trend_targets(
    "BUY", 4000.0, 1.0, m1, {"M1": m1}, settings,
    leg_size=None, stop_distance=None,
  )

  assert targets == [4005.0, 4020.0]


def test_build_trend_targets_empty_falls_back_to_fixed_ladder(monkeypatch):
  m1 = _flat_m1()
  monkeypatch.setattr(trend, "session_levels", lambda df, cfg: [])
  monkeypatch.setattr(trend, "previous_week_levels", lambda df: [])
  monkeypatch.setattr(trend, "displacement", lambda *a, **k: [])
  monkeypatch.setattr(trend, "supply_demand", lambda *a, **k: [])

  assert trend.build_trend_targets(
    "BUY", 4000.0, 1.0, m1, {"M1": m1}, settings,
    leg_size=None, stop_distance=None,
  ) == []

  fallback = trend._fixed_fallback_targets("BUY", 4000.0, 0.1)
  assert fallback == [
    4000.0 + pips * 0.1 for pips in trend._FALLBACK_TP_PIPS
  ]


def test_mode_b_falls_back_to_fixed_ladder_and_tags_reason(monkeypatch):
  m1 = _mode_b_frame(accepted=True)
  _patch_mode_b_primitives(monkeypatch, _mode_b_swings())
  monkeypatch.setattr(trend, "build_trend_targets", lambda *a, **k: [])
  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())

  decision = trend.evaluate_trend_gate(
    {"M1": m1},
    regime,
    AutoScalpDecision("waiting_for_box"),
    symbol="XAU",
    spot_price=4005.1,
    cfg=settings,
  )

  assert decision.state == "candidate"
  assert "targets: fixed-fallback" in decision.reasons
  assert decision.targets_pips == (30, 60, 90, 120, 200)


def test_early_bailout_states_carry_reasons():
  regime = trend.RegimeInfo("trend", "up", 5, 1.2, True, None, ())
  box_decision = AutoScalpDecision("waiting_for_box")

  missing = trend.evaluate_trend_gate(
    {}, regime, box_decision, symbol="XAU", spot_price=4000.0, cfg=settings,
  )
  assert missing.state == "missing_frames"
  assert missing.reasons

  insufficient = trend.evaluate_trend_gate(
    {"M1": _flat_m1(periods=5)},
    regime, box_decision, symbol="XAU", spot_price=4000.0, cfg=settings,
  )
  assert insufficient.state == "insufficient_history"
  assert insufficient.reasons

  flat_zero_range = pd.DataFrame({
    "open": [4000.0] * 61, "high": [4000.0] * 61,
    "low": [4000.0] * 61, "close": [4000.0] * 61,
  }, index=pd.date_range("2026-07-20", periods=61, freq="1min", tz="UTC"))
  invalid_atr = trend.evaluate_trend_gate(
    {"M1": flat_zero_range},
    regime, box_decision, symbol="XAU", spot_price=4000.0, cfg=settings,
  )
  assert invalid_atr.state == "invalid_atr"
  assert invalid_atr.reasons

  invalid_spot = trend.evaluate_trend_gate(
    {"M1": _flat_m1()},
    regime, box_decision, symbol="XAU", spot_price=-1.0, cfg=settings,
  )
  assert invalid_spot.state == "invalid_spot"
  assert invalid_spot.reasons
