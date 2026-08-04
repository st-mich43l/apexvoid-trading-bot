"""Complete canonical grouped configuration schema (Catalog V2)."""

from pydantic import Field

from app.configuration.models.actionability import ActionabilityConfig
from app.configuration.models.analysis import AnalysisConfig
from app.configuration.models.base import FrozenConfigModel
from app.configuration.models.bootstrap import BootstrapConfig
from app.configuration.models.contract import ContractConfig
from app.configuration.models.delivery import DeliveryConfig
from app.configuration.models.execution import ExecutionConfig
from app.configuration.models.instruments import EMPTY_INSTRUMENTS
from app.configuration.models.instruments import InstrumentsConfig
from app.configuration.models.lifecycle import LifecycleConfig
from app.configuration.models.manual_algo import ManualAlgoConfig
from app.configuration.models.market_data import MarketDataConfig
from app.configuration.models.risk import RiskConfig
from app.configuration.models.runtime import RuntimeConfig
from app.configuration.models.strategies import StrategiesConfig


class ApexVoidConfig(FrozenConfigModel):
  bootstrap: BootstrapConfig
  runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
  market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
  analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
  strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
  actionability: ActionabilityConfig = Field(default_factory=ActionabilityConfig)
  contract: ContractConfig = Field(default_factory=ContractConfig)
  execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
  risk: RiskConfig = Field(default_factory=RiskConfig)
  lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)
  delivery: DeliveryConfig
  manual_algo: ManualAlgoConfig = Field(default_factory=ManualAlgoConfig)
  # Outside ENV leaf catalog: dynamic symbol → InstrumentConfig mapping.
  instruments: InstrumentsConfig = Field(default_factory=lambda: EMPTY_INSTRUMENTS)
