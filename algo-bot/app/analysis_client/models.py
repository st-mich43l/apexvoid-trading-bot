"""Strict S12 decoder for the versioned Go Analysis Engine contracts."""

from __future__ import annotations

import json
import math
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from app.configuration.models.base import FrozenConfigModel

OpportunityTopic = "analysis.opportunity.v1"
InvalidationTopic = "analysis.opportunity.invalidated.v1"
ArbitrationTopic = "analysis.opportunity.arbitration.v1"
ExpectedProducer = "apexvoid-analysis-engine"

ArbitrationStatuses = {"winner", "suppressed", "conflict_held", "uncontested"}
ArbitrationReasonCodes = {"uncontested", "ranked_single_direction", "opposite_direction_conflict"}


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


class StopEnvelope(FrozenConfigModel):
  """Engine-owned stop-distance risk policy (Phase 4).

  desired_minimum_pips is a single-leg recommendation, not a final
  decision — see contracts/analysis/opportunity-v1.schema.json's own doc
  comment on this block for why a multi-leg group stop or a fixed-R:R
  instrument should use floor_pips as its minimum instead.
  """

  floor_pips: FiniteFloat = Field(gt=0)
  cap_pips: FiniteFloat = Field(gt=0)
  desired_minimum_pips: FiniteFloat = Field(gt=0)
  source: str = Field(min_length=1)

  @model_validator(mode="after")
  def validate_bounds(self):
    if self.cap_pips < self.floor_pips:
      raise ValueError("stop_envelope.cap_pips must be >= floor_pips")
    if self.desired_minimum_pips < self.floor_pips:
      raise ValueError("stop_envelope.desired_minimum_pips must be >= floor_pips")
    return self


class AlgorithmVersion(FrozenConfigModel):
  structure: str = Field(min_length=1)
  liquidity: str = Field(min_length=1)


class TechnicalBias(FrozenConfigModel):
  direction: Literal["BUY", "SELL"]
  layer: str = Field(min_length=1)


class HigherTimeframeBias(FrozenConfigModel):
  timeframe: Literal["H1", "H4"]
  direction: Literal["BUY", "SELL"]
  layer: str = Field(min_length=1)
  reference_time: int = Field(ge=0)


class ReactionConfirmation(FrozenConfigModel):
  zone_id: str = Field(min_length=1)
  touch_bar_time: int = Field(ge=0)
  confirmation_bar_time: int = Field(ge=0)
  reaction_type: Literal["rejection"]

  @model_validator(mode="after")
  def validate_timing(self):
    if self.confirmation_bar_time < self.touch_bar_time:
      raise ValueError("reaction confirmation must not precede the zone touch")
    return self


class TechnicalContext(FrozenConfigModel):
  """Engine-owned policy inputs for the bar that made the setup actionable.

  Additive V1 block (S13B). Absent => policy inputs are unavailable and the
  consumer must fail closed; it never substitutes a Python recomputation.
  """

  atr: FiniteFloat = Field(gt=0)
  reference_price: FiniteFloat = Field(gt=0)
  reference_time: int = Field(ge=0)
  bias: TechnicalBias | None = None
  higher_timeframes: list[HigherTimeframeBias] = Field(default_factory=list, max_length=2)
  confirmation: ReactionConfirmation | None = None

  @model_validator(mode="after")
  def validate_higher_timeframes(self):
    if len({item.timeframe for item in self.higher_timeframes}) != len(self.higher_timeframes):
      raise ValueError("higher_timeframes must have distinct timeframe identifiers")
    return self


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
  stop_envelope: StopEnvelope | None = None

  @model_validator(mode="after")
  def validate_trade_geometry(self):
    if self.expires_at < self.created_at:
      raise ValueError("expires_at must be >= created_at")
    if self.technical_context is not None and self.technical_context.reference_time > self.created_at:
      raise ValueError("technical_context.reference_time must be <= created_at (no look-ahead)")
    if self.technical_context is not None:
      bar_minutes = {"M1": 1, "M3": 3, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
      observed_minutes = bar_minutes.get(self.timeframe or "")
      if self.technical_context.higher_timeframes and observed_minutes is None:
        raise ValueError("timeframe is required when higher-timeframe context is present")
      observed_close = self.technical_context.reference_time + (observed_minutes or 0) * 60
      for higher in self.technical_context.higher_timeframes:
        if higher.reference_time + bar_minutes[higher.timeframe] * 60 > observed_close:
          raise ValueError("higher-timeframe candle must be closed at opportunity observation")
      confirmation = self.technical_context.confirmation
      if confirmation is not None and confirmation.confirmation_bar_time > self.technical_context.reference_time:
        raise ValueError("reaction confirmation must not follow the observed closed bar")
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


class AnalysisOpportunityArbitration(FrozenConfigModel):
  """Go's cross-strategy conflict-resolution decision for one opportunity.

  Republished whenever it changes, independent of the opportunity's own
  lifecycle (see contracts/analysis/opportunity-arbitration-v1.schema.json's
  own doc comment for why this is not a field on AnalysisOpportunity).
  """

  opportunity_id: str = Field(min_length=1)
  symbol: str = Field(min_length=1)
  status: str
  reason_code: str
  conflicting_with: list[str] = Field(default_factory=list)
  # Cross-strategy thesis correlation (Phase 3): present only when this
  # opportunity shares its real-world structural identity with at least
  # one other live candidate. See go_opportunity_policy's own handling for
  # how this replaces the Python-side ATR-bucket heuristic.
  thesis_id: str | None = None
  merged_with: list[str] = Field(default_factory=list)
  decided_at: int = Field(ge=0)

  @model_validator(mode="after")
  def validate_enums(self):
    if self.status not in ArbitrationStatuses:
      raise ValueError(f"unknown arbitration status {self.status!r}")
    if self.reason_code not in ArbitrationReasonCodes:
      raise ValueError(f"unknown arbitration reason_code {self.reason_code!r}")
    return self


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


class ArbitrationEnvelope(AnalysisEnvelopeBase):
  event_type: Literal[ArbitrationTopic]
  payload: AnalysisOpportunityArbitration


AnalysisEvent = OpportunityEnvelope | InvalidationEnvelope | ArbitrationEnvelope


def parse_analysis_event(topic: str, raw: bytes | str) -> AnalysisEvent:
  """Decode and semantically validate a Kafka record before it reaches policy."""
  if topic not in {OpportunityTopic, InvalidationTopic, ArbitrationTopic}:
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
    elif topic == InvalidationTopic:
      event = InvalidationEnvelope.model_validate(decoded)
    else:
      event = ArbitrationEnvelope.model_validate(decoded)
  except ValidationError as exc:
    error = exc.errors(include_input=False, include_url=False)[0]
    location = ".".join(str(part) for part in error["loc"])
    raise AnalysisContractError(f"contract validation failed at {location}: {error['msg']}") from None
  if event.event_type != topic:
    raise AnalysisContractError("topic and envelope event_type must match")
  return event
