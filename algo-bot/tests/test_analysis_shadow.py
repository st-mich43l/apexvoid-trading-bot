import json

import pytest

from app.analysis_client.models import OpportunityTopic, parse_analysis_event
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository
from app.analysis_client.shadow import AnalysisShadowEvaluator
from app.persistence import store
from tests.test_analysis_client_models import _opportunity


@pytest.mark.asyncio
async def test_shadow_records_contract_gap_and_never_publishes_a_trade_plan(sql):
  await store.init_db()
  event = parse_analysis_event(OpportunityTopic, json.dumps(_opportunity()))
  repository = PostgresAnalysisOpportunityRepository()
  await repository.apply(event, topic=OpportunityTopic, partition=0, offset=1)
  decision = await AnalysisShadowEvaluator(repository).evaluate_creation(event)
  assert decision.outcome == "contract_gap"
  row = await sql.row("SELECT outcome, missing_fields FROM analysis_shadow_decisions WHERE opportunity_id = 'opp-1'")
  assert row["outcome"] == "contract_gap"
  assert "current_price" in json.loads(row["missing_fields"])
