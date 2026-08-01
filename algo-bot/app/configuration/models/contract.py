"""Cross-service interpretation and protocol model shells."""

from pydantic import Field

from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class ContractVersionsConfig(FrozenConfigModel):
  trade_plan: int = config_field(
    7,
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.VERSION,
    risk=RiskClassification.CROSS_SERVICE_CONTRACT,
    kind=ConfigKind.PROTOCOL_CONSTANT,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description="TradePlan protocol version implemented by both services.",
  )


class InstrumentContractConfig(FrozenConfigModel):
  pip_size: float = config_field(
    0.1,
    legacy_attr="auto_trade_xau_pip_size",
    env="AUTO_TRADE_XAU_PIP_SIZE",
    aliases=("AUTO_TRADE_PIP_SIZE",),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PIPS,
    risk=RiskClassification.CROSS_SERVICE_CONTRACT,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description="Absolute XAU price represented by one configured pip.",
  )


class ContractConfig(FrozenConfigModel):
  versions: ContractVersionsConfig = Field(
    default_factory=ContractVersionsConfig,
  )
  instrument: InstrumentContractConfig = Field(
    default_factory=InstrumentContractConfig,
  )
