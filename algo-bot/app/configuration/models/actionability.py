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
    def disable_mode_with_switch(self):
        if not self.enabled and self.mode != 'off':
            object.__setattr__(self, 'mode', 'off')
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

class ActionabilityConfig(FrozenConfigModel):
    contested_corridor: ActionabilityContestedCorridorConfig = Field(default_factory=ActionabilityContestedCorridorConfig)
    counter_bias: ActionabilityCounterBiasConfig = Field(default_factory=ActionabilityCounterBiasConfig)
    gates: ActionabilityGatesConfig = Field(default_factory=ActionabilityGatesConfig)
    key_level_role: ActionabilityKeyLevelRoleConfig = Field(default_factory=ActionabilityKeyLevelRoleConfig)
    overlapping_zones: ActionabilityOverlappingZonesConfig = Field(default_factory=ActionabilityOverlappingZonesConfig)
    scanner_gates: ActionabilityScannerGatesConfig = Field(default_factory=ActionabilityScannerGatesConfig)
    structural_anchor: ActionabilityStructuralAnchorConfig = Field(default_factory=ActionabilityStructuralAnchorConfig)
    structural_guard: ActionabilityStructuralGuardConfig = Field(default_factory=ActionabilityStructuralGuardConfig)
    target_room: ActionabilityTargetRoomConfig = Field(default_factory=ActionabilityTargetRoomConfig)
    zone_reconciliation: ActionabilityZoneReconciliationConfig = Field(default_factory=ActionabilityZoneReconciliationConfig)
