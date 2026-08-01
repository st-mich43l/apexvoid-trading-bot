"""Operational profile and feature-switch model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class AutoTradeRuntimeConfig(FrozenConfigModel):
  enabled: bool = config_field(
    False,
    legacy_attr="auto_trade_enabled",
    env="AUTO_TRADE_ENABLED",
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description="Autonomous execution master switch.",
  )
  dry_run: bool = config_field(
    True,
    legacy_attr="auto_trade_dry_run",
    env="AUTO_TRADE_DRY_RUN",
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description="Broker submission dry-run switch.",
  )


class ScannerRuntimeConfig(FrozenConfigModel):
  enabled: bool = config_field(
    False,
    legacy_attr="scanner_enabled",
    env="SCANNER_ENABLED",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description="Price-action scanner process switch.",
  )


class RuntimeConfig(FrozenConfigModel):
  profile: str = config_field(
    "conservative",
    legacy_attr="auto_trade_profile",
    env="AUTO_TRADE_PROFILE",
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description="Selected immutable runtime profile name.",
  )
  auto_trade: AutoTradeRuntimeConfig = Field(
    default_factory=AutoTradeRuntimeConfig,
  )
  scanner: ScannerRuntimeConfig = Field(
    default_factory=ScannerRuntimeConfig,
  )
