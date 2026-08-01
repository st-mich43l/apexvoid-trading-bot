"""Candidate and setup lifecycle model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class CandidateLifecycleConfig(FrozenConfigModel):
  execution_maximum_age_seconds: int = config_field(
    90,
    item_id="python.settings.auto_trade_candidate_max_age_seconds",
    legacy_attr="auto_trade_candidate_max_age_seconds",
    env="AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS",
    aliases=("AUTO_TRADE_CANDIDATE_MAX_AGE",),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description="Maximum candidate age accepted for execution.",
  )
  storage_ttl_seconds: int = config_field(
    86400,
    item_id="python.settings.auto_trade_candidate_ttl",
    legacy_attr="auto_trade_candidate_ttl",
    env="AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS",
    aliases=("AUTO_TRADE_CANDIDATE_TTL",),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description="Redis retention period for candidate records.",
  )


class LifecycleConfig(FrozenConfigModel):
  candidate: CandidateLifecycleConfig = Field(
    default_factory=CandidateLifecycleConfig,
  )
