"""S14E: Go-origin cards use the canonical Manual/Auto card helpers, the trading contract keeps full
numeric precision while the card shows the instrument's approved precision, and the executor-injected
risk leg is a separate, default-off gate for Go-origin plans.

Real PostgreSQL + real Redis (production Lua); the price data is a real replayed Go opportunity
(contracts/analysis/replay), rebased in time only.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.autotrade import go_opportunity_policy as pol
from app.autotrade import setup_card
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from app.autotrade.setup_card import format_plan_published_root_card
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides
from tests.test_s14d_go_full_chain import (  # noqa: F401 - fixtures + helpers
  _freeze_technique_killzone_hour,
  _no_news_by_default,
  consumer_for,
  cycle,
  h,
  live_inputs,
  plans,
  prod,
)

pytestmark = pytest.mark.real_redis

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "contracts" / "autotrade" / "xau-ladder-spec.json").read_text())
REPLAYED = ROOT / "contracts" / "analysis" / "examples" / "go-replayed-supply-unrounded-opportunity.json"


def replayed_event_line() -> str:
  """One real replayed Go supply opportunity with unrounded prices (provenance inside the file)."""
  event = json.loads(REPLAYED.read_text())
  event.pop("_provenance", None)
  return json.dumps(event)


def rebased(now: int) -> dict:
  event = json.loads(replayed_event_line())
  payload = event["payload"]
  shift = int(now) - 60 - payload["created_at"]
  event["occurred_at"] += shift
  event["produced_at"] += shift
  for key in ("formed_at", "created_at", "expires_at"):
    if payload.get(key) is not None:
      payload[key] += shift
  tech = payload["technical_context"]
  tech["reference_time"] += shift
  tech["confirmation"]["touch_bar_time"] += shift
  tech["confirmation"]["confirmation_bar_time"] += shift
  for higher in tech["higher_timeframes"]:
    higher["reference_time"] += shift
  return event


async def deliver_and_publish(h, prod, monkeypatch):
  live_inputs(monkeypatch, bid=4293.0, ask=4293.2)                    # inside the replayed zone 4291.21-4296.87
  await h.grant()
  await h._ensure()
  now = int(h.clock.now)
  raw = rebased(now)
  record = SimpleNamespace(topic=OpportunityTopic, partition=0, offset=1, timestamp=(now - 5) * 1000, value=json.dumps(raw).encode())
  await consumer_for(h).process_record(record)
  await cycle(prod, n=2)
  return raw["payload"], deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0]


# ---- precision: the contract keeps it, the card shows the approved precision --------------------------------------

@pytest.mark.asyncio
async def test_contract_keeps_full_precision_while_the_card_shows_the_instruments_approved_precision(h, prod, monkeypatch):
  go, match = await deliver_and_publish(h, prod, monkeypatch)
  (plan,) = await plans(prod)
  # exact decimal text of Go's floats survives into the trading contract
  assert plan["entry"]["zone_low"] == str(Decimal(repr(go["entry"]["low"]))) == "4291.21"
  assert plan["entry"]["zone_high"] == str(Decimal(repr(go["entry"]["high"]))) == "4296.87"
  assert plan["source_structure"]["invalidation_price"] == str(Decimal(repr(go["invalidation"]["price"]))) == "4298.970714285714"
  assert match.absolute_target_price == go["targets"][0]["price"]["price"] == 4284.159928571428      # the match keeps Go's exact target
  # the stop the executor places is the planner's tick-aligned price, not a blanket rounding of the contract
  assert Decimal(plan["stop"]["price"]).as_tuple().exponent >= -2
  # ...and Go's own target is NOT what gets executed: the plan's TP is the builder's (recorded, not hidden)
  assert plan["targets"][0]["price"] != str(Decimal(repr(go["targets"][0]["price"]["price"])))

  card = format_plan_published_root_card(
    match, stop_price=float(plan["stop"]["price"]), target_prices=tuple(float(t["price"]) for t in plan["targets"]),
  )
  for raw in ("4291.21", "4296.87", "4298.97", "4284.159", "4.2014"):
    assert raw not in card                                                                 # no raw contract digits on the card
  assert "4,291 - 4,297" in card and "4,299" in card                                       # the existing Manual Algo whole-point XAU display


@pytest.mark.asyncio
async def test_card_carries_every_presentation_element_from_the_canonical_helper(h, prod, monkeypatch):
  _go, match = await deliver_and_publish(h, prod, monkeypatch)
  (plan,) = await plans(prod)
  card = format_plan_published_root_card(match, stop_price=float(plan["stop"]["price"]), target_prices=tuple(float(t["price"]) for t in plan["targets"]))
  assert "XAU" in card and "M5" in card                                                    # instrument + timeframe
  assert "📉" in card and "SELL" in card                                                    # direction
  assert "Supply Demand" in card and "⭐" in card                                           # setup identity
  assert "Entry Zone" in card and "SL:" in card and "risk" in card                         # entry, stop and its risk
  assert "TP1" in card                                                                     # TP ladder
  assert "SETUP FORMING" in card or "IN ZONE" in card or "PLAN PUBLISHED" in card          # status slot (edited in place afterwards)


@pytest.mark.asyncio
async def test_a_second_render_edits_the_one_root_card_and_never_starts_a_second_thread(h, prod, monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  _go, match = await deliver_and_publish(h, prod, monkeypatch)
  sent: list[str] = []
  edited: list[tuple[int, str]] = []

  async def send(*args, **kwargs):
    sent.append(str(args[-1]) if args else "")
    return SimpleNamespace(message_id=7001)

  async def edit(chat_id, message_id, text):
    edited.append((message_id, text))

  async def delete(chat_id, message_id):
    raise AssertionError("a plan card must never be deleted and re-posted")

  first = await setup_card.ensure_plan_published_root_card(prod, match, send_fn=send, edit_fn=edit, delete_fn=delete)
  second = await setup_card.ensure_plan_published_root_card(prod, match, send_fn=send, edit_fn=edit, delete_fn=delete)
  assert first == second == 7001
  assert len(sent) == 1                                                                    # exactly one Telegram root for the setup


# ---- the separate risk-leg gate for Go-origin plans ---------------------------------------------------------------

def test_the_go_origin_risk_leg_gate_is_off_by_default_and_names_the_spec_tag():
  assert runtime_config.analysis.technical_authority.go_origin_risk_leg_enabled is False
  assert pol.RISK_LEG_DISABLED_TAG == SPEC["go_origin_risk_leg_disabled_tag"] == "risk_leg:disabled"


@pytest.mark.parametrize("enabled,expect_tag", [(False, True), (True, False)])
def test_go_matches_carry_the_disable_tag_until_the_gate_is_on(enabled, expect_tag):
  event = parse_analysis_event(OpportunityTopic, replayed_event_line())
  match = pol.build_strategy_match(event, profile=pol.REVIEWED_SCOPES["supply"], epoch=1, now=event.payload.created_at + 1, risk_leg_enabled=enabled)
  assert (pol.RISK_LEG_DISABLED_TAG in match.tags) is expect_tag


@pytest.mark.asyncio
async def test_the_plan_a_default_go_deployment_publishes_disables_the_executor_risk_leg(h, prod, monkeypatch):
  await deliver_and_publish(h, prod, monkeypatch)
  (plan,) = await plans(prod)
  assert "risk_leg:disabled" in plan["analysis"]["tags"]


@pytest.mark.asyncio
async def test_turning_the_gate_on_removes_the_tag_so_the_executor_may_inject_the_leg(h, prod, monkeypatch):
  install_runtime_overrides(monkeypatch, {"analysis.technical_authority.go_origin_risk_leg_enabled": True})
  h.policy = pol.GoOpportunityPolicy(h.repo, fence=h.fence, clock=h.clock, multiple_matches_enabled=lambda: True)
  await deliver_and_publish(h, prod, monkeypatch)
  (plan,) = await plans(prod)
  assert "risk_leg:disabled" not in plan["analysis"]["tags"]
