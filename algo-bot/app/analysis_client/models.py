"""Strict S12 decoder for the versioned Go Analysis Engine contracts."""

from __future__ import annotations

import json
import math
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from app.configuration.models.base import FrozenConfigModel

OpportunityTopic = "analysis.opportunity.v1"
InvalidationTopic = "analysis.opportunity.invalidated.v1"
ExpectedProducer = "apexvoid-analysis-engine"


class AnalysisContractError(ValueError):
  """Raised when a record is not a valid semantic Analysis V1 event."""


FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class PriceLevel(FrozenConfigModel):
  price: FiniteFloat
  label: str | None = None


class EntryZone(FrozenConfigModel):
  low: FiniteFloat
  high: FiniteFloat

  @model_validator(mode="after")
  def validate_order(self):
    if self.low > self.high:
      raise ValueError("entry.low must be <= entry.high")
    return self


class Target(FrozenConfigModel):
  price: PriceLevel


class Evidence(FrozenConfigModel):
  code: str = Field(min_length=1)


class Quality(FrozenConfigModel):
  overall: FiniteFloat = Field(ge=0, le=1)
  components: dict[str, FiniteFloat] = Field(default_factory=dict)


class AlgorithmVersion(FrozenConfigModel):
  structure: str = Field(min_length=1)
  liquidity: str = Field(min_length=1)


class TechnicalBias(FrozenConfigModel):
  direction: Literal["BUY", "SELL"]
  layer: str = Field(min_length=1)


class TechnicalContext(FrozenConfigModel):
  """Engine-owned policy inputs for the bar that made the setup actionable.

  Additive V1 block (S13B). Absent => policy inputs are unavailable and the
  consumer must fail closed; it never substitutes a Python recomputation.
  """

  atr: FiniteFloat = Field(gt=0)
  reference_price: FiniteFloat = Field(gt=0)
  reference_time: int = Field(ge=0)
  bias: TechnicalBias | None = None


class AnalysisOpportunity(FrozenConfigModel):
  id: str = Field(min_length=1)
  strategy: str = Field(min_length=1)
  symbol: str = Field(min_length=1)
  timeframe: str | None = None
  direction: Literal["BUY", "SELL"]
  entry: EntryZone
  invalidation: PriceLevel
  targets: list[Target] = Field(min_length=1)
  evidence: list[Evidence] = Field(min_length=1)
  quality: Quality
  algorithm_version: AlgorithmVersion
  formed_at: int | None = Field(default=None, ge=0)
  created_at: int = Field(ge=0)
  expires_at: int = Field(ge=0)
  technical_context: TechnicalContext | None = None

  @model_validator(mode="after")
  def validate_trade_geometry(self):
    if self.expires_at < self.created_at:
      raise ValueError("expires_at must be >= created_at")
    if self.technical_context is not None and self.technical_context.reference_time > self.created_at:
      raise ValueError("technical_context.reference_time must be <= created_at (no look-ahead)")
    if self.formed_at is not None and self.formed_at > self.created_at:
      raise ValueError("formed_at must be <= created_at")
    if self.direction == "BUY":
      if self.invalidation.price >= self.entry.low:
        raise ValueError("BUY invalidation must be below entry.low")
      if any(target.price.price <= self.entry.high for target in self.targets):
        raise ValueError("BUY targets must be above entry.high")
    else:
      if self.invalidation.price <= self.entry.high:
        raise ValueError("SELL invalidation must be above entry.high")
      if any(target.price.price >= self.entry.low for target in self.targets):
        raise ValueError("SELL targets must be below entry.low")
    return self


class AnalysisOpportunityInvalidated(FrozenConfigModel):
  opportunity_id: str = Field(min_length=1)
  symbol: str = Field(min_length=1)
  strategy: str = Field(min_length=1)
  reason_code: str = Field(min_length=1)
  invalidated_at: int = Field(ge=0)


class AnalysisEnvelopeBase(FrozenConfigModel):
  event_id: str = Field(min_length=1)
  event_type: str = Field(min_length=1)
  event_version: int = Field(ge=1)
  occurred_at: int = Field(ge=0)
  produced_at: int = Field(ge=0)
  producer: str = Field(min_length=1)
  correlation_id: str = Field(min_length=1)
  causation_id: str | None = None
  config_version: int | None = None
  config_fingerprint: str | None = None

  @model_validator(mode="after")
  def validate_envelope(self):
    if self.event_version != 1:
      raise ValueError("only event_version=1 is supported")
    if self.producer != ExpectedProducer:
      raise ValueError(f"unexpected producer {self.producer!r}")
    if self.produced_at < self.occurred_at:
      raise ValueError("produced_at must be >= occurred_at")
    return self


class OpportunityEnvelope(AnalysisEnvelopeBase):
  event_type: Literal[OpportunityTopic]
  payload: AnalysisOpportunity


class InvalidationEnvelope(AnalysisEnvelopeBase):
  event_type: Literal[InvalidationTopic]
  payload: AnalysisOpportunityInvalidated


AnalysisEvent = OpportunityEnvelope | InvalidationEnvelope


def parse_analysis_event(topic: str, raw: bytes | str) -> AnalysisEvent:
  """Decode and semantically validate a Kafka record before it reaches policy."""
  if topic not in {OpportunityTopic, InvalidationTopic}:
    raise AnalysisContractError(f"unexpected analysis topic {topic!r}")
  try:
    decoded = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
  except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise AnalysisContractError(f"invalid JSON: {exc.msg if hasattr(exc, 'msg') else exc}") from None
  if not isinstance(decoded, dict):
    raise AnalysisContractError("event envelope must be a JSON object")
  try:
    event: AnalysisEvent
    if topic == OpportunityTopic:
      event = OpportunityEnvelope.model_validate(decoded)
    else:
      event = InvalidationEnvelope.model_validate(decoded)
  except ValidationError as exc:
    error = exc.errors(include_input=False, include_url=False)[0]
    location = ".".join(str(part) for part in error["loc"])
    raise AnalysisContractError(f"contract validation failed at {location}: {error['msg']}") from None
  if event.event_type != topic:
    raise AnalysisContractError("topic and envelope event_type must match")
  return event
