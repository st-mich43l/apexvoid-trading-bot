"""Entry, stop and target execution model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class EntryConfig(FrozenConfigModel):
  contract_tolerance_pips: float = config_field(
    3.0,
    legacy_attr="auto_trade_entry_contract_tolerance_pips",
    env="AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS",
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    unit=ConfigUnit.PIPS,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description="Maximum executable-entry deviation from the plan contract.",
  )


class TargetingConfig(FrozenConfigModel):
  default_ladder_pips: str = config_field(
    "30,60,90,120,200",
    legacy_attr="auto_trade_tp_pips",
    env="AUTO_TRADE_TARGET_PLANS_PIPS",
    aliases=("AUTO_TRADE_TP_PIPS",),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    unit=ConfigUnit.PIPS,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description="Comma-separated default take-profit ladder in pips.",
  )


class ExecutionConfig(FrozenConfigModel):
  entry: EntryConfig = Field(default_factory=EntryConfig)
  targeting: TargetingConfig = Field(default_factory=TargetingConfig)
