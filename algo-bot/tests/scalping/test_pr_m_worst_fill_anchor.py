"""PR-M: worst-case fill anchoring, stop_inside_zone, chase fraction, realized R."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.autotrade.execution_confirmation import (
  ZONE_ACCESS_RETEST_ONLY,
  scalp_effective_chase_pips,
  scalp_zone_access,
)
from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
  CONTEXT_VERSION,
  LIVE_OUTCOME_VERSION,
  ScalpContextSnapshot,
  ScalpOpportunity,
  OPPORTUNITY_VERSION,
)
from app.scalping.outcomes import (
  ExcursionState,
  finalize_live_outcome,
  resolve_risk_denominator,
  EXIT_FULL_STOP,
)
from app.scalping.strategies import (
  _stop_pips,
  _worst_fill,
  _zone_stop_ordered,
  discover_range_sweep,
)


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
    context=SimpleNamespace(maximum_m5_age_seconds=420, m1_lookback_bars=60),
    location=SimpleNamespace(
      range_buy_maximum_position=0.35,
      range_sell_minimum_position=0.65,
      pullback_buy_maximum_position=0.75,
      pullback_sell_minimum_position=0.25,
    ),
    activation=SimpleNamespace(
      trigger_maximum_age_bars=2,
      maximum_chase_pips=40.0,
      maximum_chase_stop_fraction=0.15,
      rearm_distance_atr=0.25,
    ),
    target=SimpleNamespace(
      preferred_ladder_pips="20,25,30",
      minimum_net_target_pips=10.0,
    ),
    stop=SimpleNamespace(minimum_pips=12.0, maximum_pips=30.0, buffer_atr=0.1),
    policy=SimpleNamespace(
      minimum_reward_risk=1.10,
      maximum_opportunities_per_cycle=3,
      maximum_active_opportunities=10,
      maximum_spread_pips=5.0,
    ),
    risk=SimpleNamespace(mode="shadow", risk_fraction_per_trade=0.10),
  )
  for key, value in overrides.items():
    setattr(scalp_cfg, key, value)
  return SimpleNamespace(strategies=SimpleNamespace(scalping=scalp_cfg))


def _assert_worst_fill_stop(opp: ScalpOpportunity, *, pip_size: float = 0.1) -> None:
  worst = _worst_fill(
    direction=opp.direction, zone_low=opp.zone_low, zone_high=opp.zone_high,
  )
  derived = abs(worst - float(opp.invalidation_price)) / pip_size
  assert derived == pytest.approx(float(opp.expected_stop_pips), abs=1e-6)
  assert float(opp.expected_reward_risk) == pytest.approx(
    float(opp.expected_target_pips) / float(opp.expected_stop_pips),
    abs=1e-6,
  )


@pytest.mark.parametrize("direction", ("BUY", "SELL"))
@pytest.mark.parametrize(
  "archetype",
  (ARCHETYPE_RANGE_SWEEP, ARCHETYPE_IMPULSE_PULLBACK, ARCHETYPE_BREAKOUT_RETEST),
)
def test_zone_stop_ordered_rejects_inside(direction, archetype):
  if direction == "BUY":
    assert not _zone_stop_ordered(
      direction=direction, invalidation=100.5, zone_low=100.0, zone_high=101.0,
    )
    assert _zone_stop_ordered(
      direction=direction, invalidation=99.0, zone_low=100.0, zone_high=101.0,
    )
  else:
    assert not _zone_stop_ordered(
      direction=direction, invalidation=100.5, zone_low=100.0, zone_high=101.0,
    )
    assert _zone_stop_ordered(
      direction=direction, invalidation=102.0, zone_low=100.0, zone_high=101.0,
    )
  del archetype  # same geometric check for every archetype


def test_stop_pips_widening_floor_never_inward():
  cfg = _cfg()
  # Structural below minimum → floor; invalidation moves outward from worst.
  stop, reject = _stop_pips(structural=6.0, cfg=cfg)
  assert reject is None
  assert stop == pytest.approx(12.0)
  # Structural above maximum → reject, no silent clamp.
  stop_hi, reject_hi = _stop_pips(structural=40.0, cfg=cfg)
  assert stop_hi is None
  assert reject_hi == "stop_exceeds_maximum"
  # Structural inside envelope unchanged.
  stop_mid, reject_mid = _stop_pips(structural=18.0, cfg=cfg)
  assert reject_mid is None
  assert stop_mid == pytest.approx(18.0)


def test_failure_a_widening_from_worst_clears_zone(monkeypatch):
  """Failure A prices: old trigger-anchor put SL inside zone; worst-fill clears it."""
  key_level = 4432.04
  buffer = 0.29
  zone_low = key_level - buffer
  zone_high = key_level + buffer * 2
  trigger_close = 4433.19
  structural_stop_price = 4432.01
  pip = 0.1
  buy_ev = {
    "pattern": "sweep_reclaim",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "extreme": structural_stop_price + buffer,
    "close": trigger_close,
    "edge": key_level,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_sweep_reclaim",
    lambda df, *, direction, edge_price, tolerance, lookback_bars=1: (
      buy_ev if direction == "BUY" else None
    ),
  )
  atr = 20.0  # M5 ATR must not drive the L7 M1 stop buffer.
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-failure-a",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=4420.0,
    dealing_range_high=4450.0,
    dealing_range_position=0.2,
    active_range_low=key_level,
    active_range_high=key_level + 20.0,
    active_range_eq=key_level + 10.0,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=80.0,
    sell_corridor_room_pips=80.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=atr,
    m1_atr=buffer / 1.2,
  )
  import pandas as pd

  idx = pd.date_range("2026-08-30 10:00", periods=5, freq="1min", tz="UTC")
  flat = pd.DataFrame(
    {
      "open": [trigger_close] * 5,
      "high": [trigger_close] * 5,
      "low": [structural_stop_price] * 5,
      "close": [trigger_close] * 5,
    },
    index=idx,
  )
  idle: list[str] = []
  cfg = _cfg()
  cfg.strategies.scalping.policy.maximum_spread_pips = 1.0
  found = discover_range_sweep(
    ctx, None, flat, cfg, pip_size=pip, now=1_780_003_600, idle_reasons=idle,
  )
  assert abs(zone_low - (key_level - buffer)) < 1e-9
  assert abs(zone_high - (key_level + buffer * 2)) < 1e-9
  # Zone width ~8.7p < 12p floor → widening from worst fill pushes SL outside.
  assert len(found) == 1
  opp = found[0]
  assert opp.invalidation_price < opp.zone_low < opp.zone_high
  _assert_worst_fill_stop(opp, pip_size=pip)
  assert "range_sweep:stop_inside_zone" not in idle


def test_failure_a_stop_inside_zone_when_structural_stays_inside(monkeypatch):
  """Wide zone + structural stop inside zone with no floor needed → reject."""
  key_level = 4000.0
  # Large ATR → wide zone; structural stop sits inside and already ≥ minimum.
  atr = 20.0
  buffer = max(0.2, atr * 0.05)  # 1.0
  zone_low = key_level - buffer
  zone_high = key_level + buffer * 2
  # Stop inside zone, 15 pips below worst (zone_high) → no floor, stays inside.
  structural_extreme = zone_high - 1.5 + buffer  # stop_price = extreme - buffer
  buy_ev = {
    "pattern": "sweep_reclaim",
    "direction": "BUY",
    "bar_ts": 1_780_003_600,
    "extreme": structural_extreme,
    "close": zone_high + 0.5,
    "edge": key_level,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_sweep_reclaim",
    lambda df, *, direction, edge_price, tolerance, lookback_bars=1: (
      buy_ev if direction == "BUY" else None
    ),
  )
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-inside-zone",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=3980.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.2,
    active_range_low=key_level,
    active_range_high=key_level + 50.0,
    active_range_eq=key_level + 25.0,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=200.0,
    sell_corridor_room_pips=200.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=atr,
  )
  import pandas as pd

  idx = pd.date_range("2026-08-30 10:00", periods=5, freq="1min", tz="UTC")
  flat = pd.DataFrame(
    {"open": [4001.0] * 5, "high": [4002.0] * 5, "low": [3999.0] * 5, "close": [4001.0] * 5},
    index=idx,
  )
  idle: list[str] = []
  found = discover_range_sweep(
    ctx, None, flat, _cfg(), pip_size=0.1, now=1_780_003_600, idle_reasons=idle,
  )
  assert found == []
  assert "range_sweep:stop_inside_zone" in idle
  # Geometry check for the numbers the site would have produced.
  stop_price = structural_extreme - buffer
  worst = zone_high
  structural = (worst - stop_price) / 0.1
  assert structural >= 12.0
  assert zone_low < stop_price < zone_high


def test_failure_b_worst_case_rr_from_zone_high():
  """Failure B: RR measured from zone_high is ≈0.64, not the published 1.0R."""
  zone_low, zone_high = 4441.52, 4442.62
  invalidation = 4440.85
  tp1 = 4443.75
  pip = 0.1
  worst = _worst_fill(direction="BUY", zone_low=zone_low, zone_high=zone_high)
  assert worst == pytest.approx(zone_high)
  stop = (worst - invalidation) / pip
  reward = (tp1 - worst) / pip
  rr = reward / stop
  assert rr == pytest.approx(0.64, abs=0.02)
  # Old trigger-anchored RR would have been ~1.0 from close 4442.28.
  trigger = 4442.28
  old_stop = (trigger - invalidation) / pip
  old_reward = (tp1 - trigger) / pip
  assert old_reward / old_stop == pytest.approx(1.0, abs=0.05)


def test_chase_cap_is_stop_fraction():
  cfg = _cfg()
  # 12p stop × 0.15 = 1.8; flat cap 40 → effective 1.8
  assert scalp_effective_chase_pips(cfg, stop_pips=12.0) == pytest.approx(1.8)
  # Exact boundary: chase at cap is still executable.
  access = scalp_zone_access(
    "BUY", 100.0, 101.18, 100.0, 101.0, 0.0,
    pip_size=0.1,
    maximum_chase_pips=1.8,
  )
  assert access.status == "chase"
  assert access.chase_pips == pytest.approx(1.8)
  missed = scalp_zone_access(
    "BUY", 100.0, 101.19, 100.0, 101.0, 0.0,
    pip_size=0.1,
    maximum_chase_pips=1.8,
  )
  assert missed.status == "chase_missed"


def test_breakout_retest_not_chase_eligible():
  access = scalp_zone_access(
    "BUY", 100.0, 102.0, 100.0, 101.0, 0.0,
    pip_size=0.1,
    maximum_chase_pips=40.0,
    zone_access_mode=ZONE_ACCESS_RETEST_ONLY,
  )
  assert access.status == "approach_wait"
  assert not access.executable


def test_realized_r_denominator_and_ratio():
  # Failure B: fill 4443.57, invalidation 4440.85, planned 14.33 → ≈1.90
  realized, source, ratio = resolve_risk_denominator(
    fill_price=4443.57,
    invalidation_price=4440.85,
    pip_size=0.1,
    expected_stop_pips=14.33,
  )
  assert source == "realized"
  assert realized == pytest.approx(27.2, abs=0.05)
  assert ratio == pytest.approx(27.2 / 14.33, abs=0.05)

  # Failure A-like: tiny realized vs planned 12 → ≈0.11
  realized_a, source_a, ratio_a = resolve_risk_denominator(
    fill_price=4432.12,
    invalidation_price=4431.99,
    pip_size=0.1,
    expected_stop_pips=12.0,
  )
  assert source_a == "realized"
  assert ratio_a == pytest.approx(1.3 / 12.0, abs=0.02)

  planned, source_p, ratio_p = resolve_risk_denominator(
    fill_price=None,
    invalidation_price=4440.85,
    pip_size=0.1,
    expected_stop_pips=14.33,
  )
  assert source_p == "planned"
  assert planned == pytest.approx(14.33)
  assert ratio_p is None


def test_finalize_stamps_schema_version_and_source():
  excursion = ExcursionState(
    opportunity_id="oid",
    episode_id="ep",
    symbol="XAU",
    archetype=ARCHETYPE_BREAKOUT_RETEST,
    direction="BUY",
    session="london",
    htf_bias="range",
    regime="range",
    entry_price=4443.57,
    invalidation_price=4440.85,
    stop_pips=27.2,
    planned_target_pips=14.33,
    planned_rr=0.64,
    group_id="g",
    match_id="m",
    opened_at=1,
    pip_size=0.1,
    max_high=4443.57,
    min_low=4440.85,
    expected_stop_pips=14.33,
    risk_denominator_source="realized",
    planned_vs_realized_stop_ratio=27.2 / 14.33,
    version=LIVE_OUTCOME_VERSION,
  )
  outcome = finalize_live_outcome(
    excursion, exit_path=EXIT_FULL_STOP, realized_pips=-27.2, closed_at=2,
  )
  assert outcome.version == LIVE_OUTCOME_VERSION
  assert outcome.risk_denominator_source == "realized"
  assert outcome.planned_vs_realized_stop_ratio == pytest.approx(27.2 / 14.33)
  assert outcome.expected_stop_pips == pytest.approx(14.33)
