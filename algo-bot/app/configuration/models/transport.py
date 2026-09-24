"""Durable event-plane transport settings owned by the Algo Bot."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.configuration.metadata import (
  ConfigOwner,
  ConfigUnit,
  ContextDefault,
  DefaultContext,
  ReloadPolicy,
  RiskClassification,
  config_field,
)
from app.configuration.models.base import FrozenConfigModel


class KafkaTopicsConfig(FrozenConfigModel):
  analysis_opportunity: str = config_field(
    "analysis.opportunity.v1",
    canonical_env=None,
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.CROSS_SERVICE_CONTRACT,
    shared_with_ctrader=True,
    description="Kafka topic carrying versioned Go analysis opportunities.",
    default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, "analysis.opportunity.v1"),),
    validation_summary="Kafka contract topic; changes require coordinated deployment.",
  )
  analysis_opportunity_invalidated: str = config_field(
    "analysis.opportunity.invalidated.v1",
    canonical_env=None,
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.CROSS_SERVICE_CONTRACT,
    shared_with_ctrader=True,
    description="Kafka topic carrying terminal Go analysis opportunity events.",
    default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, "analysis.opportunity.invalidated.v1"),),
    validation_summary="Kafka contract topic; changes require coordinated deployment.",
  )


class KafkaConfig(FrozenConfigModel):
  enabled: bool = config_field(
    True,
    canonical_env=None,
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.INFRASTRUCTURE,
    shared_with_ctrader=True,
    description="Whether the deployed Kafka event plane is available.",
    default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),),
    validation_summary="Pydantic required/type coercion only.",
  )
  brokers: list[str] = config_field(
    ["kafka:9092"],
    canonical_env=None,
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.URL,
    risk=RiskClassification.INFRASTRUCTURE,
    shared_with_ctrader=True,
    description="Kafka bootstrap brokers for durable business events.",
    default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, ["kafka:9092"]),),
    validation_summary="At least one broker is required when the consumer is enabled.",
    min_length=1,
  )
  algo_bot_client_id: str = config_field(
    "apexvoid-algo-bot",
    canonical_env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    description="Kafka client identifier used by the Algo Bot consumer.",
    default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, "apexvoid-algo-bot"),),
    validation_summary="Pydantic required/type coercion only.",
  )
  topics: KafkaTopicsConfig = Field(default_factory=KafkaTopicsConfig)


class TransportConfig(FrozenConfigModel):
  kafka: KafkaConfig = Field(default_factory=KafkaConfig)
