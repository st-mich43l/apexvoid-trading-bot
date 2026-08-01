"""Complete inactive actionability configuration domain."""

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
  allowed: bool = config_field(True,
    item_id='python.settings.auto_trade_allow_counter_bias',
    legacy_attr='auto_trade_allow_counter_bias',
    env='AUTO_TRADE_ALLOW_COUNTER_BIAS',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_allow_counter_bias configuration mapped to actionability.counter_bias.allowed.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, False),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  map_counter_bias_min_confluence: int = config_field(2,
    item_id='python.settings.auto_trade_map_counter_bias_min_confluence',
    legacy_attr='auto_trade_map_counter_bias_min_confluence',
    env='AUTO_TRADE_MAP_COUNTER_BIAS_MIN_CONFLUENCE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_map_counter_bias_min_confluence configuration mapped to actionability.counter_bias.map_counter_bias_min_confluence.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  minimum_confluence: int = config_field(3,
    item_id='python.settings.scanner_gate_counter_bias_min_confluence',
    legacy_attr='scanner_gate_counter_bias_min_confluence',
    env='SCANNER_GATE_COUNTER_BIAS_MIN_CONFLUENCE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy scanner_gate_counter_bias_min_confluence configuration mapped to actionability.counter_bias.minimum_confluence.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  suppress_in_range: bool = config_field(False,
    item_id='python.settings.scanner_gate_suppress_counter_bias_in_range',
    legacy_attr='scanner_gate_suppress_counter_bias_in_range',
    env='SCANNER_GATE_SUPPRESS_COUNTER_BIAS_IN_RANGE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy scanner_gate_suppress_counter_bias_in_range configuration mapped to actionability.counter_bias.suppress_in_range.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ActionabilityGatesConfig(FrozenConfigModel):
  edge_proximity_atr: float = config_field(0.5,
    item_id='python.settings.auto_trade_edge_proximity_atr',
    legacy_attr='auto_trade_edge_proximity_atr',
    env='AUTO_TRADE_EDGE_PROXIMITY_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_edge_proximity_atr configuration mapped to actionability.gates.edge_proximity_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  eq_exclusion_fraction: float = config_field(0.15,
    item_id='python.settings.auto_trade_eq_exclusion_fraction',
    legacy_attr='auto_trade_eq_exclusion_fraction',
    env='AUTO_TRADE_EQ_EXCLUSION_FRACTION',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_eq_exclusion_fraction configuration mapped to actionability.gates.eq_exclusion_fraction.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.15),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  htf_veto_enabled: bool = config_field(True,
    item_id='python.settings.auto_trade_htf_veto_enabled',
    legacy_attr='auto_trade_htf_veto_enabled',
    env='AUTO_TRADE_HTF_VETO_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_htf_veto_enabled configuration mapped to actionability.gates.htf_veto_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  market_map_guard_enabled: bool = config_field(True,
    item_id='python.settings.auto_trade_market_map_guard_enabled',
    legacy_attr='auto_trade_market_map_guard_enabled',
    env='AUTO_TRADE_MARKET_MAP_GUARD_ENABLED',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    description='Legacy auto_trade_market_map_guard_enabled configuration mapped to actionability.gates.market_map_guard_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, True),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.Bool + AutoTradeOptions.Validate',
  )
  max_entry_atr: float = config_field(2.0,
    item_id='python.settings.max_entry_atr',
    legacy_attr='max_entry_atr',
    env='MAX_ENTRY_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy max_entry_atr configuration mapped to actionability.gates.max_entry_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  min_confluence: int = config_field(2,
    item_id='python.settings.auto_trade_min_confluence',
    legacy_attr='auto_trade_min_confluence',
    env='AUTO_TRADE_MIN_CONFLUENCE',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_min_confluence configuration mapped to actionability.gates.min_confluence.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 2),
    ),
    validation_summary='Pydantic required/type coercion only; EnvironmentResolver.Int + AutoTradeOptions.Validate',
  )
  news_guard_minutes: int = config_field(30,
    item_id='python.settings.auto_trade_news_guard_minutes',
    legacy_attr='auto_trade_news_guard_minutes',
    env='AUTO_TRADE_NEWS_GUARD_MINUTES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.MINUTES,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_news_guard_minutes configuration mapped to actionability.gates.news_guard_minutes.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 30),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  opposing_barrier_veto_enabled: bool = config_field(True,
    item_id='python.settings.auto_trade_opposing_barrier_veto_enabled',
    legacy_attr='auto_trade_opposing_barrier_veto_enabled',
    env='AUTO_TRADE_OPPOSING_BARRIER_VETO_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_opposing_barrier_veto_enabled configuration mapped to actionability.gates.opposing_barrier_veto_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  proximal_band_atr: float = config_field(0.5,
    item_id='python.settings.proximal_band_atr',
    legacy_attr='proximal_band_atr',
    env='PROXIMAL_BAND_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy proximal_band_atr configuration mapped to actionability.gates.proximal_band_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  range_context_disagreement_gate_enabled: bool = config_field(False,
    item_id='python.settings.range_context_disagreement_gate_enabled',
    legacy_attr='range_context_disagreement_gate_enabled',
    env='RANGE_CONTEXT_DISAGREEMENT_GATE_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy range_context_disagreement_gate_enabled configuration mapped to actionability.gates.range_context_disagreement_gate_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ActionabilityTargetRoomConfig(FrozenConfigModel):
  barrier_buffer_atr: float = config_field(0.5,
    item_id='python.settings.auto_trade_opposing_barrier_atr',
    legacy_attr='auto_trade_opposing_barrier_atr',
    env='AUTO_TRADE_OPPOSING_BARRIER_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_opposing_barrier_atr configuration mapped to actionability.target_room.barrier_buffer_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  minimum_capped_target_pips: float = config_field(15.0,
    item_id='python.settings.auto_trade_min_capped_target_pips',
    legacy_attr='auto_trade_min_capped_target_pips',
    env='AUTO_TRADE_MIN_CAPPED_TARGET_PIPS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PIPS,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_min_capped_target_pips configuration mapped to actionability.target_room.minimum_capped_target_pips.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 15.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ActionabilityOverlappingZonesConfig(FrozenConfigModel):
  veto_enabled: bool = config_field(True,
    item_id='python.settings.auto_trade_overlap_veto_enabled',
    legacy_attr='auto_trade_overlap_veto_enabled',
    env='AUTO_TRADE_OVERLAP_VETO_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_overlap_veto_enabled configuration mapped to actionability.overlapping_zones.veto_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ActionabilityStructuralGuardConfig(FrozenConfigModel):
  guard_mode: str = config_field('balanced',
    item_id='python.settings.auto_trade_structural_guard_mode',
    legacy_attr='auto_trade_structural_guard_mode',
    env='AUTO_TRADE_STRUCTURAL_GUARD_MODE',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_structural_guard_mode configuration mapped to actionability.structural_guard.guard_mode.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'balanced'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'balanced'),
    ),
    allowed_values=('observe', 'balanced', 'strict'),
    validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.String + AutoTradeOptions.Validate',
    pattern='^(observe|balanced|strict)$',
  )

  @field_validator("guard_mode", mode="before")
  @classmethod
  def normalize_guard_mode(cls, value):
    return value.strip().lower() if isinstance(value, str) else value


class ActionabilityZoneReconciliationConfig(FrozenConfigModel):
  enabled: bool = config_field(True,
    item_id='python.settings.auto_trade_zone_reconcile_enabled',
    legacy_attr='auto_trade_zone_reconcile_enabled',
    env='AUTO_TRADE_ZONE_RECONCILE_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy auto_trade_zone_reconcile_enabled configuration mapped to actionability.zone_reconciliation.enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator',
  )
  mode: str = config_field('enforce',
    item_id='python.settings.auto_trade_zone_reconcile_mode',
    legacy_attr='auto_trade_zone_reconcile_mode',
    env='AUTO_TRADE_ZONE_RECONCILE_MODE',
    owner=ConfigOwner.SHARED,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.EXECUTION_SAFETY,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.WARNING,
    description='Legacy auto_trade_zone_reconcile_mode configuration mapped to actionability.zone_reconciliation.mode.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'enforce'),
      ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 'enforce'),
    ),
    allowed_values=('off', 'shadow', 'enforce'),
    validation_summary='Pydantic type coercion + Settings cross-field model validator; EnvironmentResolver.String + AutoTradeOptions.Validate',
    pattern='^(off|shadow|enforce)$',
  )

  @field_validator("mode", mode="before")
  @classmethod
  def normalize_mode(cls, value):
    return value.strip().lower() if isinstance(value, str) else value

  @model_validator(mode="after")
  def disable_mode_with_switch(self):
    if not self.enabled and self.mode != "off":
      object.__setattr__(self, "mode", "off")
    return self


class ActionabilityContestedCorridorConfig(FrozenConfigModel):
  gap_atr: float = config_field(0.5,
    item_id='python.settings.contested_corridor_gap_atr',
    legacy_attr='contested_corridor_gap_atr',
    env='CONTESTED_CORRIDOR_GAP_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy contested_corridor_gap_atr configuration mapped to actionability.contested_corridor.gap_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ActionabilityKeyLevelRoleConfig(FrozenConfigModel):
  enabled: bool = config_field(False,
    item_id='python.settings.key_level_role_ambiguity_gate_enabled',
    legacy_attr='key_level_role_ambiguity_gate_enabled',
    env='KEY_LEVEL_ROLE_AMBIGUITY_GATE_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy key_level_role_ambiguity_gate_enabled configuration mapped to actionability.key_level_role.enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ActionabilityScannerGatesConfig(FrozenConfigModel):
  actionability_gate_enabled: bool = config_field(False,
    item_id='python.settings.scanner_actionability_gate_enabled',
    legacy_attr='scanner_actionability_gate_enabled',
    env='SCANNER_ACTIONABILITY_GATE_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy scanner_actionability_gate_enabled configuration mapped to actionability.scanner_gates.actionability_gate_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  conflict_margin: float = config_field(1.0,
    item_id='python.settings.scanner_conflict_margin',
    legacy_attr='scanner_conflict_margin',
    env='SCANNER_CONFLICT_MARGIN',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy scanner_conflict_margin configuration mapped to actionability.scanner_gates.conflict_margin.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  zone_width_gate_enabled: bool = config_field(False,
    item_id='python.settings.scanner_zone_width_gate_enabled',
    legacy_attr='scanner_zone_width_gate_enabled',
    env='SCANNER_ZONE_WIDTH_GATE_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy scanner_zone_width_gate_enabled configuration mapped to actionability.scanner_gates.zone_width_gate_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class ActionabilityStructuralAnchorConfig(FrozenConfigModel):
  maximum_source_touches: int = config_field(0,
    item_id='python.settings.scanner_gate_max_source_touches',
    legacy_attr='scanner_gate_max_source_touches',
    env='SCANNER_GATE_MAX_SOURCE_TOUCHES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy scanner_gate_max_source_touches configuration mapped to actionability.structural_anchor.maximum_source_touches.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  required: bool = config_field(False,
    item_id='python.settings.scanner_gate_require_structural_anchor',
    legacy_attr='scanner_gate_require_structural_anchor',
    env='SCANNER_GATE_REQUIRE_STRUCTURAL_ANCHOR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy scanner_gate_require_structural_anchor configuration mapped to actionability.structural_anchor.required.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


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
