"""Structure-gated autonomous scalp entries into the confirmed zone."""

from __future__ import annotations

import pytest

from app.autotrade.execution_route import (
  ROUTE_MARKET,
  ROUTE_MARKET_WITH_LIMIT_SCALE,
  ROUTE_ZONE_SPLIT,
  SCALP_MICRO_CLIPS,
  resolve_execution_route_plan,
  scalp_micro_grid_legs,
)


pytestmark = pytest.mark.no_database


def test_buy_grid_steps_down_from_quote_to_distal():
  legs = scalp_micro_grid_legs(
    side="BUY", low=4000.0, high=4005.0, quote=4004.0, digits=2,
  )
  assert len(legs) == SCALP_MICRO_CLIPS
  assert legs[0] == 4004.0
  assert legs[-1] == 4000.0
  assert legs == tuple(sorted(legs, reverse=True))


def test_sell_grid_steps_up_from_quote_to_distal():
  legs = scalp_micro_grid_legs(
    side="SELL", low=4000.0, high=4005.0, quote=4001.0, digits=2,
  )
  assert len(legs) == SCALP_MICRO_CLIPS
  assert legs[0] == 4001.0
  assert legs[-1] == 4005.0
  assert legs == tuple(sorted(legs))


def test_hfs_route_is_single_leg_market_not_micro_grid():
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4004.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="Range Sweep Scalp",
    strategy_family="scalp",
  )
  assert plan.valid is True
  assert plan.route == ROUTE_MARKET
  assert plan.planned_leg_entry_prices == ()
  assert plan.planned_leg_volume_ratios == ()
  assert plan.planned_entry_price == 4004.0
  assert plan.immediate_market is True
  assert plan.routing_reason == "scalp: single-leg market (no micro-grid)"


@pytest.mark.parametrize(
  ("direction", "quote"),
  [
    ("BUY", 4004.0),
    ("SELL", 4001.0),
  ],
)
def test_xau_hfs_auto_route_is_single_leg_market(
  direction: str,
  quote: float,
):
  from tests.test_config_effective_instrument_context import (
    _load_production_example,
  )

  cfg = _load_production_example().config
  xau = cfg.for_instrument("XAU")
  plan = resolve_execution_route_plan(
    direction=direction,
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=quote,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="Range Sweep Scalp",
    strategy_family="scalp",
    entry_clips=xau.targeting.entry_clips,
  )

  assert plan.valid is True
  assert plan.route == ROUTE_MARKET
  assert plan.planned_leg_entry_prices == ()
  assert plan.routing_reason == "scalp: single-leg market (no micro-grid)"


def test_hfs_chase_sell_books_full_market_not_five_legs_into_abandoned_zone():
  """Live 2026-08-21: quote below supply, five equal clips → only L1 rode TP."""
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4563.98,
    zone_low=4566.9775,
    zone_high=4567.86625,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="Range Sweep Scalp",
    strategy_family="scalp",
  )
  assert plan.valid is True
  assert plan.route == ROUTE_MARKET
  assert plan.entry_geometry == "below"
  assert plan.planned_leg_entry_prices == ()
  assert plan.planned_leg_volume_ratios == ()
  assert plan.planned_entry_price == 4563.98
  assert plan.immediate_market is True


def test_hfs_chase_buy_books_full_market_not_micro_grid():
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4010.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="Impulse Pullback Scalp",
    strategy_family="scalp",
  )
  assert plan.route == ROUTE_MARKET
  assert plan.entry_geometry == "above"
  assert plan.planned_leg_entry_prices == ()
  assert plan.immediate_market is True


def test_technique_fvg_uses_its_declared_zone_scale_policy():
  # Owner 2026-09-08 (bad technique entries): technique strategies no
  # longer share scalp's single-leg-market-only short-circuit - the
  # GROUP RECOVERY REQUIRED bug that motivated it (v8:a80bf164…) is fixed
  # generally in TradePlanRuntime now, so FVG/OB/IFVG/CRT/supply_demand
  # fall through to their own declared policy (limit + zone_scale),
  # DCA-ing into the zone at progressively better prices instead of
  # market-filling at whatever price confirmation happened to land on.
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="limit",
    entry_distribution="zone_scale",
    executable_quote=4004.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="FVG",
    strategy_family="zone",
  )
  assert plan.valid is True
  assert plan.route == ROUTE_ZONE_SPLIT
  assert len(plan.planned_leg_entry_prices) == 2
  assert plan.routing_reason != "technique: single-leg market (no micro-grid)"


def test_key_level_reaction_is_not_forced_onto_scalp_grid():
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4004.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="Key Level",
    strategy_family="key_level",
  )
  assert plan.route == "market"
  assert plan.planned_leg_entry_prices == ()


def test_breakout_retest_outside_zone_uses_market_watch_not_immediate():
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4429.62,
    zone_low=4430.76,
    zone_high=4433.36,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="Breakout Retest Scalp",
    strategy_family="scalp",
  )
  assert plan.valid is True
  assert plan.route == ROUTE_MARKET
  assert plan.entry_geometry == "below"
  assert plan.immediate_market is False
  assert "market_watch" in plan.routing_reason
