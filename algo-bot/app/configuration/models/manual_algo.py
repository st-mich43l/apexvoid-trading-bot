"""Owner-triggered manual-algo model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class ManualAlgoRuntimeConfig(FrozenConfigModel):
  enabled: bool = config_field(
    False,
    legacy_attr="manual_algo_enabled",
    env="MANUAL_ALGO_ENABLED",
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description="Owner-triggered manual execution switch.",
  )


class ManualAlgoConfig(FrozenConfigModel):
  runtime: ManualAlgoRuntimeConfig = Field(
    default_factory=ManualAlgoRuntimeConfig,
  )
