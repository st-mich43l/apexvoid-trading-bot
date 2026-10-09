"""Every scalp on gold is sized and targeted as a scalp.

Production 2026-10-09: a Range Edge Scalp on XAU was handed treatments written for the
structural gold strategies - the 50-70 pip stop envelope, the 1R-4R ladder (and, with its
two-leg entry, the 15-pips-inside-the-stop risk leg). This file walks the whole scalp
family so a scalp cannot silently inherit a swing treatment again.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.analysis_client.provenance import GO_ORIGIN_TAG
from app.autotrade import go_opportunity_policy as pol
from app.autotrade.execution_policy import SCALP_MAX_TARGET_R, evaluate_execution_policy
from app.autotrade.strategy_taxonomy import is_m1_scalp_strategy, is_scalp_strategy
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match
from tests.test_trade_plan_builder import _match as builder_match
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database

# The five scalps in the 21-strategy catalog, by Go id.
SCALPS = {
  "range_edge": "Range Edge Scalp",
  "fade_scalp": "Fade Scalp",
  "range_sweep": "Range Sweep Scalp",
  "impulse_pullback": "Impulse Pullback Scalp",
  "scalp_breakout_retest": "Breakout Retest Scalp",
}
# The Go stop envelope each of them gets on gold (analysis-engine computeStopEnvelope).
GOLD_ENVELOPE = {
  "range_edge": (15, 45), "fade_scalp": (15, 45),
  "range_sweep": (12, 45), "impulse_pullback": (12, 45), "scalp_breakout_retest": (12, 45),
}


def test_the_scalp_family_is_exactly_these_five():
  scalps = {
    scope.catalog_id for scope in pol.REVIEWED_SCOPES.values()
    if is_scalp_strategy(scope.legacy_strategy, strategy_mode=scope.strategy_mode)
  }
  assert scalps == set(SCALPS)
  for catalog_id, name in SCALPS.items():
    scope = next(s for s in pol.REVIEWED_SCOPES.values() if s.catalog_id == catalog_id)
    assert scope.legacy_strategy == name


def _gold_match(catalog_id: str):
  floor, cap = GOLD_ENVELOPE[catalog_id]
  return _policy_match(
    strategy=SCALPS[catalog_id], symbol="XAU", direction="SELL",
    entry_low=4176.60, entry_high=4178.08, current_price=4176.57, atr=4.0,
    structure_swing=4178.84, tags=(GO_ORIGIN_TAG,), go_invalidation_price=4178.84,
    go_stop_envelope_floor_pips=float(floor), go_stop_envelope_cap_pips=float(cap),
    go_stop_envelope_desired_minimum_pips=float(floor),
    go_stop_envelope_source="scalp_room",
    targets_pips=(23, 46, 69, 92),
  )


@pytest.mark.parametrize("catalog_id", sorted(SCALPS))
def test_a_gold_scalp_never_gets_a_swing_stop_or_a_swing_ladder(catalog_id):
  evaluation = evaluate_execution_policy(
    _gold_match(catalog_id), spot_price=4176.57, executable_quote=4176.57,
    regime="range", pip_size=0.1, cfg=_cfg(),
  )
  assert evaluation.allowed, (catalog_id, evaluation.reason_code, evaluation.message)
  measured = evaluation.measured
  _, cap = GOLD_ENVELOPE[catalog_id]
  assert float(measured["planned_stop_pips"]) <= cap, measured["planned_stop_pips"]
  multiples = [float(value) for value in measured["planned_target_r_multiples"]]
  assert multiples and max(multiples) <= SCALP_MAX_TARGET_R, multiples
  assert not measured.get("planned_trail_after_target_id")
  assert not measured.get("execution_zone_expanded")


@pytest.mark.parametrize("name", sorted(SCALPS.values()))
def test_every_scalp_is_recognised_by_every_scalp_gate(name):
  assert is_scalp_strategy(name)


def test_only_the_m1_scalps_are_m1():
  assert {n for n in SCALPS.values() if is_m1_scalp_strategy(n)} == {
    "Range Sweep Scalp", "Impulse Pullback Scalp", "Breakout Retest Scalp",
  }


def _two_leg_plan(strategy: str, family: str):
  match = builder_match(
    entry_low=4143.16, entry_high=4147.06, structure_swing=4141.46,
    targets=(23, 46), structural_kind="key_level", strategy=strategy, family=family,
  )
  measured = {
    "planned_execution_route": "market_with_limit_scale",
    "planned_stop_price": "4141.46",
    "planned_entry_price": 4147.06,
    "planned_entry_zone_low": 4143.16,
    "planned_entry_zone_high": 4147.06,
    "planned_leg_entry_prices": [4147.06, 4145.11],
    "planned_leg_volume_ratios": [0.8, 0.2],
    "planned_stop_entry_price": "4147.06",
  }
  return build_trade_plan_from_strategy_match(
    match, plan_id="p", setup_id="s", thesis_id="t", pip_size=Decimal("0.1"),
    spot_price=4147.0, regime="range", cfg=_cfg(), executable_quote=4147.0,
    max_volume=1000, approved_measured=measured, account_equity=2172.0,
  )


def test_a_scalp_plan_carries_no_swing_risk_leg_but_a_structural_one_does():
  assert _two_leg_plan("Key Level", "key_level").entry.risk_leg is not None
  for name in ("Range Edge Scalp", "Fade Scalp"):
    plan = _two_leg_plan(name, "range_reversion")
    assert len(plan.entry.legs) == 2
    assert plan.entry.risk_leg is None, name


@pytest.mark.parametrize("name,family", [
  ("Range Edge Scalp", "range_reversion"),
  ("Fade Scalp", "range_reversion"),
  ("Range Sweep Scalp", "range_reversion"),
  ("Impulse Pullback Scalp", "range_reversion"),
  ("Breakout Retest Scalp", "range_reversion"),
])
def test_every_go_scalp_is_a_scalp_plan_for_sizing(name, family):
  # Go scalps carry family "range_reversion" and a go_m5_* mode; the plan builder
  # used to key on family == "scalp" and so sized all five as non-scalps.
  plan = _two_leg_plan(name, family)
  assert plan.risk.risk_percent == Decimal("0.5"), plan.risk.risk_percent


def test_a_structural_strategy_keeps_the_one_percent_risk_field():
  assert _two_leg_plan("Key Level", "key_level").risk.risk_percent == Decimal("1.0")


def test_the_only_shared_enable_switches_are_the_known_ones():
  # Pinned so a new shared switch is a decision, not an accident. Every other
  # reviewed strategy has its own switch; the Go side has one per strategy and
  # per-instrument containment (observe_only_strategies) switches any one off.
  from collections import defaultdict

  from app.autotrade import strategy_catalog as catalog

  by_switch = defaultdict(set)
  for scope in pol.REVIEWED_SCOPES.values():
    profile = catalog.lookup_profile(scope.legacy_strategy)
    assert profile is not None, scope.catalog_id
    by_switch[profile.enable_setting].add(scope.catalog_id)
  shared = {switch: ids for switch, ids in by_switch.items() if len(ids) > 1}
  assert shared == {
    "auto_algo.strategies.scalping.mode": {"impulse_pullback", "range_sweep", "scalp_breakout_retest"},
    "auto_algo.strategies.technique.sd.enabled": {"supply", "demand"},
  }
