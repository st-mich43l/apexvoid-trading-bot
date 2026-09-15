"""Scalping unit tests — context, microstructure, strategies, risk, paper."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.scalping.activation import evaluate_scalp_activation
from app.scalping.context import (
  build_scalp_context_snapshot,
  compute_context_id,
  is_context_fresh,
  is_impulse_pullback_session_allowed,
  permitted_archetypes_for_session,
)
from app.scalping.lifecycle import transition
from app.scalping.microstructure import (
  detect_breakout_retest,
  detect_impulse_pullback,
  detect_sweep_reclaim,
  build_micro_structure,
  find_compression_box,
  macro_momentum_direction,
)
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
  ARMED,
  DISCOVERED,
  EXECUTABLE,
  EXPIRED,
  MISSED,
  OPPORTUNITY_VERSION,
  ScalpContextSnapshot,
  ScalpLifecycleRecord,
  ScalpOpportunity,
  CONTEXT_VERSION,
)
from app.scalping.ranking import rank_opportunities, score_opportunity
from app.scalping.replay import aggregate_report, evaluate_paper_outcome
from app.scalping.risk import ScalpRiskState, evaluate_risk, risk_fraction
from app.scalping.strategies import _stop_buffer, discover_range_sweep


pytestmark = pytest.mark.no_database


def _cfg(**overrides):
  scalp_cfg = SimpleNamespace(
    mode="shadow",
    archetypes=SimpleNamespace(
      range_sweep_enabled=True,
      impulse_pullback_enabled=True,
      breakout_retest_enabled=True,
      impulse_pullback_allowed_sessions="london",
    ),
    breakout=SimpleNamespace(
      box_max_atr=1.5,
      min_break_atr=0.25,
      min_box_bars=8,
      max_box_bars=20,
      retest_lookback_bars=5,
      require_retest_rejection=True,
      min_touches_per_side=2,
      touch_tol_atr=0.20,
    ),
    context=SimpleNamespace(
      maximum_m5_age_seconds=420,
      m1_lookback_bars=60,
      current_context_ttl_seconds=3600,
      historic_context_ttl_seconds=86400,
    ),
    location=SimpleNamespace(
      range_buy_maximum_position=0.35,
      range_sell_minimum_position=0.65,
      pullback_buy_maximum_position=0.75,
      pullback_sell_minimum_position=0.25,
    ),
    activation=SimpleNamespace(
      trigger_maximum_age_bars=2,
      maximum_chase_pips=5.0,
      maximum_chase_stop_fraction=0.15,
      rearm_distance_atr=0.25,
    ),
    target=SimpleNamespace(
      preferred_ladder_pips="20,25,30",
      minimum_net_target_pips=15.0,
    ),
    stop=SimpleNamespace(minimum_pips=12.0, maximum_pips=30.0, buffer_atr=0.1),
    policy=SimpleNamespace(
      minimum_reward_risk=1.10,
      maximum_opportunities_per_cycle=3,
      maximum_active_opportunities=10,
      maximum_spread_pips=5.0,
    ),
    risk=SimpleNamespace(
      mode="shadow",
      risk_fraction_per_trade=0.10,
      maximum_concurrent_positions=1,
      maximum_session_trades=12,
      maximum_daily_trades=30,
      maximum_consecutive_losses=3,
      cooldown_after_loss_minutes=5,
      daily_loss_limit_r=3.0,
      session_loss_limit_r=2.0,
    ),
  )
  for key, value in overrides.items():
    setattr(scalp_cfg, key, value)
  return SimpleNamespace(
    strategies=SimpleNamespace(scalping=scalp_cfg),
    market_data=SimpleNamespace(
      sessions=SimpleNamespace(
        asia_start=22, london_start=7, ny_start=13, daily_rollover_utc_hour=21,
      ),
    ),
  )


def _m5_range(low=4000.0, high=4100.0, bars=40, end_ts=1_780_000_000):
  rows = []
  index = []
  for i in range(bars):
    mid = (low + high) / 2
    rows.append({
      "open": mid, "high": high - 1, "low": low + 1, "close": mid, "volume": 1.0,
    })
    index.append(pd.Timestamp(end_ts - (bars - i) * 300, unit="s", tz="UTC"))
  return pd.DataFrame(rows, index=index)


def test_context_id_stable_for_same_structure():
  a = compute_context_id("XAU", 100, 4000.0, 4100.0, 4020.0, 4080.0, "range")
  b = compute_context_id("XAU", 100, 4000.0, 4100.0, 4020.0, 4080.0, "range")
  assert a == b


def test_non_xau_context_fails_closed():
  m5 = _m5_range()
  snap = build_scalp_context_snapshot(
    symbol="US30", m5=m5, m15=None, h1=None, price=4050.0,
    pip_size=0.1, atr=5.0, now=1_780_000_000, cfg=_cfg(),
  )
  assert snap is None


def test_context_freshness():
  m5 = _m5_range()
  snap = build_scalp_context_snapshot(
    symbol="XAU", m5=m5, m15=m5, h1=None, price=4050.0,
    pip_size=0.1, atr=5.0, now=int(m5.index[-1].timestamp()), cfg=_cfg(),
  )
  assert snap is not None
  assert is_context_fresh(snap, snap.m5_bar_ts + 100, 420)
  assert not is_context_fresh(snap, snap.m5_bar_ts + 1000, 420)


def test_asia_permits_enabled_archetypes_under_technique_pack():
  """Asia: range/breakout only. Impulse is London/NY killzone-only."""
  from types import SimpleNamespace
  from app.scalping.models import (
    ARCHETYPE_BREAKOUT_RETEST,
    ARCHETYPE_IMPULSE_PULLBACK,
    ARCHETYPE_RANGE_SWEEP,
  )
  cfg = SimpleNamespace(
    market_data=SimpleNamespace(
      sessions=SimpleNamespace(
        london_start=7, ny_start=13, asia_start=22, daily_rollover_utc_hour=21,
      ),
    ),
    execution=SimpleNamespace(
      technique=SimpleNamespace(
        enforce=True,
        include_late_ny=True,
        london_window_hours=3,
        ny_window_hours=3,
        scalp_require_killzone=True,
      ),
    ),
    strategies=SimpleNamespace(
      scalping=SimpleNamespace(
        archetypes=SimpleNamespace(
          range_sweep_enabled=True,
          impulse_pullback_enabled=True,
          breakout_retest_enabled=True,
        ),
      ),
    ),
  )
  assert permitted_archetypes_for_session("asia", hour=3, cfg=cfg) == (
    ARCHETYPE_RANGE_SWEEP,
    ARCHETYPE_IMPULSE_PULLBACK,
    ARCHETYPE_BREAKOUT_RETEST,
  )
  assert ARCHETYPE_IMPULSE_PULLBACK in permitted_archetypes_for_session(
    "asia", hour=3, cfg=cfg,
  )
  assert permitted_archetypes_for_session("rollover", cfg=cfg) == (
    ARCHETYPE_RANGE_SWEEP,
    ARCHETYPE_IMPULSE_PULLBACK,
    ARCHETYPE_BREAKOUT_RETEST,
  )
  assert permitted_archetypes_for_session("london", hour=8, cfg=cfg) == (
    ARCHETYPE_RANGE_SWEEP,
    ARCHETYPE_IMPULSE_PULLBACK,
    ARCHETYPE_BREAKOUT_RETEST,
  )
  # NY afternoon / outside killzone: full enabled set (technique decides).
  assert permitted_archetypes_for_session("new_york", hour=16, cfg=cfg) == (
    ARCHETYPE_RANGE_SWEEP,
    ARCHETYPE_IMPULSE_PULLBACK,
    ARCHETYPE_BREAKOUT_RETEST,
  )


def test_lower_edge_sweep_reclaim():
  idx = pd.date_range("2026-07-01 10:00", periods=3, freq="1min", tz="UTC")
  df = pd.DataFrame([
    {"open": 4010, "high": 4012, "low": 4008, "close": 4011, "volume": 1},
    {"open": 4011, "high": 4012, "low": 4007, "close": 4010, "volume": 1},
    {"open": 4009, "high": 4011, "low": 3998, "close": 4006, "volume": 1},
  ], index=idx)
  # close above edge 4000 after sweeping below
  df.iloc[-1, df.columns.get_loc("low")] = 3995.0
  df.iloc[-1, df.columns.get_loc("close")] = 4002.0
  df.iloc[-1, df.columns.get_loc("open")] = 3998.0
  hit = detect_sweep_reclaim(df, direction="BUY", edge_price=4000.0, tolerance=1.0)
  assert hit is not None
  assert hit["pattern"] == "sweep_reclaim"


def test_edge_touch_reclaim_counts_without_piercing_below():
  # Wick exactly to the edge (not through) used to return None forever.
  idx = pd.date_range("2026-07-01 10:00", periods=2, freq="1min", tz="UTC")
  df = pd.DataFrame([
    {"open": 4010, "high": 4012, "low": 4008, "close": 4009, "volume": 1},
    {"open": 4001, "high": 4005, "low": 4000.0, "close": 4004, "volume": 1},
  ], index=idx)
  hit = detect_sweep_reclaim(
    df, direction="BUY", edge_price=4000.0, tolerance=1.0, lookback_bars=1,
  )
  assert hit is not None
  assert hit["extreme"] == 4000.0


def test_sweep_reclaim_lookback_picks_prior_bar():
  # Reclaim on bar -2; newest bar is noise. Activation allows age=2 bars so
  # discovery must recover the prior reclaim instead of reporting discovered=0.
  idx = pd.date_range("2026-07-01 10:00", periods=3, freq="1min", tz="UTC")
  df = pd.DataFrame([
    {"open": 4010, "high": 4012, "low": 4008, "close": 4011, "volume": 1},
    {"open": 3998, "high": 4005, "low": 3995, "close": 4002, "volume": 1},
    # Newest is mid-range noise — no edge touch within tolerance.
    {"open": 4005, "high": 4008, "low": 4004, "close": 4007, "volume": 1},
  ], index=idx)
  assert detect_sweep_reclaim(
    df, direction="BUY", edge_price=4000.0, tolerance=1.0, lookback_bars=1,
  ) is None
  hit = detect_sweep_reclaim(
    df, direction="BUY", edge_price=4000.0, tolerance=1.0, lookback_bars=2,
  )
  assert hit is not None
  assert hit["bar_ts"] == int(idx[1].timestamp())


def test_shallow_pullback_blocks():
  idx = pd.date_range("2026-07-01 10:00", periods=20, freq="1min", tz="UTC")
  closes = list(range(4000, 4020))
  df = pd.DataFrame({
    "open": [c - 0.5 for c in closes],
    "high": [c + 1 for c in closes],
    "low": [c - 1 for c in closes],
    "close": closes,
    "volume": [1] * 20,
  }, index=idx)
  # tiny pullback from extreme
  df.iloc[-1, df.columns.get_loc("close")] = 4019.0
  df.iloc[-1, df.columns.get_loc("open")] = 4018.5
  result = detect_impulse_pullback(df, direction="BUY", min_retracement=0.25)
  assert result is not None
  assert result.get("rejected") is True
  assert result.get("reason") == "pullback_too_shallow"


def test_breakout_without_retest_waits():
  idx = pd.date_range("2026-07-01 10:00", periods=6, freq="1min", tz="UTC")
  df = pd.DataFrame({
    "open": [4050, 4051, 4052, 4055, 4060, 4062],
    "high": [4052, 4053, 4054, 4058, 4065, 4064],
    "low": [4048, 4049, 4050, 4053, 4059, 4060],
    "close": [4051, 4052, 4053, 4057, 4063, 4063],
    "volume": [1] * 6,
  }, index=idx)
  result = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
  )
  assert result is not None
  assert result.get("state") == "wait_retest"


def _breakout_retest_touch_two_bars_back_df():
  # Break at bar -3, rejection retest at bar -2 (wick through level, close
  # reclaim above), hold at bar -1 above the level without another touch.
  idx = pd.date_range("2026-07-01 10:00", periods=5, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open":  [4049.0, 4050.0, 4051.0, 4056.0, 4054.8],
    "high":  [4051.0, 4052.0, 4059.0, 4056.5, 4056.0],
    "low":   [4047.0, 4048.0, 4050.0, 4053.0, 4055.2],
    "close": [4050.0, 4051.0, 4057.0, 4055.5, 4055.5],
    "volume": [1] * 5,
  }, index=idx)


def test_breakout_retest_lookback_bars_1_misses_prior_bar_touch():
  """Newest-bar-only lookback misses the prior rejection retest."""
  df = _breakout_retest_touch_two_bars_back_df()
  result = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
  )
  assert result is not None
  assert result.get("state") == "wait_retest"
  assert result.get("accepted") is True


def test_breakout_retest_lookback_bars_2_recovers_prior_bar_touch():
  """Wider lookback recovers the prior rejection; hold is newest close."""
  df = _breakout_retest_touch_two_bars_back_df()
  hit = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
    retest_lookback_bars=2,
  )
  assert hit is not None
  assert hit["state"] == "armed"
  assert hit["pattern"] == "breakout_retest"
  assert hit["direction"] == "BUY"
  assert hit["close"] == pytest.approx(4055.5)
  assert hit["bar_ts"] == int(df.index[-1].timestamp())


def test_breakout_retest_still_invalidates_on_current_bar_failed_hold():
  """A stale rejection must not resurrect a candidate that failed hold."""
  df = _breakout_retest_touch_two_bars_back_df()
  df = df.copy()
  df.iloc[-1, df.columns.get_loc("close")] = 4053.0  # closed back below 4055
  df.iloc[-1, df.columns.get_loc("open")] = 4054.0
  result = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
    retest_lookback_bars=2,
  )
  assert result is not None
  assert result.get("state") == "failed_break"


def test_breakout_requires_rejection_not_touch_only():
  """Touch that closes back into the box is not a rejection retest."""
  idx = pd.date_range("2026-07-01 10:00", periods=5, freq="1min", tz="UTC")
  df = pd.DataFrame({
    "open":  [4049.0, 4050.0, 4051.0, 4054.0, 4054.8],
    "high":  [4051.0, 4052.0, 4059.0, 4056.0, 4056.0],
    "low":   [4047.0, 4048.0, 4050.0, 4053.0, 4055.2],
    "close": [4050.0, 4051.0, 4057.0, 4054.0, 4055.5],  # bar -2 closes below level
    "volume": [1] * 5,
  }, index=idx)
  waiting = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
    retest_lookback_bars=2, require_retest_rejection=True,
  )
  assert waiting is not None
  assert waiting.get("state") == "wait_retest"
  # Touch-only mode still arms when hold is valid.
  hit = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
    retest_lookback_bars=2, require_retest_rejection=False,
  )
  assert hit is not None
  assert hit.get("state") == "armed"


def test_discover_breakout_retest_passes_configured_lookback(monkeypatch):
  """Wiring check — uses wait_retest path so full opportunity surface unused."""
  from app.scalping import strategies as strat_mod
  from app.scalping.models import ARCHETYPE_BREAKOUT_RETEST

  captured: dict = {}
  original = strat_mod.detect_breakout_retest

  def _spy(*args, **kwargs):
    captured["retest_lookback_bars"] = kwargs.get("retest_lookback_bars")
    captured["box_high"] = kwargs.get("box_high")
    captured["box_low"] = kwargs.get("box_low")
    return original(*args, **kwargs)

  monkeypatch.setattr(strat_mod, "detect_breakout_retest", _spy)
  monkeypatch.setattr(
    strat_mod,
    "find_compression_box",
    lambda *a, **k: {
      "box_low": 4040.0,
      "box_high": 4055.0,
      "box_bars": 10,
      "compression_atr": 0.8,
      "touch_count": 6,
    },
  )
  cfg = _cfg()
  idx = pd.date_range("2026-07-01 10:00", periods=6, freq="1min", tz="UTC")
  df = pd.DataFrame({
    "open":  [4050, 4051, 4052, 4055, 4060, 4062],
    "high":  [4052, 4053, 4054, 4058, 4065, 4064],
    "low":   [4048, 4049, 4050, 4053, 4059, 4060],
    "close": [4051, 4052, 4053, 4057, 4063, 4063],
    "volume": [1] * 6,
  }, index=idx)
  context = SimpleNamespace(
    active_range_low=4000.0,  # envelope must NOT be used
    active_range_high=4200.0,
    atr=2.0,
    symbol="XAU",
    permitted_archetypes={ARCHETYPE_BREAKOUT_RETEST},
  )
  strat_mod.discover_breakout_retest(
    context, micro=None, m1_df=df, cfg=cfg, pip_size=0.1, now=1_780_000_000,
  )
  assert captured["retest_lookback_bars"] == 5
  assert captured["box_high"] == 4055.0
  assert captured["box_low"] == 4040.0


def test_breakout_retest_recovers_break_older_than_three_bars():
  """Default break lookback recovers a break at bar -5 with later rejection."""
  idx = pd.date_range("2026-07-01 10:00", periods=8, freq="1min", tz="UTC")
  # bar -5 (index 2): accepted BUY break above 4055
  # bar -1: rejection (low touches level) + hold above
  df = pd.DataFrame({
    "open":  [4049.0, 4050.0, 4051.0, 4058.0, 4057.0, 4056.0, 4056.5, 4055.2],
    "high":  [4051.0, 4052.0, 4060.0, 4059.0, 4058.0, 4057.0, 4057.0, 4056.5],
    "low":   [4047.0, 4048.0, 4050.0, 4055.5, 4055.0, 4055.2, 4055.1, 4054.5],
    "close": [4050.0, 4051.0, 4058.0, 4057.0, 4056.0, 4055.8, 4056.0, 4055.8],
    "volume": [1] * 8,
  }, index=idx)
  missed = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
    retest_lookback_bars=4, break_lookback_bars=3,
  )
  assert missed is not None
  assert missed.get("state") == "wait_break"
  hit = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
    retest_lookback_bars=4,
  )
  assert hit is not None
  assert hit.get("state") == "armed"
  assert hit.get("pattern") == "breakout_retest"
  assert hit["close"] == pytest.approx(4055.8)


def test_breakout_retest_hold_allows_mixed_body_above_level():
  """Hold is close-beyond-level only — mixed body above still arms."""
  df = _breakout_retest_touch_two_bars_back_df().copy()
  df.iloc[-1, df.columns.get_loc("open")] = 4056.5
  df.iloc[-1, df.columns.get_loc("close")] = 4055.2
  df.iloc[-1, df.columns.get_loc("high")] = 4056.8
  df.iloc[-1, df.columns.get_loc("low")] = 4055.0
  hit = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
    retest_lookback_bars=2,
  )
  assert hit is not None
  assert hit["state"] == "armed"
  assert hit["pattern"] == "breakout_retest"


def test_find_compression_box_accepts_tight_multi_touch():
  idx = pd.date_range("2026-07-01 10:00", periods=14, freq="1min", tz="UTC")
  # 10-bar coil ~1.0 wide, then expansion bars after the box.
  opens = [4050.0] * 10 + [4051.0, 4052.0, 4054.0, 4056.0]
  highs = [4050.5, 4050.8, 4051.0, 4050.6, 4051.0, 4050.7, 4051.0, 4050.9, 4051.0, 4050.5,
           4053.0, 4055.0, 4057.0, 4058.0]
  lows = [4049.5, 4049.2, 4049.0, 4049.4, 4049.0, 4049.3, 4049.0, 4049.1, 4049.0, 4049.5,
          4050.5, 4051.0, 4053.0, 4055.0]
  closes = [4050.0] * 10 + [4052.0, 4054.0, 4056.0, 4057.0]
  df = pd.DataFrame({
    "open": opens, "high": highs, "low": lows, "close": closes, "volume": [1] * 14,
  }, index=idx)
  box = find_compression_box(
    df, atr=2.0, min_box_bars=8, max_box_bars=12, box_max_atr=1.5, min_touches_per_side=2,
  )
  assert box is not None
  assert box["box_high"] - box["box_low"] <= 1.5 * 2.0
  assert box["touch_count"] >= 4


def test_find_compression_box_rejects_wide_envelope():
  idx = pd.date_range("2026-07-01 10:00", periods=12, freq="1min", tz="UTC")
  # Expanding swing — like a 24-bar M5 envelope on M1.
  df = pd.DataFrame({
    "open": list(range(4000, 4012)),
    "high": list(range(4002, 4014)),
    "low": list(range(3998, 4010)),
    "close": list(range(4001, 4013)),
    "volume": [1] * 12,
  }, index=idx)
  box = find_compression_box(
    df, atr=2.0, min_box_bars=8, max_box_bars=12, box_max_atr=1.5, min_touches_per_side=2,
  )
  assert box is None


def test_breakout_math_stamp_present():
  from app.scalping.math_strategies import evaluate_breakout_retest_continuation

  gate = evaluate_breakout_retest_continuation(
    direction="BUY",
    price=4056.0,
    atr=2.0,
    box_low=4040.0,
    box_high=4055.0,
    level=4055.0,
    barrier=4070.0,
    target_min_price=1.0,
    break_displacement=2.0,
    retest_rejection=True,
    accepted_break=True,
  )
  assert gate.allowed is True
  assert gate.reason_code == "breakout_retest_ok"


def test_risk_daily_cap_and_no_martingale():
  cfg = _cfg()
  assert risk_fraction(cfg) == 0.10
  state = ScalpRiskState(daily_trades=30)
  decision = evaluate_risk(state, cfg, session="london", now=1_780_000_000)
  assert decision.allowed is False
  assert decision.reason_code == "scalp_daily_trade_cap"


def test_risk_loss_streak_blocks_after_cooldown_until_reset():
  from app.scalping.risk import (
    apply_loss_streak_cooldown_reset,
    record_scalp_outcome,
  )

  cfg = _cfg()
  now = 1_780_000_000
  state = ScalpRiskState(consecutive_losses=3, last_loss_ts=now - 60)
  during = evaluate_risk(state, cfg, session="london", now=now)
  assert during.allowed is False
  assert during.reason_code == "scalp_loss_streak_cooldown"

  after_cooldown = evaluate_risk(
    state, cfg, session="london", now=now + 10 * 60,
  )
  assert after_cooldown.allowed is False
  assert after_cooldown.reason_code == "scalp_loss_streak_active"

  cleared = apply_loss_streak_cooldown_reset(
    state, cfg, now=now + 10 * 60,
  )
  assert cleared.consecutive_losses == 0
  allowed = evaluate_risk(cleared, cfg, session="london", now=now + 10 * 60)
  assert allowed.allowed is True

  winning = record_scalp_outcome(
    ScalpRiskState(consecutive_losses=2, last_loss_ts=now),
    result_pips=20.0,
    stop_pips=20.0,
    now=now,
    closed=True,
  )
  assert winning.consecutive_losses == 0


def test_record_scalp_outcome_skips_accrual_without_stop():
  from app.scalping.risk import ScalpRiskState, record_scalp_outcome

  state = ScalpRiskState(daily_r=-1.0, session_r=-0.5, open_positions=1)
  result = record_scalp_outcome(
    state,
    result_pips=-14.0,
    stop_pips=None,
    now=1_780_000_000,
    closed=True,
    group_id="v8:no-stop",
  )
  assert result.skipped_no_stop is True
  assert result.accrued_r is None
  assert result.state.daily_r == -1.0
  assert result.state.session_r == -0.5
  # Position bookkeeping still closes.
  assert result.state.open_positions == 0


def test_record_scalp_outcome_accrues_exact_pips_over_stop():
  from app.scalping.risk import ScalpRiskState, record_scalp_outcome

  result = record_scalp_outcome(
    ScalpRiskState(),
    result_pips=-14.0,
    stop_pips=14.0,
    now=1_780_000_000,
    closed=True,
  )
  assert result.skipped_no_stop is False
  assert result.accrued_r == pytest.approx(-1.0)
  assert result.state.daily_r == pytest.approx(-1.0)
  assert result.state.session_r == pytest.approx(-1.0)


def test_record_scalp_outcome_never_uses_hardcoded_20_fallback():
  from app.scalping.risk import ScalpRiskState, record_scalp_outcome

  # Old behaviour: stop missing → divide by 20 → -14/20 = -0.7R.
  result = record_scalp_outcome(
    ScalpRiskState(daily_r=0.0, session_r=0.0),
    result_pips=-14.0,
    stop_pips=0.0,
    now=1,
    closed=True,
  )
  assert result.skipped_no_stop is True
  assert result.state.daily_r == 0.0


def test_daily_loss_limit_unsticks_at_trading_day_rollover():
  """Live 2026-08-13: daily_r sat at -3.75R from a loss on 2026-08-12,
  scalp_daily_loss_limit stayed tripped over 24h later because day_key was
  persisted but never compared against anything -- the reset never ran.
  """
  from app.scalping.risk import apply_daily_reset

  cfg = _cfg()
  # Matches the live redis snapshot: stale/never-set day_key, breached limit.
  state = ScalpRiskState(daily_trades=7, daily_r=-3.75, day_key="")
  now = 1_786_631_040  # 2026-08-13 14:24 UTC

  stuck = evaluate_risk(state, cfg, session="london", now=now)
  assert stuck.allowed is False
  assert stuck.reason_code == "scalp_daily_loss_limit"

  reset_state = apply_daily_reset(state, cfg, now=now, session="london")
  assert reset_state.daily_r == 0.0
  assert reset_state.daily_trades == 0
  assert reset_state.day_key != ""

  unstuck = evaluate_risk(reset_state, cfg, session="london", now=now)
  assert unstuck.allowed is True


def test_daily_reset_is_noop_within_same_trading_day():
  from app.scalping.risk import apply_daily_reset

  cfg = _cfg()
  now = 1_786_631_040  # 2026-08-13 14:24 UTC
  # Prime day_key for "now"'s trading day, as a real load->reset->accumulate
  # cycle would, before asserting a later same-day call leaves counters alone.
  primed = apply_daily_reset(ScalpRiskState(), cfg, now=now, session="london")
  state = ScalpRiskState(
    daily_trades=4, daily_r=-1.5,
    day_key=primed.day_key, session_key=primed.session_key,
  )

  ten_minutes_later = apply_daily_reset(
    state, cfg, now=now + 600, session="london"
  )
  assert ten_minutes_later.daily_trades == 4
  assert ten_minutes_later.daily_r == -1.5


def test_daily_reset_boundary_is_trading_rollover_not_utc_midnight():
  from app.scalping.risk import apply_daily_reset

  cfg = _cfg()
  before_rollover = 1_786_568_340  # 2026-08-12 20:59 UTC
  after_rollover = 1_786_568_460  # 2026-08-12 21:01 UTC
  primed = apply_daily_reset(
    ScalpRiskState(), cfg, now=before_rollover, session="late_ny"
  )
  state = ScalpRiskState(
    daily_trades=5, daily_r=-2.0,
    day_key=primed.day_key, session_key=primed.session_key,
  )

  state = apply_daily_reset(state, cfg, now=before_rollover, session="late_ny")
  # Same trading day as before_rollover -- must not reset yet.
  assert state.daily_trades == 5
  assert state.daily_r == -2.0

  state = apply_daily_reset(state, cfg, now=after_rollover, session="rollover")
  # Crossed the 21:00 UTC trading-day boundary -- must reset.
  assert state.daily_trades == 0
  assert state.daily_r == 0.0


def test_session_reset_clears_session_counters_on_session_change():
  from app.scalping.risk import apply_daily_reset

  cfg = _cfg()
  now = 1_786_631_040  # 2026-08-13 14:24 UTC
  state = ScalpRiskState(session_trades=6, session_r=-1.8, day_key="")
  state = apply_daily_reset(state, cfg, now=now, session="london")
  assert state.session_trades == 0
  assert state.session_r == 0.0

  state.session_trades = 6
  state.session_r = -1.8
  same_session = apply_daily_reset(state, cfg, now=now + 60, session="london")
  assert same_session.session_trades == 6
  assert same_session.session_r == -1.8

  new_session = apply_daily_reset(
    same_session, cfg, now=now + 120, session="new_york"
  )
  assert new_session.session_trades == 0
  assert new_session.session_r == 0.0


def test_lifecycle_explicit_transitions():
  record = ScalpLifecycleRecord("oid", "eid", DISCOVERED, "ctx", 1)
  armed = transition(record, ARMED, reason="ok", now=2)
  assert armed.state == ARMED
  missed = transition(armed, MISSED, reason="chase", now=3)
  assert missed.state == MISSED


def test_prune_stale_armed_expires_and_clears_active():
  import asyncio

  from app.scalping.lifecycle import prune_stale_active, save_lifecycle, active_key
  from app.scalping.models import ScalpLifecycleRecord, ARMED

  class _FakeRedis:
    def __init__(self):
      self.kv = {}
      self.sets = {}

    async def set(self, key, value):
      self.kv[key] = value

    async def get(self, key):
      return self.kv.get(key)

    async def sadd(self, key, member):
      self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
      self.sets.setdefault(key, set()).discard(member)

    async def smembers(self, key):
      return set(self.sets.get(key, set()))

  async def _run():
    client = _FakeRedis()
    rec = ScalpLifecycleRecord("oid1", "eid", ARMED, "ctx", updated_at=1_000)
    await save_lifecycle(client, "XAU", rec)
    assert "oid1" in client.sets[active_key("XAU")]
    n = await prune_stale_active(client, "XAU", now=1_000 + 16 * 60)
    assert n == 1
    assert "oid1" not in client.sets.get(active_key("XAU"), set())
    raw = await client.get("scalp:lifecycle:XAU:oid1")
    assert "stale_armed_expired" in raw or EXPIRED in raw

  asyncio.run(_run())


def test_paper_same_bar_conservative_stop():
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="o1",
    context_id="c1",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=1,
    source_bar_ts=1,
    zone_low=4000,
    zone_high=4002,
    key_level=4001,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=1,
    trigger_price=4001.0,
    invalidation_price=3995.0,
    expected_target_price=4020.0,
    expected_target_pips=20,
    expected_stop_pips=15,
    expected_reward_risk=1.3,
    location_position=0.2,
    score=1.0,
    reasons=("t",),
    expires_at=100,
  )
  bars = pd.DataFrame([{
    "open": 4001, "high": 4025, "low": 3990, "close": 4010, "volume": 1,
  }])
  outcome = evaluate_paper_outcome(opp, bars, pip_size=0.1)
  assert outcome.outcome == "stop"
  assert outcome.exit_reason == "same_bar_stop_priority"


def test_activation_blocks_buy_in_premium():
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="c",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=4000.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.8,
    active_range_low=4070.0,
    active_range_high=4090.0,
    active_range_eq=4080.0,
    nearest_support_low=4070.0,
    nearest_support_high=4072.0,
    nearest_resistance_low=4088.0,
    nearest_resistance_high=4090.0,
    buy_corridor_room_pips=40.0,
    sell_corridor_room_pips=40.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=5.0,
  )
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="o",
    context_id="c",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=100,
    source_bar_ts=100,
    zone_low=4078.0,
    zone_high=4082.0,
    key_level=4080.0,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=90,
    trigger_price=4080.0,
    invalidation_price=4070.0,
    expected_target_price=4100.0,
    expected_target_pips=20,
    expected_stop_pips=15,
    expected_reward_risk=1.3,
    location_position=0.8,
    score=1.0,
    reasons=("t",),
    expires_at=200,
  )
  decision = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4079.5,
    quote_ask=4080.5,
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert decision.allowed is False
  assert decision.hard_block is True


def test_activation_chases_momentum_within_chase_budget():
  """Price past zone high must chase, not wait as quote_outside_zone."""
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="c",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=4000.0,
    dealing_range_high=4200.0,
    dealing_range_position=0.25,
    active_range_low=4000.0,
    active_range_high=4100.0,
    active_range_eq=4050.0,
    nearest_support_low=4000.0,
    nearest_support_high=4002.0,
    nearest_resistance_low=4098.0,
    nearest_resistance_high=4100.0,
    buy_corridor_room_pips=40.0,
    sell_corridor_room_pips=40.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=5.0,
  )
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="chase-o",
    context_id="c",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=100,
    source_bar_ts=100,
    zone_low=4010.0,
    zone_high=4012.0,
    key_level=4011.0,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=90,
    trigger_price=4011.0,
    invalidation_price=4005.0,
    expected_target_price=4035.0,
    expected_target_pips=25,
    expected_stop_pips=15,
    expected_reward_risk=1.5,
    location_position=0.25,
    score=1.0,
    reasons=("t",),
    expires_at=200,
  )
  cfg = _cfg()
  cfg.strategies.scalping.activation.maximum_chase_pips = 100.0
  cfg.strategies.scalping.activation.maximum_chase_stop_fraction = 1.0
  # 10 pips above zone high — used to soft-wait forever; must chase.
  decision = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4012.9,
    quote_ask=4013.0,
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=cfg,
  )
  assert decision.allowed is True
  assert decision.measured.get("chase_entry") is True
  assert decision.measured.get("chase_pips") == pytest.approx(10.0)

  missed = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4022.9,
    quote_ask=4023.0,  # 110 pips above zone → miss past 100
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=cfg,
  )
  assert missed.allowed is False
  assert missed.reason_code == "entry_cancelled_chase"


def test_activation_breakout_retest_waits_below_sell_zone():
  """Breakout retest SELL must not chase when quote is below the retest band."""
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="c",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=4400.0,
    dealing_range_high=4450.0,
    dealing_range_position=0.5,
    active_range_low=4410.0,
    active_range_high=4440.0,
    active_range_eq=4425.0,
    nearest_support_low=4410.0,
    nearest_support_high=4412.0,
    nearest_resistance_low=4438.0,
    nearest_resistance_high=4440.0,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_BREAKOUT_RETEST,),
    atr=4.0,
  )
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="br-o",
    context_id="c",
    symbol="XAU",
    archetype=ARCHETYPE_BREAKOUT_RETEST,
    direction="SELL",
    discovered_at=100,
    source_bar_ts=100,
    zone_low=4430.76,
    zone_high=4433.36,
    key_level=4432.06,
    trigger_type="breakout_retest",
    trigger_bar_ts=90,
    trigger_price=4431.0,
    invalidation_price=4434.0,
    expected_target_price=4425.96,
    expected_target_pips=74,
    expected_stop_pips=8,
    expected_reward_risk=9.25,
    location_position=0.5,
    score=1.0,
    reasons=("micro_breakout_retest",),
    expires_at=200,
    measured={
      "breakout_evidence": {
        "accepted_break": True,
        "correct_key_level_role": True,
        "retest_of_broken_level": True,
        "retest_rejection": True,
        "directionally_valid_close": True,
        "target_room_beyond_breakout": True,
      },
    },
  )
  decision = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4429.62,
    quote_ask=4429.72,
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert decision.allowed is False
  assert decision.hard_block is False
  assert decision.reason_code == "quote_outside_zone"
  assert decision.measured.get("zone_access_mode") == "retest_only"


def test_activation_allows_one_to_one_room_when_min_net_fits():
  """Live 2026-08-06 09:08: HFS Impulse Pullback BUY target=30 stop=30 died
  on scalp_net_rr_insufficient after cost haircut. Min net room is enough.
  """
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="c",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=4250.0,
    dealing_range_high=4300.0,
    dealing_range_position=0.52,
    active_range_low=4250.0,
    active_range_high=4285.0,
    active_range_eq=4267.0,
    nearest_support_low=4250.0,
    nearest_support_high=4252.0,
    nearest_resistance_low=4283.0,
    nearest_resistance_high=4285.0,
    buy_corridor_room_pips=40.0,
    sell_corridor_room_pips=40.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_IMPULSE_PULLBACK,),
    atr=5.0,
  )
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="rr-o",
    context_id="c",
    symbol="XAU",
    archetype=ARCHETYPE_IMPULSE_PULLBACK,
    direction="BUY",
    discovered_at=100,
    source_bar_ts=100,
    zone_low=4278.42,
    zone_high=4279.40,
    key_level=4278.91,
    trigger_type="impulse_pullback",
    trigger_bar_ts=90,
    trigger_price=4278.91,
    invalidation_price=4270.0,
    expected_target_price=4281.91,
    expected_target_pips=30,
    expected_stop_pips=30,
    expected_reward_risk=1.0,
    location_position=0.52,
    score=1.0,
    reasons=("impulse_pullback_continuation",),
    expires_at=200,
  )
  decision = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4278.9,
    quote_ask=4279.0,
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert decision.allowed is True
  assert decision.reason_code == "scalp_activation_allowed"
  assert decision.measured.get("net_rr_below_policy") is True
  assert decision.measured.get("preference_telemetry") is True
  assert decision.measured["net_reward_risk"] < 1.10


def test_ranking_prefers_higher_score():
  ctx = ScalpContextSnapshot(
    version=1, context_id="c", symbol="XAU", created_at=1,
    h1_bar_ts=None, m15_bar_ts=None, m5_bar_ts=1, htf_bias="up",
    m5_structure="bullish", regime="trend", dealing_range_low=4000,
    dealing_range_high=4100, dealing_range_position=0.2,
    active_range_low=4000, active_range_high=4050, active_range_eq=4025,
    nearest_support_low=4000, nearest_support_high=4002,
    nearest_resistance_low=4048, nearest_resistance_high=4050,
    buy_corridor_room_pips=30, sell_corridor_room_pips=30,
    session="london", permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,), atr=4,
  )
  from app.scalping.models import ScalpDecision, ScalpScore

  def make(score_hint, oid, pos):
    opp = ScalpOpportunity(
      version=1, opportunity_id=oid, context_id="c", symbol="XAU",
      archetype=ARCHETYPE_RANGE_SWEEP, direction="BUY", discovered_at=1,
      source_bar_ts=1, zone_low=4000, zone_high=4005, key_level=4002,
      trigger_type="sweep_reclaim", trigger_bar_ts=1, trigger_price=4002,
      invalidation_price=3990, expected_target_price=4025, expected_target_pips=25,
      expected_stop_pips=15, expected_reward_risk=1.5, location_position=pos,
      score=score_hint, reasons=("r",), expires_at=10,
    )
    decision = ScalpDecision(True, False, "ok", score_hint, {"spread_pips": 1.0, "location_position": pos})
    score = score_opportunity(opp, ctx, decision, spread_pips=1.0)
    return opp, decision, score

  ranked = rank_opportunities([make(0.5, "a", 0.4), make(0.9, "b", 0.1)], maximum=1)
  assert len(ranked) == 1
  assert ranked[0][0].opportunity_id == "b"


def test_aggregate_report_expectancy():
  rows = [
    {"outcome": "target", "net_r": 1.2, "session": "london", "archetype": "range_sweep"},
    {"outcome": "stop", "net_r": -1.0, "session": "london", "archetype": "range_sweep"},
  ]
  report = aggregate_report(rows)
  assert report["count"] == 2
  assert report["win_rate"] == 0.5
  assert report["expectancy_r"] == pytest.approx(0.1)


def test_stop_pips_rejects_above_maximum_widens_below_minimum():
  """Maximum is a reject; minimum is a widening floor — never a silent clamp."""
  from app.scalping.strategies import _stop_pips

  cfg = _cfg()
  assert _stop_pips(structural=42.0, cfg=cfg) == (None, "stop_exceeds_maximum")
  assert _stop_pips(structural=8.0, cfg=cfg) == (12.0, None)
  assert _stop_pips(structural=18.0, cfg=cfg) == (18.0, None)
  assert _stop_pips(structural=0.0, cfg=cfg) == (None, "stop_not_positive")


def test_stop_pips_rejects_when_structure_cannot_clear_spread_cost():
  from app.scalping.strategies import _stop_pips

  cfg = _cfg()
  assert _stop_pips(
    structural=11.9, cfg=cfg, spread_pips=3.0,
  ) == (None, "stop_below_spread_multiple")
  assert _stop_pips(
    structural=12.0, cfg=cfg, spread_pips=3.0,
  ) == (12.0, None)


def test_scalp_target_prefers_1to2_falls_back_to_1to1():
  # Owner 2026-08-11: every scalp is 1:2 when the room supports it, 1:1
  # otherwise - never a ladder, never anything outside this pair.
  from app.scalping.strategies import _select_target

  # Room comfortably fits 2x stop -> 1:2 wins.
  buy_price, buy_pips = _select_target(
    direction="BUY", worst_fill=4000.0, room_pips=40.0, stop_pips=18.0,
    min_net=15.0, pip_size=0.1,
  )
  assert buy_pips == pytest.approx(36.0)
  assert buy_price == pytest.approx(4003.6)

  sell_price, sell_pips = _select_target(
    direction="SELL", worst_fill=4000.0, room_pips=40.0, stop_pips=18.0,
    min_net=15.0, pip_size=0.1,
  )
  assert sell_pips == pytest.approx(36.0)
  assert sell_price == pytest.approx(3996.4)

  # Room fits 1x stop but not 2x -> falls back to 1:1, not a trimmed 1:2.
  price, pips = _select_target(
    direction="BUY", worst_fill=4000.0, room_pips=20.0, stop_pips=18.0,
    min_net=15.0, pip_size=0.1,
  )
  assert pips == pytest.approx(18.0)
  assert price == pytest.approx(4001.8)

  # Stop below the minimum net target at 1:1 but 1:2 clears it -> 1:2 wins,
  # not the old "no opportunity" outcome from when only 1:1 existed.
  price, pips = _select_target(
    direction="BUY", worst_fill=4000.0, room_pips=40.0, stop_pips=10.0,
    min_net=15.0, pip_size=0.1,
  )
  assert pips == pytest.approx(20.0)

  # Neither ratio fits the available room -> no opportunity, not a trimmed
  # target outside the {1:1, 1:2} pair.
  assert _select_target(
    direction="BUY", worst_fill=4000.0, room_pips=15.0, stop_pips=18.0,
    min_net=15.0, pip_size=0.1,
  ) is None

  assert _select_target(
    direction="BUY", worst_fill=4000.0, room_pips=40.0, stop_pips=None,
    min_net=15.0, pip_size=0.1,
  ) is None


def test_impulse_pullback_episode_id_survives_an_m5_context_rollover(monkeypatch):
  # Live 2026-08-06: a WAITING FILL card for an HFS Impulse Pullback never
  # updated to POSITION ACTIVATED - a second, brand-new card appeared below
  # it instead once the position actually filled. Root cause: episode_id
  # (opportunity.episode_id, the identity same_thesis()/dedupe_matches()
  # compares across scan cycles) was hashed from context.context_id, which
  # itself bakes in m5_bar_ts (context.py's compute_context_id). The exact
  # same still-unfilled impulse/pullback pattern gets a brand-new episode_id
  # the instant the M5 candle rolls over mid-wait, fails dedup against its
  # own earlier self, and spawns an independent second match/plan/card while
  # the first sits orphaned. episode_id must depend only on the pattern's
  # own stable geometry (origin/extreme/direction/symbol), never on the
  # M5-bar-bucketed context identity.
  from app.scalping.strategies import discover_impulse_pullback

  local_sell_match = {
    "pattern": "impulse_pullback",
    "direction": "SELL",
    "bar_ts": 1_780_003_600,
    "origin": 4270.0,
    "extreme": 4230.0,
    "pullback_extreme": 4251.5,
    "retracement": 0.5,
    "preferred": True,
    "close": 4250.0,
    "impulse_len": 40.0,
    "body_dominance": 0.8,
    "mean_impulse_body": 2.0,
    "mean_pullback_body": 0.5,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_impulse_pullback",
    lambda df, *, direction: local_sell_match if direction == "SELL" else None,
  )
  flat = _drift_bars(direction="BUY", step=0.0)

  def _ctx(context_id: str, m5_bar_ts: int) -> ScalpContextSnapshot:
    return ScalpContextSnapshot(
      version=CONTEXT_VERSION,
      context_id=context_id,
      symbol="XAU",
      created_at=m5_bar_ts,
      h1_bar_ts=None,
      m15_bar_ts=None,
      m5_bar_ts=m5_bar_ts,
      htf_bias="unknown",
      m5_structure="range",
      regime="range",
      dealing_range_low=4230.0,
      dealing_range_high=4274.0,
      dealing_range_position=0.95,
      active_range_low=None,
      active_range_high=None,
      active_range_eq=None,
      nearest_support_low=None,
      nearest_support_high=None,
      nearest_resistance_low=None,
      nearest_resistance_high=None,
      buy_corridor_room_pips=None,
      sell_corridor_room_pips=200.0,
      session="london",
      permitted_archetypes=("impulse_pullback",),
      atr=2.0,
      m1_atr=1.0,
      zones=({"bottom": 4251.0, "top": 4252.0, "side": "supply",
              "touches": 3, "mitigated": False, "score": 5.0},),
    )

  # Same real-world opportunity, discovered on two different M5 bars (an
  # M5 rollover while still waiting for fill) - context_id necessarily
  # differs since it's bucketed by m5_bar_ts.
  first = discover_impulse_pullback(
    _ctx("ctx-bar-1", 1_780_003_600), None, flat, _cfg(), pip_size=0.1,
    now=1_780_003_600,
  )
  second = discover_impulse_pullback(
    _ctx("ctx-bar-2", 1_780_003_900), None, flat, _cfg(), pip_size=0.1,
    now=1_780_003_900,
  )
  assert len(first) == 1
  assert len(second) == 1
  assert first[0].context_id != second[0].context_id
  assert first[0].episode_id == second[0].episode_id
  assert first[0].episode_id != ""


def _drift_bars(*, direction: str, bars: int = 60, step: float = 0.7, start: float = 4230.0):
  """A wide, gentle net drift -- mimics a real reclaim: not every bar is
  directional (unlike _thrust_bars), the NET displacement over the whole
  window is what matters here.
  """
  rows = []
  index = []
  price = start
  sign = 1.0 if direction == "BUY" else -1.0
  for i in range(bars):
    wiggle = 0.3 if i % 3 == 0 else -0.15  # net still trends, not monotonic
    o = price
    c = price + sign * step + sign * wiggle
    h = max(o, c) + 0.2
    low = min(o, c) - 0.2
    rows.append({"open": o, "high": h, "low": low, "close": c, "volume": 1.0})
    index.append(pd.Timestamp(1_780_000_000 + i * 60, unit="s", tz="UTC"))
    price = c
  return pd.DataFrame(rows, index=index)


def test_macro_momentum_direction_detects_buy_bias():
  df = _drift_bars(direction="BUY")
  assert macro_momentum_direction(df, atr=8.0) == "BUY"


def test_macro_momentum_direction_detects_sell_bias():
  df = _drift_bars(direction="SELL")
  assert macro_momentum_direction(df, atr=8.0) == "SELL"


def test_macro_momentum_direction_none_when_insufficient_bars():
  df = _drift_bars(direction="BUY", bars=40)
  assert macro_momentum_direction(df, atr=8.0, lookback_bars=60) is None


def test_macro_momentum_direction_none_when_displacement_too_small():
  df = _drift_bars(direction="BUY", step=0.05)
  assert macro_momentum_direction(df, atr=8.0) is None


def test_impulse_pullback_allows_sell_against_fresh_reclaim(monkeypatch):
  # Counter-bias and macro momentum are ranking signals, not discovery vetoes.
  from app.scalping.strategies import discover_impulse_pullback

  local_sell_match = {
    "pattern": "impulse_pullback",
    "direction": "SELL",
    "bar_ts": 1_780_003_600,
    "origin": 4270.0,
    "extreme": 4230.0,
    "pullback_extreme": 4251.5,
    "retracement": 0.5,
    "preferred": True,
    "close": 4250.0,
    "impulse_len": 40.0,
    "body_dominance": 0.8,
    "mean_impulse_body": 2.0,
    "mean_pullback_body": 0.5,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_impulse_pullback",
    lambda df, *, direction: local_sell_match if direction == "SELL" else None,
  )

  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-reclaim",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="unknown",
    m5_structure="range",
    regime="range",
    dealing_range_low=4230.0,
    dealing_range_high=4274.0,
    dealing_range_position=0.95,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=None,
    sell_corridor_room_pips=200.0,
    session="london",
    permitted_archetypes=("impulse_pullback",),
    atr=2.0,
    m1_atr=1.0,
    zones=({"bottom": 4251.0, "top": 4252.0, "side": "supply",
            "touches": 3, "mitigated": False, "score": 5.0},),
  )

  reclaiming = _drift_bars(direction="BUY")
  found = discover_impulse_pullback(
    ctx, None, reclaiming, _cfg(), pip_size=0.1, now=1_780_003_600,
  )
  assert len(found) == 1
  assert found[0].direction == "SELL"


def test_impulse_pullback_discovers_sell_when_htf_bias_up(monkeypatch):
  from app.scalping.strategies import discover_impulse_pullback

  match = {
    "pattern": "impulse_pullback",
    "direction": "SELL",
    "bar_ts": 1_780_003_600,
    "origin": 4270.0,
    "extreme": 4230.0,
    "pullback_extreme": 4251.5,
    "retracement": 0.5,
    "preferred": True,
    "close": 4250.0,
    "impulse_len": 40.0,
    "body_dominance": 0.8,
    "mean_impulse_body": 2.0,
    "mean_pullback_body": 0.5,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_impulse_pullback",
    lambda df, *, direction: match if direction == "SELL" else None,
  )
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-up-bias",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=4230.0,
    dealing_range_high=4274.0,
    dealing_range_position=0.95,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=None,
    sell_corridor_room_pips=200.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_IMPULSE_PULLBACK,),
    atr=2.0,
    m1_atr=1.0,
    zones=({"bottom": 4251.0, "top": 4252.0, "side": "supply",
            "touches": 3, "mitigated": False, "score": 5.0},),
  )
  m1 = _drift_bars(direction="BUY", step=0.0)
  found = discover_impulse_pullback(
    ctx, None, m1, _cfg(), pip_size=0.1, now=1_780_003_600,
  )
  assert len(found) == 1
  assert found[0].direction == "SELL"


def test_activation_allows_counter_bias_and_stamps_htf_bias():
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-counter",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=4230.0,
    dealing_range_high=4274.0,
    dealing_range_position=0.95,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=None,
    sell_corridor_room_pips=200.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_IMPULSE_PULLBACK,),
    atr=8.0,
  )
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="opp-counter",
    context_id=ctx.context_id,
    symbol="XAU",
    archetype=ARCHETYPE_IMPULSE_PULLBACK,
    direction="SELL",
    discovered_at=1_780_003_600,
    source_bar_ts=1_780_003_600,
    zone_low=4248.0,
    zone_high=4252.0,
    key_level=4250.0,
    trigger_type="impulse_pullback",
    trigger_bar_ts=1_780_003_590,
    trigger_price=4250.0,
    invalidation_price=4270.0,
    expected_target_price=4230.0,
    expected_target_pips=20.0,
    expected_stop_pips=15.0,
    expected_reward_risk=1.33,
    location_position=0.95,
    score=0.0,
    reasons=("impulse_pullback",),
    expires_at=1_780_004_500,
  )
  decision = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4249.9,
    quote_ask=4250.1,
    quote_ts=1_780_003_600,
    now=1_780_003_600,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert decision.allowed is True
  assert decision.measured["htf_bias"] == "up"


def test_range_sweep_allows_macro_displacement_against_direction(monkeypatch):
  # Keep structural stop inside [12, 30] — this test is about macro-displacement
  # not being a discovery veto, not about the stop envelope.
  sell_ev = {
    "pattern": "sweep_reclaim",
    "bar_ts": 1_780_003_600,
    "close": 4098.0,
    # stop_price = extreme + buffer must clear zone_high from worst fill
    # without exceeding maximum_pips (30).
    "extreme": 4100.5,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_sweep_reclaim",
    lambda df, *, direction, edge_price, tolerance, lookback_bars: (
      sell_ev if direction == "SELL" else None
    ),
  )
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-range-sweep",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="up",
    m5_structure="range",
    regime="range",
    dealing_range_low=4000.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.95,
    active_range_low=4000.0,
    active_range_high=4100.0,
    active_range_eq=4050.0,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=50.0,
    sell_corridor_room_pips=200.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=8.0,
  )
  # Fresh BUY displacement >= 2.5 ATR would have vetoed the SELL sweep.
  m1 = _drift_bars(direction="BUY", bars=60, step=0.7, start=4050.0)
  found = discover_range_sweep(
    ctx, None, m1, _cfg(), pip_size=0.1, now=1_780_003_600,
  )
  assert len(found) == 1
  assert found[0].direction == "SELL"


def test_impulse_pullback_hard_gates_outside_london_session(monkeypatch):
  from app.scalping.strategies import discover_impulse_pullback, idle_discovery_reasons

  match = {
    "pattern": "impulse_pullback",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "origin": 4590.0,
    "extreme": 4600.0,
    "pullback_extreme": 4593.5,
    "retracement": 0.5,
    "preferred": True,
    "close": 4595.0,
    "impulse_len": 10.0,
    "body_dominance": 0.8,
    "mean_impulse_body": 2.0,
    "mean_pullback_body": 0.5,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_impulse_pullback",
    lambda df, *, direction: match if direction == "BUY" else None,
  )

  base = dict(
    version=CONTEXT_VERSION,
    context_id="ctx-asia",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=4580.0,
    dealing_range_high=4610.0,
    dealing_range_position=0.5,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    permitted_archetypes=(ARCHETYPE_IMPULSE_PULLBACK,),
    atr=4.0,
    m1_atr=1.0,
    zones=({"bottom": 4593.0, "top": 4594.0, "side": "demand",
            "touches": 3, "mitigated": False, "score": 5.0},),
  )
  idx = pd.date_range("2026-08-28 03:00", periods=40, freq="1min", tz="UTC")
  m1 = pd.DataFrame(
    {
      "open": [4594.0] * 40,
      "high": [4596.0] * 40,
      "low": [4593.0] * 40,
      "close": [4595.0] * 40,
      "volume": [1] * 40,
    },
    index=idx,
  )

  asia_ctx = ScalpContextSnapshot(**base, session="asia")
  assert discover_impulse_pullback(asia_ctx, None, m1, _cfg(), pip_size=0.1, now=1_780_003_600) == []
  reasons = idle_discovery_reasons(asia_ctx, m1, _cfg(), pip_size=0.1)
  assert "impulse_pullback:outside_allowed_session:asia" in reasons

  london_ctx = ScalpContextSnapshot(**base, session="london")
  found = discover_impulse_pullback(london_ctx, None, m1, _cfg(), pip_size=0.1, now=1_780_003_600)
  assert len(found) == 1
  assert found[0].direction == "BUY"

  opp = found[0]
  asia_activation = evaluate_scalp_activation(
    opp,
    asia_ctx,
    quote_bid=4593.5,
    quote_ask=4593.6,
    quote_ts=1_780_003_600,
    now=1_780_003_600,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert not asia_activation.allowed
  assert asia_activation.reason_code == "scalp_impulse_outside_allowed_session"

  london_activation = evaluate_scalp_activation(
    opp,
    london_ctx,
    quote_bid=4593.5,
    quote_ask=4593.6,
    quote_ts=1_780_003_600,
    now=1_780_003_600,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert london_activation.allowed

  assert is_impulse_pullback_session_allowed("london", _cfg())
  assert not is_impulse_pullback_session_allowed("asia", _cfg())


def _assert_scalp_stop_invariant(opp: ScalpOpportunity, *, pip_size: float = 0.1) -> None:
  """Never delete: worst-fill/invalidation distance must equal expected_stop_pips."""
  from app.scalping.strategies import _worst_fill

  worst = _worst_fill(
    direction=opp.direction, zone_low=opp.zone_low, zone_high=opp.zone_high,
  )
  derived = abs(float(worst) - float(opp.invalidation_price)) / pip_size
  assert derived == pytest.approx(float(opp.expected_stop_pips), abs=1e-6)
  assert float(opp.expected_reward_risk) == pytest.approx(
    float(opp.expected_target_pips) / float(opp.expected_stop_pips),
    abs=1e-6,
  )


def test_detect_impulse_pullback_returns_pullback_extreme():
  # Wide impulse leg (~80 pts), then a deeper local pullback (~40% retrace)
  # so detection accepts and exposes pullback_extreme.
  idx = pd.date_range("2026-07-01 10:00", periods=30, freq="1min", tz="UTC")
  rows = []
  price = 4000.0
  for _ in range(18):
    o, c = price, price + 4.0
    rows.append({"open": o, "high": c + 0.3, "low": o - 0.2, "close": c, "volume": 1})
    price = c
  # Impulse extreme ~4072. Pull back toward 4040, then bullish continuation.
  pullback_lows = [4065.0, 4055.0, 4045.0, 4040.0, 4042.0, 4048.0]
  for i, low in enumerate(pullback_lows):
    o = 4050.0 - i * 1.5
    c = o - 2.0 if i < 4 else o + 3.0
    rows.append({
      "open": o,
      "high": max(o, c) + 0.5,
      "low": low,
      "close": c,
      "volume": 1,
    })
  # Final bullish continuation bar.
  rows.append({
    "open": 4046.0, "high": 4052.0, "low": 4045.0, "close": 4051.0, "volume": 1,
  })
  df = pd.DataFrame(rows, index=idx[: len(rows)])
  result = detect_impulse_pullback(df, direction="BUY", min_retracement=0.25)
  assert result is not None
  assert not result.get("rejected"), result
  assert "pullback_extreme" in result
  origin = float(result["origin"])
  extreme = float(result["extreme"])
  pullback = float(result["pullback_extreme"])
  assert origin <= pullback <= extreme
  assert pullback > origin or pullback == origin
  # Local pullback low is above the impulse origin on this fixture.
  assert pullback >= 4040.0
  assert "origin" in result  # origin kept for identity / telemetry


def test_impulse_pullback_stop_uses_pullback_extreme_not_origin(monkeypatch):
  from app.scalping.strategies import discover_impulse_pullback

  # Origin is ~80 pips away; the M5 demand zone is the level reference.
  match = {
    "pattern": "impulse_pullback",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "origin": 3980.0,
    "extreme": 4060.0,
    "pullback_extreme": 4048.5,
    "retracement": 0.45,
    "preferred": True,
    "close": 4050.0,
    "impulse_len": 80.0,
    "body_dominance": 0.8,
    "mean_impulse_body": 2.0,
    "mean_pullback_body": 0.5,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_impulse_pullback",
    lambda df, *, direction: match if direction == "BUY" else None,
  )
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-pb-extreme",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=3980.0,
    dealing_range_high=4060.0,
    dealing_range_position=0.5,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_IMPULSE_PULLBACK,),
    atr=2.0,
    m1_atr=1.0,
    zones=({"bottom": 4048.0, "top": 4049.0, "side": "demand",
            "touches": 3, "mitigated": False, "score": 5.0},),
  )
  flat = _drift_bars(direction="BUY", step=0.0, start=4050.0)
  found = discover_impulse_pullback(
    ctx, None, flat, _cfg(), pip_size=0.1, now=1_780_003_600,
  )
  assert len(found) == 1
  opp = found[0]
  _assert_scalp_stop_invariant(opp)
  origin_stop_pips = (opp.trigger_price - (match["origin"] - 0.2)) / 0.1
  assert opp.expected_stop_pips < origin_stop_pips * 0.5
  assert opp.key_level == pytest.approx(4048.0)
  assert opp.invalidation_price < opp.key_level


def test_scalp_stop_invariant_holds_for_all_archetypes(monkeypatch):
  from app.scalping.strategies import (
    discover_breakout_retest,
    discover_impulse_pullback,
    discover_range_sweep,
  )

  pip = 0.1
  idle: list[str] = []

  # --- range_sweep BUY with structural ~18 pips (inside envelope) ---
  buy_ev = {
    "pattern": "sweep_reclaim",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "extreme": 3999.8,
    "close": 4001.0,
    "edge": 4000.0,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_sweep_reclaim",
    lambda df, *, direction, edge_price, tolerance, lookback_bars=1: (
      buy_ev if direction == "BUY" else None
    ),
  )
  range_ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-inv-range",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=3990.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.2,
    active_range_low=4000.0,
    active_range_high=4100.0,
    active_range_eq=4050.0,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=2.0,
  )
  flat = _drift_bars(direction="BUY", step=0.0, start=4000.0)
  range_found = discover_range_sweep(
    range_ctx, None, flat, _cfg(), pip_size=pip, now=1_780_003_600, idle_reasons=idle,
  )
  assert range_found
  for opp in range_found:
    _assert_scalp_stop_invariant(opp, pip_size=pip)

  # --- impulse_pullback ---
  pb = {
    "pattern": "impulse_pullback",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "origin": 3980.0,
    "extreme": 4020.0,
    "pullback_extreme": 3998.5,
    "retracement": 0.5,
    "preferred": True,
    "close": 4000.0,
    "impulse_len": 40.0,
    "body_dominance": 0.8,
    "mean_impulse_body": 2.0,
    "mean_pullback_body": 0.5,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_impulse_pullback",
    lambda df, *, direction: pb if direction == "BUY" else None,
  )
  pb_ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-inv-pb",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=3980.0,
    dealing_range_high=4020.0,
    dealing_range_position=0.5,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_IMPULSE_PULLBACK,),
    atr=2.0,
    m1_atr=1.0,
    zones=({"bottom": 3998.0, "top": 3999.0, "side": "demand",
            "touches": 3, "mitigated": False, "score": 5.0},),
  )
  pb_found = discover_impulse_pullback(
    pb_ctx, None, flat, _cfg(), pip_size=pip, now=1_780_003_600, idle_reasons=idle,
  )
  assert pb_found
  for opp in pb_found:
    _assert_scalp_stop_invariant(opp, pip_size=pip)

  # --- breakout_retest ---
  monkeypatch.setattr(
    "app.scalping.strategies.find_compression_box",
    lambda *a, **k: {
      "box_low": 3990.0,
      "box_high": 4000.0,
      "box_bars": 12,
      "compression_atr": 0.5,
      "touch_count": 4,
    },
  )
  monkeypatch.setattr(
    "app.scalping.strategies.detect_breakout_retest",
    lambda df, *, direction, **kwargs: (
      {
        "pattern": "breakout_retest",
        "direction": "BUY",
        "bar_ts": 1_780_003_600,
        "close": 4002.0,
        "level": 4000.0,
        "state": "armed",
        "accepted_break": True,
        "correct_key_level_role": True,
        "retest_of_broken_level": True,
        "retest_rejection": True,
        "directionally_valid_close": True,
        "break_displacement": 2.0,
      }
      if direction == "BUY"
      else None
    ),
  )
  # Last bar low near level so structural stop stays inside [12, 30].
  m1 = flat.copy()
  m1.iloc[-1, m1.columns.get_loc("low")] = 3999.5
  m1.iloc[-1, m1.columns.get_loc("high")] = 4002.5
  m1.iloc[-1, m1.columns.get_loc("close")] = 4002.0
  bo_ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-inv-bo",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=3980.0,
    dealing_range_high=4050.0,
    dealing_range_position=0.5,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_BREAKOUT_RETEST,),
    atr=2.0,
  )
  bo_found = discover_breakout_retest(
    bo_ctx, None, m1, _cfg(), pip_size=pip, now=1_780_003_600, idle_reasons=idle,
  )
  assert bo_found
  for opp in bo_found:
    _assert_scalp_stop_invariant(opp, pip_size=pip)


def test_structural_stop_above_maximum_rejects_with_idle_reason(monkeypatch):
  from app.scalping.strategies import discover_range_sweep

  buy_ev = {
    "pattern": "sweep_reclaim",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "extreme": 3960.0,  # ~40+ pips below entry after buffer
    "close": 4001.0,
    "edge": 4000.0,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_sweep_reclaim",
    lambda df, *, direction, edge_price, tolerance, lookback_bars=1: (
      buy_ev if direction == "BUY" else None
    ),
  )
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-stop-max",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=3950.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.2,
    active_range_low=4000.0,
    active_range_high=4100.0,
    active_range_eq=4050.0,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=2.0,
  )
  idle: list[str] = []
  flat = _drift_bars(direction="BUY", step=0.0, start=4000.0)
  found = discover_range_sweep(
    ctx, None, flat, _cfg(), pip_size=0.1, now=1_780_003_600, idle_reasons=idle,
  )
  assert found == []
  assert "range_sweep:stop_exceeds_maximum" in idle


def test_structural_stop_below_minimum_widens_invalidation_outward(monkeypatch):
  from app.scalping.strategies import discover_range_sweep

  # BUY: the noise-scaled buffer keeps the structural stop above the old
  # minimum floor instead of letting the 12-pip floor determine the result.
  buy_ev = {
    "pattern": "sweep_reclaim",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "extreme": 3999.8,
    "close": 4001.0,
    "edge": 4000.0,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_sweep_reclaim",
    lambda df, *, direction, edge_price, tolerance, lookback_bars=1: (
      buy_ev if direction == "BUY" else None
    ),
  )
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-stop-min-buy",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=3990.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.2,
    active_range_low=4000.0,
    active_range_high=4100.0,
    active_range_eq=4050.0,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=2.0,
  )
  flat = _drift_bars(direction="BUY", step=0.0, start=4000.0)
  found = discover_range_sweep(
    ctx, None, flat, _cfg(), pip_size=0.1, now=1_780_003_600,
  )
  assert len(found) == 1
  opp = found[0]
  assert opp.expected_stop_pips == pytest.approx(24.5)
  _assert_scalp_stop_invariant(opp)
  buffer = _stop_buffer(ctx, _cfg(), 0.1)
  raw_stop_price = float(buy_ev["extreme"]) - buffer
  assert opp.invalidation_price == pytest.approx(raw_stop_price)
  assert opp.invalidation_price < opp.trigger_price

  # SELL: same floor — invalidation moves further above entry.
  sell_ev = {
    "pattern": "sweep_reclaim",
    "direction": "SELL",
    "bar_ts": 1_780_003_600,
    "extreme": 4100.2,
    "close": 4099.0,
    "edge": 4100.0,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_sweep_reclaim",
    lambda df, *, direction, edge_price, tolerance, lookback_bars=1: (
      sell_ev if direction == "SELL" else None
    ),
  )
  sell_ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-stop-min-sell",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=4000.0,
    dealing_range_high=4110.0,
    dealing_range_position=0.9,
    active_range_low=4000.0,
    active_range_high=4100.0,
    active_range_eq=4050.0,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=2.0,
  )
  sell_found = discover_range_sweep(
    sell_ctx, None, flat, _cfg(), pip_size=0.1, now=1_780_003_600,
  )
  assert len(sell_found) == 1
  sell_opp = sell_found[0]
  assert sell_opp.expected_stop_pips == pytest.approx(24.5)
  _assert_scalp_stop_invariant(sell_opp)
  sell_buffer = _stop_buffer(sell_ctx, _cfg(), 0.1)
  raw_sell_stop = float(sell_ev["extreme"]) + sell_buffer
  assert sell_opp.invalidation_price == pytest.approx(raw_sell_stop)
  assert sell_opp.invalidation_price > sell_opp.trigger_price


def test_structural_stop_inside_envelope_leaves_fields_untouched(monkeypatch):
  from app.scalping.strategies import discover_range_sweep

  # The M1/spread buffer is used consistently for the in-envelope case.
  buy_ev = {
    "pattern": "sweep_reclaim",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "extreme": 3999.8,
    "close": 4001.0,
    "edge": 4000.0,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_sweep_reclaim",
    lambda df, *, direction, edge_price, tolerance, lookback_bars=1: (
      buy_ev if direction == "BUY" else None
    ),
  )
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-stop-inside",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=3990.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.2,
    active_range_low=4000.0,
    active_range_high=4100.0,
    active_range_eq=4050.0,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=2.0,
  )
  flat = _drift_bars(direction="BUY", step=0.0, start=4000.0)
  found = discover_range_sweep(
    ctx, None, flat, _cfg(), pip_size=0.1, now=1_780_003_600,
  )
  assert len(found) == 1
  opp = found[0]
  buffer = _stop_buffer(ctx, _cfg(), 0.1)
  raw_stop_price = float(buy_ev["extreme"]) - buffer
  zone_low = float(ctx.active_range_low) - buffer
  zone_high = float(ctx.active_range_low) + buffer * 2
  worst = zone_high  # BUY
  raw_pips = (worst - raw_stop_price) / 0.1
  assert 12.0 < raw_pips < 30.0
  assert opp.expected_stop_pips == pytest.approx(raw_pips)
  assert opp.invalidation_price == pytest.approx(raw_stop_price)
  assert opp.invalidation_price < zone_low
  _assert_scalp_stop_invariant(opp)
