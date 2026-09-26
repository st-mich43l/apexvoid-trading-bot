"""S14E: the single reviewed XAU ladder specification, Python half.

contracts/autotrade/xau-ladder-spec.json holds hand-computed cases. The C# suite runs the same
file against Manual Algo (AutoTradeEngine) and the Auto Algo executor's risk leg (TradePlanRuntime),
so the three implementations cannot drift apart unnoticed.
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


def test_constants_are_the_specs():
  assert tuple(Decimal(str(r)) for r in xau_ladder.ENTRY_LEG_RATIOS) == tuple(Decimal(r) for r in SPEC["entry_leg_ratios"])
  risk = SPEC["risk_leg"]
  assert xau_ladder.RISK_LEG_LOTS_DEFAULT == f(risk["lots_default"])
  assert xau_ladder.RISK_LEG_LOTS_BELOW_EQUITY_FLOOR == f(risk["lots_below_equity_floor"])
  assert xau_ladder.RISK_LEG_EQUITY_FLOOR == f(risk["equity_floor"])
  assert xau_ladder.RISK_LEG_PIPS_FROM_STOP == f(risk["pips_from_stop"])
  assert xau_ladder.XAU_DIGITS == INSTRUMENT["digits"]


@pytest.mark.parametrize("case", SPEC["entry_price_cases"], ids=lambda c: c["name"])
def test_entry_leg_prices_match_the_spec(case):
  prices = xau_ladder.entry_leg_prices(case["direction"], f(case["zone_low"]), f(case["zone_high"]), f(case["stop"]))
  assert (prices.shallow, prices.deep) == (f(case["shallow"]), f(case["deep"]))


@pytest.mark.parametrize("case", SPEC["risk_price_cases"], ids=lambda c: c["name"])
def test_risk_leg_price_matches_the_spec(case):
  assert xau_ladder.risk_leg_price(case["direction"], f(case["stop"]), f(INSTRUMENT["pip_size"])) == f(case["price"])


@pytest.mark.parametrize("case", SPEC["risk_volume_cases"], ids=lambda c: f"equity_{c['equity']}")
def test_risk_leg_volume_matches_the_spec(case):
  lots = xau_ladder.risk_leg_volume(f(case["equity"]))
  assert lots == f(case["lots"])
  assert xau_ladder.volume_for_lots(
    lots, lot_size=INSTRUMENT["lot_size"], min_volume=INSTRUMENT["min_volume"],
    step_volume=INSTRUMENT["step_volume"], max_volume=INSTRUMENT["max_volume"],
  ) == case["volume"]


def test_volume_for_lots_ports_the_brokers_floor_step_minimum_and_maximum():
  kw = dict(lot_size=10_000, min_volume=100, step_volume=100, max_volume=100_000)
  assert xau_ladder.volume_for_lots(0.019, **kw) == 100             # floors to the step, still >= the minimum
  assert xau_ladder.volume_for_lots(0.005, **kw) == 0               # below the minimum: no order
  assert xau_ladder.volume_for_lots(0.1234, **kw) == 1200           # floor to a multiple of the step
  assert xau_ladder.volume_for_lots(11.0, **kw) == 0                # above the maximum: refused, never clamped
  assert xau_ladder.volume_for_lots(0.0, **kw) == 0 and xau_ladder.volume_for_lots(-1, **kw) == 0


def test_prices_round_half_away_from_zero_not_half_to_even_or_binary_float():
  assert xau_ladder.round_price(4101.005) == 4101.01                # float rounding would give 4101.0
  assert xau_ladder.round_price(4101.015) == 4101.02
  assert xau_ladder.round_price(-4101.005) == -4101.01


def test_worst_case_currency_loss_counts_every_leg_including_the_risk_leg():
  legs = xau_ladder.build_ladder("SELL", 4341.0, 4344.0, 4346.0, pip_size=0.1, total_entry_volume=0.10, equity=5_000.0)
  # 0.08 lots x 50 pips + 0.02 lots x 35 pips + 0.05 lots x 15 pips, at $10 per pip per lot
  assert xau_ladder.worst_case_group_loss(legs, 4346.0, pip_size=0.1, pip_value_per_lot=10.0) == pytest.approx(0.08 * 50 * 10 + 0.02 * 35 * 10 + 0.05 * 15 * 10)


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


@pytest.mark.parametrize("case", SPEC["equity_table"]["cases"], ids=lambda c: f"equity_{c['equity']}")
def test_equity_table_lots_match_the_spec(case):
  assert xau_ladder.equity_table_lots(f(case["equity"])) == f(case["lots"])
