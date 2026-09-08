"""DCA-into-zone scale ladder for reaction families (owner spec, 2026-07).

Leg 1 fills at the zone's proximal edge with the configured first-leg
fraction (default 70%); the remainder only fills at a further,
momentum-confirmed price scale_step_atr*ATR deeper into the zone - a real
resting limit order, so it only fills if price actually travels there (no
separate live momentum poll needed). A zone too narrow to qualify for the
ladder falls back to a single entry at the computed price.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.configuration.canonical_fixtures import execution_cfg

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.execution_route import (
  ROUTE_MARKET,
  ROUTE_ZONE_SPLIT,
  resolve_execution_route_plan,
)


pytestmark = pytest.mark.no_database


def _cfg(**overrides):
  values = {
    "auto_trade_zone_fill_enabled": True,
    "auto_trade_zone_fill_min_atr": 0.5,
    "auto_trade_inside_zone_market_entry_enabled": True,
    "auto_trade_zone_fill_fallback_enabled": True,
    "auto_trade_xau_price_digits": 2,
    "auto_trade_zone_scale_first_leg_fraction": 0.70,
    "auto_trade_zone_scale_step_atr": 0.5,
    "auto_trade_reaction_scale_enabled": True,
    "auto_trade_reaction_market_fraction": 0.70,
    "auto_trade_reaction_scale_fraction": 0.30,
    "auto_trade_reaction_scale_step_atr": 0.5,
    "auto_trade_reaction_scale_invalid_policy": "single_market",
  }
  values.update(overrides)
  return execution_cfg(**values)


def _policy_match(**overrides):
  values = {
    "strategy": "Key Level",
    "direction": "SELL",
    "entry_low": 4035.0,
    "entry_high": 4036.5,
    "current_price": 4035.5,
    "confluence": 3,
    "atr": 1.0,
    "structure_swing": 4038.0,
    # Primary TP must stay ≤ stop_min so room-sync leaves [40,60] band —
    # TP60 collapsed min=max=60 and furthest leg overshot the hard cap.
    "targets_pips": (40,),
    "target_price": None,
    "risk_multiplier": 1.0,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


@pytest.mark.parametrize(
  "strategy",
  [
    "Key Level",
    "Session Level",
    "Trendline",
  ],
)
def test_key_session_trendline_use_market_with_limit_scale(strategy):
  evaluation = evaluate_execution_policy(
    _policy_match(strategy=strategy),
    spot_price=4035.5,
    executable_quote=4035.5,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert evaluation.allowed
  assert evaluation.measured["planned_execution_route"] == "market_with_limit_scale"
  assert evaluation.measured["planned_leg_volume_ratios"] == pytest.approx([0.70, 0.30])


@pytest.mark.parametrize(
  "strategy",
  [
    "Demand Zone Reaction",
    "Supply Zone Reaction",
  ],
)
def test_demand_supply_keep_zone_scale_limit_ladder(strategy):
  evaluation = evaluate_execution_policy(
    _policy_match(strategy=strategy),
    spot_price=4035.5,
    executable_quote=4035.5,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert evaluation.allowed
  assert evaluation.measured["order_type_preference"] == "limit"
  assert evaluation.measured["entry_distribution"] == "zone_scale"
  assert evaluation.measured["planned_execution_route"] == "zone_split"


def test_all_reaction_families_use_the_zone_scale_ladder():
  # Backward-compat name: Demand/Supply still use zone_scale → zone_split;
  # Key/Session/Trendline use market_with_limit_scale instead.
  for strategy in (
    "Demand Zone Reaction",
    "Supply Zone Reaction",
  ):
    test_demand_supply_keep_zone_scale_limit_ladder(strategy)
  for strategy in (
    "Key Level",
    "Session Level",
    "Trendline",
  ):
    test_key_session_trendline_use_market_with_limit_scale(strategy)


def test_sell_zone_first_leg_is_proximal_low_at_seventy_percent():
  # Demand Zone (not market_with_limit_scale) still anchors L1 at proximal.
  evaluation = evaluate_execution_policy(
    _policy_match(direction="SELL", strategy="Demand Zone Reaction"),
    spot_price=4035.0,
    executable_quote=4035.0,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  measured = evaluation.measured
  assert measured["planned_execution_route"] == "zone_split"
  assert measured["planned_leg_entry_prices"][0] == pytest.approx(4035.0)
  assert measured["planned_leg_volume_ratios"] == pytest.approx([0.70, 0.30])
  assert measured["planned_leg_entry_prices"][1] == pytest.approx(4035.5)


def test_sell_zone_already_inside_anchors_first_leg_at_current_price():
  # Key Level market_with_limit_scale: L1 is the live quote (market), L2 is
  # one step deeper into the zone as a resting limit.
  evaluation = evaluate_execution_policy(
    _policy_match(direction="SELL"),
    spot_price=4035.5,
    executable_quote=4035.5,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  measured = evaluation.measured
  assert measured["planned_execution_route"] == "market_with_limit_scale"
  assert measured["planned_leg_entry_prices"][0] == pytest.approx(4035.5)
  assert measured["planned_leg_entry_prices"][1] == pytest.approx(4036.0)


def test_buy_zone_first_leg_is_proximal_high_at_seventy_percent():
  # BUY Key Level at the high edge: L1 market at quote, L2 deeper limit.
  evaluation = evaluate_execution_policy(
    _policy_match(direction="BUY", structure_swing=4033.5),
    spot_price=4036.5,
    executable_quote=4036.5,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  measured = evaluation.measured
  assert measured["planned_execution_route"] == "market_with_limit_scale"
  assert measured["planned_leg_entry_prices"][0] == pytest.approx(4036.5)
  assert measured["planned_leg_volume_ratios"] == pytest.approx([0.70, 0.30])
  assert measured["planned_leg_entry_prices"][1] == pytest.approx(4036.0)


def test_buy_zone_already_inside_anchors_first_leg_at_current_price():
  evaluation = evaluate_execution_policy(
    _policy_match(direction="BUY", structure_swing=4033.5),
    spot_price=4036.0,
    executable_quote=4036.0,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  measured = evaluation.measured
  assert measured["planned_execution_route"] == "market_with_limit_scale"
  assert measured["planned_leg_entry_prices"][0] == pytest.approx(4036.0)
  assert measured["planned_leg_entry_prices"][1] == pytest.approx(4035.5)


def test_key_session_trendline_outside_zone_keeps_limit_ladder():
  # Approaching from outside must not fire L1 market; resting DCA ladder.
  evaluation = evaluate_execution_policy(
    _policy_match(strategy="Key Level", direction="SELL"),
    spot_price=4034.5,
    executable_quote=4034.5,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert evaluation.allowed
  assert evaluation.measured["planned_execution_route"] == "zone_split"
  assert evaluation.measured["order_type_preference"] == "limit"


def test_scale_ladder_never_places_the_second_leg_past_the_far_edge():
  # Zone only 0.35 wide (above a 0.3*ATR qualification floor) with a
  # 0.5*ATR step - the second leg would overshoot past the far edge, so it
  # must clamp to the far edge instead.
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="limit",
    entry_distribution="zone_scale",
    executable_quote=4035.1,
    zone_low=4035.0,
    zone_high=4035.35,
    atr=1.0,
    zone_fill_enabled=True,
    zone_fill_min_atr=0.3,
    scale_step_atr=0.5,
  )
  assert plan.route == ROUTE_ZONE_SPLIT
  assert plan.planned_leg_entry_prices[1] == pytest.approx(4035.35)


def test_narrow_zone_falls_back_to_a_single_entry_at_that_price():
  # Zone width 0.3 with ATR=1.0 -> 0.3 < 0.5*ATR qualification floor, so the
  # ladder is unavailable; must act like a single entry at the computed
  # price rather than reject or silently keep waiting.
  evaluation = evaluate_execution_policy(
    _policy_match(entry_low=4035.0, entry_high=4035.3),
    spot_price=4035.15,
    executable_quote=4035.15,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert evaluation.allowed
  assert evaluation.measured["planned_execution_route"] == "market"
  assert evaluation.measured["planned_leg_entry_prices"] == []


def test_first_leg_fraction_and_step_are_configurable():
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="limit",
    entry_distribution="zone_scale",
    executable_quote=4035.0,
    zone_low=4035.0,
    zone_high=4040.0,
    atr=1.0,
    zone_fill_enabled=True,
    zone_fill_min_atr=0.5,
    scale_first_leg_fraction=0.5,
    scale_step_atr=1.0,
  )
  assert plan.planned_leg_volume_ratios == pytest.approx([0.5, 0.5])
  assert plan.planned_leg_entry_prices == pytest.approx([4035.0, 4036.0])


def test_market_preference_still_cannot_use_zone_scale():
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="market",
    entry_distribution="zone_scale",
    executable_quote=4035.1,
    zone_low=4035.0,
    zone_high=4040.0,
    atr=1.0,
    zone_fill_enabled=True,
  )
  assert not plan.valid
  assert plan.route == ROUTE_ZONE_SPLIT
