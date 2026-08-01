"""Telegram and reporting delivery model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class WeeklyReportConfig(FrozenConfigModel):
  enabled: bool = config_field(
    True,
    legacy_attr="weekly_report_enabled",
    env="WEEKLY_REPORT_ENABLED",
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.IMMEDIATE,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.DELIVERY,
    description="Weekly performance report switch.",
  )


class ReportsConfig(FrozenConfigModel):
  weekly: WeeklyReportConfig = Field(default_factory=WeeklyReportConfig)


class DeliveryConfig(FrozenConfigModel):
  reports: ReportsConfig = Field(default_factory=ReportsConfig)
