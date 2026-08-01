"""Sizing and exposure model shells."""

from decimal import Decimal

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class SizingConfig(FrozenConfigModel):
  risk_pct: Decimal = config_field(
    Decimal("2"),
    item_id="ctrader.env.AUTO_TRADE_RISK_PCT",
    legacy_attr=None,
    env="AUTO_TRADE_RISK_PCT",
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description="Base account-equity risk percentage.",
  )


class RiskConfig(FrozenConfigModel):
  sizing: SizingConfig = Field(default_factory=SizingConfig)
