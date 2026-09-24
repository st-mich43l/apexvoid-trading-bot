import json
from dataclasses import dataclass

import pytest

from app.analysis_client.consumer import AnalysisOpportunityConsumer
from app.analysis_client.models import OpportunityTopic
from app.analysis_client.repository import LifecycleResult
from tests.test_analysis_client_models import _opportunity


@dataclass
class _Record:
  topic: str
  partition: int
  offset: int
  value: bytes


class _Repository:
  def __init__(self):
    self.applied = []
    self.rejections = []
    self.decisions = []

  async def apply(self, event, **kwargs):
    self.applied.append((event, kwargs))
    return LifecycleResult("created", event.payload.id)

  async def record_rejection(self, **kwargs):
    self.rejections.append(kwargs)

  async def record_shadow_decision(self, **kwargs):
    self.decisions.append(kwargs)


@pytest.mark.asyncio
async def test_consumer_persists_valid_record_then_evaluates_shadow():
  repository = _Repository()
  from app.analysis_client.shadow import AnalysisShadowEvaluator
  consumer = AnalysisOpportunityConsumer(repository, shadow=AnalysisShadowEvaluator(repository), mode="go_shadow")
  record = _Record(OpportunityTopic, 1, 9, json.dumps(_opportunity()).encode())
  await consumer.process_record(record)
  assert len(repository.applied) == 1
  assert repository.decisions[0]["outcome"] == "contract_gap"


@pytest.mark.asyncio
async def test_consumer_durably_records_malformed_record_without_applying():
  repository = _Repository()
  consumer = AnalysisOpportunityConsumer(repository, shadow=None, mode="python")
  await consumer.process_record(_Record(OpportunityTopic, 1, 10, b"not json"))
  assert repository.applied == []
  assert repository.rejections[0]["partition"] == 1
