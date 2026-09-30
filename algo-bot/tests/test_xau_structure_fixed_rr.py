"""XAU technique structure fixed_rr — scalp path stays separate."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match
from app.core.instrument_geometry import (
  fixed_reward_risk,
  technique_fixed_rr_targeting,
)
from app.scalping.context import is_scalping_symbol
from app.scalping.models import OPPORTUNITY_VERSION, ScalpOpportunity
from app.scalping.publish import _scalp_target_ladder
from tests.test_config_effective_instrument_context import _load_production_example
from tests.test_execution_pipeline_integrity import _policy_match
from tests.test_publish_trade_plan_v8 import _match


pytestmark = pytest.mark.no_database


def test_technique_fixed_rr_targeting_skips_m1_scalp():
  cfg = _load_production_example().config
  assert fixed_reward_risk("XAU", cfg) == 4.0
  key = technique_fixed_rr_targeting("XAU", "Key Level", cfg)
  assert key is not None
  assert float(key.reward_risk) == 4.0
  assert technique_fixed_rr_targeting("XAU", "Impulse Pullback Scalp", cfg) is None
  assert technique_fixed_rr_targeting("XAU", "Impulse Pullback Scalp", cfg) is None
  assert technique_fixed_rr_targeting("EURUSD", "Key Level", cfg) is not None
  assert technique_fixed_rr_targeting("EURUSD", "Range Sweep Scalp", cfg) is None


def test_xau_still_hosts_m1_scalping_with_technique_fixed_rr():
  cfg = _load_production_example().config
  assert is_scalping_symbol("XAU", cfg)
  assert not is_scalping_symbol("EURUSD", cfg)
  assert float(cfg.for_instrument("XAU").strategies.scalping.policy.minimum_reward_risk) == 1.10


def test_xau_key_level_expands_fixed_rr_targets_from_stop():
  cfg = _load_production_example().config
  match = _policy_match(
    strategy="Key Level",
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
    # Room must fit the full 4R target (final stop lands at 55p within the
    # new 50-60p band -> 4R = 220p) with margin, not just the old 3R shape.
    available_target_room_pips=300.0,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["target_policy_mode"] == "fixed_rr"
  multiples = [
    float(value)
    for value in evaluation.measured.get("planned_target_r_multiples")
  ]
  assert multiples == [1.0, 2.0, 3.0, 4.0]
  stop_pips = float(evaluation.measured["planned_final_stop_pips"])
  targets = [float(value) for value in evaluation.measured["planned_target_pips"]]
  assert len(targets) == 4
  assert targets[0] == pytest.approx(stop_pips * 1.0, rel=0.02)
  assert targets[1] == pytest.approx(stop_pips * 2.0, rel=0.02)
  assert targets[2] == pytest.approx(stop_pips * 3.0, rel=0.02)
  assert targets[3] == pytest.approx(stop_pips * 4.0, rel=0.02)
  assert evaluation.measured["breakeven_after_r"] == pytest.approx(1.0)
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
    assert evaluation.measured["target_policy_mode"] == "scalp_rr"


def test_live_go_range_sweep_is_capped_to_one_r_two_r():
  """Production replay: the opposite M5 edge is room, not a 291-pip TP."""
  cfg = _load_production_example().config
  match = _policy_match(
    strategy="Range Sweep Scalp",
    family="range_reversion",
    strategy_mode="go_m5_m1_range_sweep",
    symbol="XAU",
    direction="SELL",
    entry_low=4194.90,
    entry_high=4196.46,
    current_price=4195.27,
    atr=4.232,
    structure_swing=4198.888285714286,
    go_invalidation_price=4198.888285714286,
    targets_pips=(291,),
    absolute_target_price=4165.78,
    structural_kind="range_sweep",
    tags=("origin:go", "catalog:range_sweep"),
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=4195.27,
    executable_quote=4194.88,
    regime="range",
    pip_size=0.1,
    cfg=cfg,
  )

  assert evaluation.allowed is True
  assert evaluation.measured["target_policy_mode"] == "scalp_rr"
  assert evaluation.measured["planned_target_r_multiples"] == ["1.0", "2.0"]
  assert evaluation.measured["planned_target_close_ratios"] == ["0.5", "0.5"]
  stop_pips = float(evaluation.measured["planned_final_stop_pips"])
  target_pips = [
    float(value) for value in evaluation.measured["planned_target_pips"]
  ]
  assert target_pips == pytest.approx([stop_pips, stop_pips * 2], abs=0.11)
  assert target_pips[-1] < 100

  plan_match = _match(
    match_id="go-range-sweep-production-replay",
    thesis_id="range-sweep-thesis",
    strategy="Range Sweep Scalp",
    family="range_reversion",
    strategy_mode="go_m5_m1_range_sweep",
    direction="SELL",
    source_tf="M1",
    entry_low=4194.90,
    entry_high=4196.46,
    current_price=4195.27,
    atr=4.232,
    structure_swing=4198.888285714286,
    go_invalidation_price=4198.888285714286,
    targets_pips=(291,),
    absolute_target_price=4165.78,
    structural_zone_id="range-sweep:2797",
    structural_zone_low=4194.90,
    structural_zone_high=4196.46,
    structural_kind="range_sweep",
    structural_timeframe="M5",
    tags=("origin:go", "catalog:range_sweep"),
  )
  plan = build_trade_plan_from_strategy_match(
    plan_match,
    plan_id="v8:go-range-sweep-production-replay",
    setup_id=plan_match.match_id,
    thesis_id=plan_match.thesis_id,
    pip_size=Decimal("0.1"),
    spot_price=4195.27,
    executable_quote=4194.88,
    regime="range",
    cfg=cfg.for_instrument("XAU"),
    max_volume=100000,
    approved_measured=evaluation.measured,
  )
  assert [target.close_ratio for target in plan.targets] == [
    Decimal("0.5"), Decimal("0.5"),
  ]
  assert [target.price for target in plan.targets] == [
    Decimal(value) for value in evaluation.measured["planned_target_prices"]
  ]
  assert plan.targets[-1].price > Decimal("4185")


def test_live_go_xau_fvg_gets_30_pip_execution_band_only():
  """Production replay: raw 15.4-pip FVG remains structural provenance."""
  cfg = _load_production_example().config
  match = _policy_match(
    strategy="FVG",
    family="supply_demand",
    strategy_mode="go_m5_fvg",
    symbol="XAU",
    direction="BUY",
    entry_low=4195.90,
    entry_high=4197.44,
    current_price=4196.84,
    atr=4.232,
    structure_swing=4193.783928571428,
    go_invalidation_price=4193.783928571428,
    targets_pips=(100,),
    absolute_target_price=4207.44,
    structural_kind="fvg",
    tags=("origin:go", "catalog:fvg"),
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=4196.84,
    executable_quote=4196.84,
    regime="trend",
    pip_size=0.1,
    cfg=cfg,
  )

  assert evaluation.allowed is True
  assert evaluation.measured["execution_zone_expanded"] is True
  assert evaluation.measured["planned_entry_zone_low"] == pytest.approx(4194.44)
  assert evaluation.measured["planned_entry_zone_high"] == pytest.approx(4197.44)
  assert match.entry_low == 4195.90 and match.entry_high == 4197.44
  assert evaluation.measured["planned_leg_entry_prices"] == pytest.approx(
    [4197.44, 4195.94],
  )

  plan_match = _match(
    match_id="go-fvg-production-replay",
    thesis_id="fvg-thesis",
    strategy="FVG",
    family="supply_demand",
    strategy_mode="go_m5_fvg",
    direction="BUY",
    source_tf="M5",
    entry_low=4195.90,
    entry_high=4197.44,
    current_price=4196.84,
    atr=4.232,
    structure_swing=4193.783928571428,
    go_invalidation_price=4193.783928571428,
    targets_pips=(100,),
    absolute_target_price=4207.44,
    structural_zone_id="fvg:1983",
    structural_zone_low=4195.90,
    structural_zone_high=4197.44,
    structural_kind="fvg",
    structural_timeframe="M5",
    tags=("origin:go", "catalog:fvg"),
  )
  plan = build_trade_plan_from_strategy_match(
    plan_match,
    plan_id="v8:go-fvg-production-replay",
    setup_id=plan_match.match_id,
    thesis_id=plan_match.thesis_id,
    pip_size=Decimal("0.1"),
    spot_price=4196.84,
    executable_quote=4196.84,
    regime="trend",
    cfg=cfg.for_instrument("XAU"),
    max_volume=100000,
    approved_measured=evaluation.measured,
  )
  assert plan.source_structure.low == Decimal("4195.9")
  assert plan.source_structure.high == Decimal("4197.44")
  assert [leg.price for leg in plan.entry.legs] == [
    Decimal("4197.44"), Decimal("4195.94"),
  ]


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
