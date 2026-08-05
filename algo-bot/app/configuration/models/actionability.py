"""Complete Canonical Catalog V2 configuration domain. """
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

class ActionabilityCounterBiasConfig(FrozenConfigModel):
    allowed: bool = config_field(True, canonical_env='AUTO_TRADE_ALLOW_COUNTER_BIAS', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate', evidence_notes=('Python schema default is true while cTrader FromEnvironment defaults false; unresolved.',))
    map_counter_bias_min_confluence: int = config_field(2, canonical_env='AUTO_TRADE_MAP_COUNTER_BIAS_MIN_CONFLUENCE', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),), validation_summary='Pydantic required/type coercion only')
    minimum_confluence: int = config_field(3, canonical_env='SCANNER_GATE_COUNTER_BIAS_MIN_CONFLUENCE', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),), validation_summary='Pydantic required/type coercion only')
    suppress_in_range: bool = config_field(False, canonical_env='SCANNER_GATE_SUPPRESS_COUNTER_BIAS_IN_RANGE', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')

class ActionabilityGatesConfig(FrozenConfigModel):
    edge_proximity_atr: float = config_field(0.5, canonical_env='AUTO_TRADE_EDGE_PROXIMITY_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),), validation_summary='Pydantic required/type coercion only')
    eq_exclusion_fraction: float = config_field(0.15, canonical_env='AUTO_TRADE_EQ_EXCLUSION_FRACTION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (fraction).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.15),), validation_summary='Pydantic required/type coercion only')
    htf_veto_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_HTF_VETO_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')
    market_map_guard_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_MARKET_MAP_GUARD_ENABLED', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True)), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Bool + AutoTradeOptions.Validate', evidence_notes=('Direct demo_eval inherits mapped-zone true; root Compose demo_eval injects false.',))
    max_entry_atr: float = config_field(2.0, canonical_env='MAX_ENTRY_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 2.0),), validation_summary='Pydantic required/type coercion only')
    min_confluence: int = config_field(2, canonical_env='AUTO_TRADE_MIN_CONFLUENCE', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 2), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 2)), validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate')
    news_guard_minutes: int = config_field(30, canonical_env='AUTO_TRADE_NEWS_GUARD_MINUTES', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.MINUTES, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (minutes).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 30),), validation_summary='Pydantic required/type coercion only')
    opposing_barrier_veto_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_OPPOSING_BARRIER_VETO_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')
    proximal_band_atr: float = config_field(0.5, canonical_env='PROXIMAL_BAND_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),), validation_summary='Pydantic required/type coercion only')
    range_context_disagreement_gate_enabled: bool = config_field(False, canonical_env='RANGE_CONTEXT_DISAGREEMENT_GATE_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')

class ActionabilityTargetRoomConfig(FrozenConfigModel):
    barrier_buffer_atr: float = config_field(0.5, canonical_env='AUTO_TRADE_OPPOSING_BARRIER_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),), validation_summary='Pydantic required/type coercion only')
    minimum_capped_target_pips: float = config_field(15.0, canonical_env='AUTO_TRADE_MIN_CAPPED_TARGET_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 15.0),), validation_summary='Pydantic required/type coercion only')
    scalp_barrier_buffer_atr: float = config_field(0.15, canonical_env='AUTO_TRADE_SCALP_OPPOSING_BARRIER_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Barrier buffer applied to range/scalp setups (Range Edge Scalp, Fade Scalp, etc) instead of barrier_buffer_atr - scalping close to structure is the intended style, not a violation of it (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.15),), validation_summary='Pydantic required/type coercion only')
    scalp_minimum_capped_target_pips: float = config_field(8.0, canonical_env='AUTO_TRADE_SCALP_MIN_CAPPED_TARGET_PIPS', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.PIPS, risk=RiskClassification.EXECUTION_SAFETY, description='Minimum capped target applied to range/scalp setups instead of minimum_capped_target_pips (pips).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 8.0),), validation_summary='Pydantic required/type coercion only')

class ActionabilityOverlappingZonesConfig(FrozenConfigModel):
    veto_enabled: bool = config_field(True, canonical_env='AUTO_TRADE_OVERLAP_VETO_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')

class ActionabilityStructuralGuardConfig(FrozenConfigModel):
    guard_mode: str = config_field('balanced', canonical_env='AUTO_TRADE_STRUCTURAL_GUARD_MODE', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'balanced'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'balanced')), allowed_values=('observe', 'balanced', 'strict'), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.String + AutoTradeOptions.Validate', pattern='^(observe|balanced|strict)$')

    @field_validator('guard_mode', mode='before')
    @classmethod
    def normalize_guard_mode(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

class ActionabilityZoneReconciliationConfig(FrozenConfigModel):
    enabled: bool = config_field(True, canonical_env='AUTO_TRADE_ZONE_RECONCILE_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic type coercion + Settings cross-field model validator')
    mode: str = config_field('enforce', canonical_env='AUTO_TRADE_ZONE_RECONCILE_MODE', owner=ConfigOwner.SHARED, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.EXECUTION_SAFETY, shared_with_ctrader=True, mismatch_policy=MismatchPolicy.WARNING, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'enforce'), ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'enforce')), allowed_values=('off', 'shadow', 'enforce'), validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.String + AutoTradeOptions.Validate', pattern='^(off|shadow|enforce)$')

    @field_validator('mode', mode='before')
    @classmethod
    def normalize_mode(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode='after')
    def validate_disabled_mode(self):
        if not self.enabled and self.mode != 'off':
            raise ValueError(
                'disabled zone reconciliation requires mode=off; '
                'canonical resolution derives this value before model validation'
            )
        return self

class ActionabilityContestedCorridorConfig(FrozenConfigModel):
    gap_atr: float = config_field(0.5, canonical_env='CONTESTED_CORRIDOR_GAP_ATR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ATR, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (atr).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),), validation_summary='Pydantic required/type coercion only')

class ActionabilityKeyLevelRoleConfig(FrozenConfigModel):
    enabled: bool = config_field(False, canonical_env='KEY_LEVEL_ROLE_AMBIGUITY_GATE_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')

class ActionabilityScannerGatesConfig(FrozenConfigModel):
    actionability_gate_enabled: bool = config_field(False, canonical_env='SCANNER_ACTIONABILITY_GATE_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')
    conflict_margin: float = config_field(1.0, canonical_env='SCANNER_CONFLICT_MARGIN', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.SCORE, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),), validation_summary='Pydantic required/type coercion only')
    zone_width_gate_enabled: bool = config_field(False, canonical_env='SCANNER_ZONE_WIDTH_GATE_ENABLED', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')

class ActionabilityStructuralAnchorConfig(FrozenConfigModel):
    maximum_source_touches: int = config_field(0, canonical_env='SCANNER_GATE_MAX_SOURCE_TOUCHES', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.COUNT, risk=RiskClassification.EXECUTION_SAFETY, description='Controls  (count).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0),), validation_summary='Pydantic required/type coercion only')
    required: bool = config_field(False, canonical_env='SCANNER_GATE_REQUIRE_STRUCTURAL_ANCHOR', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Controls .', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, False),), validation_summary='Pydantic required/type coercion only')

class ActionabilityEntryLocationReversalConfig(FrozenConfigModel):
    buy_maximum_position: float = config_field(0.5, canonical_env='ENTRY_LOCATION_REVERSAL_BUY_MAX_POSITION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Maximum effective range position for BUY reversals (0=low,1=high).', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)
    sell_minimum_position: float = config_field(0.5, canonical_env='ENTRY_LOCATION_REVERSAL_SELL_MIN_POSITION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Minimum effective range position for SELL reversals.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)
    extreme_buy_block_position: float = config_field(0.65, canonical_env='ENTRY_LOCATION_REVERSAL_EXTREME_BUY_BLOCK', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Hard-block BUY reversals at or above this range position in enforce mode.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.65),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)
    extreme_sell_block_position: float = config_field(0.35, canonical_env='ENTRY_LOCATION_REVERSAL_EXTREME_SELL_BLOCK', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Hard-block SELL reversals at or below this range position in enforce mode.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.35),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)

class ActionabilityEntryLocationRangeReversionConfig(FrozenConfigModel):
    buy_maximum_position: float = config_field(0.4, canonical_env='ENTRY_LOCATION_RANGE_BUY_MAX_POSITION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Maximum effective range position for range-reversion BUY entries.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.4),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)
    sell_minimum_position: float = config_field(0.6, canonical_env='ENTRY_LOCATION_RANGE_SELL_MIN_POSITION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Minimum effective range position for range-reversion SELL entries.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.6),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)
    equilibrium_exclusion_width: float = config_field(0.2, canonical_env='ENTRY_LOCATION_RANGE_EQ_EXCLUSION_WIDTH', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Width around 0.5 in which range-reversion entries are rejected.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.2),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)

class ActionabilityEntryLocationTrendPullbackConfig(FrozenConfigModel):
    buy_maximum_position: float = config_field(0.7, canonical_env='ENTRY_LOCATION_TREND_BUY_MAX_POSITION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Maximum effective range position for bullish trend pullback BUY.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.7),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)
    sell_minimum_position: float = config_field(0.3, canonical_env='ENTRY_LOCATION_TREND_SELL_MIN_POSITION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.FRACTION, risk=RiskClassification.EXECUTION_SAFETY, description='Minimum effective range position for bearish trend pullback SELL.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.3),), validation_summary='Pydantic required/type coercion only', ge=0, le=1)

class ActionabilityEntryLocationBreakoutRetestConfig(FrozenConfigModel):
    allow_directional_expansion: bool = config_field(True, canonical_env='ENTRY_LOCATION_BREAKOUT_ALLOW_EXPANSION', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.BOOLEAN, risk=RiskClassification.EXECUTION_SAFETY, description='Allow accepted breakout-retest setups to bypass reversal location rules.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, True),), validation_summary='Pydantic required/type coercion only')

class ActionabilityEntryLocationConfig(FrozenConfigModel):
    mode: str = config_field('shadow', canonical_env='ENTRY_LOCATION_MODE', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.EXECUTION_SAFETY, description='Entry-location policy mode: off, shadow (record only), or enforce.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'shadow'),), allowed_values=('off', 'shadow', 'enforce'), validation_summary='Pydantic type coercion + field validator', pattern='^(off|shadow|enforce)$')
    missing_context_policy: str = config_field('block', canonical_env='ENTRY_LOCATION_MISSING_CONTEXT_POLICY', owner=ConfigOwner.PYTHON, reload=ReloadPolicy.NEW_SETUP_ONLY, runtime_reload=ReloadPolicy.RESTART, unit=ConfigUnit.ENUM, risk=RiskClassification.EXECUTION_SAFETY, description='When effective dealing range is missing: block (fail closed in enforce) or allow.', default_contexts=(ContextDefault(DefaultContext.PYTHON_SCHEMA, 'block'),), allowed_values=('block', 'allow'), validation_summary='Pydantic type coercion + field validator', pattern='^(block|allow)$')
    breakout_retest: ActionabilityEntryLocationBreakoutRetestConfig = Field(default_factory=ActionabilityEntryLocationBreakoutRetestConfig)
    range_reversion: ActionabilityEntryLocationRangeReversionConfig = Field(default_factory=ActionabilityEntryLocationRangeReversionConfig)
    reversal: ActionabilityEntryLocationReversalConfig = Field(default_factory=ActionabilityEntryLocationReversalConfig)
    trend_pullback: ActionabilityEntryLocationTrendPullbackConfig = Field(default_factory=ActionabilityEntryLocationTrendPullbackConfig)

    @field_validator('mode', 'missing_context_policy', mode='before')
    @classmethod
    def normalize_entry_location_enums(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

class ActionabilityConfig(FrozenConfigModel):
    contested_corridor: ActionabilityContestedCorridorConfig = Field(default_factory=ActionabilityContestedCorridorConfig)
    counter_bias: ActionabilityCounterBiasConfig = Field(default_factory=ActionabilityCounterBiasConfig)
    entry_location: ActionabilityEntryLocationConfig = Field(default_factory=ActionabilityEntryLocationConfig)
    gates: ActionabilityGatesConfig = Field(default_factory=ActionabilityGatesConfig)
    key_level_role: ActionabilityKeyLevelRoleConfig = Field(default_factory=ActionabilityKeyLevelRoleConfig)
    overlapping_zones: ActionabilityOverlappingZonesConfig = Field(default_factory=ActionabilityOverlappingZonesConfig)
    scanner_gates: ActionabilityScannerGatesConfig = Field(default_factory=ActionabilityScannerGatesConfig)
    structural_anchor: ActionabilityStructuralAnchorConfig = Field(default_factory=ActionabilityStructuralAnchorConfig)
    structural_guard: ActionabilityStructuralGuardConfig = Field(default_factory=ActionabilityStructuralGuardConfig)
    target_room: ActionabilityTargetRoomConfig = Field(default_factory=ActionabilityTargetRoomConfig)
    zone_reconciliation: ActionabilityZoneReconciliationConfig = Field(default_factory=ActionabilityZoneReconciliationConfig)
