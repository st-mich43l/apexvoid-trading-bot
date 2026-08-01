"""Market-data acquisition model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class CTraderFeedConfig(FrozenConfigModel):
  symbol: str = config_field(
    "XAUUSD",
    legacy_attr=None,
    env="CTRADER_SYMBOL",
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.INFRASTRUCTURE,
    description="Broker symbol consumed by the cTrader feed.",
  )
  backfill_bars: int = config_field(
    1500,
    legacy_attr=None,
    env="CTRADER_BACKFILL_BARS",
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.INFRASTRUCTURE,
    description="Historical bars requested when the feed starts.",
  )


class CalendarConfig(FrozenConfigModel):
  enabled: bool = config_field(
    True,
    legacy_attr="calendar_enabled",
    env="CALENDAR_ENABLED",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description="Economic-calendar ingestion switch.",
  )


class MarketDataConfig(FrozenConfigModel):
  ctrader_feed: CTraderFeedConfig = Field(default_factory=CTraderFeedConfig)
  calendar: CalendarConfig = Field(default_factory=CalendarConfig)
