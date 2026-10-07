"""The reviewed XAU ladder specification, Python half (entry-leg prices).

contracts/autotrade/xau-ladder-spec.json holds hand-computed cases. The C# suite runs the same
file against Manual Algo (AutoTradeEngine) and the Auto Algo executor's risk leg (TradePlanRuntime),
so the implementations cannot drift apart unnoticed.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.autotrade import execution_route, xau_ladder

pytestmark = pytest.mark.no_database

SPEC = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "autotrade" / "xau-ladder-spec.json").read_text())
INSTRUMENT = SPEC["instrument"]


def f(value: str) -> float:
  return float(Decimal(value))


def test_digits_are_the_specs():
  assert xau_ladder.XAU_DIGITS == INSTRUMENT["digits"]


@pytest.mark.parametrize("case", SPEC["entry_price_cases"], ids=lambda c: c["name"])
def test_entry_leg_prices_match_the_spec(case):
  prices = xau_ladder.entry_leg_prices(case["direction"], f(case["zone_low"]), f(case["zone_high"]), f(case["stop"]))
  assert (prices.shallow, prices.deep) == (f(case["shallow"]), f(case["deep"]))


def test_prices_round_half_away_from_zero_not_half_to_even_or_binary_float():
  assert xau_ladder.round_price(4101.005) == 4101.01                # float rounding would give 4101.0
  assert xau_ladder.round_price(4101.015) == 4101.02
  assert xau_ladder.round_price(-4101.005) == -4101.01


# ---- the two ladders are NOT the same rule: pinned so any change is deliberate ---------------------------------------

def test_auto_algos_entry_ladder_differs_from_manual_algos_and_is_pinned_as_such():
  """Manual: Deep = zone midpoint. Auto (execution_route): leg 2 = one ATR step deeper than the
  proximal edge, capped at the far edge. Same zone, different second leg."""
  low, high = 4085.00, 4089.50
  manual_deep = xau_ladder.entry_leg_prices("BUY", low, high, 4082.0).deep
  assert manual_deep == 4087.25
  _l1, auto_l2 = execution_route._scale_ladder_legs(side="BUY", low=low, high=high, proximal=high, atr=2.0, scale_step_atr=1.0, digits=2)
  assert auto_l2 == 4087.50 and auto_l2 != manual_deep                # one ATR step
  _l1, auto_l2_far = execution_route._scale_ladder_legs(side="BUY", low=low, high=high, proximal=high, atr=10.0, scale_step_atr=1.0, digits=2)
  assert auto_l2_far == low and auto_l2_far != manual_deep            # capped at the far edge, not the midpoint
