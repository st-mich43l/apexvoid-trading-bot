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


class _Policy:
  def __init__(self, fail_times=0):
    self.created, self.terminal, self.fail_times = [], [], fail_times

  async def on_creation(self, event, result):
    if self.fail_times:
      self.fail_times -= 1
      raise ConnectionError("redis down")
    self.created.append(event.payload.id)
    return "match_written"

  async def on_terminal(self, event, result):
    self.terminal.append(event.payload.opportunity_id)
    return "match_withdrawn"


@pytest.mark.asyncio
async def test_policy_hook_runs_only_in_go_mode():
  for mode in ("python", "go_shadow"):
    repository, policy = _Repository(), _Policy()
    consumer = AnalysisOpportunityConsumer(repository, shadow=None, mode=mode, policy=policy)
    await consumer.process_record(_Record(OpportunityTopic, 1, 1, json.dumps(_opportunity()).encode()))
    assert policy.created == [], mode
  repository, policy = _Repository(), _Policy()
  consumer = AnalysisOpportunityConsumer(repository, shadow=None, mode="go", policy=policy)
  await consumer.process_record(_Record(OpportunityTopic, 1, 2, json.dumps(_opportunity()).encode()))
  assert policy.created == ["opp-1"]


@pytest.mark.asyncio
async def test_policy_failure_propagates_so_the_offset_is_not_committed():
  """The loop only commits after process_record returns; a raised error means
  redelivery, and the idempotent policy retries. Fail closed, never half-done."""
  repository, policy = _Repository(), _Policy(fail_times=1)
  consumer = AnalysisOpportunityConsumer(repository, shadow=None, mode="go", policy=policy)
  record = _Record(OpportunityTopic, 1, 3, json.dumps(_opportunity()).encode())
  with pytest.raises(ConnectionError):
    await consumer.process_record(record)
  assert repository.applied and policy.created == []       # durably recorded, plan not made
  await consumer.process_record(record)                     # redelivery
  assert policy.created == ["opp-1"]


@pytest.mark.asyncio
async def test_terminal_events_reach_the_policy_in_go_mode():
  from tests.test_analysis_client_models import _invalidated
  from app.analysis_client.models import InvalidationTopic
  class _TerminalRepository(_Repository):
    async def apply(self, event, **kwargs):
      return LifecycleResult("terminated", event.payload.opportunity_id)

  repository, policy = _TerminalRepository(), _Policy()
  consumer = AnalysisOpportunityConsumer(repository, shadow=None, mode="go", policy=policy)
  await consumer.process_record(_Record(InvalidationTopic, 1, 4, json.dumps(_invalidated()).encode()))
  assert policy.terminal == ["opp-1"]
