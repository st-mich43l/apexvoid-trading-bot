"""Go invalidation is the technical floor for the stop; execution may widen it.

Production 2026-09-30: XAU FVG 4184.81-4186.10 with Go stop 4183.00 traded with
legs 12-19 pips from the stop (ATR 36 pips) while the card claimed 30 pips.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.protective_stop import ProtectiveStopError, plan_go_invalidation_stop
from tests.support.canonical_fixtures import _load_production_example
from tests.test_execution_pipeline_integrity import _policy_match

pytestmark = pytest.mark.no_database


def _stop(direction="BUY", *, legs=(4186.10, 4184.60), stop=4183.00, zone=(4183.10, 4186.10), **kw):
  return plan_go_invalidation_stop(
    direction=direction,
    entry_zone_low=zone[0],
    entry_zone_high=zone[1],
    planned_leg_prices=list(legs),
    resolved_leg_volumes=[0.8, 0.2],
    invalidation_price=stop,
    minimum_stop_pips=kw.pop("minimum_stop_pips", 40),
    maximum_stop_pips=kw.pop("maximum_stop_pips", 60),
    pip_size=Decimal("0.1"),
    digits=2,
    **kw,
  )


def test_go_stop_inside_the_envelope_is_untouched():
  plan = _stop(stop=4181.50, widen_to_minimum=True)
  assert plan.final_stop_price == Decimal("4181.50")
  assert plan.source == "go_invalidation" and plan.adjustment == "none"


def test_too_tight_stop_is_widened_to_the_minimum_from_the_widest_leg():
  plan = _stop(stop=4183.00, widen_to_minimum=True)
  assert plan.final_stop_price == Decimal("4182.10")
  assert plan.final_stop_pips == Decimal("40")
  assert plan.source == "go_invalidation_widened" and plan.adjustment == "envelope_minimum"
  assert plan.raw_stop_price == Decimal("4183.00")


def test_sell_stop_widens_upward():
  plan = _stop(
    "SELL", legs=(4195.00, 4196.50), zone=(4195.00, 4198.00),
    stop=4198.50, widen_to_minimum=True,
  )
  assert plan.final_stop_price == Decimal("4199.00")
  assert plan.final_stop_pips == Decimal("40")


def test_stop_is_never_moved_closer_than_go():
  plan = _stop(stop=4181.00, widen_to_minimum=True)
  assert plan.final_stop_price == Decimal("4181.00")


def test_stop_follows_the_band_extension_and_keeps_gos_buffer():
  # Band grew 17.1 pips below Go's zone low 4184.81; Go's buffer (1.81) is
  # kept around the band that is actually traded.
  plan = _stop(stop=4183.001, band_extension_pips=17.1)
  assert plan.final_stop_price == Decimal("4181.29")
  assert plan.adjustment == "band_extension"
  assert plan.base_stop_price == Decimal("4183.00")


def test_fx_style_enforcement_still_rejects_instead_of_widening():
  with pytest.raises(ProtectiveStopError) as info:
    _stop(stop=4183.00, enforce_minimum_stop=True, widen_to_minimum=False)
  assert str(info.value) == "stop_below_go_invalidation_envelope"


def test_widening_cannot_exceed_the_envelope_maximum():
  with pytest.raises(ProtectiveStopError) as info:
    _stop(stop=4183.00, widen_to_minimum=True, minimum_stop_pips=70, maximum_stop_pips=60)
  assert "invalid" in str(info.value)
  with pytest.raises(ProtectiveStopError) as info:
    _stop(stop=4179.00, widen_to_minimum=True)
  assert str(info.value) == "stop_exceeds_go_invalidation_envelope"


def _fvg(**overrides):
  return _policy_match(
    strategy="FVG", family="supply_demand", strategy_mode="go_m5_fvg",
    symbol="XAU", direction="BUY", structural_kind="fvg",
    tags=("origin:go", "catalog:fvg"), **overrides,
  )


def test_production_2026_09_30_1146_xau_fvg_gets_a_real_stop():
  cfg = _load_production_example().config
  match = _fvg(
    entry_low=4184.81, entry_high=4186.10, current_price=4185.19, atr=3.6179,
    structure_swing=4183.001071428572, go_invalidation_price=4183.001071428572,
    targets_pips=(45,), absolute_target_price=4190.64,
  )
  evaluation = evaluate_execution_policy(
    match, spot_price=4185.19, executable_quote=4185.19,
    regime="trend", pip_size=0.1, cfg=cfg,
  )
  measured = evaluation.measured
  assert evaluation.allowed is True
  assert Decimal(measured["planned_stop_price"]) < Decimal("4183.001")
  assert float(measured["planned_stop_pips"]) >= 40
  assert Decimal(measured["planned_stop_price"]) == Decimal("4181.10")
  assert measured["stop_source"] == "go_invalidation_widened"
  assert measured["planned_leg_entry_prices"] == pytest.approx([4186.10, 4184.60])
  assert match.go_invalidation_price == 4183.001071428572
  assert Decimal(measured["planned_target_prices"][0]) == Decimal("4191.10")


def test_wide_go_stop_on_a_genuine_fvg_is_left_alone():
  cfg = _load_production_example().config
  match = _fvg(
    entry_low=4183.00, entry_high=4186.10, current_price=4185.19, atr=6.0,
    structure_swing=4180.60, go_invalidation_price=4180.60,
    targets_pips=(90,), absolute_target_price=4195.10,
  )
  evaluation = evaluate_execution_policy(
    match, spot_price=4185.19, executable_quote=4185.19,
    regime="trend", pip_size=0.1, cfg=cfg,
  )
  assert evaluation.allowed is True
  assert Decimal(evaluation.measured["planned_stop_price"]) == Decimal("4180.60")
  assert evaluation.measured["stop_source"] == "go_invalidation"


def test_production_2026_09_30_1223_xau_ifvg_no_longer_trades_an_18_pip_stop():
  cfg = _load_production_example().config
  match = _policy_match(
    strategy="iFVG", family="supply_demand", strategy_mode="go_m5_ifvg",
    symbol="XAU", direction="BUY", structural_kind="ifvg",
    tags=("origin:go", "catalog:ifvg"),
    entry_low=4185.91, entry_high=4186.27, current_price=4186.10, atr=3.4536,
    structure_swing=4184.183214285714, go_invalidation_price=4184.183214285714,
    targets_pips=(45,), absolute_target_price=4190.77,
  )
  evaluation = evaluate_execution_policy(
    match, spot_price=4186.10, executable_quote=4186.10,
    regime="trend", pip_size=0.1, cfg=cfg,
  )
  measured = evaluation.measured
  assert evaluation.allowed is True
  assert float(measured["planned_stop_pips"]) >= 40
  assert Decimal(measured["planned_stop_price"]) < Decimal("4184.183")
  assert measured["stop_source"] == "go_invalidation_widened"
