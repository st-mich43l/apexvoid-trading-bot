"""Complete inactive analysis configuration domain."""

from pydantic import Field
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


class AnalysisMeasurementsConfig(FrozenConfigModel):
  alert_overlap_suppress: float = config_field(0.5,
    item_id='python.settings.alert_overlap_suppress',
    legacy_attr='alert_overlap_suppress',
    env='ALERT_OVERLAP_SUPPRESS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy alert_overlap_suppress configuration mapped to analysis.measurements.alert_overlap_suppress.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  coil_contract: float = config_field(0.8,
    item_id='python.settings.coil_contract',
    legacy_attr='coil_contract',
    env='COIL_CONTRACT',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy coil_contract configuration mapped to analysis.measurements.coil_contract.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.8),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  eq_band: float = config_field(0.1,
    item_id='python.settings.eq_band',
    legacy_attr='eq_band',
    env='EQ_BAND',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy eq_band configuration mapped to analysis.measurements.eq_band.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.1),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  inducement_band_atr: float = config_field(0.3,
    item_id='python.settings.inducement_band_atr',
    legacy_attr='inducement_band_atr',
    env='INDUCEMENT_BAND_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy inducement_band_atr configuration mapped to analysis.measurements.inducement_band_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.3),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  max_merged_zone_atr: float = config_field(3.0,
    item_id='python.settings.max_merged_zone_atr',
    legacy_attr='max_merged_zone_atr',
    env='MAX_MERGED_ZONE_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy max_merged_zone_atr configuration mapped to analysis.measurements.max_merged_zone_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 3.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  regime_chop_alert_share: float = config_field(0.75,
    item_id='python.settings.regime_chop_alert_share',
    legacy_attr='regime_chop_alert_share',
    env='REGIME_CHOP_ALERT_SHARE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy regime_chop_alert_share configuration mapped to analysis.measurements.regime_chop_alert_share.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.75),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  scanner_conflict_overlap: float = config_field(0.5,
    item_id='environment.SCANNER_CONFLICT_OVERLAP',
    legacy_attr=None,
    env='SCANNER_CONFLICT_OVERLAP',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Operational environment option SCANNER_CONFLICT_OVERLAP.',
    validation_summary='direct environment read or deployment parser',
    deprecated=True,
    terminal_deprecation_reason='No runtime consumer; retained as Phase 1 audit evidence.',
  )
  strict_pd_gate: bool = config_field(False,
    item_id='python.settings.strict_pd_gate',
    legacy_attr='strict_pd_gate',
    env='STRICT_PD_GATE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy strict_pd_gate configuration mapped to analysis.measurements.strict_pd_gate.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, False),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  tp_min_spacing_atr: float = config_field(0.5,
    item_id='python.settings.tp_min_spacing_atr',
    legacy_attr='tp_min_spacing_atr',
    env='TP_MIN_SPACING_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy tp_min_spacing_atr configuration mapped to analysis.measurements.tp_min_spacing_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisDetectorsScoringConfig(FrozenConfigModel):
  coil: float = config_field(1.5,
    item_id='hardcoded.analysis.detector_coil_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.detectors.scoring.coil.',
    validation_summary='none; source constant',
  )


class AnalysisDetectorsReactionConfig(FrozenConfigModel):
  maximum_distance_atr: float = config_field(1.0,
    item_id='hardcoded.analysis.detector_reaction_max_atr',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.detectors.reaction.maximum_distance_atr.',
    validation_summary='none; source constant',
  )


class AnalysisDetectorsStarThresholdsConfig(FrozenConfigModel):
  three: float = config_field(12.0,
    item_id='hardcoded.analysis.detector_three_star_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.detectors.star_thresholds.three.',
    validation_summary='none; source constant',
  )
  two: float = config_field(8.0,
    item_id='hardcoded.analysis.detector_two_star_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.detectors.star_thresholds.two.',
    validation_summary='none; source constant',
  )


class AnalysisDetectorsConfig(FrozenConfigModel):
  reaction: AnalysisDetectorsReactionConfig = Field(default_factory=AnalysisDetectorsReactionConfig)
  scoring: AnalysisDetectorsScoringConfig = Field(default_factory=AnalysisDetectorsScoringConfig)
  star_thresholds: AnalysisDetectorsStarThresholdsConfig = Field(default_factory=AnalysisDetectorsStarThresholdsConfig)


class AnalysisDisplacementConfig(FrozenConfigModel):
  atr_mult: float = config_field(1.5,
    item_id='python.settings.displacement_atr_mult',
    legacy_attr='displacement_atr_mult',
    env='DISPLACEMENT_ATR_MULT',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy displacement_atr_mult configuration mapped to analysis.displacement.atr_mult.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  body_fraction: float = config_field(0.6,
    item_id='hardcoded.analysis.displacement_body_fraction',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.displacement.body_fraction.',
    validation_summary='none; source constant',
  )
  minimum_range_atr: float = config_field(1.0,
    item_id='hardcoded.analysis.displacement_range_atr',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.displacement.minimum_range_atr.',
    validation_summary='none; source constant',
  )


class AnalysisMarketMapConfig(FrozenConfigModel):
  band_max_atr: float = config_field(2.0,
    item_id='python.settings.map_band_max_atr',
    legacy_attr='map_band_max_atr',
    env='MAP_BAND_MAX_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_band_max_atr configuration mapped to analysis.market_map.band_max_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  change_min: float = config_field(1.0,
    item_id='python.settings.map_change_min',
    legacy_attr='map_change_min',
    env='MAP_CHANGE_MIN',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_change_min configuration mapped to analysis.market_map.change_min.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  fallback_radius_price: float = config_field(30.0,
    item_id='python.settings.map_fallback_radius',
    legacy_attr='map_fallback_radius',
    env='MAP_FALLBACK_RADIUS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_fallback_radius configuration mapped to analysis.market_map.fallback_radius_price.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 30.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  major_score: float = config_field(12.0,
    item_id='python.settings.map_major_score',
    legacy_attr='map_major_score',
    env='MAP_MAJOR_SCORE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_major_score configuration mapped to analysis.market_map.major_score.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 12.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  max_distance_atr: float = config_field(15.0,
    item_id='python.settings.map_max_distance_atr',
    legacy_attr='map_max_distance_atr',
    env='MAP_MAX_DISTANCE_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_max_distance_atr configuration mapped to analysis.market_map.max_distance_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 15.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  max_per_side: int = config_field(4,
    item_id='python.settings.map_max_per_side',
    legacy_attr='map_max_per_side',
    env='MAP_MAX_PER_SIDE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_max_per_side configuration mapped to analysis.market_map.max_per_side.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 4),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  max_touches: int = config_field(2,
    item_id='python.settings.map_max_touches',
    legacy_attr='map_max_touches',
    env='MAP_MAX_TOUCHES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_max_touches configuration mapped to analysis.market_map.max_touches.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  min_level_touches: int = config_field(4,
    item_id='python.settings.map_min_level_touches',
    legacy_attr='map_min_level_touches',
    env='MAP_MIN_LEVEL_TOUCHES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_min_level_touches configuration mapped to analysis.market_map.min_level_touches.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 4),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  min_per_side: int = config_field(2,
    item_id='python.settings.map_min_per_side',
    legacy_attr='map_min_per_side',
    env='MAP_MIN_PER_SIDE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_min_per_side configuration mapped to analysis.market_map.min_per_side.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  min_zone_score: float = config_field(6.0,
    item_id='python.settings.map_min_zone_score',
    legacy_attr='map_min_zone_score',
    env='MAP_MIN_ZONE_SCORE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_min_zone_score configuration mapped to analysis.market_map.min_zone_score.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 6.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  scalp_radius_price: float = config_field(15.0,
    item_id='python.settings.map_scalp_radius',
    legacy_attr='map_scalp_radius',
    env='MAP_SCALP_RADIUS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_scalp_radius configuration mapped to analysis.market_map.scalp_radius_price.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 15.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  scan_interval_minutes: int = config_field(60,
    item_id='python.settings.map_scan_interval_minutes',
    legacy_attr='map_scan_interval_minutes',
    env='MAP_SCAN_INTERVAL_MINUTES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.MINUTES,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy map_scan_interval_minutes configuration mapped to analysis.market_map.scan_interval_minutes.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 60),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  session_band_atr: float = config_field(0.1,
    item_id='hardcoded.analysis.session_band_atr',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.market_map.session_band_atr.',
    validation_summary='none; source constant',
  )


class AnalysisTrendlinesConfig(FrozenConfigModel):
  dedup_slope_percent: float = config_field(0.2,
    item_id='hardcoded.analysis.trendline_dedup_slope_percent',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.trendlines.dedup_slope_percent.',
    validation_summary='none; source constant',
  )
  dedup_value_atr: float = config_field(0.5,
    item_id='hardcoded.analysis.trendline_dedup_value_atr',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.trendlines.dedup_value_atr.',
    validation_summary='none; source constant',
  )
  maximum_slope_atr: float = config_field(0.15,
    item_id='python.settings.tl_max_slope_atr',
    legacy_attr='tl_max_slope_atr',
    env='TL_MAX_SLOPE_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy tl_max_slope_atr configuration mapped to analysis.trendlines.maximum_slope_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.15),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  minimum_touches: int = config_field(3,
    item_id='python.settings.tl_min_touches',
    legacy_attr='tl_min_touches',
    env='TL_MIN_TOUCHES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy tl_min_touches configuration mapped to analysis.trendlines.minimum_touches.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  tolerance_atr: float = config_field(0.3,
    item_id='python.settings.tl_tol_atr',
    legacy_attr='tl_tol_atr',
    env='TL_TOL_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy tl_tol_atr configuration mapped to analysis.trendlines.tolerance_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.3),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisZonesScoringConfig(FrozenConfigModel):
  fresh: float = config_field(3.0,
    item_id='hardcoded.analysis.zone_fresh_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.fresh.',
    validation_summary='none; source constant',
  )
  grade_a_grab: float = config_field(2.0,
    item_id='hardcoded.analysis.zone_grab_a_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.grade_a_grab.',
    validation_summary='none; source constant',
  )
  higher_timeframe: float = config_field(3.0,
    item_id='hardcoded.analysis.zone_htf_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.higher_timeframe.',
    validation_summary='none; source constant',
  )
  key_level: float = config_field(2.0,
    item_id='hardcoded.analysis.zone_key_level_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.key_level.',
    validation_summary='none; source constant',
  )
  liquidity: float = config_field(2.0,
    item_id='hardcoded.analysis.zone_liquidity_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.liquidity.',
    validation_summary='none; source constant',
  )
  premium_discount: float = config_field(2.0,
    item_id='hardcoded.analysis.zone_pd_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.premium_discount.',
    validation_summary='none; source constant',
  )
  round_number: float = config_field(1.0,
    item_id='hardcoded.analysis.zone_round_number_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.round_number.',
    validation_summary='none; source constant',
  )
  session_level: float = config_field(2.0,
    item_id='hardcoded.analysis.zone_session_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.session_level.',
    validation_summary='none; source constant',
  )
  single_touch: float = config_field(1.0,
    item_id='hardcoded.analysis.zone_single_touch_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.single_touch.',
    validation_summary='none; source constant',
  )
  source_cap: float = config_field(5.0,
    item_id='hardcoded.analysis.zone_source_score_cap',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.source_cap.',
    validation_summary='none; source constant',
  )
  trendline: float = config_field(1.5,
    item_id='hardcoded.analysis.zone_trendline_score',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.SCORE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.scoring.trendline.',
    validation_summary='none; source constant',
  )


class AnalysisZonesReconciliationConfig(FrozenConfigModel):
  maximum_affected_fraction: float = config_field(0.2,
    item_id='hardcoded.analysis.zone_reconcile_max_fraction',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.reconciliation.maximum_affected_fraction.',
    validation_summary='none; source constant',
  )
  minimum_overlap: float = config_field(0.5,
    item_id='hardcoded.analysis.zone_reconcile_overlap',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.reconciliation.minimum_overlap.',
    validation_summary='none; source constant',
  )
  minimum_remainder_price: float = config_field(2.0,
    item_id='hardcoded.analysis.zone_min_width',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.reconciliation.minimum_remainder_price.',
    validation_summary='none; source constant',
  )
  minimum_sample: int = config_field(5,
    item_id='hardcoded.analysis.zone_reconcile_min_sample',
    legacy_attr=None,
    env=None,
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.CODE_RELEASE,
    runtime_reload=ReloadPolicy.CODE_RELEASE,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    kind=ConfigKind.ALGORITHM_CONSTANT,
    description='Config-like hardcoded value proposed at analysis.zones.reconciliation.minimum_sample.',
    validation_summary='none; source constant',
  )


class AnalysisZonesDiscoveryConfig(FrozenConfigModel):
  maximum_width_atr: float = config_field(1.5,
    item_id='python.settings.max_zone_width_atr',
    legacy_attr='max_zone_width_atr',
    env='MAX_ZONE_WIDTH_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEW_SETUP_ONLY,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.EXECUTION_SAFETY,
    description='Legacy max_zone_width_atr configuration mapped to analysis.zones.discovery.maximum_width_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisZonesSymbolContractConfig(FrozenConfigModel):
  major_maximum_width_price: float = config_field(10.0,
    item_id='python.settings.xau_major_zone_max_width_price',
    legacy_attr='xau_major_zone_max_width_price',
    env='XAU_MAJOR_ZONE_MAX_WIDTH_PRICE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy xau_major_zone_max_width_price configuration mapped to analysis.zones.symbol_contract.major_maximum_width_price.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 10.0),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator',
  )
  minimum_width_price: float = config_field(3.0,
    item_id='python.settings.xau_zone_min_width_price',
    legacy_attr='xau_zone_min_width_price',
    env='XAU_ZONE_MIN_WIDTH_PRICE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy xau_zone_min_width_price configuration mapped to analysis.zones.symbol_contract.minimum_width_price.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 3.0),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator',
  )
  preferred_maximum_width_price: float = config_field(6.0,
    item_id='python.settings.xau_zone_preferred_max_width_price',
    legacy_attr='xau_zone_preferred_max_width_price',
    env='XAU_ZONE_PREFERRED_MAX_WIDTH_PRICE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy xau_zone_preferred_max_width_price configuration mapped to analysis.zones.symbol_contract.preferred_maximum_width_price.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 6.0),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator',
  )
  preferred_minimum_width_price: float = config_field(3.0,
    item_id='python.settings.xau_zone_preferred_min_width_price',
    legacy_attr='xau_zone_preferred_min_width_price',
    env='XAU_ZONE_PREFERRED_MIN_WIDTH_PRICE',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy xau_zone_preferred_min_width_price configuration mapped to analysis.zones.symbol_contract.preferred_minimum_width_price.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 3.0),
    ),
    validation_summary='Pydantic type coercion + Settings cross-field model validator',
  )

  @model_validator(mode="after")
  def validate_width_order(self):
    if not (
      0
      < self.minimum_width_price
      <= self.preferred_minimum_width_price
      <= self.preferred_maximum_width_price
      <= self.major_maximum_width_price
    ):
      raise ValueError(
        "XAU zone widths must satisfy minimum <= preferred minimum "
        "<= preferred maximum <= major maximum"
      )
    return self


class AnalysisZonesConfluenceConfig(FrozenConfigModel):
  merge_gap_price: float = config_field(1.0,
    item_id='python.settings.zone_merge_gap',
    legacy_attr='zone_merge_gap',
    env='ZONE_MERGE_GAP',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy zone_merge_gap configuration mapped to analysis.zones.confluence.merge_gap_price.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisZonesConfig(FrozenConfigModel):
  alert_ttl: int = config_field(14400,
    item_id='python.settings.zone_alert_ttl',
    legacy_attr='zone_alert_ttl',
    env='ZONE_ALERT_TTL',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.SECONDS,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy zone_alert_ttl configuration mapped to analysis.zones.alert_ttl.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 14400),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  confluence: AnalysisZonesConfluenceConfig = Field(default_factory=AnalysisZonesConfluenceConfig)
  discovery: AnalysisZonesDiscoveryConfig = Field(default_factory=AnalysisZonesDiscoveryConfig)
  merge_max_width: float = config_field(6.0,
    item_id='python.settings.zone_merge_max_width',
    legacy_attr='zone_merge_max_width',
    env='ZONE_MERGE_MAX_WIDTH',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy zone_merge_max_width configuration mapped to analysis.zones.merge_max_width.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 6.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  merge_overlap: float = config_field(0.5,
    item_id='python.settings.zone_merge_overlap',
    legacy_attr='zone_merge_overlap',
    env='ZONE_MERGE_OVERLAP',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy zone_merge_overlap configuration mapped to analysis.zones.merge_overlap.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  reconciliation: AnalysisZonesReconciliationConfig = Field(default_factory=AnalysisZonesReconciliationConfig)
  scoring: AnalysisZonesScoringConfig = Field(default_factory=AnalysisZonesScoringConfig)
  symbol_contract: AnalysisZonesSymbolContractConfig = Field(default_factory=AnalysisZonesSymbolContractConfig)
  width: str = config_field('body',
    item_id='python.settings.zone_width',
    legacy_attr='zone_width',
    env='ZONE_WIDTH',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ENUM,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy zone_width configuration mapped to analysis.zones.width.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'body'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisAtrConfig(FrozenConfigModel):
  length: int = config_field(14,
    item_id='python.settings.atr_length',
    legacy_attr='atr_length',
    env='ATR_LENGTH',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy atr_length configuration mapped to analysis.atr.length.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 14),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisBreakoutConfig(FrozenConfigModel):
  accept_bars: int = config_field(2,
    item_id='python.settings.breakout_accept_bars',
    legacy_attr='breakout_accept_bars',
    env='BREAKOUT_ACCEPT_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy breakout_accept_bars configuration mapped to analysis.breakout.accept_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  buffer_atr: float = config_field(0.1,
    item_id='python.settings.breakout_buffer_atr',
    legacy_attr='breakout_buffer_atr',
    env='BREAKOUT_BUFFER_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy breakout_buffer_atr configuration mapped to analysis.breakout.buffer_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.1),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  max_age_bars: int = config_field(6,
    item_id='python.settings.breakout_max_age_bars',
    legacy_attr='breakout_max_age_bars',
    env='BREAKOUT_MAX_AGE_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy breakout_max_age_bars configuration mapped to analysis.breakout.max_age_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 6),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisRegimeChopConfig(FrozenConfigModel):
  edge_frac: float = config_field(0.25,
    item_id='python.settings.chop_edge_frac',
    legacy_attr='chop_edge_frac',
    env='CHOP_EDGE_FRAC',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy chop_edge_frac configuration mapped to analysis.regime.chop.edge_frac.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.25),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  filter_enabled: bool = config_field(True,
    item_id='python.settings.chop_filter_enabled',
    legacy_attr='chop_filter_enabled',
    env='CHOP_FILTER_ENABLED',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy chop_filter_enabled configuration mapped to analysis.regime.chop.filter_enabled.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, True),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  lookback: int = config_field(24,
    item_id='python.settings.chop_lookback',
    legacy_attr='chop_lookback',
    env='CHOP_LOOKBACK',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy chop_lookback configuration mapped to analysis.regime.chop.lookback.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 24),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  range_atr: float = config_field(4.0,
    item_id='python.settings.chop_range_atr',
    legacy_attr='chop_range_atr',
    env='CHOP_RANGE_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy chop_range_atr configuration mapped to analysis.regime.chop.range_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 4.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisRegimeConfig(FrozenConfigModel):
  chop: AnalysisRegimeChopConfig = Field(default_factory=AnalysisRegimeChopConfig)


class AnalysisLevelsConfig(FrozenConfigModel):
  equal_tol_atr: float = config_field(0.15,
    item_id='python.settings.equal_tol_atr',
    legacy_attr='equal_tol_atr',
    env='EQUAL_TOL_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy equal_tol_atr configuration mapped to analysis.levels.equal_tol_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.15),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  level_cluster_atr: float = config_field(0.5,
    item_id='python.settings.level_cluster_atr',
    legacy_attr='level_cluster_atr',
    env='LEVEL_CLUSTER_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy level_cluster_atr configuration mapped to analysis.levels.level_cluster_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  minimum_key_touches: int = config_field(2,
    item_id='python.settings.key_level_min_touches',
    legacy_attr='key_level_min_touches',
    env='KEY_LEVEL_MIN_TOUCHES',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.COUNT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy key_level_min_touches configuration mapped to analysis.levels.minimum_key_touches.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  round_step: float = config_field(5.0,
    item_id='python.settings.round_step',
    legacy_attr='round_step',
    env='ROUND_STEP',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PRICE,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy round_step configuration mapped to analysis.levels.round_step.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 5.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisTriggersM1Config(FrozenConfigModel):
  patterns: str = config_field('wick_rejection,body_close,strong_close,pin_bar,engulfing,hammer',
    item_id='python.settings.m1_trigger_patterns',
    legacy_attr='m1_trigger_patterns',
    env='M1_TRIGGER_PATTERNS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy m1_trigger_patterns configuration mapped to analysis.triggers.m1.patterns.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 'wick_rejection,body_close,strong_close,pin_bar,engulfing,hammer'),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  strong_close_pct: float = config_field(0.2,
    item_id='python.settings.m1_trigger_strong_close_pct',
    legacy_attr='m1_trigger_strong_close_pct',
    env='M1_TRIGGER_STRONG_CLOSE_PCT',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy m1_trigger_strong_close_pct configuration mapped to analysis.triggers.m1.strong_close_pct.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  wick_fraction: float = config_field(0.5,
    item_id='python.settings.m1_trigger_wick_fraction',
    legacy_attr='m1_trigger_wick_fraction',
    env='M1_TRIGGER_WICK_FRACTION',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy m1_trigger_wick_fraction configuration mapped to analysis.triggers.m1.wick_fraction.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisTriggersConfig(FrozenConfigModel):
  m1: AnalysisTriggersM1Config = Field(default_factory=AnalysisTriggersM1Config)


class AnalysisMomentumConfig(FrozenConfigModel):
  body_frac: float = config_field(0.6,
    item_id='python.settings.momentum_body_frac',
    legacy_attr='momentum_body_frac',
    env='MOMENTUM_BODY_FRAC',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy momentum_body_frac configuration mapped to analysis.momentum.body_frac.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.6),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  lookback: int = config_field(8,
    item_id='python.settings.momentum_lookback',
    legacy_attr='momentum_lookback',
    env='MOMENTUM_LOOKBACK',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy momentum_lookback configuration mapped to analysis.momentum.lookback.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 8),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisRangesConfig(FrozenConfigModel):
  lookback: int = config_field(50,
    item_id='python.settings.range_lookback',
    legacy_attr='range_lookback',
    env='RANGE_LOOKBACK',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy range_lookback configuration mapped to analysis.ranges.lookback.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 50),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisReactionsConfig(FrozenConfigModel):
  max_atr: float = config_field(0.5,
    item_id='python.settings.reaction_max_atr',
    legacy_attr='reaction_max_atr',
    env='REACTION_MAX_ATR',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy reaction_max_atr configuration mapped to analysis.reactions.max_atr.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisLiquiditySweepConfig(FrozenConfigModel):
  body_frac: float = config_field(0.5,
    item_id='python.settings.sweep_body_frac',
    legacy_attr='sweep_body_frac',
    env='SWEEP_BODY_FRAC',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.FRACTION,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy sweep_body_frac configuration mapped to analysis.liquidity.sweep.body_frac.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.5),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  react_bars: int = config_field(3,
    item_id='python.settings.sweep_react_bars',
    legacy_attr='sweep_react_bars',
    env='SWEEP_REACT_BARS',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy sweep_react_bars configuration mapped to analysis.liquidity.sweep.react_bars.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisLiquidityConfig(FrozenConfigModel):
  sweep: AnalysisLiquiditySweepConfig = Field(default_factory=AnalysisLiquiditySweepConfig)


class AnalysisSwingsZigzagConfig(FrozenConfigModel):
  atr_mult: float = config_field(1.0,
    item_id='python.settings.zigzag_atr_mult',
    legacy_attr='zigzag_atr_mult',
    env='ZIGZAG_ATR_MULT',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.ATR,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy zigzag_atr_mult configuration mapped to analysis.swings.zigzag.atr_mult.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 1.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  pct: float = config_field(0.0,
    item_id='python.settings.zigzag_pct',
    legacy_attr='zigzag_pct',
    env='ZIGZAG_PCT',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.PERCENT,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy zigzag_pct configuration mapped to analysis.swings.zigzag.pct.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 0.0),
    ),
    validation_summary='Pydantic required/type coercion only',
  )


class AnalysisSwingsConfig(FrozenConfigModel):
  fractal_size: int = config_field(2,
    item_id='python.settings.swing_fractal_n',
    legacy_attr='swing_fractal_n',
    env='SWING_FRACTAL_N',
    owner=ConfigOwner.PYTHON,
    reload=ReloadPolicy.NEXT_SCANNER_CYCLE,
    runtime_reload=ReloadPolicy.RESTART,
    unit=ConfigUnit.BARS,
    risk=RiskClassification.ANALYSIS_BEHAVIOR,
    description='Legacy swing_fractal_n configuration mapped to analysis.swings.fractal_size.',
    default_contexts=(
      ContextDefault(DefaultContext.PYTHON_SCHEMA, 2),
    ),
    validation_summary='Pydantic required/type coercion only',
  )
  zigzag: AnalysisSwingsZigzagConfig = Field(default_factory=AnalysisSwingsZigzagConfig)


class AnalysisConfig(FrozenConfigModel):
  atr: AnalysisAtrConfig = Field(default_factory=AnalysisAtrConfig)
  breakout: AnalysisBreakoutConfig = Field(default_factory=AnalysisBreakoutConfig)
  detectors: AnalysisDetectorsConfig = Field(default_factory=AnalysisDetectorsConfig)
  displacement: AnalysisDisplacementConfig = Field(default_factory=AnalysisDisplacementConfig)
  levels: AnalysisLevelsConfig = Field(default_factory=AnalysisLevelsConfig)
  liquidity: AnalysisLiquidityConfig = Field(default_factory=AnalysisLiquidityConfig)
  market_map: AnalysisMarketMapConfig = Field(default_factory=AnalysisMarketMapConfig)
  measurements: AnalysisMeasurementsConfig = Field(default_factory=AnalysisMeasurementsConfig)
  momentum: AnalysisMomentumConfig = Field(default_factory=AnalysisMomentumConfig)
  ranges: AnalysisRangesConfig = Field(default_factory=AnalysisRangesConfig)
  reactions: AnalysisReactionsConfig = Field(default_factory=AnalysisReactionsConfig)
  regime: AnalysisRegimeConfig = Field(default_factory=AnalysisRegimeConfig)
  swings: AnalysisSwingsConfig = Field(default_factory=AnalysisSwingsConfig)
  trendlines: AnalysisTrendlinesConfig = Field(default_factory=AnalysisTrendlinesConfig)
  triggers: AnalysisTriggersConfig = Field(default_factory=AnalysisTriggersConfig)
  zones: AnalysisZonesConfig = Field(default_factory=AnalysisZonesConfig)
