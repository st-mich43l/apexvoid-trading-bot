"""Complete inactive lifecycle configuration domain."""

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


class LifecycleScalingConfig(FrozenConfigModel):
  cooldown_bars: int = config_field(3,
    item_id='ctrader.env.AUTO_TRADE_ADD_COOLDOWN_BARS',
    legacy_attr=None,
    env='AUTO_TRADE_ADD_COOLDOWN_BARS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_ADD_COOLDOWN_BARS mapped to lifecycle.scaling.cooldown_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 3),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )
  max_age_bars: int = config_field(3,
    item_id='ctrader.env.AUTO_TRADE_ADD_MAX_AGE_BARS',
    legacy_attr=None,
    env='AUTO_TRADE_ADD_MAX_AGE_BARS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_ADD_MAX_AGE_BARS mapped to lifecycle.scaling.max_age_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 3),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )


class LifecycleReconciliationConfig(FrozenConfigModel):
  absence_recheck_seconds: int = config_field(3,
    item_id='ctrader.env.AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS',
    legacy_attr=None,
    env='AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS mapped to lifecycle.reconciliation.absence_recheck_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 3),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )
  missing_confirmations: int = config_field(2,
    item_id='ctrader.env.AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS',
    legacy_attr=None,
    env='AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS mapped to lifecycle.reconciliation.missing_confirmations.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 2),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )
  missing_recheck_seconds: int = config_field(3,
    item_id='ctrader.env.AUTO_TRADE_POSITION_MISSING_RECHECK_SECONDS',
    legacy_attr=None,
    env='AUTO_TRADE_POSITION_MISSING_RECHECK_SECONDS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_POSITION_MISSING_RECHECK_SECONDS mapped to lifecycle.reconciliation.missing_recheck_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 3),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )
  recovery_timeout_seconds: int = config_field(30,
    item_id='ctrader.env.AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS',
    legacy_attr=None,
    env='AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS mapped to lifecycle.reconciliation.recovery_timeout_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 30),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )


class LifecycleRangeFlipConfig(FrozenConfigModel):
  confirm_timeout_seconds: int = config_field(30,
    item_id='ctrader.env.AUTO_TRADE_FLIP_CONFIRM_TIMEOUT_SECONDS',
    legacy_attr=None,
    env='AUTO_TRADE_FLIP_CONFIRM_TIMEOUT_SECONDS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_FLIP_CONFIRM_TIMEOUT_SECONDS mapped to lifecycle.range_flip.confirm_timeout_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 30),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )


class LifecycleZoneConfig(FrozenConfigModel):
  cooldown_atr: float = config_field(1.0,
    item_id='python.settings.auto_trade_zone_cooldown_atr',
    legacy_attr='auto_trade_zone_cooldown_atr',
    env='AUTO_TRADE_ZONE_COOLDOWN_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.LIFECYCLE,
    description='Legacy auto_trade_zone_cooldown_atr configuration mapped to lifecycle.zone.cooldown_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  cooldown_enabled: bool = config_field(True,
    item_id='python.settings.auto_trade_zone_cooldown_enabled',
    legacy_attr='auto_trade_zone_cooldown_enabled',
    env='AUTO_TRADE_ZONE_COOLDOWN_ENABLED',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.LIFECYCLE,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_zone_cooldown_enabled configuration mapped to lifecycle.zone.cooldown_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  cooldown_minutes: int = config_field(60,
    item_id='ctrader.env.AUTO_TRADE_ZONE_COOLDOWN_MINUTES',
    legacy_attr=None,
    env='AUTO_TRADE_ZONE_COOLDOWN_MINUTES',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.MINUTES,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_ZONE_COOLDOWN_MINUTES mapped to lifecycle.zone.cooldown_minutes.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 60),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )
  fill_ttl_bars: int = config_field(3,
    item_id='ctrader.env.AUTO_TRADE_ZONE_FILL_TTL_BARS',
    legacy_attr=None,
    env='AUTO_TRADE_ZONE_FILL_TTL_BARS',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.LIFECYCLE,
    description='cTrader runtime option AUTO_TRADE_ZONE_FILL_TTL_BARS mapped to lifecycle.zone.fill_ttl_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 3),
    ),
    validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )


class LifecycleExecutorConfig(FrozenConfigModel):
  candidate_heartbeat_seconds: int = config_field(30,
    item_id='hardcoded.lifecycle.candidate_heartbeat_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.executor.candidate_heartbeat_seconds.',
    validation_summary='none; source constant',
  )
  candidate_lease_seconds: int = config_field(120,
    item_id='hardcoded.lifecycle.candidate_lease_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.executor.candidate_lease_seconds.',
    validation_summary='none; source constant',
  )


class LifecycleDeliveryConfig(FrozenConfigModel):
  notification_dedup_seconds: int = config_field(604800,
    item_id='hardcoded.lifecycle.notification_dedup_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.delivery.notification_dedup_seconds.',
    validation_summary='none; source constant',
  )


class LifecycleRangeContextConfig(FrozenConfigModel):
  private_source_max_age_seconds: int = config_field(150,
    item_id='hardcoded.lifecycle.private_range_source_max_age_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.range_context.private_source_max_age_seconds.',
    validation_summary='none; source constant',
  )
  scanner_source_max_age_seconds: int = config_field(660,
    item_id='hardcoded.lifecycle.scanner_range_source_max_age_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.range_context.scanner_source_max_age_seconds.',
    validation_summary='none; source constant',
  )


class LifecycleStrategyMatchConfig(FrozenConfigModel):
  maximum_age_seconds: int = config_field(420,
    item_id='python.settings.auto_trade_strategy_match_max_age_seconds',
    legacy_attr='auto_trade_strategy_match_max_age_seconds',
    env='AUTO_TRADE_STRATEGY_MATCH_MAX_AGE_SECONDS',
    aliases=('AUTO_TRADE_FORMING_MAX_AGE_SECONDS',),
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    description='Legacy auto_trade_strategy_match_max_age_seconds configuration mapped to lifecycle.strategy_match.maximum_age_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 420),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  ready_consumer_health_ttl_seconds: int = config_field(300,
    item_id='hardcoded.lifecycle.ready_consumer_health_ttl_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.strategy_match.ready_consumer_health_ttl_seconds.',
    validation_summary='none; source constant',
  )


class LifecycleSetupConfig(FrozenConfigModel):
  audit_retention_seconds: int = config_field(86400,
    item_id='hardcoded.lifecycle.setup_audit_retention_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.setup.audit_retention_seconds.',
    validation_summary='none; source constant',
  )
  terminal_retention_seconds: int = config_field(86400,
    item_id='hardcoded.lifecycle.terminal_setup_retention_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.setup.terminal_retention_seconds.',
    validation_summary='none; source constant',
  )


class LifecycleZoneWatchConfig(FrozenConfigModel):
  retention_seconds: int = config_field(604800,
    item_id='hardcoded.lifecycle.zone_watch_retention_seconds',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at lifecycle.zone_watch.retention_seconds.',
    validation_summary='none; source constant',
  )


class LifecycleRangeBoxConfig(FrozenConfigModel):
  retirement_seconds: int = config_field(14400,
    item_id='python.settings.auto_trade_box_retire_seconds',
    legacy_attr='auto_trade_box_retire_seconds',
    env='AUTO_TRADE_BOX_RETIRE_SECONDS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    description='Legacy auto_trade_box_retire_seconds configuration mapped to lifecycle.range_box.retirement_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 14400),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class LifecycleCandidateConfig(FrozenConfigModel):
  execution_maximum_age_seconds: int = config_field(90,
    item_id='python.settings.auto_trade_candidate_max_age_seconds',
    legacy_attr='auto_trade_candidate_max_age_seconds',
    env='AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS',
    aliases=('AUTO_TRADE_CANDIDATE_MAX_AGE',),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description='Legacy auto_trade_candidate_max_age_seconds configuration mapped to lifecycle.candidate.execution_maximum_age_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 90),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 90),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )
  storage_ttl_seconds: int = config_field(86400,
    item_id='python.settings.auto_trade_candidate_ttl',
    legacy_attr='auto_trade_candidate_ttl',
    env='AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS',
    aliases=('AUTO_TRADE_CANDIDATE_TTL',),
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.LIFECYCLE,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_candidate_ttl configuration mapped to lifecycle.candidate.storage_ttl_seconds.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 86400),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 86400),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )


class LifecycleMappedZoneConfig(FrozenConfigModel):
  reaction_rearm_atr: float = config_field(0.5,
    item_id='python.settings.auto_trade_map_reaction_rearm_atr',
    legacy_attr='auto_trade_map_reaction_rearm_atr',
    env='AUTO_TRADE_MAP_REACTION_REARM_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.LIFECYCLE,
    description='Legacy auto_trade_map_reaction_rearm_atr configuration mapped to lifecycle.mapped_zone.reaction_rearm_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  reaction_rearm_bars: int = config_field(3,
    item_id='python.settings.auto_trade_map_reaction_rearm_bars',
    legacy_attr='auto_trade_map_reaction_rearm_bars',
    env='AUTO_TRADE_MAP_REACTION_REARM_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.LIFECYCLE,
    description='Legacy auto_trade_map_reaction_rearm_bars configuration mapped to lifecycle.mapped_zone.reaction_rearm_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class LifecycleRetestConfig(FrozenConfigModel):
  trigger_validity_bars: int = config_field(2,
    item_id='python.settings.auto_trade_retest_trigger_validity_bars',
    legacy_attr='auto_trade_retest_trigger_validity_bars',
    env='AUTO_TRADE_RETEST_TRIGGER_VALIDITY_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_WORKER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.LIFECYCLE,
    description='Legacy auto_trade_retest_trigger_validity_bars configuration mapped to lifecycle.retest.trigger_validity_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator',
    ge=1,
    le=5,
  )


class LifecycleConfig(FrozenConfigModel):
  candidate: LifecycleCandidateConfig = Field(default_factory=LifecycleCandidateConfig)
  delivery: LifecycleDeliveryConfig = Field(default_factory=LifecycleDeliveryConfig)
  executor: LifecycleExecutorConfig = Field(default_factory=LifecycleExecutorConfig)
  mapped_zone: LifecycleMappedZoneConfig = Field(default_factory=LifecycleMappedZoneConfig)
  range_box: LifecycleRangeBoxConfig = Field(default_factory=LifecycleRangeBoxConfig)
  range_context: LifecycleRangeContextConfig = Field(default_factory=LifecycleRangeContextConfig)
  range_flip: LifecycleRangeFlipConfig = Field(default_factory=LifecycleRangeFlipConfig)
  reconciliation: LifecycleReconciliationConfig = Field(default_factory=LifecycleReconciliationConfig)
  retest: LifecycleRetestConfig = Field(default_factory=LifecycleRetestConfig)
  scaling: LifecycleScalingConfig = Field(default_factory=LifecycleScalingConfig)
  setup: LifecycleSetupConfig = Field(default_factory=LifecycleSetupConfig)
  strategy_match: LifecycleStrategyMatchConfig = Field(default_factory=LifecycleStrategyMatchConfig)
  zone: LifecycleZoneConfig = Field(default_factory=LifecycleZoneConfig)
  zone_watch: LifecycleZoneWatchConfig = Field(default_factory=LifecycleZoneWatchConfig)
