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

  def __init__(self, repository: PostgresAnalysisOpportunityRepository, *, shadow: AnalysisShadowEvaluator | None, mode: str):
    self._repository = repository
    self._shadow = shadow
    self._mode = mode

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
    if self._mode == "go_shadow" and isinstance(event, OpportunityEnvelope):
      decision = await self._shadow.evaluate_creation(event) if self._shadow else None
      log.info("Go analysis shadow opportunity=%s disposition=%s outcome=%s", result.opportunity_id, result.disposition, decision.outcome if decision else "disabled")
    else:
      log.info("Go analysis lifecycle opportunity=%s disposition=%s", result.opportunity_id, result.disposition)


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
  shadow = AnalysisShadowEvaluator(repository) if authority.mode == "go_shadow" else None
  handler = AnalysisOpportunityConsumer(repository, shadow=shadow, mode=authority.mode)
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
    while True:
      record = await consumer.getone()
      # If durable handling fails, do not commit: Kafka redelivers the event.
      await handler.process_record(record)
      await consumer.commit({TopicPartition(record.topic, record.partition): record.offset + 1})
  finally:
    await consumer.stop()
