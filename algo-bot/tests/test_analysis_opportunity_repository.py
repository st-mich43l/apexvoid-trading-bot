import json

import pytest

from app.analysis_client.models import InvalidationTopic, OpportunityTopic, parse_analysis_event
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository
from tests.test_analysis_client_models import _invalidated, _opportunity
from app.persistence import store


@pytest.mark.asyncio
async def test_creation_replay_and_terminal_tombstone_are_idempotent(sql):
  await store.init_db()
  repository = PostgresAnalysisOpportunityRepository()
  creation = parse_analysis_event(OpportunityTopic, json.dumps(_opportunity()))
  first = await repository.apply(creation, topic=OpportunityTopic, partition=0, offset=5)
  replay = await repository.apply(creation, topic=OpportunityTopic, partition=0, offset=5)
  assert first.disposition == "created"
  assert replay.disposition == "duplicate_delivery"

  terminal = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated()))
  assert (await repository.apply(terminal, topic=InvalidationTopic, partition=0, offset=6)).disposition == "terminated"
  late = parse_analysis_event(OpportunityTopic, json.dumps(_opportunity(event_id="evt-create-late")))
  assert (await repository.apply(late, topic=OpportunityTopic, partition=0, offset=7)).disposition == "late_creation_rejected"
  assert await repository.active_for_symbol("XAU", now=200) == []


@pytest.mark.asyncio
async def test_unknown_terminal_creates_tombstone_before_late_creation(sql):
  await store.init_db()
  repository = PostgresAnalysisOpportunityRepository()
  terminal = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated()))
  outcome = await repository.apply(terminal, topic=InvalidationTopic, partition=1, offset=1)
  assert outcome.disposition == "unknown_terminal_tombstoned"
  creation = parse_analysis_event(OpportunityTopic, json.dumps(_opportunity()))
  outcome = await repository.apply(creation, topic=OpportunityTopic, partition=1, offset=2)
  assert outcome.disposition == "late_creation_rejected"
  row = await sql.row("SELECT state, terminal_event_id FROM analysis_opportunities WHERE opportunity_id = 'opp-1'")
  assert dict(row) == {"state": "expired", "terminal_event_id": "evt-terminal-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("reason_code", "expected_state"),
  [
    ("SETUP_EXPIRED", "expired"),
    ("STRUCTURE_INVALIDATED", "invalidated"),
    ("SESSION_EXPIRED", "invalidated"),
  ],
)
async def test_go_terminal_reason_classification_is_exact(sql, reason_code, expected_state):
  await store.init_db()
  repository = PostgresAnalysisOpportunityRepository()
  terminal = parse_analysis_event(
    InvalidationTopic,
    json.dumps(_invalidated(event_id=f"evt-{reason_code}", payload={**_invalidated()["payload"], "reason_code": reason_code})),
  )
  await repository.apply(terminal, topic=InvalidationTopic, partition=2, offset=1)
  row = await sql.row("SELECT state FROM analysis_opportunities WHERE opportunity_id = 'opp-1'")
  assert row["state"] == expected_state


@pytest.mark.asyncio
async def test_duplicate_terminal_after_restart_is_a_noop(sql):
  await store.init_db()
  terminal = parse_analysis_event(InvalidationTopic, json.dumps(_invalidated()))
  first = PostgresAnalysisOpportunityRepository()
  assert (await first.apply(terminal, topic=InvalidationTopic, partition=3, offset=1)).disposition == "unknown_terminal_tombstoned"
  # A newly constructed repository models process restart; Kafka redelivery
  # must still be fenced by the durable event id/partition offset.
  restarted = PostgresAnalysisOpportunityRepository()
  assert (await restarted.apply(terminal, topic=InvalidationTopic, partition=3, offset=1)).disposition == "duplicate_delivery"
