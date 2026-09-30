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
