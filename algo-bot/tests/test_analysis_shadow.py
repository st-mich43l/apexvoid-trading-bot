import json

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository
from app.analysis_client.shadow import AnalysisShadowEvaluator, ShadowDecision
from app.persistence import store
from tests.test_analysis_client_models import _opportunity


@pytest.mark.asyncio
async def test_unwired_shadow_says_nothing_was_evaluated_and_never_publishes(sql):
  """No dry-run policy wired: honest ``shadow_unavailable`` (the old static
  ``contract_gap`` is gone; the real dry run lives in test_s14a_shadow_dry_run)."""
  await store.init_db()
  event = parse_analysis_event(OpportunityTopic, json.dumps(_opportunity()))
  repository = PostgresAnalysisOpportunityRepository()
  await repository.apply(event, topic=OpportunityTopic, partition=0, offset=1)
  decision = await AnalysisShadowEvaluator(repository).evaluate_creation(event)
  assert (decision.outcome, decision.reason) == ("shadow_unavailable", "dry_run_policy_not_configured")
  row = await sql.row("SELECT outcome, reason FROM analysis_shadow_decisions WHERE opportunity_id = 'opp-1'")
  assert (row["outcome"], row["reason"]) == ("shadow_unavailable", "dry_run_policy_not_configured")


@pytest.mark.asyncio
async def test_a_wired_dry_run_owns_the_decision_and_receives_the_publish_time(sql):
  await store.init_db()
  event = parse_analysis_event(OpportunityTopic, json.dumps(_opportunity()))
  seen = {}

  async def dry_run(ev, *, published_at=None):
    seen["published_at"] = published_at
    return ShadowDecision("would_wait", "waiting_retest_entry_zone")

  decision = await AnalysisShadowEvaluator(PostgresAnalysisOpportunityRepository(), dry_run=dry_run).evaluate_creation(event, published_at=123)
  assert decision.outcome == "would_wait" and seen == {"published_at": 123}
  assert await sql.val("SELECT count(*) FROM analysis_shadow_decisions") == 0    # the dry run records its own decision
