"""Strategy policy model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class TrendStrategyConfig(FrozenConfigModel):
  enabled: bool = config_field(
    False,
    legacy_attr="auto_trade_trend_enabled",
    env="AUTO_TRADE_TREND_ENABLED",
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.STRATEGY_BEHAVIOR,
    shared_with_ctrader=True,
    description="Trend strategy enablement.",
  )
  minimum_bos: int = config_field(
    2,
    legacy_attr="trend_min_bos",
    env="TREND_MIN_BOS",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.STRATEGY_BEHAVIOR,
    description="Minimum break-of-structure count for trend qualification.",
  )


class StrategiesConfig(FrozenConfigModel):
  trend: TrendStrategyConfig = Field(default_factory=TrendStrategyConfig)
