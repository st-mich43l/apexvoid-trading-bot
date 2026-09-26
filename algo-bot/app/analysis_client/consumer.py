"""Manual-commit Kafka consumer for Go Analysis Engine opportunities (S12A)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.analysis_client.models import (
  AnalysisContractError,
  InvalidationEnvelope,
  OpportunityEnvelope,
  OpportunityTopic,
  InvalidationTopic,
  parse_analysis_event,
)
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository
from app.analysis_client.shadow import AnalysisShadowEvaluator
from app.core.config import runtime_config
from app.persistence import redis_state

log = logging.getLogger(__name__)


class AnalysisOpportunityConsumer:
  """Applies records durably, then asks the caller to advance the offset."""

  def __init__(
    self,
    repository: PostgresAnalysisOpportunityRepository,
    *,
    shadow: AnalysisShadowEvaluator | None,
    mode: str,
    policy: Any | None = None,
  ):
    self._repository = repository
    self._shadow = shadow
    self._mode = mode
    # Optional Go->policy hook (S13C, mode="go" only). Duck-typed so this
    # package never imports autotrade or any legacy detector.
    self._policy = policy

  async def process_record(self, record: Any) -> None:
    topic = str(record.topic)
    partition = int(record.partition)
    offset = int(record.offset)
    try:
      event = parse_analysis_event(topic, record.value)
    except AnalysisContractError as exc:
      await self._repository.record_rejection(
        topic=topic, partition=partition, offset=offset, reason=str(exc), raw_payload=record.value,
      )
      log.warning("rejected Go analysis event topic=%s partition=%s offset=%s reason=%s", topic, partition, offset, exc)
      return
    result = await self._repository.apply(event, topic=topic, partition=partition, offset=offset)
    stamp = getattr(record, "timestamp", None)  # Kafka publish time, ms
    published_at = int(stamp // 1000) if isinstance(stamp, (int, float)) and stamp > 0 else None
    if self._mode == "go_shadow" and isinstance(event, OpportunityEnvelope):
      decision = await self._shadow.evaluate_creation(event, published_at=published_at) if self._shadow else None
      log.info("Go analysis shadow opportunity=%s disposition=%s outcome=%s", result.opportunity_id, result.disposition, decision.outcome if decision else "disabled")
    else:
      log.info("Go analysis lifecycle opportunity=%s disposition=%s", result.opportunity_id, result.disposition)
    if self._mode == "go" and self._policy is not None:
      # Exceptions propagate on purpose: the offset is then NOT committed, the
      # event is redelivered, and the (idempotent) policy retries. Failing
      # closed means a missed plan, never a duplicate or a half-authorised one.
      if isinstance(event, OpportunityEnvelope):
        outcome = await self._policy.on_creation(event, result, published_at=published_at)
      else:
        outcome = await self._policy.on_terminal(event, result)
      log.info("Go analysis policy opportunity=%s outcome=%s", result.opportunity_id, outcome)


async def analysis_opportunity_consumer_loop() -> None:
  """Run one durable consumer; offset commit follows successful DB handling."""
  authority = runtime_config.analysis.technical_authority
  if not authority.consumer_enabled:
    return
  kafka = runtime_config.transport.kafka
  if not kafka.enabled:
    raise RuntimeError("analysis opportunity consumer enabled but Kafka transport is disabled")
  try:
    from aiokafka import AIOKafkaConsumer, TopicPartition
  except ImportError as exc:  # deployment must install requirements before feature enablement
    raise RuntimeError("aiokafka is required for the analysis opportunity consumer") from exc
  repository = PostgresAnalysisOpportunityRepository()
  shadow = None
  if authority.mode == "go_shadow":
    from app.autotrade.go_shadow_policy import GoShadowPolicy
    shadow = AnalysisShadowEvaluator(repository, dry_run=GoShadowPolicy(repository).dry_run_creation)
  policy = None
  if authority.mode == "go":
    from app.autotrade.go_opportunity_policy import GoOpportunityPolicy
    policy = GoOpportunityPolicy(repository)
  handler = AnalysisOpportunityConsumer(repository, shadow=shadow, mode=authority.mode, policy=policy)
  consumer = AIOKafkaConsumer(
    OpportunityTopic,
    InvalidationTopic,
    bootstrap_servers=kafka.brokers,
    client_id=kafka.algo_bot_client_id,
    group_id=authority.consumer_group,
    enable_auto_commit=False,
    auto_offset_reset="earliest",
  )
  await consumer.start()
  await redis_state.publish_component_health(component="analysis_opportunity_consumer", state="ready")
  try:
    await run_consumer_loop(consumer, handler, partition_key=TopicPartition)
  finally:
    await consumer.stop()


async def run_consumer_loop(consumer: Any, handler: AnalysisOpportunityConsumer, *, partition_key: Callable[[str, int], Any]) -> None:
  """The commit discipline, separated from Kafka so it is testable end to end.

  An offset is committed only after ``process_record`` returned: a failure in
  the ledger *or* the policy leaves it uncommitted and the record is redelivered
  (every step is idempotent by identity), which is what makes a crash between
  "match written" and "offset committed" safe. Poison records are recorded as
  rejections inside ``process_record`` and do advance.
  """
  while True:
    record = await consumer.getone()
    await handler.process_record(record)
    await consumer.commit({partition_key(record.topic, record.partition): record.offset + 1})
