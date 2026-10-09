"""Go-origin cards use the canonical Manual/Auto card helpers, and technical structure keeps full
numeric precision while execution policy narrows fill permission and the card shows approved precision, and the executor-injected
risk leg remains an execution-policy concern rather than a technical-source concern.

Real PostgreSQL + real Redis (production Lua); the price data is a real replayed Go opportunity
(analysis-engine/testdata), rebased in time only.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.autotrade import go_opportunity_policy as pol
from app.autotrade import setup_card
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from app.autotrade.setup_card import format_plan_published_root_card
from tests.support.canonical_fixtures import install_runtime_overrides
from tests.support.canonical_fixtures import _load_production_example
from tests.test_go_full_chain import (  # noqa: F401 - fixtures + helpers
  _no_news_by_default,
  consumer_for,
  arbitration_record,
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
  from app.core import config as config_module

  production = _load_production_example().config
  install_runtime_overrides(
    monkeypatch,
    base=config_module.runtime_config.model_copy(
      update={"instruments": production.instruments},
    ),
  )
  live_inputs(monkeypatch, bid=4293.0, ask=4293.2)                    # inside the replayed zone 4291.21-4296.87
  install_runtime_overrides(
    monkeypatch, {
      "instruments.XAU.stop_envelope.max_pips": 100,
      "instruments.XAU.targeting.mode": "fixed_rr",
      "instruments.XAU.auto_entry.mode": "single_best",
      "execution.zone_scaling.fill_enabled": False,
    },
  )
  await h.activate()
  await h._ensure()
  now = int(h.clock.now)
  raw = rebased(now)
  record = SimpleNamespace(topic=OpportunityTopic, partition=0, offset=1, timestamp=(now - 5) * 1000, value=json.dumps(raw).encode())
  consumer = consumer_for(h)
  await consumer.process_record(record)
  await consumer.process_record(arbitration_record(now, raw["payload"]["id"]))
  await cycle(prod, n=2)
  return raw["payload"], deserialize_matches(await prod.get(strategy_matches_key("XAU")))[0]


# ---- precision: structure keeps it, execution narrows fill permission, card rounds -------------------------

@pytest.mark.asyncio
async def test_contract_keeps_full_precision_while_the_card_shows_the_instruments_approved_precision(h, prod, monkeypatch):
  go, match = await deliver_and_publish(h, prod, monkeypatch)
  (plan,) = await plans(prod)
  # The source structure keeps Go's exact band. SELL market-watch execution
  # caps its adverse low at the policy-planned quote and retains the better
  # technical high; it cannot silently accept a worse fill across the band.
  assert plan["source_structure"]["low"] == str(Decimal(repr(go["entry"]["low"]))) == "4291.21"
  # The executed band is the planner's: on gold it is rounded to the numbers the card prints,
  # lies inside Go's band (give or take that rounding) and never widens the technical zone.
  band_low, band_high = float(plan["entry"]["zone_low"]), float(plan["entry"]["zone_high"])
  assert band_low == round(band_low) and band_high == round(band_high)
  assert go["entry"]["low"] - 0.5 <= band_low < band_high <= go["entry"]["high"] + 0.5
  assert plan["source_structure"]["high"] == str(Decimal(repr(go["entry"]["high"]))) == "4296.87"
  assert plan["source_structure"]["invalidation_price"] == str(Decimal(repr(go["invalidation"]["price"]))) == "4298.970714285714"
  assert match.absolute_target_price == go["targets"][0]["price"]["price"] == 4284.159928571428      # the match keeps Go's exact target
  # the stop the executor places is the planner's price on the card's numbers, beyond Go's band
  assert float(plan["stop"]["price"]) == round(float(plan["stop"]["price"]))
  assert float(plan["stop"]["price"]) > float(plan["entry"]["zone_high"])
  # ...and Go's own target is NOT what gets executed: the plan's TP is the builder's (recorded, not hidden)
  assert plan["targets"][0]["price"] != str(Decimal(repr(go["targets"][0]["price"]["price"])))
  assert len(plan["targets"]) == 4
  assert [target["close_ratio"] for target in plan["targets"]] == [
    "0.4", "0.2", "0.2", "0.2",
  ]

  # The card is rendered with the span the published plan holds, exactly as production does.
  entry_span = await setup_card.published_plan_entry_span(prod, match.match_id)
  card = format_plan_published_root_card(
    match, stop_price=float(plan["stop"]["price"]), target_prices=tuple(float(t["price"]) for t in plan["targets"]),
    entry_span=entry_span,
  )
  for raw in ("4291.21", "4296.87", "4298.97", "4284.159", "4.2014"):
    assert raw not in card                                                                 # no raw contract digits on the card
  # Whole-point XAU display, and the card prints exactly the plan's own numbers.
  from app.autotrade.trade_card import format_price
  assert format_price(band_low, "XAU") in card and format_price(band_high, "XAU") in card
  assert format_price(float(plan["stop"]["price"]), "XAU") in card


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


# ---- the Manual-Algo-style risk leg belongs to execution policy -------------------------------

@pytest.mark.no_database
def test_go_adapter_does_not_make_risk_leg_a_source_specific_decision():
  assert SPEC["risk_leg_disabled_tag"] == "risk_leg:disabled"
  event = parse_analysis_event(OpportunityTopic, replayed_event_line())
  match = pol.build_strategy_match(
    event, profile=pol.REVIEWED_SCOPES["supply"], now=event.payload.created_at + 1,
  )
  assert not any(tag.startswith("risk_leg:") for tag in match.tags)


@pytest.mark.asyncio
async def test_the_plan_keeps_risk_leg_ownership_out_of_go_provenance(h, prod, monkeypatch):
  await deliver_and_publish(h, prod, monkeypatch)
  (plan,) = await plans(prod)
  assert not any(tag.startswith("risk_leg:") for tag in plan["analysis"]["tags"])
