"""Complete inactive manual-algo configuration domain."""

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


class ManualAlgoScalingConfig(FrozenConfigModel):
  first_leg_lots: float = config_field(0.05,
    item_id='hardcoded.manual_algo.first_leg_lots',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.LOTS,
    risk=RiskClassification.EXECUTION_SAFETY,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at manual_algo.scaling.first_leg_lots.',
    validation_summary='none; source constant',
  )
  first_leg_threshold_lots: float = config_field(0.13,
    item_id='hardcoded.manual_algo.first_leg_threshold_lots',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.LOTS,
    risk=RiskClassification.EXECUTION_SAFETY,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at manual_algo.scaling.first_leg_threshold_lots.',
    validation_summary='none; source constant',
  )


class ManualAlgoRuntimeConfig(FrozenConfigModel):
  dry_run: bool = config_field(True,
    item_id='python.settings.manual_algo_dry_run',
    legacy_attr='manual_algo_dry_run',
    env='MANUAL_ALGO_DRY_RUN',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy manual_algo_dry_run configuration mapped to manual_algo.runtime.dry_run.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  enabled: bool = config_field(False,
    item_id='python.settings.manual_algo_enabled',
    legacy_attr='manual_algo_enabled',
    env='MANUAL_ALGO_ENABLED',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy manual_algo_enabled configuration mapped to manual_algo.runtime.enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  owner_execution_dm_enabled: bool = config_field(False,
    item_id='python.settings.manual_algo_owner_execution_dm_enabled',
    legacy_attr='manual_algo_owner_execution_dm_enabled',
    env='MANUAL_ALGO_OWNER_EXECUTION_DM_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy manual_algo_owner_execution_dm_enabled configuration mapped to manual_algo.runtime.owner_execution_dm_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ManualAlgoSizingConfig(FrozenConfigModel):
  risk_percent: float = config_field(2.0,
    item_id='python.settings.manual_algo_risk_pct',
    legacy_attr='manual_algo_risk_pct',
    env='MANUAL_ALGO_RISK_PCT',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy manual_algo_risk_pct configuration mapped to manual_algo.sizing.risk_percent.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ManualAlgoStreamsConfig(FrozenConfigModel):
  intents: str = config_field('manual_trade:intents',
    item_id='python.settings.manual_trade_intent_stream',
    legacy_attr='manual_trade_intent_stream',
    env='MANUAL_TRADE_INTENT_STREAM',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy manual_trade_intent_stream configuration mapped to manual_algo.streams.intents.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'manual_trade:intents'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  manual_trade_command_stream: str = config_field('manual_trade:commands',
    item_id='python.settings.manual_trade_command_stream',
    legacy_attr='manual_trade_command_stream',
    env='MANUAL_TRADE_COMMAND_STREAM',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.IDENTIFIER,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    description='Legacy manual_trade_command_stream configuration mapped to manual_algo.streams.manual_trade_command_stream.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'manual_trade:commands'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'manual_trade:commands'),
    ),
    validation_summary='Pydantic required/type coercion only',
    evidence_notes=(
      'Python exposes the stream through Settings while the executor also owns a direct command-stream binding.',
    ),
  )
  manual_trade_command_stream_maxlen: int = config_field(1000,
    item_id='python.settings.manual_trade_command_stream_maxlen',
    legacy_attr='manual_trade_command_stream_maxlen',
    env='MANUAL_TRADE_COMMAND_STREAM_MAXLEN',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy manual_trade_command_stream_maxlen configuration mapped to manual_algo.streams.manual_trade_command_stream_maxlen.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1000),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  manual_trade_intent_stream_maxlen: int = config_field(1000,
    item_id='python.settings.manual_trade_intent_stream_maxlen',
    legacy_attr='manual_trade_intent_stream_maxlen',
    env='MANUAL_TRADE_INTENT_STREAM_MAXLEN',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy manual_trade_intent_stream_maxlen configuration mapped to manual_algo.streams.manual_trade_intent_stream_maxlen.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1000),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ManualAlgoConfig(FrozenConfigModel):
  runtime: ManualAlgoRuntimeConfig = Field(default_factory=ManualAlgoRuntimeConfig)
  scaling: ManualAlgoScalingConfig = Field(default_factory=ManualAlgoScalingConfig)
  sizing: ManualAlgoSizingConfig = Field(default_factory=ManualAlgoSizingConfig)
  streams: ManualAlgoStreamsConfig = Field(default_factory=ManualAlgoStreamsConfig)
