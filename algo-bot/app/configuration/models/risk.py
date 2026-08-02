"""Complete inactive risk configuration domain."""

from decimal import Decimal

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


class RiskSizingConfig(FrozenConfigModel):
  add_max_group_risk_pct: Decimal = config_field(Decimal('3.0'),
    item_id='ctrader.env.AUTO_TRADE_ADD_MAX_GROUP_RISK_PCT',
    legacy_attr=None,
    env='AUTO_TRADE_ADD_MAX_GROUP_RISK_PCT',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='cTrader runtime option AUTO_TRADE_ADD_MAX_GROUP_RISK_PCT mapped to risk.sizing.add_max_group_risk_pct.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('3.0')),
    ),
    validation_summary='EnvironmentResolver.Decimal + AutoTradeOptions.Validate',
  )
  add_require_risk_free: bool = config_field(False,
    item_id='ctrader.env.AUTO_TRADE_ADD_REQUIRE_RISK_FREE',
    legacy_attr=None,
    env='AUTO_TRADE_ADD_REQUIRE_RISK_FREE',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='cTrader runtime option AUTO_TRADE_ADD_REQUIRE_RISK_FREE mapped to risk.sizing.add_require_risk_free.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False),
    ),
    validation_summary='EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  add_risk_fraction: float = config_field(0.5,
    item_id='python.settings.auto_trade_add_risk_fraction',
    legacy_attr='auto_trade_add_risk_fraction',
    env='AUTO_TRADE_ADD_RISK_FRACTION',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    shared_with_ctrader=True,
    description='Legacy auto_trade_add_risk_fraction configuration mapped to risk.sizing.add_risk_fraction.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Decimal + AutoTradeOptions.Validate',
  )
  equity_table_version: str = config_field('owner_equity_v1',
    item_id='python.settings.auto_trade_equity_table_version',
    legacy_attr='auto_trade_equity_table_version',
    env='AUTO_TRADE_EQUITY_TABLE_VERSION',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description='Legacy auto_trade_equity_table_version configuration mapped to risk.sizing.equity_table_version.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'owner_equity_v1'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'owner_equity_v1'),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.String + AutoTradeOptions.Validate',
  )
  mode: str = config_field('equity_table',
    item_id='python.settings.auto_trade_sizing_mode',
    legacy_attr='auto_trade_sizing_mode',
    env='AUTO_TRADE_SIZING_MODE',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description='Legacy auto_trade_sizing_mode configuration mapped to risk.sizing.mode.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'equity_table'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'equity_table'),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.String + AutoTradeOptions.Validate',
  )
  one_sided_range_risk_multiplier: float = config_field(0.5,
    item_id='python.settings.auto_trade_one_sided_range_risk_multiplier',
    legacy_attr='auto_trade_one_sided_range_risk_multiplier',
    env='AUTO_TRADE_ONE_SIDED_RANGE_RISK_MULTIPLIER',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.MULTIPLIER,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_one_sided_range_risk_multiplier configuration mapped to risk.sizing.one_sided_range_risk_multiplier.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  post_impulse_risk_multiplier: float = config_field(0.5,
    item_id='python.settings.auto_trade_post_impulse_risk_multiplier',
    legacy_attr='auto_trade_post_impulse_risk_multiplier',
    env='AUTO_TRADE_POST_IMPULSE_RISK_MULTIPLIER',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.MULTIPLIER,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_post_impulse_risk_multiplier configuration mapped to risk.sizing.post_impulse_risk_multiplier.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  range_max_risk_multiplier: float = config_field(2.0,
    item_id='python.settings.auto_trade_range_max_risk_multiplier',
    legacy_attr='auto_trade_range_max_risk_multiplier',
    env='AUTO_TRADE_RANGE_MAX_RISK_MULTIPLIER',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.MULTIPLIER,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_range_max_risk_multiplier configuration mapped to risk.sizing.range_max_risk_multiplier.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  risk_pct: Decimal = config_field(Decimal('2'),
    item_id='ctrader.env.AUTO_TRADE_RISK_PCT',
    legacy_attr=None,
    env='AUTO_TRADE_RISK_PCT',
    owner=ConfigOwner.CTRADER,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='cTrader runtime option AUTO_TRADE_RISK_PCT mapped to risk.sizing.risk_pct.',
    default_contexts=(
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, Decimal('2')),
    ),
    validation_summary='EnvironmentResolver.Decimal + AutoTradeOptions.Validate',
  )


class RiskExposureConfig(FrozenConfigModel):
  allow_concurrent_strategies: bool = config_field(False,
    item_id='python.settings.auto_trade_allow_concurrent_strategies',
    legacy_attr='auto_trade_allow_concurrent_strategies',
    env='AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_allow_concurrent_strategies configuration mapped to risk.exposure.allow_concurrent_strategies.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  allow_hedged_xau: bool = config_field(False,
    item_id='python.settings.auto_trade_allow_hedged_xau',
    legacy_attr='auto_trade_allow_hedged_xau',
    env='AUTO_TRADE_ALLOW_HEDGED_XAU',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_allow_hedged_xau configuration mapped to risk.exposure.allow_hedged_xau.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  non_hedged_opposite_policy: str = config_field('reject',
    item_id='python.settings.auto_trade_non_hedged_opposite_policy',
    legacy_attr='auto_trade_non_hedged_opposite_policy',
    env='AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_non_hedged_opposite_policy configuration mapped to risk.exposure.non_hedged_opposite_policy.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'reject'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'reject'),
    ),
    allowed_values=('broker_netting', 'close_then_reverse', 'reject'),
    validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.String + AutoTradeOptions.Validate',
    pattern='^(broker_netting|close_then_reverse|reject)$',
  )

  @field_validator("non_hedged_opposite_policy", mode="before")
  @classmethod
  def normalize_non_hedged_opposite_policy(cls, value):
    return value.strip().lower() if isinstance(value, str) else value
  opposing_minimum_separation_price: float = config_field(15.0,
    item_id='python.settings.auto_trade_opposing_active_min_price',
    legacy_attr='auto_trade_opposing_active_min_price',
    env='AUTO_TRADE_OPPOSING_ACTIVE_MIN_PRICE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_opposing_active_min_price configuration mapped to risk.exposure.opposing_minimum_separation_price.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 15.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  require_flat_for_range: bool = config_field(True,
    item_id='python.settings.auto_trade_require_flat_for_range',
    legacy_attr='auto_trade_require_flat_for_range',
    env='AUTO_TRADE_REQUIRE_FLAT_FOR_RANGE',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    shared_with_ctrader=True,
    description='Legacy auto_trade_require_flat_for_range configuration mapped to risk.exposure.require_flat_for_range.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )


class RiskPositionLimitsConfig(FrozenConfigModel):
  max_tracked_candidates: int = config_field(5,
    item_id='python.settings.auto_trade_max_tracked_candidates',
    legacy_attr='auto_trade_max_tracked_candidates',
    env='AUTO_TRADE_MAX_TRACKED_CANDIDATES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_max_tracked_candidates configuration mapped to risk.position_limits.max_tracked_candidates.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  maximum_per_symbol: int = config_field(1,
    item_id='python.settings.auto_trade_max_active_positions_per_symbol',
    legacy_attr='auto_trade_max_active_positions_per_symbol',
    env='AUTO_TRADE_MAX_ACTIVE_POSITIONS_PER_SYMBOL',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_max_active_positions_per_symbol configuration mapped to risk.position_limits.maximum_per_symbol.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  same_direction_stack_size_fraction: float = config_field(0.6,
    item_id='python.settings.auto_trade_same_direction_stack_size_fraction',
    legacy_attr='auto_trade_same_direction_stack_size_fraction',
    env='AUTO_TRADE_SAME_DIRECTION_STACK_SIZE_FRACTION',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_same_direction_stack_size_fraction configuration mapped to risk.position_limits.same_direction_stack_size_fraction.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.6),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class RiskTiersConfig(FrozenConfigModel):
  a_multiplier: float = config_field(1.0,
    item_id='python.settings.auto_trade_tier_a_risk_multiplier',
    legacy_attr='auto_trade_tier_a_risk_multiplier',
    env='AUTO_TRADE_TIER_A_RISK_MULTIPLIER',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.MULTIPLIER,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_tier_a_risk_multiplier configuration mapped to risk.tiers.a_multiplier.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  b_multiplier: float = config_field(0.5,
    item_id='python.settings.auto_trade_tier_b_risk_multiplier',
    legacy_attr='auto_trade_tier_b_risk_multiplier',
    env='AUTO_TRADE_TIER_B_RISK_MULTIPLIER',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.MULTIPLIER,
    risk=RiskClassification.BROKER_ACCOUNT_SAFETY,
    description='Legacy auto_trade_tier_b_risk_multiplier configuration mapped to risk.tiers.b_multiplier.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class RiskConfig(FrozenConfigModel):
  exposure: RiskExposureConfig = Field(default_factory=RiskExposureConfig)
  position_limits: RiskPositionLimitsConfig = Field(default_factory=RiskPositionLimitsConfig)
  sizing: RiskSizingConfig = Field(default_factory=RiskSizingConfig)
  tiers: RiskTiersConfig = Field(default_factory=RiskTiersConfig)
