"""XAU technique structure fixed_rr — scalp path stays separate."""

from __future__ import annotations

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.core.instrument_geometry import (
  fixed_reward_risk,
  technique_fixed_rr_targeting,
)
from app.scalping.context import is_scalping_symbol
from app.scalping.models import OPPORTUNITY_VERSION, ScalpOpportunity
from app.scalping.publish import _scalp_target_ladder
from tests.test_config_effective_instrument_context import _load_production_example
from tests.test_execution_pipeline_integrity import _policy_match


pytestmark = pytest.mark.no_database


def test_technique_fixed_rr_targeting_skips_m1_scalp():
  cfg = _load_production_example().config
  assert fixed_reward_risk("XAU", cfg) == 3.0
  key = technique_fixed_rr_targeting("XAU", "Key Level Reaction", cfg)
  assert key is not None
  assert float(key.reward_risk) == 3.0
  assert technique_fixed_rr_targeting("XAU", "Impulse Pullback Scalp", cfg) is None
  assert technique_fixed_rr_targeting("XAU", "Impulse Pullback Scalp", cfg) is None
  assert technique_fixed_rr_targeting("EURUSD", "Key Level Reaction", cfg) is not None
  assert technique_fixed_rr_targeting("EURUSD", "Range Sweep Scalp", cfg) is None


def test_xau_still_hosts_m1_scalping_with_technique_fixed_rr():
  cfg = _load_production_example().config
  assert is_scalping_symbol("XAU", cfg)
  assert not is_scalping_symbol("EURUSD", cfg)
  assert float(cfg.for_instrument("XAU").strategies.scalping.policy.minimum_reward_risk) == 1.10


def test_xau_key_level_expands_fixed_rr_targets_from_stop():
  cfg = _load_production_example().config
  match = _policy_match(
    strategy="Key Level Reaction",
    family="reaction",
    strategy_mode="with_trend",
    symbol="XAU",
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=4102.5,
    executable_quote=4102.5,
    regime="trend",
    pip_size=0.1,
    cfg=cfg,
    available_target_room_pips=200.0,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["target_policy_mode"] == "fixed_rr"
  multiples = [
    float(value)
    for value in evaluation.measured.get("planned_target_r_multiples")
  ]
  assert multiples == [0.5, 1.0, 2.0, 3.0]
  stop_pips = float(evaluation.measured["planned_final_stop_pips"])
  targets = [float(value) for value in evaluation.measured["planned_target_pips"]]
  assert len(targets) == 4
  assert targets[0] == pytest.approx(stop_pips * 0.5, rel=0.02)
  assert targets[1] == pytest.approx(stop_pips * 1.0, rel=0.02)
  assert targets[2] == pytest.approx(stop_pips * 2.0, rel=0.02)
  assert targets[3] == pytest.approx(stop_pips * 3.0, rel=0.02)
  assert evaluation.measured["breakeven_after_r"] == pytest.approx(0.5)
  assert evaluation.measured["target_room_fallback_used"] is False
  assert evaluation.measured["planned_target_close_ratios"] == (
    ["0.4", "0.2", "0.2", "0.2"]
  )


def test_xau_scalp_match_does_not_expand_technique_fixed_rr_ladder():
  cfg = _load_production_example().config
  match = _policy_match(
    strategy="Impulse Pullback Scalp",
    family="scalp",
    strategy_mode="scalp_m1",
    symbol="XAU",
    targets_pips=(20, 40),
    full_tp_pips=40,
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=4102.5,
    executable_quote=4102.5,
    regime="trend",
    pip_size=0.1,
    cfg=cfg,
    available_target_room_pips=80.0,
  )
  if evaluation.allowed:
    assert evaluation.measured.get("target_policy_mode") != "fixed_rr"


def test_xau_scalp_publish_ladder_stays_one_r_two_r():
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="opp-1",
    context_id="ctx",
    symbol="XAU",
    archetype="impulse_pullback",
    direction="BUY",
    discovered_at=1,
    source_bar_ts=1,
    zone_low=4100.0,
    zone_high=4101.0,
    key_level=4100.5,
    trigger_type="body_close",
    trigger_bar_ts=1,
    trigger_price=4100.5,
    invalidation_price=4098.0,
    expected_target_price=4104.0,
    expected_target_pips=40.0,
    expected_stop_pips=20.0,
    expected_reward_risk=2.0,
    location_position=0.3,
    score=1.0,
    reasons=("test",),
    expires_at=100,
  )
  final_pips, ladder = _scalp_target_ladder(opp, _load_production_example().config)
  assert final_pips == 40
  assert ladder == (20, 40)
