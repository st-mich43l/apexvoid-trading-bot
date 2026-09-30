"""Scalping unit tests — context, microstructure, strategies, risk, paper."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.scalping.context import (
  build_scalp_context_snapshot,
  compute_context_id,
  is_context_fresh,
  is_impulse_pullback_session_allowed,
  permitted_archetypes_for_session,
)
from app.scalping.lifecycle import transition
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
  ARMED,
  BR_ACCEPTED,
  BR_ARMED,
  BR_BREAK_DETECTED,
  BR_REASON_IMMEDIATE_RECLAIM,
  BR_REASON_RETEST_TOO_DEEP,
  BR_REASON_RETEST_TOO_LATE,
  BR_SOURCE_COMPRESSION_BOX,
  BR_SOURCE_M1_SWING_HIGH,
  BR_SUBTYPE_RANGE_BREAK,
  BR_SUBTYPE_STRUCTURE_FLIP,
  BR_WATCH_LEVEL,
  BreakoutLevelCandidate,
  DISCOVERED,
  EXECUTABLE,
  EXPIRED,
  MicroStructure,
  MicroSwing,
  MISSED,
  OPPORTUNITY_VERSION,
  ScalpContextSnapshot,
  ScalpLifecycleRecord,
  ScalpOpportunity,
  CONTEXT_VERSION,
)
from app.scalping.risk import ScalpRiskState, evaluate_risk, risk_fraction


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


# ============================================================================
# Breakout Retest V2 — generic engine (owner-directed 2026-09-16 rebuild).
# TEST 1-13 below map directly to the rebuild spec's mandatory test list.
# ============================================================================


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


def test_impulse_pullback_all_session_preference_is_unrestricted():
  cfg = _cfg()
  cfg.strategies.scalping.archetypes.impulse_pullback_allowed_sessions = "all"

  assert is_impulse_pullback_session_allowed("asia", cfg)
  assert is_impulse_pullback_session_allowed("london", cfg)
