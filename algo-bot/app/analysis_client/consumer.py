"""Manual-commit Kafka consumer for Analysis Engine opportunities."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.analysis_client.models import (
  AnalysisContractError,
  ArbitrationEnvelope,
  ArbitrationTopic,
  OpportunityEnvelope,
  OpportunityTopic,
  InvalidationTopic,
  parse_analysis_event,
)
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository
from app.core.config import runtime_config

log = logging.getLogger(__name__)


class AnalysisOpportunityConsumer:
  """Applies records durably, then asks the caller to advance the offset."""

  def __init__(
    self,
    repository: PostgresAnalysisOpportunityRepository,
    *,
    policy: Any,
  ):
    self._repository = repository
    # The Kafka topic is the only automatic technical source. Keep the policy
    # hook duck-typed so this package never imports legacy detectors.
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
    if isinstance(event, ArbitrationEnvelope):
      # A time-varying decision, not an opportunity lifecycle fact - it
      # never touches the Postgres opportunity-lifecycle repository
      # (analysis_opportunities/analysis_opportunity_events), only the
      # live StrategyMatch in Redis via the policy hook.
      outcome = await self._policy.on_arbitration_decision(event)
      log.info("Go arbitration decision opportunity=%s status=%s outcome=%s", event.payload.opportunity_id, event.payload.status, outcome)
      return
    result = await self._repository.apply(event, topic=topic, partition=partition, offset=offset)
    stamp = getattr(record, "timestamp", None)  # Kafka publish time, ms
    published_at = int(stamp // 1000) if isinstance(stamp, (int, float)) and stamp > 0 else None
    log.info("Go analysis lifecycle opportunity=%s disposition=%s", result.opportunity_id, result.disposition)
    # Exceptions propagate on purpose: the offset is then NOT committed, the
    # event is redelivered, and the idempotent policy retries. Failing closed
    # means a missed plan, never a duplicate or a half-built one.
    if isinstance(event, OpportunityEnvelope):
      outcome = await self._policy.on_creation(event, result, published_at=published_at)
    else:
      outcome = await self._policy.on_terminal(event, result)
    log.info("Go analysis policy opportunity=%s outcome=%s", result.opportunity_id, outcome)


async def analysis_opportunity_consumer_loop() -> None:
  """Run one durable consumer; offset commit follows successful DB handling."""
  analysis_config = runtime_config.analysis.technical_authority
  if not analysis_config.consumer_enabled:
    raise RuntimeError("live Go analysis consumer is disabled")
  kafka = runtime_config.transport.kafka
  if not kafka.enabled:
    raise RuntimeError("analysis opportunity consumer enabled but Kafka transport is disabled")
  try:
    from aiokafka import AIOKafkaConsumer, TopicPartition
  except ImportError as exc:  # deployment must install requirements before feature enablement
    raise RuntimeError("aiokafka is required for the analysis opportunity consumer") from exc
  repository = PostgresAnalysisOpportunityRepository()
  from app.autotrade.go_opportunity_policy import GoOpportunityPolicy
  policy = GoOpportunityPolicy(repository)
  handler = AnalysisOpportunityConsumer(repository, policy=policy)
  consumer = AIOKafkaConsumer(
    OpportunityTopic,
    InvalidationTopic,
    ArbitrationTopic,
    bootstrap_servers=kafka.brokers,
    client_id=kafka.algo_bot_client_id,
    group_id=analysis_config.consumer_group,
    enable_auto_commit=False,
    auto_offset_reset="earliest",
  )
  await consumer.start()
  # Production incident 2026-09-28: this used to publish its own one-shot "ready" key
  # (component="analysis_opportunity_consumer") that nothing ever corrected on failure —
  # the consumer joined its group, died on the first fetch (aiokafka.errors.
  # UnsupportedCodecError, no snappy codec installed), and that key stayed falsely
  # "ready" for hours while run_supervised's own component_health for this loop
  # correctly went to "fatal". run_supervised (app.main._spawn_supervised, name=
  # "analysis_opportunity_consumer_loop") already publishes ready/degraded/fatal for
  # this loop's whole lifetime; a second, narrower, never-corrected key only invites
  # exactly that staleness. Read component_health:analysis_opportunity_consumer_loop.
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
