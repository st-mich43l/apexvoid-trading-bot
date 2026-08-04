"""Complete Canonical Catalog V2 configuration domain. """
from pydantic import Field
from pydantic import field_validator
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
    direct_publish_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_DIRECT_PUBLISH_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')
    dry_run: bool = config_field(True, canonical_env='AUTO_TRADE_DRY_RUN', owner=ConfigOwner.SHARED, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.CROSS_SERVICE_CONTRACT, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    enabled: bool = config_field(False, canonical_env='AUTO_TRADE_ENABLED', owner=ConfigOwner.SHARED, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.CROSS_SERVICE_CONTRACT, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    strategy_match_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_STRATEGY_MATCH_ENABLED', deprecated_env_aliases=('AUTO_TRADE_STRATEGY_BRIDGE_ENABLED', 'AUTO_TRADE_FORMING_GATE_ENABLED'), owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')

class RuntimeScannerConfig(FrozenConfigModel):
    enabled: bool = config_field(False, canonical_env='SCANNER_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEXT_SCANNER_CYCLE, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.INFRASTRUCTURE, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')

class RuntimeConfig(FrozenConfigModel):
    auto_trade: RuntimeAutoTradeConfig = Field(default_factory=RuntimeAutoTradeConfig)
    profile: str = config_field('conservative', canonical_env='AUTO_TRADE_PROFILE', owner=ConfigOwner.SHARED, reload=ReloadPolicy.RESTART, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.CROSS_SERVICE_CONTRACT, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'conservative'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'conservative')), allowed_values=('conservative', 'demo_eval'), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.String + AutoTradeOptions.Validate', pattern='^(conservative|demo_eval)$')
    scanner: RuntimeScannerConfig = Field(default_factory=RuntimeScannerConfig)

    @field_validator('profile', mode='before')
    @classmethod
    def normalize_profile(cls, value):
        return value.strip().lower() if isinstance(value, str) else value
