import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.analysis_client import consumer as consumer_module
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
  timestamp: int | None = None


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
async def test_consumer_durably_records_malformed_record_without_applying():
  repository = _Repository()
  consumer = AnalysisOpportunityConsumer(repository, policy=object())
  await consumer.process_record(_Record(OpportunityTopic, 1, 10, b"not json"))
  assert repository.applied == []
  assert repository.rejections[0]["partition"] == 1


class _Policy:
  def __init__(self, fail_times=0):
    self.created, self.terminal, self.fail_times = [], [], fail_times

  async def on_creation(self, event, result, **_):
    if self.fail_times:
      self.fail_times -= 1
      raise ConnectionError("redis down")
    self.created.append(event.payload.id)
    return "match_written"

  async def on_terminal(self, event, result):
    self.terminal.append(event.payload.opportunity_id)
    return "match_withdrawn"


@pytest.mark.asyncio
async def test_policy_hook_runs_for_every_valid_kafka_event():
  repository, policy = _Repository(), _Policy()
  consumer = AnalysisOpportunityConsumer(repository, policy=policy)
  await consumer.process_record(_Record(OpportunityTopic, 1, 2, json.dumps(_opportunity()).encode()))
  assert policy.created == ["opp-1"]


@pytest.mark.asyncio
async def test_policy_failure_propagates_so_the_offset_is_not_committed():
  """The loop only commits after process_record returns; a raised error means
  redelivery, and the idempotent policy retries. Fail closed, never half-done."""
  repository, policy = _Repository(), _Policy(fail_times=1)
  consumer = AnalysisOpportunityConsumer(repository, policy=policy)
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
  consumer = AnalysisOpportunityConsumer(repository, policy=policy)
  await consumer.process_record(_Record(InvalidationTopic, 1, 4, json.dumps(_invalidated()).encode()))
  assert policy.terminal == ["opp-1"]


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_consumer_uses_canonical_runtime_kafka_client_id(monkeypatch):
  captured = {}

  class _KafkaConsumer:
    def __init__(self, *topics, **kwargs):
      captured["topics"] = topics
      captured.update(kwargs)

    async def start(self):
      return None

    async def stop(self):
      return None

  monkeypatch.setitem(
    sys.modules,
    "aiokafka",
    SimpleNamespace(AIOKafkaConsumer=_KafkaConsumer, TopicPartition=object),
  )
  monkeypatch.setattr(
    consumer_module,
    "runtime_config",
    SimpleNamespace(
      analysis=SimpleNamespace(
        technical_authority=SimpleNamespace(
          consumer_enabled=True,
          consumer_group="go-opportunities",
        ),
      ),
      runtime=SimpleNamespace(
        kafka=SimpleNamespace(
          enabled=True,
          brokers=["kafka:9092"],
          client_id=SimpleNamespace(algo_bot="apexvoid-algo-bot"),
        ),
      ),
    ),
  )
  monkeypatch.setattr(consumer_module, "PostgresAnalysisOpportunityRepository", lambda: object())
  monkeypatch.setattr(consumer_module, "run_consumer_loop", lambda *args, **kwargs: _noop())
  monkeypatch.setattr(
    "app.autotrade.go_opportunity_policy.GoOpportunityPolicy",
    lambda repository: object(),
  )

  async def _noop():
    return None

  await consumer_module.analysis_opportunity_consumer_loop()

  assert captured["client_id"] == "apexvoid-algo-bot"
  assert captured["bootstrap_servers"] == ["kafka:9092"]
