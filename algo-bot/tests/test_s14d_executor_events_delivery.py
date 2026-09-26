"""S14D: the executor's real events for a Go-derived plan reach Telegram delivery and the journal.

The events are the ones ctrader-engine's TradePlanRuntime emitted for the Go-derived plan in its broker
simulator (contracts/autotrade/go-derived-plan-executor-events.json, drift-guarded by
GoDerivedPlanChainTests). Here they go through the real journal ingestion (PostgreSQL) and the real
delivery handler (Telegram calls stubbed): the last hop of
  Kafka event -> ... -> TradePlan V8 -> cTrader ack -> execution events -> Telegram + journal.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository
from app.autotrade import delivery, setup_card, stats_ingestion
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  PLAN_BUILT,
  PLAN_PUBLISHED,
  create_setup,
  transition_setup,
)
from app.persistence import redis_state, store
from tests.test_go_opportunity_policy import golden

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "autotrade"
EVENTS = json.loads((CONTRACTS / "go-derived-plan-executor-events.json").read_text())
PLAN = json.loads((CONTRACTS / "go-derived-plan-xau-supply.json").read_text())
MATCH_ID, PLAN_ID, OPPORTUNITY_ID = "go_opp_chain", "v8:go_opp_chain", "opp_chain"


async def _published_setup(client):
  await create_setup(client, setup_id=MATCH_ID, thesis_id=PLAN["thesis_id"], symbol="XAU")
  for state in ("watching", "touched", "forming", CONFIRMED, PLAN_BUILT, PLAN_PUBLISHED):
    await transition_setup(client, MATCH_ID, state)
  await setup_card.save_forming_card(client, MATCH_ID, chat_id=123, message_id=4001, text="🟢 <b>PLAN PUBLISHED</b> · SELL XAU")


def test_the_executor_events_carry_the_go_provenance_end_to_end():
  assert [e["type"] for e in EVENTS] == ["order_filled", "position_closed"]
  for event in EVENTS:
    assert event["match_id"] == MATCH_ID and event["candidate_id"] == PLAN_ID == event["group_id"]
    assert event["thesis_id"] == PLAN["thesis_id"]                                   # Python's stable structural thesis
    assert event["structural_zone_id"] == PLAN["source_structure"]["structure_id"]   # the Go zone
    assert event["stream"] == "algo_auto" and event["direction"] == "SELL" and event["setup"] == "Supply Demand"
    assert event["stop_loss"] == float(PLAN["stop"]["price"])                        # protective stop from the plan


@pytest.mark.asyncio
async def test_events_reach_the_journal_with_a_trace_back_to_the_kafka_opportunity(sql):
  await store.init_db()
  client = redis_state.get_client()
  repo = PostgresAnalysisOpportunityRepository()
  raw = golden(1_790_000_100, id=OPPORTUNITY_ID)
  raw["event_id"] = "evt-chain"
  await repo.apply(parse_analysis_event(OpportunityTopic, json.dumps(raw)), topic=OpportunityTopic, partition=0, offset=1)

  entries = [(f"{i + 1}-0", {"payload": json.dumps(e)}) for i, e in enumerate(EVENTS)]
  cursor = await stats_ingestion.process_auto_trade_stats_entries(client, entries, cursor="0-0")
  assert cursor == "2-0"

  result = await sql.row("SELECT * FROM auto_trade_results WHERE group_id = $1", PLAN_ID)
  assert (result["setup_type"], result["direction"], result["trade_stream"], result["symbol"]) == ("Supply Demand", "SELL", "algo_auto", "XAU")
  assert result["result_pips"] == pytest.approx(86.0) and result["booked_tp_count"] == 1 and result["exit_path"] == "tp1_stop"
  assert result["stop_pips"] == pytest.approx(57.3, abs=0.05)                        # |4354.10 - 4359.83| in XAU pips
  # provenance: the journal's group id IS the plan id, which embeds the match id, which embeds the Kafka opportunity id
  traced = await sql.val(
    "SELECT o.opportunity_id FROM auto_trade_results r JOIN analysis_opportunities o ON r.group_id = 'v8:go_' || o.opportunity_id WHERE r.group_id = $1",
    PLAN_ID,
  )
  assert traced == OPPORTUNITY_ID
  # replaying the same executor events is idempotent: one journal row
  await stats_ingestion.process_auto_trade_stats_entries(client, entries, cursor="0-0")
  assert await sql.val("SELECT count(*) FROM auto_trade_results WHERE group_id = $1", PLAN_ID) == 1


@pytest.mark.asyncio
async def test_events_reach_telegram_as_replies_under_the_one_root_card(sql, monkeypatch):
  await store.init_db()
  client = redis_state.get_client()
  await _published_setup(client)
  sent: list[tuple[str, dict]] = []
  edited: list[tuple[int, str]] = []

  async def send(text, **kwargs):
    sent.append((text, kwargs))
    return SimpleNamespace(message_id=5000 + len(sent))

  async def edit(chat_id, message_id, text):
    edited.append((message_id, text))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", edit)

  for event in EVENTS:
    assert await delivery._deliver_auto_trade_event(client, event, profile="internal", chat_id=123, send=send)

  texts = [t for t, _ in sent]
  assert texts[0].startswith("🟢 active") and "order filled" in texts[0]
  assert any("TP1" in t and "+86 pips" in t for t in texts)                          # the target and its result reach the owner
  card = await setup_card.load_forming_card(client, MATCH_ID)
  assert card is not None and int(card["message_id"]) == 4001                          # still the single root card: no second thread
  assert not any(t.strip().startswith("SETUP") for t in texts)                         # no duplicate "forming" presentation
