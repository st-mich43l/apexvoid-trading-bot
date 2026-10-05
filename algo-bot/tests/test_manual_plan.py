"""Owner /algo intent -> TradePlan V8 (the only order path for manual signals)."""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

import pytest

from app.autotrade.trade_plan import TradePlan
from app.signals.manual_intent import ManualTradeIntent
from app.signals.manual_plan import (
  MANUAL_FAMILY,
  _leg_prices,
  build_manual_trade_plan,
  is_manual_plan_id,
)

pytestmark = pytest.mark.no_database

SPEC = json.loads(
  (Path(__file__).resolve().parents[2] / "contracts" / "autotrade" / "xau-ladder-spec.json").read_text()
)


def _intent(**overrides) -> ManualTradeIntent:
  base = dict(
    intent_id="manual:47:0",
    manual_signal_id=47,
    revision=0,
    direction="SELL",
    symbol="XAU",
    entry_low=4100.0,
    entry_high=4105.0,
    sl=4110.0,
    tps=(4095.0, 4090.0, 4080.0),
    created_at=int(time.time()),
    expires_at=None,
    setup_type="golden-fib",
    confluence=2,
    execution_mode="algo",
  )
  base.update(overrides)
  return ManualTradeIntent(**base)


@pytest.mark.parametrize("case", SPEC["entry_price_cases"], ids=lambda c: c["name"])
def test_ladder_leg_prices_match_the_reviewed_spec(case):
  intent = _intent(
    direction=case["direction"],
    entry_low=float(case["zone_low"]),
    entry_high=float(case["zone_high"]),
    sl=float(case["stop"]),
  )
  shallow, deep = _leg_prices(intent, 2)
  assert (shallow, deep) == (Decimal(case["shallow"]), Decimal(case["deep"]))


def test_the_plan_is_a_valid_v8_plan_that_round_trips():
  plan = build_manual_trade_plan(_intent())

  assert plan.version == 8
  assert TradePlan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan


def test_identity_is_the_intent_id_and_marked_manual():
  plan = build_manual_trade_plan(_intent())

  assert plan.plan_id == "manual:47:0"
  assert plan.setup_id == "manual:47:0"
  assert plan.thesis_id == "manual-thesis:47"
  assert is_manual_plan_id(plan.plan_id)
  assert plan.analysis.strategy_family == MANUAL_FAMILY
  assert plan.analysis.confluence == 2


def test_owner_stop_and_targets_are_declared_absolutely_and_never_rederived():
  plan = build_manual_trade_plan(_intent())

  assert plan.stop.price == Decimal("4110.00")
  assert plan.stop.source == "owner_instruction"
  assert [t.price for t in plan.targets] == [
    Decimal("4095.00"), Decimal("4090.00"), Decimal("4080.00"),
  ]
  assert sum(t.close_ratio for t in plan.targets) == Decimal("1")


def test_xau_zone_ladder_is_80_20_shallow_then_deep_and_keeps_the_risk_leg():
  plan = build_manual_trade_plan(_intent())

  assert plan.entry.type == "limit_ladder"
  assert [(l.leg_id, l.price, l.volume_ratio) for l in plan.entry.legs] == [
    ("L1", Decimal("4100.00"), Decimal("0.8")),
    ("L2", Decimal("4102.50"), Decimal("0.2")),
  ]
  assert "risk_leg:disabled" not in plan.analysis.tags


def test_buy_uses_the_high_edge_as_shallow():
  plan = build_manual_trade_plan(_intent(
    direction="BUY", entry_low=4088.1, entry_high=4090.0, sl=4082.5,
    tps=(4096.0, 4104.0),
  ))

  assert plan.entry.legs[0].price == Decimal("4090.00")
  assert plan.entry.legs[1].price == Decimal("4089.05")


def test_single_entry_override_collapses_to_one_limit_without_a_risk_leg():
  plan = build_manual_trade_plan(_intent(single_entry_override=True))

  assert plan.entry.type == "single_limit"
  assert plan.entry.order_price == Decimal("4100.00")
  assert "risk_leg:disabled" in plan.analysis.tags
  assert plan.sizing is not None and plan.sizing.entry_distribution == "single"


@pytest.mark.parametrize(
  ("tps", "expected"),
  [
    ((4095.0,), [Decimal("1")]),
    ((4095.0, 4090.0), [Decimal("0.4"), Decimal("0.6")]),
  ],
)
def test_close_ratios_follow_the_instrument_manual_split(tps, expected):
  plan = build_manual_trade_plan(_intent(tps=tps))

  assert [t.close_ratio for t in plan.targets] == expected


def test_breakeven_after_tp1_only_when_there_is_more_than_one_target():
  assert build_manual_trade_plan(_intent()).management.be_after_target_id == "TP1"
  assert build_manual_trade_plan(_intent(tps=(4095.0,))).management.be_after_target_id is None


def test_stop_trails_to_the_owners_tp1_after_tp2_when_a_later_target_exists():
  three = build_manual_trade_plan(_intent())
  assert three.management.trail_after_target_id == "TP2"
  assert three.management.trail_to_target_id == "TP1"

  two = build_manual_trade_plan(_intent(tps=(4095.0, 4090.0)))
  assert two.management.trail_after_target_id is None
  assert two.management.trail_to_target_id is None


def test_fx_manual_signal_is_a_single_limit_at_the_proximal_edge():
  plan = build_manual_trade_plan(_intent(
    symbol="EURUSD", direction="BUY", entry_low=1.0850, entry_high=1.0855,
    sl=1.0830, tps=(1.0880,),
  ))

  assert plan.entry.type == "single_limit"
  assert plan.entry.order_price == Decimal("1.08550")
  assert plan.risk.max_volume == 100_000_000


def test_expiry_defaults_to_a_day_when_the_trade_day_end_is_unknown():
  now = int(time.time())
  plan = build_manual_trade_plan(_intent(created_at=now, expires_at=None), now_ts=now)

  assert plan.expires_at == now + 24 * 3600


def test_an_invalid_owner_signal_is_refused_not_published():
  with pytest.raises(ValueError, match="invalid"):
    build_manual_trade_plan(_intent(direction="BUY", sl=4110.0))  # stop above a BUY zone
