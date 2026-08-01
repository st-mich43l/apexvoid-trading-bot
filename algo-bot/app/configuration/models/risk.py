"""Sizing and exposure model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class SizingConfig(FrozenConfigModel):
  base_risk_percent: float = config_field(
    2.0,
    legacy_attr="auto_trade_risk_pct",
    env="AUTO_TRADE_RISK_PCT",
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    shared_with_ctrader=True,
    description="Base account-equity risk percentage.",
  )


class RiskConfig(FrozenConfigModel):
  sizing: SizingConfig = Field(default_factory=SizingConfig)
