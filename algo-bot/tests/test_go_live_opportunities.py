"""Go publishes the opportunity IDs it currently holds live; Python reads them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.autotrade.go_live_opportunities import (
  go_live_opportunities_key,
  go_live_opportunity_ids,
  parse_live_opportunities,
)
from app.autotrade.go_opportunity_policy import match_id_for, opportunity_id_for_match_id
from app.persistence import redis_state

pytestmark = pytest.mark.no_database

GO_SOURCE = (
  Path(__file__).resolve().parents[2]
  / "analysis-engine" / "internal" / "transport" / "redis" / "liveopportunities.go"
)


def test_key_is_uppercase_and_matches_the_literal_go_publishes_under():
  assert go_live_opportunities_key("xau") == "analysis:live_opportunities:XAU"
  assert '"analysis:live_opportunities:"' in GO_SOURCE.read_text()


def test_match_id_round_trips_to_the_go_opportunity_id():
  assert opportunity_id_for_match_id(match_id_for("opp_abc")) == "opp_abc"


def test_parse_returns_the_published_ids():
  raw = json.dumps({"symbol": "XAU", "generated_at": 1, "ids": ["opp_a", "opp_b", "", 7]})
  assert parse_live_opportunities(raw) == frozenset({"opp_a", "opp_b"})


def test_an_empty_ids_list_is_a_real_empty_set_not_unavailable():
  assert parse_live_opportunities('{"ids": []}') == frozenset()


@pytest.mark.parametrize("raw", ["not json", "{}", '{"ids": null}', '{"ids": "opp_a"}'])
def test_a_document_without_an_ids_list_is_rejected(raw):
  with pytest.raises((ValueError, json.JSONDecodeError)):
    parse_live_opportunities(raw)


@pytest.mark.asyncio
async def test_missing_key_is_unavailable_not_empty():
  client = redis_state.get_client()
  assert await go_live_opportunity_ids(client, "XAU") is None


@pytest.mark.asyncio
async def test_published_set_is_read_back():
  client = redis_state.get_client()
  await client.set(go_live_opportunities_key("XAU"), json.dumps({"ids": ["opp_a"]}))
  assert await go_live_opportunity_ids(client, "XAU") == frozenset({"opp_a"})


@pytest.mark.asyncio
async def test_older_projection_is_unavailable_when_event_requires_a_newer_snapshot():
  client = redis_state.get_client()
  await client.set(
    go_live_opportunities_key("XAU"),
    json.dumps({"generated_at": 100, "ids": ["opp_a"]}),
  )
  assert await go_live_opportunity_ids(client, "XAU", minimum_generated_at=101) is None
  assert await go_live_opportunity_ids(client, "XAU", minimum_generated_at=100) == frozenset({"opp_a"})


@pytest.mark.asyncio
async def test_v1_projection_without_timestamp_remains_compatible():
  client = redis_state.get_client()
  await client.set(go_live_opportunities_key("XAU"), json.dumps({"ids": ["opp_a"]}))
  assert await go_live_opportunity_ids(client, "XAU", minimum_generated_at=101) == frozenset({"opp_a"})


@pytest.mark.asyncio
async def test_a_garbled_set_is_unavailable_not_empty():
  client = redis_state.get_client()
  await client.set(go_live_opportunities_key("XAU"), "{broken")
  assert await go_live_opportunity_ids(client, "XAU") is None
