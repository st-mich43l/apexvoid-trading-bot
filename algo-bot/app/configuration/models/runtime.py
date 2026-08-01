"""Complete inactive runtime configuration domain."""

from pydantic import Field

from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import ContextDefault
from app.configuration.metadata import DefaultContext
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel


class RuntimeAutoTradeConfig(FrozenConfigModel):
  direct_publish_enabled: bool = config_field(True,
    item_id='python.settings.auto_trade_direct_publish_enabled',
    legacy_attr='auto_trade_direct_publish_enabled',
    env='AUTO_TRADE_DIRECT_PUBLISH_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_direct_publish_enabled configuration mapped to runtime.auto_trade.direct_publish_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  dry_run: bool = config_field(True,
    item_id='python.settings.auto_trade_dry_run',
    legacy_attr='auto_trade_dry_run',
    env='AUTO_TRADE_DRY_RUN',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.CROSS_SERVICE_CONTRACT,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description='Legacy auto_trade_dry_run configuration mapped to runtime.auto_trade.dry_run.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  enabled: bool = config_field(False,
    item_id='python.settings.auto_trade_enabled',
    legacy_attr='auto_trade_enabled',
    env='AUTO_TRADE_ENABLED',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.CROSS_SERVICE_CONTRACT,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description='Legacy auto_trade_enabled configuration mapped to runtime.auto_trade.enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  strategy_match_enabled: bool = config_field(True,
    item_id='python.settings.auto_trade_strategy_match_enabled',
    legacy_attr='auto_trade_strategy_match_enabled',
    env='AUTO_TRADE_STRATEGY_MATCH_ENABLED',
    aliases=('AUTO_TRADE_STRATEGY_BRIDGE_ENABLED', 'AUTO_TRADE_FORMING_GATE_ENABLED'),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_strategy_match_enabled configuration mapped to runtime.auto_trade.strategy_match_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )


class RuntimeScannerConfig(FrozenConfigModel):
  enabled: bool = config_field(False,
    item_id='python.settings.scanner_enabled',
    legacy_attr='scanner_enabled',
    env='SCANNER_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.INFRASTRUCTURE,
    description='Legacy scanner_enabled configuration mapped to runtime.scanner.enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class RuntimeConfig(FrozenConfigModel):
  auto_trade: RuntimeAutoTradeConfig = Field(default_factory=RuntimeAutoTradeConfig)
  profile: str = config_field('conservative',
    item_id='python.settings.auto_trade_profile',
    legacy_attr='auto_trade_profile',
    env='AUTO_TRADE_PROFILE',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.RESTART,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.CROSS_SERVICE_CONTRACT,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_profile configuration mapped to runtime.profile.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'conservative'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'conservative'),
    ),
    allowed_values=('conservative', 'demo_eval'),
    validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.String + AutoTradeOptions.Validate',
  )
  scanner: RuntimeScannerConfig = Field(default_factory=RuntimeScannerConfig)
