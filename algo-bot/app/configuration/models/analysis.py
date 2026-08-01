"""Pure analysis model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class AtrConfig(FrozenConfigModel):
  length: int = config_field(
    14,
    item_id="python.settings.atr_length",
    legacy_attr="atr_length",
    env="ATR_LENGTH",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description="ATR rolling window length.",
  )


class TrendlineConfig(FrozenConfigModel):
  minimum_touches: int = config_field(
    3,
    item_id="python.settings.tl_min_touches",
    legacy_attr="tl_min_touches",
    env="TL_MIN_TOUCHES",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description="Minimum touches needed to accept a trendline.",
  )


class AnalysisConfig(FrozenConfigModel):
  atr: AtrConfig = Field(default_factory=AtrConfig)
  trendlines: TrendlineConfig = Field(default_factory=TrendlineConfig)
