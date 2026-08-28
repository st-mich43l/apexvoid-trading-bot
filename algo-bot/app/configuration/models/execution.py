"""Complete Canonical Catalog V2 configuration domain. """
from decimal import Decimal
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
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

class ExecutionScalingAddConfig(FrozenConfigModel):
    level_buffer_atr: Decimal = config_field(Decimal('1'), canonical_env='AUTO_TRADE_ADD_LEVEL_BUFFER_ATR', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_ADD_LEVEL_BUFFER_ATR controlling  (atr).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('1')),), validation_summary='EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    min_stop_pips: int = config_field(30, canonical_env='AUTO_TRADE_ADD_MIN_STOP_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 30), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 30)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate')
    pullback_enabled: bool = config_field(False, canonical_env='AUTO_TRADE_ADD_PULLBACK_ENABLED', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_ADD_PULLBACK_ENABLED controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False),), validation_summary='EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    pullback_max_retrace: Decimal = config_field(Decimal('0.7'), canonical_env='AUTO_TRADE_ADD_PULLBACK_MAX_RETRACE', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_ADD_PULLBACK_MAX_RETRACE controlling  (fraction).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('0.7')),), validation_summary='EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    pullback_min_retrace: Decimal = config_field(Decimal('0.2'), canonical_env='AUTO_TRADE_ADD_PULLBACK_MIN_RETRACE', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_ADD_PULLBACK_MIN_RETRACE controlling  (fraction).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('0.2')),), validation_summary='EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    size_ratio: float = config_field(0.5, canonical_env='AUTO_TRADE_ADD_SIZE_RATIO', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls  (fraction).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.5)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    stop_buffer_atr: float = config_field(0.3, canonical_env='AUTO_TRADE_ADD_STOP_BUFFER_ATR', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.3), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.3)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate')

class ExecutionScalingConfig(FrozenConfigModel):
    add: ExecutionScalingAddConfig = Field(default_factory=ExecutionScalingAddConfig)

class ExecutionPolicyConfig(FrozenConfigModel):
    box_min_rr: Decimal = config_field(Decimal('1.25'), canonical_env='AUTO_TRADE_BOX_MIN_RR', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.MULTIPLIER, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_BOX_MIN_RR controlling  (multiplier).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('1.25')),), validation_summary='EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    displacement_override_lookback_bars: int = config_field(3, canonical_env='AUTO_TRADE_DISPLACEMENT_OVERRIDE_LOOKBACK_BARS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BARS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (bars).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),), validation_summary='Pydantic required/type coercion only')
    execution_cost_pips: float = config_field(1.0, canonical_env='AUTO_TRADE_EXECUTION_COST_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),), validation_summary='Pydantic required/type coercion only')
    execution_zone_max_width_atr: float = config_field(2.0, canonical_env='AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 2.0), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 2.0)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Decimal + AutoTradeOptions.Validate', gt=0)
    execution_zone_max_width_pips: float = config_field(100.0, canonical_env='AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 100.0), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 100)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Decimal + AutoTradeOptions.Validate', gt=0)
    flip_exit_buffer_pips: int = config_field(10, canonical_env='AUTO_TRADE_FLIP_EXIT_BUFFER_PIPS', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_FLIP_EXIT_BUFFER_PIPS controlling  (pips).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 10),), validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate')
    group_close_allocation: str = config_field('pro_rata', canonical_env='AUTO_TRADE_GROUP_CLOSE_ALLOCATION', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'pro_rata'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'pro_rata')), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.String + AutoTradeOptions.Validate')
    label: str = config_field('apexvoid-auto', canonical_env='AUTO_TRADE_LABEL', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_LABEL controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'apexvoid-auto'),), validation_summary='EnvironmentResolver.String + AutoTradeOptions.Validate')
    max_tranches: int = config_field(2, canonical_env='AUTO_TRADE_MAX_TRANCHES', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 2), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 2)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate')
    pip_value_per_lot: Decimal = config_field(Decimal('10'), canonical_env='AUTO_TRADE_PIP_VALUE_PER_LOT', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.MONEY_PER_PIP_PER_LOT, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_PIP_VALUE_PER_LOT controlling  (money_per_pip_per_lot).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('10')),), validation_summary='EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    require_demo_only_token: bool = config_field(False, canonical_env='AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN controlling .', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False),), validation_summary='EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    structural_reaction_lookback_bars: int = config_field(3, canonical_env='AUTO_TRADE_STRUCTURAL_REACTION_LOOKBACK_BARS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BARS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (bars).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),), validation_summary='Pydantic type coercion + Settings cross-field model validator', ge=1)

class ExecutionBrokerRecoveryConfig(FrozenConfigModel):
    absence_confirmations: int = config_field(2, canonical_env='AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PRICE, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS controlling  (price).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 2),), validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate')

class ExecutionEntryConfig(FrozenConfigModel):
    contract_tolerance_pips: float = config_field(3.0, canonical_env='AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3.0), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 3)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    inside_zone_market_entry_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_INSIDE_ZONE_MARKET_ENTRY_ENABLED', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    max_spread_pips: int = config_field(5, canonical_env='AUTO_TRADE_MAX_SPREAD_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Max bid/ask spread allowed at publish (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 5), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 5)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate')
    maximum_chase_distance_pips: float = config_field(40.0, canonical_env='AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 40.0), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 40)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Int + AutoTradeOptions.Validate', gt=0)
    poll_ms: int = config_field(250, canonical_env='AUTO_TRADE_POLL_MS', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.MILLISECONDS, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_POLL_MS controlling  (milliseconds).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 250),), validation_summary='EnvironmentResolver.Int + AutoTradeOptions.Validate')

class ExecutionTargetingConfig(FrozenConfigModel):
    default_ladder_pips: str = config_field('30,60,90,120,200', canonical_env='AUTO_TRADE_TARGET_PLANS_PIPS', deprecated_env_aliases=('AUTO_TRADE_TP_PIPS',), owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '30,60,90,120,200'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '30,60,90,120,200')), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.IntList + AutoTradeOptions.Validate')
    post_fill_target_fallback: str = config_field('fill_relative', canonical_env='AUTO_TRADE_POST_FILL_TARGET_FALLBACK', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.STRING, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'fill_relative'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'fill_relative')), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.String + AutoTradeOptions.Validate')
    range_ladder_pips: str = config_field('15,20,30,40,50,70', canonical_env='AUTO_TRADE_RANGE_TARGETS_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '15,20,30,40,50,70'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, '15,20,30,40,50,70')), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.IntList + AutoTradeOptions.Validate')
    tp_weights: list[int] = config_field([20, 20, 20, 20, 20], canonical_env='AUTO_TRADE_TP_WEIGHTS', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_TP_WEIGHTS controlling  (count).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, [20, 20, 20, 20, 20]),), validation_summary='EnvironmentResolver.IntList + AutoTradeOptions.Validate')
    unfilled_leg_after_tp_policy: str = config_field('cancel', canonical_env='AUTO_TRADE_UNFILLED_LEG_AFTER_TP_POLICY', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'cancel'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'cancel')), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.String + AutoTradeOptions.Validate')

class ExecutionZoneScalingConfig(FrozenConfigModel):
    fill_enabled: bool = config_field(False, canonical_env='AUTO_TRADE_ZONE_FILL_ENABLED', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    fill_fallback_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_ZONE_FILL_FALLBACK_ENABLED', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    fill_min_atr: float = config_field(0.5, canonical_env='AUTO_TRADE_ZONE_FILL_MIN_ATR', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.5)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    fill_min_lots: Decimal = config_field(Decimal('0.09'), canonical_env='AUTO_TRADE_ZONE_FILL_MIN_LOTS', owner=ConfigOwner.CTRADER, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.LOTS, risk=RiskClassification.EXECUTION_SAFETY, description='cTrader configuration option AUTO_TRADE_ZONE_FILL_MIN_LOTS controlling  (lots).', default_contexts=(ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('0.09')),), validation_summary='EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    first_leg_fraction: float = config_field(0.8, canonical_env='AUTO_TRADE_ZONE_SCALE_FIRST_LEG_FRACTION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (fraction).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.8),), validation_summary='Pydantic required/type coercion only')
    scale_step_atr: float = config_field(0.5, canonical_env='AUTO_TRADE_ZONE_SCALE_STEP_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),), validation_summary='Pydantic required/type coercion only')
    scale_undersized_policy: str = config_field('single_entry', canonical_env='AUTO_TRADE_ZONE_SCALE_UNDERSIZED_POLICY', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'single_entry'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'single_entry')), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.String + AutoTradeOptions.Validate')

class ExecutionStopsReactionConfig(FrozenConfigModel):
    room_floor_pips: int = config_field(40, canonical_env='AUTO_TRADE_REACTION_ROOM_STOP_FLOOR_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 40),), validation_summary='Pydantic required/type coercion only')

class ExecutionStopsTrendConfig(FrozenConfigModel):
    minimum_pips: int = config_field(40, canonical_env='AUTO_TRADE_TREND_STOP_MIN_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 40), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 40)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate')

class ExecutionStopsConfig(FrozenConfigModel):
    be_buffer_ticks: int = config_field(6, canonical_env='AUTO_TRADE_BE_BUFFER_TICKS', deprecated_env_aliases=('AUTO_TRADE_BE_BUFFER_PIPS',), owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.TICKS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (ticks).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 6), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 6)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Int + AutoTradeOptions.Validate', ge=0, lt=1000)
    reaction: ExecutionStopsReactionConfig = Field(default_factory=ExecutionStopsReactionConfig)
    sl_distance: float = config_field(6.5, canonical_env='AUTO_TRADE_SL_DISTANCE', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PRICE, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (price).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 6.5), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 6.5)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    stop_push_beyond_zone: bool = config_field(True, canonical_env='AUTO_TRADE_STOP_PUSH_BEYOND_ZONE', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    trend: ExecutionStopsTrendConfig = Field(default_factory=ExecutionStopsTrendConfig)
    wick_stop_buffer_atr: float = config_field(0.15, canonical_env='AUTO_TRADE_WICK_STOP_BUFFER_ATR', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.15), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.15)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate')

class ExecutionMappedZoneConfig(FrozenConfigModel):
    counter_bias_min_score: float = config_field(6.0, canonical_env='AUTO_TRADE_MAP_COUNTER_BIAS_MIN_SCORE', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.SCORE, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 6.0),), validation_summary='Pydantic required/type coercion only')
    execute_distance_atr: float = config_field(1.5, canonical_env='AUTO_TRADE_MAP_EXECUTE_DISTANCE_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.5),), validation_summary='Pydantic required/type coercion only')
    execute_tolerance_atr: float = config_field(0.15, canonical_env='AUTO_TRADE_MAP_EXECUTE_TOLERANCE_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.15),), validation_summary='Pydantic required/type coercion only')
    execute_tolerance_pips: float = config_field(3.0, canonical_env='AUTO_TRADE_MAP_EXECUTE_TOLERANCE_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3.0),), validation_summary='Pydantic required/type coercion only')
    hard_entry_drift_pips: float = config_field(20.0, canonical_env='AUTO_TRADE_MAP_HARD_ENTRY_DRIFT_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 20.0),), validation_summary='Pydantic required/type coercion only')
    max_entry_drift_atr: float = config_field(0.4, canonical_env='AUTO_TRADE_MAP_MAX_ENTRY_DRIFT_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.4),), validation_summary='Pydantic required/type coercion only')
    min_entry_drift_pips: float = config_field(10.0, canonical_env='AUTO_TRADE_MAP_MIN_ENTRY_DRIFT_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 10.0),), validation_summary='Pydantic required/type coercion only')
    reaction_lookback_bars: int = config_field(5, canonical_env='AUTO_TRADE_MAP_REACTION_LOOKBACK_BARS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BARS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (bars).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 5),), validation_summary='Pydantic required/type coercion only')
    thesis_lock_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_MAP_THESIS_LOCK_ENABLED', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    track_distance_atr: float = config_field(8.0, canonical_env='AUTO_TRADE_MAP_TRACK_DISTANCE_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 8.0),), validation_summary='Pydantic required/type coercion only')
    zone_min_width_abs: float = config_field(1.0, canonical_env='AUTO_TRADE_MAP_ZONE_MIN_WIDTH_ABS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PRICE, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (price).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),), validation_summary='Pydantic required/type coercion only')
    zone_min_width_atr: float = config_field(0.15, canonical_env='AUTO_TRADE_MAP_ZONE_MIN_WIDTH_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.15),), validation_summary='Pydantic required/type coercion only')

class ExecutionRangeConfig(FrozenConfigModel):
    box_move_sl_to_be_after_scale_out: bool = config_field(False, canonical_env='AUTO_TRADE_RANGE_BOX_MOVE_SL_TO_BE_AFTER_SCALE_OUT', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate')
    box_scale_out_fraction: float = config_field(0.5, canonical_env='AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls  (fraction).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.5)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Decimal + AutoTradeOptions.Validate', gt=0, lt=1)
    box_scale_out_threshold_pips: int = config_field(70, canonical_env='AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 70), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 70)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Int + AutoTradeOptions.Validate', gt=0)
    box_scale_out_trigger_pips: int = config_field(30, canonical_env='AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 30), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 30)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Int + AutoTradeOptions.Validate', gt=0)
    hard_entry_drift_pips: float = config_field(20.0, canonical_env='AUTO_TRADE_RANGE_HARD_ENTRY_DRIFT_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 20.0),), validation_summary='Pydantic required/type coercion only')
    max_entry_drift_atr: float = config_field(0.35, canonical_env='AUTO_TRADE_RANGE_MAX_ENTRY_DRIFT_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.35),), validation_summary='Pydantic required/type coercion only')
    min_entry_drift_pips: float = config_field(10.0, canonical_env='AUTO_TRADE_RANGE_MIN_ENTRY_DRIFT_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 10.0),), validation_summary='Pydantic required/type coercion only')
    min_rr: float = config_field(1.0, canonical_env='AUTO_TRADE_RANGE_MIN_RR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.MULTIPLIER, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (multiplier).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),), validation_summary='Pydantic required/type coercion only')
    min_target_pips: float = config_field(15.0, canonical_env='AUTO_TRADE_RANGE_MIN_TARGET_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 15.0),), validation_summary='Pydantic required/type coercion only')
    room_stop_floor_pips: int = config_field(15, canonical_env='AUTO_TRADE_RANGE_ROOM_STOP_FLOOR_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 15),), validation_summary='Pydantic required/type coercion only')
    tp_buffer_pips: float = config_field(3.0, canonical_env='AUTO_TRADE_RANGE_TP_BUFFER_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3.0), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 3)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate')

    @model_validator(mode='after')
    def validate_box_scale_out(self):
        if self.box_scale_out_trigger_pips >= self.box_scale_out_threshold_pips:
            raise ValueError('range box scale-out trigger must be below its threshold')
        return self

class ExecutionReactionConfig(FrozenConfigModel):
    market_fraction: float = config_field(0.8, canonical_env='AUTO_TRADE_REACTION_MARKET_FRACTION', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls  (fraction).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.8), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.8)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Decimal + AutoTradeOptions.Validate', gt=0)
    room_stop_min_rr: float = config_field(1.0, canonical_env='AUTO_TRADE_REACTION_ROOM_STOP_MIN_RR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.MULTIPLIER, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (multiplier).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),), validation_summary='Pydantic required/type coercion only')
    scale_fraction: float = config_field(0.2, canonical_env='AUTO_TRADE_REACTION_SCALE_FRACTION', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls  (fraction).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.2), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.2)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Decimal + AutoTradeOptions.Validate', gt=0)
    scale_invalid_policy: str = config_field('single_market', canonical_env='AUTO_TRADE_REACTION_SCALE_INVALID_POLICY', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'single_market'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'single_market')), allowed_values=('single_market', 'reject'), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.String + AutoTradeOptions.Validate', pattern='^(single_market|reject)$')
    scale_step_atr: float = config_field(0.5, canonical_env='AUTO_TRADE_REACTION_SCALE_STEP_ATR', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.5)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate')
    stop_max_pips: int = config_field(60, canonical_env='AUTO_TRADE_REACTION_STOP_MAX_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 60),), validation_summary='Pydantic required/type coercion only')
    stop_min_pips: int = config_field(40, canonical_env='AUTO_TRADE_REACTION_STOP_MIN_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 40),), validation_summary='Pydantic required/type coercion only')

    @field_validator('scale_invalid_policy', mode='before')
    @classmethod
    def normalize_scale_invalid_policy(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode='after')
    def validate_scale_fractions(self):
        if abs(self.market_fraction + self.scale_fraction - 1.0) > 1e-06:
            raise ValueError('reaction market and scale fractions must sum to 1.0')
        return self

class ExecutionRegimeConfig(FrozenConfigModel):
    direction_enabled: bool = config_field(False, canonical_env='AUTO_TRADE_REGIME_DIRECTION_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')
    direction_lookback: int = config_field(120, canonical_env='AUTO_TRADE_REGIME_DIRECTION_LOOKBACK', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BARS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (bars).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 120),), validation_summary='Pydantic required/type coercion only')
    min_directional_swings: int = config_field(3, canonical_env='AUTO_TRADE_REGIME_MIN_DIRECTIONAL_SWINGS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),), validation_summary='Pydantic required/type coercion only')
    min_displacement_atr: float = config_field(4.0, canonical_env='AUTO_TRADE_REGIME_MIN_DISPLACEMENT_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 4.0),), validation_summary='Pydantic required/type coercion only')

class ExecutionTrendConfig(FrozenConfigModel):
    hard_entry_drift_pips: float = config_field(30.0, canonical_env='AUTO_TRADE_TREND_HARD_ENTRY_DRIFT_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 30.0),), validation_summary='Pydantic required/type coercion only')
    max_entry_drift_atr: float = config_field(0.85, canonical_env='AUTO_TRADE_TREND_MAX_ENTRY_DRIFT_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.85),), validation_summary='Pydantic required/type coercion only')
    min_entry_drift_pips: float = config_field(15.0, canonical_env='AUTO_TRADE_TREND_MIN_ENTRY_DRIFT_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 15.0),), validation_summary='Pydantic required/type coercion only')
    stop_max_pips: int = config_field(60, canonical_env='AUTO_TRADE_TREND_STOP_MAX_PIPS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.FATAL, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 60), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 60)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate')

class ExecutionActivationConfig(FrozenConfigModel):
    mode: str = config_field('shadow', canonical_env='ENTRY_ACTIVATION_MODE', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.EXECUTION_SAFETY, description='Entry-activation policy mode: off, shadow (record only), or enforce reaction triggers.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'shadow'),), allowed_values=('off', 'shadow', 'enforce'), validation_summary='Pydantic type coercion + field validator', pattern='^(off|shadow|enforce)$')
    reaction_trigger_maximum_age_bars: int = config_field(2, canonical_env='ENTRY_ACTIVATION_REACTION_TRIGGER_MAX_AGE_BARS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BARS, risk=RiskClassification.EXECUTION_SAFETY, description='Maximum closed M1 bars since trigger for reaction activation.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),), validation_summary='Pydantic required/type coercion only', ge=1)

    @field_validator('mode', mode='before')
    @classmethod
    def normalize_activation_mode(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

class ExecutionTechniqueConfig(FrozenConfigModel):
    """Prod technique pack (2026-08-10): killzone + sweep/body + strict PD + SL hard-cap."""

    enforce: bool = config_field(
      True,
      canonical_env='AUTO_TRADE_TECHNIQUE_ENFORCE',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.BOOLEAN,
      risk=RiskClassification.EXECUTION_SAFETY,
      description='Master switch for killzone/sweep/PD technique pack (enforce-on).',
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),),
      validation_summary='Pydantic required/type coercion only',
    )
    include_late_ny: bool = config_field(
      True,
      canonical_env='AUTO_TRADE_TECHNIQUE_INCLUDE_LATE_NY',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.BOOLEAN,
      risk=RiskClassification.EXECUTION_SAFETY,
      description='Allow UTC 22-23 as killzone (prod dig strong hours).',
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),),
      validation_summary='Pydantic required/type coercion only',
    )
    london_window_hours: int = config_field(
      3,
      canonical_env='AUTO_TRADE_TECHNIQUE_LONDON_WINDOW_HOURS',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.COUNT,
      risk=RiskClassification.EXECUTION_SAFETY,
      description='Hours after london_start that count as London killzone.',
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),),
      validation_summary='Pydantic required/type coercion only',
      ge=1,
    )
    ny_window_hours: int = config_field(
      3,
      canonical_env='AUTO_TRADE_TECHNIQUE_NY_WINDOW_HOURS',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.COUNT,
      risk=RiskClassification.EXECUTION_SAFETY,
      description='Hours after ny_start that count as NY/London-NY killzone.',
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),),
      validation_summary='Pydantic required/type coercion only',
      ge=1,
    )
    reaction_require_killzone: bool = config_field(
      True,
      canonical_env='AUTO_TRADE_TECHNIQUE_REACTION_REQUIRE_KILLZONE',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.BOOLEAN,
      risk=RiskClassification.EXECUTION_SAFETY,
      description='Block reaction/zone publish+arm outside killzone when enforce.',
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),),
      validation_summary='Pydantic required/type coercion only',
    )
    reaction_require_publish_window: bool = config_field(
      True,
      canonical_env='AUTO_TRADE_TECHNIQUE_REACTION_REQUIRE_PUBLISH_WINDOW',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.BOOLEAN,
      risk=RiskClassification.EXECUTION_SAFETY,
      description=(
        'When true with enforce, block non-scalp publish/activation outside '
        'reaction_publish_windows. Prod trading-bot.yml defaults this off; '
        'structure and strategy technique decide regardless of UTC hour.'
      ),
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),),
      validation_summary='Pydantic required/type coercion only',
    )
    scalp_require_killzone: bool = config_field(
      True,
      canonical_env='AUTO_TRADE_TECHNIQUE_SCALP_REQUIRE_KILLZONE',
      deprecated_env_aliases=('AUTO_TRADE_TECHNIQUE_HFS_REQUIRE_KILLZONE',),
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.BOOLEAN,
      risk=RiskClassification.EXECUTION_SAFETY,
      description=(
        'When true with enforce, block M1 scalping publish/activation outside '
        'killzone. Prod trading-bot.yml defaults this off; discovery permits '
        'are structure/technique-driven regardless.'
      ),
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),),
      validation_summary='Pydantic required/type coercion only',
    )
    require_sweep_body: bool = config_field(
      True,
      canonical_env='AUTO_TRADE_TECHNIQUE_REQUIRE_SWEEP_BODY',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.BOOLEAN,
      risk=RiskClassification.EXECUTION_SAFETY,
      description='Require sweep_reclaim/body_close family confirmation for reaction.',
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),),
      validation_summary='Pydantic required/type coercion only',
    )
    reaction_publish_windows: str = config_field(
      '7-11,13-16',
      canonical_env='AUTO_TRADE_TECHNIQUE_REACTION_PUBLISH_WINDOWS',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.STRING,
      risk=RiskClassification.EXECUTION_SAFETY,
      description='UTC hour windows (exclusive end) for non-scalp reaction publish, e.g. 7-11,13-16.',
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, '7-11,13-16'),),
      validation_summary='Pydantic required/type coercion only',
    )
    strict_premium_discount: bool = config_field(
      True,
      canonical_env='AUTO_TRADE_TECHNIQUE_STRICT_PREMIUM_DISCOUNT',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.BOOLEAN,
      risk=RiskClassification.EXECUTION_SAFETY,
      description='Force BUY=discount / SELL=premium dealing-range gate when enforce.',
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),),
      validation_summary='Pydantic required/type coercion only',
    )
    strict_premium_discount_archetypes: str = config_field(
      'reversal,range_reversion',
      canonical_env='AUTO_TRADE_STRICT_PD_ARCHETYPES',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.STRING,
      risk=RiskClassification.STRATEGY_BEHAVIOR,
      description=(
        'Comma-separated location archetypes to which strict_premium_discount '
        'applies. Empty string disables the rule entirely. Setting it to '
        '"reversal,range_reversion,trend_pullback,breakout_retest,momentum,unknown" '
        'restores pre-PR-B behaviour.'
      ),
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'reversal,range_reversion'),),
      validation_summary='Comma-separated archetype tokens; unknown tokens raise',
    )
    mad_hard_gate_enabled: bool = config_field(
      False,
      canonical_env='AUTO_TRADE_TECHNIQUE_MAD_HARD_GATE_ENABLED',
      owner=ConfigOwner.PYTHON,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.BOOLEAN,
      risk=RiskClassification.EXECUTION_SAFETY,
      description=(
        'Reserved observe/research switch. Must stay false in prod — MAD is '
        'entry-quality / structure analysis only and must not block trade-plan '
        'publish or activation.'
      ),
      default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),),
      validation_summary='Pydantic required/type coercion only',
    )

    @model_validator(mode='after')
    def validate_strict_pd_archetypes(self):
      from app.analysis.entry_location import parse_strict_pd_archetypes
      parse_strict_pd_archetypes(self.strict_premium_discount_archetypes)
      return self


class ExecutionConfig(FrozenConfigModel):
    activation: ExecutionActivationConfig = Field(default_factory=ExecutionActivationConfig)
    broker_recovery: ExecutionBrokerRecoveryConfig = Field(default_factory=ExecutionBrokerRecoveryConfig)
    entry: ExecutionEntryConfig = Field(default_factory=ExecutionEntryConfig)
    mapped_zone: ExecutionMappedZoneConfig = Field(default_factory=ExecutionMappedZoneConfig)
    policy: ExecutionPolicyConfig = Field(default_factory=ExecutionPolicyConfig)
    range: ExecutionRangeConfig = Field(default_factory=ExecutionRangeConfig)
    reaction: ExecutionReactionConfig = Field(default_factory=ExecutionReactionConfig)
    regime: ExecutionRegimeConfig = Field(default_factory=ExecutionRegimeConfig)
    scaling: ExecutionScalingConfig = Field(default_factory=ExecutionScalingConfig)
    stops: ExecutionStopsConfig = Field(default_factory=ExecutionStopsConfig)
    targeting: ExecutionTargetingConfig = Field(default_factory=ExecutionTargetingConfig)
    technique: ExecutionTechniqueConfig = Field(default_factory=ExecutionTechniqueConfig)
    trend: ExecutionTrendConfig = Field(default_factory=ExecutionTrendConfig)
    zone_scaling: ExecutionZoneScalingConfig = Field(default_factory=ExecutionZoneScalingConfig)
