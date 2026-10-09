"""On gold the card price is the order price (app/autotrade/card_prices.py).

Production 2026-10-09, Key Level BUY: card "Entry Zone 4,143 - 4,147", "SL 4,141 · risk 56
pips" (4,147 to 4,141 reads as 60); the plan held legs 4147.06 / 4145.11, stop 4141.46 and
a first target of 4152.66.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.autotrade.card_prices import snap_measured_to_card_prices
from app.autotrade.trade_card import format_price
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match
from tests.test_trade_plan_builder import _match, execution_cfg

pytestmark = pytest.mark.no_database

INCIDENT = {
  "planned_execution_route": "market_with_limit_scale",
  "planned_stop_price": "4141.46",
  "planned_entry_price": 4147.06,
  "planned_entry_zone_low": 4143.16,
  "planned_entry_zone_high": 4147.06,
  "planned_leg_entry_prices": [4147.06, 4145.11],
  "go_stop_envelope_floor_pips": 50.0,
  "go_stop_envelope_cap_pips": 60.0,
}
INCIDENT_TARGETS = (56, 112, 168, 224)


def _snap(measured=None, **overrides):
  kwargs = dict(
    direction="BUY", symbol="XAU", strategy="Key Level", pip_size=0.1,
    targets_pips=INCIDENT_TARGETS, reaction_stop_max_pips=60.0,
  )
  kwargs.update(overrides)
  return snap_measured_to_card_prices(dict(measured or INCIDENT), **kwargs)


def test_the_incident_plan_is_laid_out_on_the_numbers_the_card_prints():
  out = _snap()
  assert out["planned_leg_entry_prices"] == [4147.0, 4145.0]
  assert (out["planned_entry_zone_low"], out["planned_entry_zone_high"]) == (4143.0, 4147.0)
  assert out["planned_entry_price"] == 4147.0
  # Rounded away from the entry, which here is exactly the 60 pip cap.
  assert out["planned_stop_price"] == 4141.0
  assert out["planned_stop_pips"] == "60.0"
  # Whole R multiples stay whole R multiples of the new 60 pip risk: 1R..4R.
  assert out["card_price_snap_target_prices"] == [4153.0, 4159.0, 4165.0, 4171.0]
  # Every number the card prints equals the number the plan holds.
  for price in (*out["planned_leg_entry_prices"], out["planned_stop_price"],
                *out["card_price_snap_target_prices"]):
    assert float(format_price(price, "XAU").replace(",", "")) == price
  assert out["card_price_snap"]["risk_pips"] == [56.0, 60.0]


def test_the_stop_rounds_toward_the_entry_when_away_would_leave_the_envelope():
  measured = {**INCIDENT, "planned_stop_price": "4141.9", "planned_entry_zone_high": 4147.4,
              "planned_entry_price": 4147.4, "planned_leg_entry_prices": [4147.4, 4145.2]}
  out = _snap(measured)
  # 4141.9 away is 4141 = 60 pips from 4147: inside the cap, so it is taken.
  assert out["planned_stop_price"] == 4141.0
  tight = _snap(measured, reaction_stop_max_pips=59.0, targets_pips=(55, 110))
  assert tight["planned_stop_price"] == 4142.0
  assert tight["card_price_snap"]["risk_pips"][1] == 50.0


def test_sell_mirrors_buy():
  sell = {
    "planned_execution_route": "market_with_limit_scale",
    "planned_stop_price": "4148.76", "planned_entry_price": 4143.16,
    "planned_entry_zone_low": 4143.16, "planned_entry_zone_high": 4147.06,
    "planned_leg_entry_prices": [4143.16, 4145.11],
    "go_stop_envelope_floor_pips": 50.0, "go_stop_envelope_cap_pips": 60.0,
  }
  out = _snap(sell, direction="SELL", targets_pips=(56, 112))
  assert out["planned_leg_entry_prices"] == [4143.0, 4145.0]
  # Rounded away (up) to 4149: 60 pips from the 4143 shallow leg, exactly the cap.
  assert out["planned_stop_price"] == 4149.0
  assert out["card_price_snap_target_prices"] == [4137.0, 4131.0]


@pytest.mark.parametrize("symbol,strategy", [
  ("EURUSD", "Key Level"),          # not gold
  ("XAU", "Range Sweep Scalp"),     # M1 scalp: its 15-20 pip stop cannot absorb a whole-number round
  ("XAU", "Range Edge Scalp"),      # range scalp
])
def test_only_gold_zone_plans_are_snapped(symbol, strategy):
  assert _snap(symbol=symbol, strategy=strategy) == INCIDENT


def test_nothing_is_snapped_when_the_numbers_would_collapse_or_leave_the_envelope():
  narrow = {**INCIDENT, "planned_entry_zone_low": 4146.6, "planned_entry_zone_high": 4147.06,
            "planned_leg_entry_prices": [4147.06, 4146.7]}
  assert _snap(narrow) == narrow  # both legs round to 4147
  assert _snap(reaction_stop_max_pips=45.0) == INCIDENT  # neither rounding fits the cap


def test_the_builder_publishes_the_card_numbers_as_the_plan_and_the_risk_leg_follows_the_stop():
  match = _match(
    entry_low=4143.16, entry_high=4147.06, structure_swing=4141.46,
    targets=INCIDENT_TARGETS, structural_kind="key_level", strategy="Key Level",
    family="key_level",
  )
  measured = {
    **INCIDENT,
    "planned_leg_volume_ratios": [0.8, 0.2],
    "planned_stop_entry_price": "4147.06",
  }
  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="p", setup_id="s", thesis_id="t", pip_size=Decimal("0.1"),
    spot_price=4147.0, regime="trend", cfg=execution_cfg(),
    executable_quote=4147.0, max_volume=1000, approved_measured=measured,
    account_equity=2172.0,
  )
  assert [str(leg.price) for leg in plan.entry.legs] == ["4147.0", "4145.0"]
  assert (str(plan.entry.zone_low), str(plan.entry.zone_high)) == ("4143.0", "4147.0")
  assert str(plan.stop.price) == "4141.0"
  assert [str(target.price) for target in plan.targets] == ["4153.0", "4159.0", "4165.0", "4171.0"]
  assert plan.entry.risk_leg is not None
  assert str(plan.entry.risk_leg.price) == "4142.5"
