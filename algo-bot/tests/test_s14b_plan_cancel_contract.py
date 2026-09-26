"""Python half of the shared plan-cancel contract (the C# half reads the same file)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.autotrade import go_plan_cancel as gpc

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "contracts/autotrade/plan-cancel-intent.json").read_text())


def test_keys_and_ttl_match_the_shared_contract():
  assert gpc.plan_cancel_key("v8:go_x") == FIXTURE["intent_key"].format(plan_id="v8:go_x")
  assert gpc.plan_cancel_ack_key("v8:go_x") == FIXTURE["ack_key"].format(plan_id="v8:go_x")
  assert gpc.PLAN_CANCEL_TTL_SECONDS == FIXTURE["intent_ttl_seconds"]


def test_sources_are_exactly_the_contract_sources():
  assert sorted({gpc.SOURCE_INVALIDATED, gpc.SOURCE_EXPIRED, gpc.SOURCE_ROLLBACK}) == FIXTURE["sources"]


@pytest.mark.asyncio
async def test_written_intent_has_exactly_the_contract_fields():
  from app.persistence import redis_state
  client = redis_state.get_client()
  await gpc.request_plan_cancel(client, "v8:go_x", reason="r", source=gpc.SOURCE_ROLLBACK, requested_at=5, opportunity_id="x", epoch=1)
  intent = await gpc.read_plan_cancel(client, "v8:go_x")
  assert sorted(intent) == FIXTURE["intent_fields"]
  assert intent["source"] in FIXTURE["sources"]
  assert 0 < await client.ttl(gpc.plan_cancel_key("v8:go_x")) <= FIXTURE["intent_ttl_seconds"]


def test_contract_declares_every_outcome_it_documents():
  assert sorted(FIXTURE["semantics"]) == FIXTURE["outcomes"]
