"""The XAU risk leg is declared by the planner; the card prints it.

Production 2026-10-08: the executor injected a third RISK leg (0.05 lot, stop -
15 pips) that neither the plan nor the card showed, so the broker held more than
the card said. The planner now owns the leg: ``entry.risk_leg`` in the plan, a
executor places exactly that. The Auto Algo root card does not print the leg (owner
2026-10-08).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.autotrade import risk_leg as risk_leg_module
from app.autotrade.risk_leg import load_account_equity, plan_risk_leg
from app.autotrade.trade_plan import (
  ENTRY_TYPE_LIMIT_LADDER,
  ENTRY_TYPE_SINGLE_LIMIT,
  TradePlan,
  TradePlanEntry,
  TradePlanEntryLeg,
  TradePlanError,
  TradePlanRiskLeg,
)
from tests.support.canonical_fixtures import execution_cfg
from tests.test_manual_plan import _intent
from app.signals.manual_plan import build_manual_trade_plan

pytestmark = pytest.mark.no_database

SPEC = json.loads(
  (Path(__file__).resolve().parents[2] / "contracts" / "autotrade" / "xau-ladder-spec.json").read_text()
)


def _ladder() -> TradePlanEntry:
  return TradePlanEntry(
    type=ENTRY_TYPE_LIMIT_LADDER,
    expires_at=1,
    legs=(
      TradePlanEntryLeg("L1", Decimal("4125.66"), Decimal("0.8")),
      TradePlanEntryLeg("L2", Decimal("4127.61"), Decimal("0.2")),
    ),
  )


def _leg(direction="SELL", stop="4131.51", equity=1595.0, symbol="XAU", entry=None, cfg=None):
  return plan_risk_leg(
    symbol=symbol,
    direction=direction,
    entry=entry or _ladder(),
    stop_price=Decimal(stop),
    pip_size=Decimal("0.1"),
    digits=2,
    account_equity=equity,
    cfg=cfg or execution_cfg(),
  )


@pytest.mark.parametrize("case", SPEC["risk_price_cases"], ids=lambda c: c["name"])
def test_price_matches_the_reviewed_spec(case):
  leg = _leg(direction=case["direction"], stop=case["stop"])
  assert leg is not None and leg.price == Decimal(case["price"])


@pytest.mark.parametrize("case", SPEC["risk_volume_cases"], ids=lambda c: c["equity"])
def test_lots_by_equity_match_the_reviewed_spec(case):
  leg = _leg(equity=float(case["equity"]))
  assert leg is not None and leg.lots == Decimal(case["lots"])


def test_unknown_equity_takes_the_smaller_tier():
  leg = _leg(equity=None)
  assert leg is not None and leg.lots == Decimal("0.02")


def test_the_production_incident_leg():
  # opp_9b9fcde7: SELL stop 4131.51, equity 1595.49 -> 4130.01 for 0.05 lot.
  leg = _leg()
  assert leg == TradePlanRiskLeg(price=Decimal("4130.01"), lots=Decimal("0.05"))


def test_no_leg_for_fx_single_entries_or_when_disabled():
  assert _leg(symbol="EURUSD") is None
  single = TradePlanEntry(type=ENTRY_TYPE_SINGLE_LIMIT, expires_at=1, order_price=Decimal("4125.0"))
  assert _leg(entry=single) is None
  assert _leg(cfg=execution_cfg(**{"execution.reaction_risk_leg.enabled": False})) is None
  assert _leg(cfg=SimpleNamespace(execution=SimpleNamespace())) is None


def test_the_leg_round_trips_and_stays_off_plans_without_one():
  entry = TradePlanEntry(**{**_ladder().__dict__, "risk_leg": _leg()})
  payload = entry.to_dict()
  assert payload["risk_leg"] == {"price": "4130.01", "lots": "0.05"}
  assert TradePlanEntry.from_dict(json.loads(json.dumps(payload))) == entry
  assert "risk_leg" not in _ladder().to_dict()


def test_the_contract_rejects_a_risk_leg_on_a_single_entry_or_with_bad_numbers():
  single = TradePlanEntry(type=ENTRY_TYPE_SINGLE_LIMIT, expires_at=1, order_price=Decimal("4125.0")).to_dict()
  with pytest.raises(TradePlanError):
    TradePlanEntry.from_dict({**single, "risk_leg": {"price": "4130.01", "lots": "0.05"}})
  ladder = _ladder().to_dict()
  for bad in ({"price": "0", "lots": "0.05"}, {"price": "4130.01", "lots": "0"}):
    with pytest.raises(TradePlanError):
      TradePlanEntry.from_dict({**ladder, "risk_leg": bad})


def test_manual_ladder_declares_the_risk_leg_and_a_single_entry_does_not():
  plan = build_manual_trade_plan(_intent(), account_equity=1595.0)
  assert plan.entry.risk_leg == TradePlanRiskLeg(price=Decimal("4108.50"), lots=Decimal("0.05"))
  assert TradePlan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan
  assert build_manual_trade_plan(_intent(single_entry_override=True)).entry.risk_leg is None


def test_a_risk_leg_beyond_the_stop_fails_validation():
  plan = build_manual_trade_plan(_intent(), account_equity=1595.0)
  bad_entry = TradePlanEntry(**{**plan.entry.__dict__, "risk_leg": TradePlanRiskLeg(Decimal("4111"), Decimal("0.05"))})
  bad = TradePlan(**{**plan.__dict__, "entry": bad_entry})
  with pytest.raises(TradePlanError, match="risk_leg"):
    bad.validate()


@pytest.mark.asyncio
async def test_equity_comes_from_the_executor_snapshot():
  class Client:
    def __init__(self, raw):
      self.raw = raw

    async def get(self, key):
      assert key == "auto_trade:executor_snapshot:XAU"
      return self.raw

  assert await load_account_equity(Client(json.dumps({"account_equity": 1595.49})), "XAU") == 1595.49
  assert await load_account_equity(Client(json.dumps({"account_equity": 0})), "XAU") is None
  assert await load_account_equity(Client(None), "XAU") is None
  assert await load_account_equity(Client("not json"), "XAU") is None


def test_the_root_card_never_prints_the_risk_leg():
  import app.autotrade.setup_card as setup_card

  assert not hasattr(setup_card, "format_risk_leg_line")
  assert "risk_leg" not in setup_card.format_plan_published_root_card.__code__.co_varnames
  for name in ("apply_forming_card_risk_leg", "ensure_forming_card_risk_leg", "published_plan_risk_leg"):
    assert not hasattr(setup_card, name)


def test_module_exposes_the_leg_id_the_executor_keys_on():
  from app.autotrade.trade_plan import RISK_LEG_ID

  assert RISK_LEG_ID == SPEC["risk_leg"]["leg_id"]
  assert risk_leg_module.plan_risk_leg is plan_risk_leg
