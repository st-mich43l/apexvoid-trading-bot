"""XAU-only entry-targeted risk band (2026-09-15, owner-reported).

Entry price used to come purely from zone geometry (near edge / midpoint),
with zero awareness of the risk distance that entry would produce against
the real structural stop - the stop envelope only ever clamped the STOP
afterward. ``risk_targeted_entry_price`` picks the entry instead, so risk
lands near the target (matching manual /algo's own 50 pip default) up
front; the stop envelope (min 50 / max 60) remains the backstop.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.configuration.canonical_fixtures import execution_cfg

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.protective_stop import plan_go_invalidation_stop
from app.autotrade.execution_route import (
  ROUTE_MARKET_WITH_LIMIT_SCALE,
  ROUTE_SINGLE_LIMIT,
  resolve_execution_route_plan,
  risk_targeted_entry_price,
)


pytestmark = pytest.mark.no_database


def test_risk_targeted_entry_lands_at_exactly_target_pips_when_reachable():
  # BUY: stop below the zone, 50p below is well inside [4090, 4095].
  entry = risk_targeted_entry_price(
    direction="BUY",
    structural_stop=4086.0,
    zone_low=4090.0,
    zone_high=4095.0,
    target_risk_pips=50.0,
    pip_size=0.1,
    digits=2,
  )
  assert entry == pytest.approx(4091.0)


def test_go_invalidation_is_used_verbatim_for_every_entry_leg():
  plan = plan_go_invalidation_stop(
    direction="SELL",
    entry_zone_low=4132.61,
    entry_zone_high=4139.02,
    planned_leg_prices=(4133.00, 4136.50),
    resolved_leg_volumes=(0.8, 0.2),
    invalidation_price=4142.00,
    minimum_stop_pips=50,
    maximum_stop_pips=100,
    pip_size=0.1,
    digits=2,
  )
  assert float(plan.final_stop_price) == pytest.approx(4142.00)
  assert plan.source == "go_invalidation"
  assert float(plan.final_stop_pips) == pytest.approx(90.0)


def test_go_invalidation_is_rejected_when_its_worst_leg_breaks_max_risk():
  with pytest.raises(ValueError, match="stop_exceeds_go_invalidation_envelope"):
    plan_go_invalidation_stop(
      direction="SELL",
      entry_zone_low=4132.61,
      entry_zone_high=4139.02,
      planned_leg_prices=(4133.00, 4136.50),
      resolved_leg_volumes=(0.8, 0.2),
      invalidation_price=4142.00,
      minimum_stop_pips=50,
      maximum_stop_pips=80,
      pip_size=0.1,
      digits=2,
    )


def test_go_policy_does_not_apply_fixed_rr_or_python_stop_rewrite():
  match = _match(
    direction="SELL",
    entry_low=4132.61,
    entry_high=4139.02,
    current_price=4130.0,
    structure_swing=4150.0,
    go_invalidation_price=4142.0,
    targets_pips=(40, 80),
    tags=("origin:go",),
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=4130.0,
    executable_quote=4130.0,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(**{"execution.reaction.stop_max_pips": 100}),
  )
  assert evaluation.allowed
  assert evaluation.measured["planned_stop_price"] == "4142.00"
  assert evaluation.measured["stop_source"] == "go_invalidation"
  assert "target_policy_mode" not in evaluation.measured


def test_risk_targeted_entry_sell_direction_mirrors_buy():
  # SELL: stop above the zone, 50p above the zone is inside [4090, 4095].
  entry = risk_targeted_entry_price(
    direction="SELL",
    structural_stop=4099.0,
    zone_low=4090.0,
    zone_high=4095.0,
    target_risk_pips=50.0,
    pip_size=0.1,
    digits=2,
  )
  assert entry == pytest.approx(4094.0)


def test_risk_targeted_entry_clamps_to_nearest_edge_when_zone_too_far():
  # Structural stop far below the zone - even zone_low (the closest
  # possible entry) already exceeds 50p. Best effort: zone_low, not a
  # fabricated price outside the detected zone.
  entry = risk_targeted_entry_price(
    direction="BUY",
    structural_stop=4080.0,
    zone_low=4090.0,
    zone_high=4095.0,
    target_risk_pips=50.0,
    pip_size=0.1,
    digits=2,
  )
  assert entry == pytest.approx(4090.0)


def test_risk_targeted_entry_clamps_to_farthest_edge_when_zone_too_close():
  # Structural stop close enough to the zone that even zone_high (the
  # farthest possible entry) stays under 50p. Best effort: zone_high.
  entry = risk_targeted_entry_price(
    direction="BUY",
    structural_stop=4092.0,
    zone_low=4090.0,
    zone_high=4095.0,
    target_risk_pips=50.0,
    pip_size=0.1,
    digits=2,
  )
  assert entry == pytest.approx(4095.0)


def test_resolve_route_plan_uses_risk_targeted_entry_when_provided():
  # Quote above the zone (geometry "above") so the single-limit branch
  # rests at the anchor (proximal / risk-targeted price) rather than
  # chasing toward the live quote - same geometry the existing
  # zone-edge-only tests in test_zone_scale_execution.py rely on.
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="limit",
    entry_distribution="single",
    executable_quote=4096.0,
    zone_low=4090.0,
    zone_high=4095.0,
    atr=1.0,
    digits=2,
    structural_stop=4086.0,
    target_risk_pips=50.0,
    pip_size=0.1,
  )
  assert plan.route == ROUTE_SINGLE_LIMIT
  assert plan.planned_entry_price == pytest.approx(4091.0)


def test_resolve_route_plan_uses_risk_targeted_entry_when_inside_zone():
  # ENTRY_LOGIC_REVIEW_2026-09-17.md / reproduced live 2026-09-17 (XAU Key
  # Level SELL v8:9768e2b1...): market_with_limit_scale silently discarded
  # the risk-targeted entry for leg 2 once the quote was already inside the
  # zone, re-anchoring both legs to the live quote instead. Quote here
  # (4093.0) sits inside [4090, 4095] - the exact geometry that was broken.
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="limit",
    entry_distribution="zone_scale",
    executable_quote=4093.0,
    zone_low=4090.0,
    zone_high=4095.0,
    atr=1.0,
    digits=2,
    zone_fill_enabled=True,
    zone_fill_min_atr=0.5,
    reaction_scale_enabled=True,
    reaction_scale_step_atr=0.5,
    strategy="Key Level",
    structural_stop=4086.0,
    target_risk_pips=50.0,
    pip_size=0.1,
  )
  assert plan.route == ROUTE_MARKET_WITH_LIMIT_SCALE
  # L1 stays the live quote (market, immediately fillable).
  assert plan.planned_entry_price == pytest.approx(4093.0)
  # L2 steps from the risk-targeted price (4091.0), not the quote -
  # max(zone_low, 4091.0 - 0.5*ATR) = 4090.5.
  assert plan.planned_leg_entry_prices == pytest.approx((4093.0, 4090.5))


def test_resolve_route_plan_keeps_zone_edge_when_risk_targeting_omitted():
  # No structural_stop/target_risk_pips/pip_size - byte-identical to
  # pre-2026-09-15 behavior (near-edge proximal), same as every non-XAU
  # instrument's routing today.
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="limit",
    entry_distribution="single",
    executable_quote=4096.0,
    zone_low=4090.0,
    zone_high=4095.0,
    atr=1.0,
    digits=2,
  )
  assert plan.route == ROUTE_SINGLE_LIMIT
  assert plan.planned_entry_price == pytest.approx(4095.0)


def _cfg(**overrides):
  values = {
    "auto_trade_zone_fill_enabled": True,
    "auto_trade_inside_zone_market_entry_enabled": True,
    "auto_trade_xau_price_digits": 2,
    # Matches XAU's real production floor (config/trading-bot.yml's
    # xau_fixed_4r_v1 pack stop_envelope.min_pips) - the target this
    # feature aims entries at.
    "execution.reaction.stop_min_pips": 50,
  }
  values.update(overrides)
  return execution_cfg(**values)


def _match(**overrides):
  values = {
    "strategy": "Key Level",
    "symbol": "XAU",
    "direction": "BUY",
    "entry_low": 4090.0,
    "entry_high": 4095.0,
    "current_price": 4096.0,
    "confluence": 3,
    "atr": 1.0,
    "structure_swing": 4086.3,
    "targets_pips": (300,),
    "target_price": None,
    "risk_multiplier": 1.0,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def test_xau_evaluate_execution_policy_picks_risk_targeted_entry_by_default():
  # Default cfg leaves risk_targeted_entry_enabled at its schema default
  # (True) - structure_swing=4086.3, buffer 0.3*ATR(1.0) -> raw stop
  # 4086.0; target 50p (stop_min_pips default 50) -> entry 4091.0, deep
  # inside the [4090, 4095] zone, not the old near-edge pick (4095.0 for
  # a BUY single/market route).
  evaluation = evaluate_execution_policy(
    _match(),
    spot_price=4096.0,
    executable_quote=4096.0,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert evaluation.allowed
  assert evaluation.measured["planned_entry_price"] == pytest.approx(4091.0)


def test_xau_evaluate_execution_policy_reverts_when_flag_disabled():
  evaluation = evaluate_execution_policy(
    _match(),
    spot_price=4096.0,
    executable_quote=4096.0,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(**{"execution.reaction.risk_targeted_entry_enabled": False}),
  )
  assert evaluation.allowed
  assert evaluation.measured["planned_entry_price"] == pytest.approx(4095.0)
