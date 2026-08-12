"""Pure price-action setup detectors for replayable scanner decisions."""

from dataclasses import dataclass, field, replace
import logging
import math
from typing import Callable, Protocol

import pandas as pd

from app.analysis.engine import AnalysisContext, AnalysisSettings, Regime, analyze
from app.analysis.indicators import atr as atr_indicator
from app.analysis.key_level_role import (
  ROLE_AMBIGUOUS,
  ROLE_BROKEN_RESISTANCE,
  ROLE_BROKEN_SUPPORT,
  ROLE_RESISTANCE,
  ROLE_SUPPORT,
  classify_key_level_role,
)
from app.analysis.types import DealingRange, Grab, Pool, SessionLevel
from app.analysis.regime import BoxBreak, displacement_grade
from app.analysis.scalp_ranges import ScalpBarrier, ScalpRange
from app.analysis.structure import (
  Level,
  Swing,
  Zone,
  entry_zone,
  equal_highs_lows,
  find_retest,
  fvg,
  key_levels,
  market_structure,
  order_blocks,
  swings,
)
from app.analysis.trendlines import Trendline, value_at
from app.analysis.execution_eligibility import ExecutionEligibility
from app.analysis.structural_reaction_support import (
  bias_relationship,
  box_structural_id,
  equal_level_structural_id,
  evaluate_structural_reaction,
  key_level_structural_id,
  session_level_structural_id,
  trendline_structural_id,
  zone_structural_id,
)
from app.analysis.zones import score_zones
from app.analysis.technique_detectors import (
  confluence_zone_reaction,
  crt_technique_reaction,
  fvg_technique_reaction,
  ifvg_technique_reaction,
  order_block_technique_reaction,
  supply_demand_technique_reaction,
)

log = logging.getLogger(__name__)

_EPS = 1e-9
_BUY_ZONE_SIDE = "de" + "mand"
STAR_THREE_SCORE = 12.0
STAR_TWO_SCORE = 8.0
COIL_SCORE = 1.5
REACTION_MAX_ATR = 1.0


@dataclass(frozen=True)
class IndicatorSet:
  atr: pd.Series


@dataclass(frozen=True)
class StructureSet:
  swings: list[Swing]
  bias: str
  levels: list[Level]
  equal_levels: list[Level]
  fvg_zones: list[Zone]
  order_blocks: list[Zone]
  breaks: list = field(default_factory=list)
  zones: list[Zone] = field(default_factory=list)
  liquidity_pools: list = field(default_factory=list)
  liquidity_grabs: list = field(default_factory=list)
  momentum: str = "neutral"
  session_levels: list[SessionLevel] = field(default_factory=list)
  dealing_range: DealingRange | None = None
  trendlines: list[Trendline] = field(default_factory=list)
  box_break: BoxBreak | None = None
  scalp_barriers: list[ScalpBarrier] = field(default_factory=list)
  scalp_range: ScalpRange | None = None
  regime: Regime | None = None


@dataclass(frozen=True)
class DetectorSettings:
  confluence_floor: int = 2
  max_entry_atr: float = 2.0
  max_zone_width_atr: float = 1.5
  proximal_band_atr: float = 0.5
  range_lookback: int = 50
  snap_atr_mult: float = 1.5
  atr_length: int = 14
  swing_fractal_n: int = 2
  zigzag_pct: float = 0.0
  zigzag_atr_mult: float = 1.0
  displacement_atr_mult: float = 1.5
  zone_width: str = "body"
  zone_merge_overlap: float = 0.5
  max_merged_zone_atr: float = 3.0
  equal_tol_atr: float = 0.15
  level_cluster_atr: float = 0.5
  round_step: float = 5.0
  key_level_min_touches: int = 2
  momentum_lookback: int = 8
  momentum_body_frac: float = 0.6
  session_asia_start: int = 22
  session_london_start: int = 7
  session_ny_start: int = 13
  daily_rollover_utc_hour: int = 21
  eq_band: float = 0.10
  strict_pd_gate: bool = False
  sweep_body_frac: float = 0.5
  sweep_react_bars: int = 3
  inducement_band_atr: float = 0.3
  chop_filter_enabled: bool = True
  chop_range_atr: float = 4.0
  chop_lookback: int = 24
  chop_edge_frac: float = 0.25
  tl_min_touches: int = 3
  tl_tol_atr: float = 0.3
  tl_max_slope_atr: float = 0.15
  coil_contract: float = 0.8
  breakout_buffer_atr: float = 0.1
  breakout_accept_bars: int = 2
  breakout_max_age_bars: int = 6
  allow_counter_trend: bool = True
  counter_min_zone_score: float = 10.0
  counter_extreme_pd: float = 0.25
  counter_level_min_touches: int = 3
  range_scalp_enabled: bool = True
  range_scalp_lookback: int = 48
  range_scalp_cluster_atr: float = 0.25
  range_scalp_min_touches: int = 2
  range_scalp_min_wick_frac: float = 0.25
  range_scalp_entry_tol_atr: float = 0.25
  range_scalp_min_width_atr: float = 1.0
  range_scalp_max_width_atr: float = 6.0
  range_scalp_min_room_atr: float = 0.75
  range_scalp_break_closes: int = 2
  range_scalp_min_wick_rejections: int = 1
  range_scalp_allow_rejection_only: bool = True
  zone_reconcile_enabled: bool = True
  zone_reconcile_mode: str = "enforce"
  regime_direction_enabled: bool = False
  regime_direction_lookback: int = 120
  regime_min_directional_swings: int = 3
  regime_min_displacement_atr: float = 4.0
  structural_reaction_lookback_bars: int = 3
  key_level_reaction_enabled: bool = True
  demand_reaction_enabled: bool = True
  supply_reaction_enabled: bool = True
  flip_zone_enabled: bool = True
  session_level_reaction_enabled: bool = True
  trendline_reaction_enabled: bool = True
  # Technique math publishers (feat/technique-math-strategies):
  technique_sd_enabled: bool = True
  technique_ob_enabled: bool = True
  technique_fvg_enabled: bool = True
  technique_ifvg_enabled: bool = True
  technique_crt_enabled: bool = True
  confluence_zone_enabled: bool = True
  zone_reaction_fallback_enabled: bool = False
  crt_min_atr: float = 1.5
  crt_reclaim_bars: int = 6
  fvg_max_atr: float = 2.0
  # Recovery mission (2026-07-30): these six sources were live around
  # 2026-07-28 and were deliberately dropped from DEFAULT_DETECTORS during
  # the P0 zone/M1 simplification without their own enable flags, leaving
  # no way to bring any one of them back individually. Registered in
  # LIVE_DETECTOR_REGISTRY below with an explicit replay_only_reason and
  # default False - reusing existing code, unlike key_level/demand/supply/
  # session_level/trendline (which already went through structural
  # confirmation via evaluate_structural_reaction), these use bespoke
  # confirmation logic that has not been re-verified against the current
  # pipeline (band-kind classification, canonical family merge).
  #
  # 2026-07-31: trend_pullback/snap_back/fade_scalp retrofitted onto the
  # shared evaluate_structural_reaction path (they already had a real M5
  # rejection/reaction gate, just never populated the confirmation
  # metadata the legacy pipeline needs to treat M1 as optional instead of
  # a hard, unconditional gate) and re-enabled below. box_breakout/
  # break_retest remain replay-only (bespoke confirmation still unverified).
  # momentum_ride is live: impulse/continuation with its own confirmation
  # policy (not the reversal-shaped M1 gate).
  box_breakout_enabled: bool = False
  trend_pullback_enabled: bool = True
  break_retest_enabled: bool = False
  momentum_ride_enabled: bool = True
  snap_back_enabled: bool = True
  fade_scalp_enabled: bool = True

  def analysis_settings(self) -> AnalysisSettings:
    return AnalysisSettings(
      atr_length=self.atr_length,
      swing_fractal_n=self.swing_fractal_n,
      zigzag_pct=self.zigzag_pct,
      zigzag_atr_mult=self.zigzag_atr_mult,
      displacement_atr_mult=self.displacement_atr_mult,
      zone_width=self.zone_width,
      zone_merge_overlap=self.zone_merge_overlap,
      max_merged_zone_atr=self.max_merged_zone_atr,
      equal_tol_atr=self.equal_tol_atr,
      level_cluster_atr=self.level_cluster_atr,
      round_step=self.round_step,
      key_level_min_touches=self.key_level_min_touches,
      momentum_lookback=self.momentum_lookback,
      momentum_body_frac=self.momentum_body_frac,
      session_asia_start=self.session_asia_start,
      session_london_start=self.session_london_start,
      session_ny_start=self.session_ny_start,
      daily_rollover_utc_hour=self.daily_rollover_utc_hour,
      eq_band=self.eq_band,
      sweep_body_frac=self.sweep_body_frac,
      sweep_react_bars=self.sweep_react_bars,
      inducement_band_atr=self.inducement_band_atr,
      chop_filter_enabled=self.chop_filter_enabled,
      chop_range_atr=self.chop_range_atr,
      chop_lookback=self.chop_lookback,
      tl_min_touches=self.tl_min_touches,
      tl_tol_atr=self.tl_tol_atr,
      tl_max_slope_atr=self.tl_max_slope_atr,
      coil_contract=self.coil_contract,
      breakout_buffer_atr=self.breakout_buffer_atr,
      breakout_accept_bars=self.breakout_accept_bars,
      breakout_max_age_bars=self.breakout_max_age_bars,
      range_scalp_lookback=self.range_scalp_lookback,
      range_scalp_cluster_atr=self.range_scalp_cluster_atr,
      range_scalp_min_touches=self.range_scalp_min_touches,
      range_scalp_min_wick_frac=self.range_scalp_min_wick_frac,
      range_scalp_entry_tol_atr=self.range_scalp_entry_tol_atr,
      range_scalp_min_width_atr=self.range_scalp_min_width_atr,
      range_scalp_max_width_atr=self.range_scalp_max_width_atr,
      range_scalp_min_room_atr=self.range_scalp_min_room_atr,
      range_scalp_break_closes=self.range_scalp_break_closes,
      zone_reconcile_enabled=self.zone_reconcile_enabled,
      zone_reconcile_mode=self.zone_reconcile_mode,
      regime_direction_enabled=self.regime_direction_enabled,
      regime_direction_lookback=self.regime_direction_lookback,
      regime_min_directional_swings=self.regime_min_directional_swings,
      regime_min_displacement_atr=self.regime_min_displacement_atr,
    )


def detector_settings_from(config: object | None = None) -> DetectorSettings:
  """Build detector settings from the app config for every PA consumer.

  ``config`` defaults to the authority-neutral canonical ``runtime_config`` so
  production callers never depend on the legacy Settings singleton. Tests may
  inject a canonical-shaped override (``ApexVoidConfig`` /
  ``LegacyCanonicalConfigView`` / any object exposing the canonical grouped
  paths). The lazy import keeps this module's import-time decoupling from
  ``app.core.config`` intact.
  """
  if config is None:
    from app.core.config import runtime_config
    config = runtime_config
  analysis = config.analysis
  strategies = config.strategies
  market_data = config.market_data
  actionability = config.actionability
  execution = config.execution
  # ``strategies.zone.flip.enabled`` is algorithm-owned without ENV binding, so the
  # LegacyCanonicalConfigView cannot expose it under the legacy authority.
  # Preserve the pre-migration default (True) in that path.
  try:
    flip_zone_enabled = bool(strategies.zone.flip.enabled)
  except AttributeError:
    flip_zone_enabled = True
  return DetectorSettings(
    confluence_floor=market_data.scanner.confluence_floor,
    max_entry_atr=actionability.gates.max_entry_atr,
    range_lookback=analysis.ranges.lookback,
    atr_length=analysis.atr.length,
    swing_fractal_n=analysis.swings.fractal_size,
    zigzag_pct=analysis.swings.zigzag.pct,
    zigzag_atr_mult=analysis.swings.zigzag.atr_mult,
    displacement_atr_mult=analysis.displacement.atr_mult,
    zone_width=analysis.zones.width,
    zone_merge_overlap=analysis.zones.merge_overlap,
    max_merged_zone_atr=analysis.measurements.max_merged_zone_atr,
    equal_tol_atr=analysis.levels.equal_tol_atr,
    level_cluster_atr=analysis.levels.level_cluster_atr,
    round_step=analysis.levels.round_step,
    key_level_min_touches=analysis.levels.minimum_key_touches,
    momentum_lookback=analysis.momentum.lookback,
    momentum_body_frac=analysis.momentum.body_frac,
    session_asia_start=market_data.sessions.asia_start,
    session_london_start=market_data.sessions.london_start,
    session_ny_start=market_data.sessions.ny_start,
    daily_rollover_utc_hour=market_data.sessions.daily_rollover_utc_hour,
    eq_band=analysis.measurements.eq_band,
    strict_pd_gate=(
      bool(analysis.measurements.strict_pd_gate)
      or (
        bool(getattr(getattr(execution, "technique", None), "enforce", True))
        and bool(
          getattr(
            getattr(execution, "technique", None),
            "strict_premium_discount",
            True,
          ),
        )
      )
    ),
    sweep_body_frac=analysis.liquidity.sweep.body_frac,
    sweep_react_bars=analysis.liquidity.sweep.react_bars,
    inducement_band_atr=analysis.measurements.inducement_band_atr,
    max_zone_width_atr=analysis.zones.discovery.maximum_width_atr,
    proximal_band_atr=actionability.gates.proximal_band_atr,
    chop_filter_enabled=analysis.regime.chop.filter_enabled,
    chop_range_atr=analysis.regime.chop.range_atr,
    chop_lookback=analysis.regime.chop.lookback,
    chop_edge_frac=analysis.regime.chop.edge_frac,
    tl_min_touches=analysis.trendlines.minimum_touches,
    tl_tol_atr=analysis.trendlines.tolerance_atr,
    tl_max_slope_atr=analysis.trendlines.maximum_slope_atr,
    coil_contract=analysis.measurements.coil_contract,
    breakout_buffer_atr=analysis.breakout.buffer_atr,
    breakout_accept_bars=analysis.breakout.accept_bars,
    breakout_max_age_bars=analysis.breakout.max_age_bars,
    allow_counter_trend=strategies.counter_trend.allow_counter_trend,
    counter_min_zone_score=strategies.counter_trend.min_zone_score,
    counter_extreme_pd=strategies.counter_trend.extreme_pd,
    counter_level_min_touches=strategies.counter_trend.level_min_touches,
    range_scalp_enabled=strategies.range_reversion.range_edge.enabled,
    range_scalp_lookback=strategies.range_reversion.range_edge.lookback,
    range_scalp_cluster_atr=strategies.range_reversion.range_edge.cluster_atr,
    range_scalp_min_touches=strategies.range_reversion.range_edge.min_touches,
    range_scalp_min_wick_frac=strategies.range_reversion.range_edge.min_wick_frac,
    range_scalp_entry_tol_atr=strategies.range_reversion.range_edge.entry_tol_atr,
    range_scalp_min_width_atr=strategies.range_reversion.range_edge.min_width_atr,
    range_scalp_max_width_atr=strategies.range_reversion.range_edge.max_width_atr,
    range_scalp_min_room_atr=strategies.range_reversion.range_edge.min_room_atr,
    range_scalp_break_closes=strategies.range_reversion.range_edge.break_closes,
    range_scalp_min_wick_rejections=strategies.range_reversion.range_edge.min_wick_rejections,
    range_scalp_allow_rejection_only=strategies.range_reversion.range_edge.allow_rejection_only,
    zone_reconcile_enabled=actionability.zone_reconciliation.enabled,
    zone_reconcile_mode=actionability.zone_reconciliation.mode,
    regime_direction_enabled=execution.regime.direction_enabled,
    regime_direction_lookback=execution.regime.direction_lookback,
    regime_min_directional_swings=execution.regime.min_directional_swings,
    regime_min_displacement_atr=execution.regime.min_displacement_atr,
    structural_reaction_lookback_bars=int(
      execution.policy.structural_reaction_lookback_bars
    ),
    key_level_reaction_enabled=bool(strategies.reaction.key_level.enabled),
    demand_reaction_enabled=bool(strategies.reaction.demand.enabled),
    supply_reaction_enabled=bool(strategies.reaction.supply.enabled),
    flip_zone_enabled=flip_zone_enabled,
    session_level_reaction_enabled=bool(strategies.reaction.session_level.enabled),
    trendline_reaction_enabled=bool(strategies.reaction.trendline.enabled),
    technique_sd_enabled=bool(strategies.technique.sd.enabled),
    technique_ob_enabled=bool(strategies.technique.ob.enabled),
    technique_fvg_enabled=bool(strategies.technique.fvg.enabled),
    technique_ifvg_enabled=bool(strategies.technique.ifvg.enabled),
    technique_crt_enabled=bool(strategies.technique.crt.enabled),
    confluence_zone_enabled=bool(strategies.technique.confluence.enabled),
    zone_reaction_fallback_enabled=bool(
      strategies.technique.zone_reaction_fallback.enabled
    ),
    crt_min_atr=float(strategies.technique.crt.min_atr),
    crt_reclaim_bars=int(strategies.technique.crt.reclaim_bars),
    fvg_max_atr=float(strategies.technique.fvg.max_atr),
    box_breakout_enabled=bool(strategies.selection.box_breakout_enabled),
    trend_pullback_enabled=bool(strategies.trend.pullback_enabled),
    break_retest_enabled=bool(strategies.breakout.break_retest_enabled),
    momentum_ride_enabled=bool(strategies.selection.momentum_ride_enabled),
    snap_back_enabled=bool(strategies.selection.snap_back_enabled),
    fade_scalp_enabled=bool(strategies.scalp.fade_scalp_enabled),
  )


@dataclass(frozen=True)
class DetectionContext:
  symbol: str
  tf: str
  frames: dict[str, pd.DataFrame]
  indicators: dict[str, IndicatorSet]
  structures: dict[str, StructureSet]
  htf_bias: str
  settings: DetectorSettings
  session_ok: bool = True
  spot_price: float | None = None
  spot_ts: int | None = None
  trigger_ts: str | None = None
  regime: Regime | None = None
  analysis: AnalysisContext | None = None


@dataclass(frozen=True)
class DetectionResult:
  setup: str
  direction: str
  key_level: float
  entry_zone: Zone
  current_price: float
  confluence: int
  reasons: list[str]
  mode: str = "with_trend"
  confirmation: str | None = None
  # First-class structural identity (scanner reactions).
  structural_source: str | None = None
  structural_id: str | None = None
  structural_low: float | None = None
  structural_high: float | None = None
  structural_timeframe: str | None = None
  structural_kind: str | None = None
  confirmation_type: str | None = None
  confirmation_bar_ts: str | None = None
  touch_bar_ts: str | None = None
  source_touches: int | None = None
  source_score: float | None = None
  bias_relationship: str | None = None
  # Detection/card identity after same-side structural members are merged.
  # Additive so direct detector tests and non-structural setups keep their
  # existing construction and behavior.
  confluence_zone_id: str | None = None
  confluence_tags: tuple[str, ...] = ()
  # Pure pre-lifecycle actionability annotations. They never create state;
  # scanner fills planned entry/targets before cross-side resolution.
  key_level_role: str | None = None
  planned_entry_price: float | None = None
  provisional_targets_pips: tuple[int, ...] = ()
  target_cap_pips: float | None = None
  target_room_measured: dict[str, object] | None = None
  execution_eligibility: ExecutionEligibility | None = None


class SetupDetector(Protocol):
  def __call__(self, ctx: DetectionContext) -> DetectionResult | None:
    ...


# The top structural-context timeframe (htf_order's primary entry - H1 for
# the scanner's own scanner_htf="H1,M15") needs enough closed bars before its
# swing/structure read is trustworthy. Fewer than this and _htf_bias() would
# just be guessing from noise, so bias must fail closed to "unknown" (not
# "up"/"down", and deliberately distinct from the legitimate "range" state)
# until warmup completes - setups gated on ctx.htf_bias == "up"/"down" then
# correctly do not form. ~50 H1 closes is a little over two days of data.
_MIN_PRIMARY_HTF_WARMUP_BARS = 50


def build_context(
  symbol: str,
  tf: str,
  frames: dict[str, pd.DataFrame],
  settings: DetectorSettings,
  htf_order: list[str],
) -> DetectionContext:
  analysis_ctx = analyze(frames, settings.analysis_settings(), htf_order)
  indicator_sets = {
    name: _indicator_set(df, settings.atr_length)
    for name, df in frames.items()
  }
  structure_sets = _structure_sets_from_analysis(analysis_ctx.per_tf)
  htf_bias = analysis_ctx.htf_bias
  if htf_order:
    primary_df = frames.get(htf_order[0].upper())
    if primary_df is None or len(primary_df) < _MIN_PRIMARY_HTF_WARMUP_BARS:
      htf_bias = "unknown"
  return DetectionContext(
    symbol=symbol,
    tf=tf,
    frames=frames,
    indicators=indicator_sets,
    structures=structure_sets,
    htf_bias=htf_bias,
    settings=settings,
    regime=_exec_regime(analysis_ctx, tf),
    analysis=analysis_ctx,
  )


def _indicator_set(df: pd.DataFrame, length: int = 14) -> IndicatorSet:
  return IndicatorSet(atr=atr_indicator(df, length))


def _structure_set(df: pd.DataFrame) -> StructureSet:
  ctx = analyze({"_": df})
  if "_" in ctx.per_tf:
    return _structure_sets_from_analysis(ctx.per_tf)["_"]
  items = swings(df, 2, 2)
  return StructureSet(
    swings=items,
    bias=market_structure(items),
    levels=key_levels(df),
    equal_levels=equal_highs_lows(df),
    fvg_zones=fvg(df),
    order_blocks=order_blocks(df),
  )


def _structure_sets_from_analysis(items) -> dict[str, StructureSet]:
  result = {}
  for name, item in items.items():
    equal_levels = [
      Level(
        pool.level,
        "equal_high" if pool.side == "buy" else "equal_low",
        pool.touches,
        pool.band,
        float(pool.touches),
      )
      for pool in item.liquidity_pools
      if pool.touches >= 2
    ]
    result[name] = StructureSet(
      swings=item.swings,
      bias=item.structure,
      levels=item.key_levels,
      equal_levels=equal_levels,
      fvg_zones=item.fvg_zones,
      order_blocks=item.order_blocks,
      breaks=item.breaks,
      zones=item.zones,
      liquidity_pools=item.liquidity_pools,
      liquidity_grabs=item.liquidity_grabs,
      momentum=item.momentum,
      session_levels=item.session_levels,
      dealing_range=item.dealing_range,
      trendlines=item.trendlines,
      box_break=item.box_break,
      scalp_barriers=item.scalp_barriers,
      scalp_range=item.scalp_range,
      regime=item.regime,
    )
  return result


def _exec_regime(analysis_ctx, tf: str) -> Regime | None:
  item = analysis_ctx.per_tf.get(tf.upper())
  if item is not None:
    return item.regime
  return analysis_ctx.regime


def _exec(ctx: DetectionContext) -> tuple[pd.DataFrame, IndicatorSet, StructureSet]:
  return (
    ctx.frames[ctx.tf],
    ctx.indicators[ctx.tf],
    ctx.structures[ctx.tf],
  )


def _direction(ctx: DetectionContext) -> str | None:
  local_bias = ctx.structures[ctx.tf].bias
  directional_bias = (
    local_bias
    if ctx.settings.allow_counter_trend and local_bias in {"up", "down"}
    else ctx.htf_bias
  )
  if directional_bias == "up":
    return "BUY"
  if directional_bias == "down":
    return "SELL"
  return None


def _bias_for_direction(direction: str) -> str:
  return "up" if direction == "BUY" else "down"


def _last(series: pd.Series, default: float = 0.0) -> float:
  clean = series.dropna()
  value = float(clean.iloc[-1]) if not clean.empty else default
  return value if math.isfinite(value) and value > 0 else default


def _atr(ind: IndicatorSet, fallback: float = 1.0) -> float:
  return _last(ind.atr, fallback)


def _current_price(ctx: DetectionContext, df: pd.DataFrame) -> float:
  if ctx.spot_price is not None and math.isfinite(float(ctx.spot_price)):
    return float(ctx.spot_price)
  return float(df["close"].iloc[-1])


def _nearest_level(
  levels: list[Level],
  price: float,
  direction: str,
) -> Level | None:
  if not levels:
    return None
  if direction == "BUY":
    candidates = [level for level in levels if level.price <= price + _EPS]
  else:
    candidates = [level for level in levels if level.price >= price - _EPS]
  if not candidates:
    return None
  return min(candidates, key=lambda level: abs(level.price - price))


def _level_valid(level: float, price: float, direction: str) -> bool:
  if direction == "BUY":
    return level <= price + _EPS
  return level >= price - _EPS


def _entry_valid(zone: Zone, price: float, atr: float, direction: str) -> bool:
  max_distance = max(0.0, atr) * 2.0
  if direction == "SELL":
    if price > zone.high + _EPS:
      return False
    distance = 0.0 if zone.low <= price <= zone.high else zone.low - price
  else:
    if price < zone.low - _EPS:
      return False
    distance = 0.0 if zone.low <= price <= zone.high else price - zone.high
  return distance <= max_distance + _EPS


def _entry_valid_for_settings(
  zone: Zone,
  price: float,
  atr: float,
  direction: str,
  settings: DetectorSettings,
) -> bool:
  max_distance = max(0.0, atr) * max(0.0, settings.max_entry_atr)
  if direction == "SELL":
    if price > zone.high + _EPS:
      return False
    distance = 0.0 if zone.low <= price <= zone.high else zone.low - price
  else:
    if price < zone.low - _EPS:
      return False
    distance = 0.0 if zone.low <= price <= zone.high else price - zone.high
  return distance <= max_distance + _EPS


def _rejection(df: pd.DataFrame, direction: str) -> bool:
  if df.empty:
    return False
  row = df.iloc[-1]
  open_ = float(row["open"])
  high = float(row["high"])
  low = float(row["low"])
  close = float(row["close"])
  candle_range = high - low
  if candle_range <= 0:
    return False
  body = abs(close - open_)
  upper = high - max(open_, close)
  lower = min(open_, close) - low
  lower_third = low + candle_range / 3
  upper_third = high - candle_range / 3
  if direction == "SELL":
    return upper >= body and close < open_ and close <= lower_third
  return lower >= body and close > open_ and close >= upper_third


def _strong_body_break(df: pd.DataFrame, st: StructureSet, direction: str, body_frac: float) -> bool:
  if df.empty:
    return False
  row = df.iloc[-1]
  open_ = float(row["open"])
  high = float(row["high"])
  low = float(row["low"])
  close = float(row["close"])
  candle_range = high - low
  if candle_range <= 0:
    return False
  body_ok = abs(close - open_) >= max(0.0, body_frac) * candle_range
  direction_ok = close > open_ if direction == "BUY" else close < open_
  if not (body_ok and direction_ok):
    return False
  if direction == "BUY":
    highs = [s.price for s in st.swings if s.kind == "high"]
    return not highs or close > highs[-1]
  lows = [s.price for s in st.swings if s.kind == "low"]
  return not lows or close < lows[-1]


def _candidate_zones(st: StructureSet, direction: str) -> list[Zone]:
  side = _BUY_ZONE_SIDE if direction == "BUY" else "supply"
  seen: set[tuple[float, float, str]] = set()
  zones = []
  for zone in [*st.zones, *st.order_blocks]:
    if zone.side != side:
      continue
    key = (round(zone.low, 6), round(zone.high, 6), zone.source)
    if key in seen:
      continue
    seen.add(key)
    zones.append(zone)
  return zones


def _last_touches_zone(df: pd.DataFrame, zone: Zone) -> bool:
  if df.empty:
    return False
  row = df.iloc[-1]
  return float(row["low"]) <= zone.high and float(row["high"]) >= zone.low


def _best_valid_zone(
  zones: list[Zone],
  price: float,
  atr: float,
  direction: str,
  settings: DetectorSettings,
) -> tuple[Zone, bool] | None:
  valid = [
    zone for zone in zones
    if _entry_valid_for_settings(zone, price, atr, direction, settings)
  ]
  if not valid:
    return None
  zone = min(
    valid,
    key=lambda zone: (
      -float(getattr(zone, "score", 0.0)),
      _zone_distance(zone, price, direction),
      zone.low,
    ),
  )
  return _proximal_if_wide(zone, price, atr, direction, settings)


def _proximal_if_wide(
  zone: Zone,
  price: float,
  atr: float,
  direction: str,
  settings: DetectorSettings,
) -> tuple[Zone, bool]:
  width = zone.high - zone.low
  max_width = max(0.0, settings.max_zone_width_atr) * max(0.0, atr)
  if max_width <= 0 or width <= max_width:
    return zone, False
  band = max(_EPS, settings.proximal_band_atr * max(0.0, atr))
  if direction == "SELL":
    top = min(zone.high, zone.low + band)
    return replace(zone, bottom=zone.low, top=top), True
  bottom = max(zone.low, zone.high - band)
  return replace(zone, bottom=bottom, top=zone.high), True


def _add_proximal_reason(reasons: list[str], proximal: bool) -> list[str]:
  if not proximal:
    return reasons
  return [*reasons, "proximal of wide zone"]


def _zone_distance(zone: Zone, price: float, direction: str) -> float:
  if direction == "BUY":
    if zone.low <= price <= zone.high:
      return 0.0
    return abs(price - zone.high)
  if zone.low <= price <= zone.high:
    return 0.0
  return abs(zone.low - price)


def _zone_key(zone: Zone, price: float, direction: str) -> float:
  if direction == "BUY":
    return zone.high if zone.high <= price + _EPS else zone.low
  return zone.low if zone.low >= price - _EPS else zone.high


def _confirmation_direction(ctx: DetectionContext) -> str | None:
  direction = _direction(ctx)
  if direction is None:
    return None
  if ctx.settings.allow_counter_trend:
    return direction
  return direction if ctx.htf_bias == _bias_for_direction(direction) else None


def _pd_gate(
  st: StructureSet,
  direction: str,
  settings: DetectorSettings,
  *,
  ctx: "DetectionContext | None" = None,
  setup: str = "",
) -> bool:
  range_ = st.dealing_range
  if range_ is None:
    return True
  if range_.zone == "eq":
    return False
  if direction == "BUY":
    loose = range_.zone != "premium"
    strict = range_.zone == "discount"
  else:
    loose = range_.zone != "discount"
    strict = range_.zone == "premium"
  if settings.strict_pd_gate and loose and not strict and ctx is not None:
    # Diagnostic (2026-08-11): execution.technique.strict_premium_discount
    # narrows BUY to discount-only / SELL to premium-only with zero
    # telemetry anywhere - unlike every other gate in this pipeline, there
    # was no way to tell how many candidates this was actually costing.
    # Logs only the divergence case (loose would allow, strict rejects),
    # so a count of these lines is exactly the candidate volume strict
    # mode is removing. Does not change the returned decision.
    log.info(
      "pd gate strict-only rejection symbol=%s tf=%s setup=%s direction=%s "
      "zone=%s",
      ctx.symbol, ctx.tf, setup, direction, range_.zone,
    )
  return strict if settings.strict_pd_gate else loose


def _in_chop(ctx: DetectionContext) -> bool:
  return (
    ctx.settings.chop_filter_enabled
    and ctx.regime is not None
    and ctx.regime.kind == "chop"
  )


def _chop_edge_ok(ctx: DetectionContext, zone: Zone, direction: str) -> bool:
  if not _in_chop(ctx):
    return True
  regime_ = ctx.regime
  if regime_ is None:
    return False
  low = float(regime_.range_low)
  high = float(regime_.range_high)
  height = high - low
  if height <= _EPS:
    return False
  edge_frac = max(0.0, min(0.5, ctx.settings.chop_edge_frac))
  edge = height * edge_frac
  midpoint = (zone.low + zone.high) / 2
  if direction == "SELL":
    return midpoint >= high - edge - _EPS
  return midpoint <= low + edge + _EPS


def _chop_range_reason(ctx: DetectionContext) -> str | None:
  if not _in_chop(ctx) or ctx.regime is None:
    return None
  return f"range {_number(ctx.regime.range_low)}-{_number(ctx.regime.range_high)}"


@dataclass(frozen=True)
class ConfluenceFactors:
  """Named, independently-observable confluence factors, shared by every
  detector in ``DEFAULT_DETECTORS``. Used only when a detector's zone was
  synthesised rather than drawn from the scored zone engine (``zone.score``
  is then 0 and carries no confluence signal of its own) - see
  ``_confluence_from_zone``. Two detectors observing the same factor set
  must produce the same confluence, since a reader can't tell which
  detector produced a given star rating.
  """
  htf_aligned: bool = False
  touches: int = 0
  wick_rejection: bool = False
  displacement_grade: bool = False
  session_context: bool = False
  structural_agreement: bool = False


_FACTOR_HTF_ALIGN_WEIGHT = 4.0
_FACTOR_TOUCH_UNIT_WEIGHT = 1.0
_FACTOR_TOUCH_CAP = 3
_FACTOR_WICK_REJECTION_WEIGHT = 3.0
_FACTOR_DISPLACEMENT_WEIGHT = 3.0
_FACTOR_SESSION_CONTEXT_WEIGHT = 2.0
_FACTOR_STRUCTURAL_AGREEMENT_WEIGHT = 3.0


def _confluence_from_factors(factors: ConfluenceFactors) -> int:
  score = (
    (_FACTOR_HTF_ALIGN_WEIGHT if factors.htf_aligned else 0.0)
    + min(max(0, factors.touches), _FACTOR_TOUCH_CAP) * _FACTOR_TOUCH_UNIT_WEIGHT
    + (_FACTOR_WICK_REJECTION_WEIGHT if factors.wick_rejection else 0.0)
    + (_FACTOR_DISPLACEMENT_WEIGHT if factors.displacement_grade else 0.0)
    + (_FACTOR_SESSION_CONTEXT_WEIGHT if factors.session_context else 0.0)
    + (
      _FACTOR_STRUCTURAL_AGREEMENT_WEIGHT
      if factors.structural_agreement else 0.0
    )
  )
  return 3 if score >= STAR_THREE_SCORE else 2 if score >= STAR_TWO_SCORE else 1


def _confluence_from_zone(
  zone: Zone,
  factors: ConfluenceFactors | None = None,
) -> int:
  score = float(getattr(zone, "score", 0.0))
  if score > 0:
    stars = 3 if score >= STAR_THREE_SCORE else 2 if score >= STAR_TWO_SCORE else 1
  else:
    stars = _confluence_from_factors(factors or ConfluenceFactors())
  if getattr(zone, "touches", 0) >= 1:
    stars = min(stars, 2)
  return max(1, stars)


def _merge_score_reasons(base: list[str], zone: Zone) -> list[str]:
  score_reasons = list(getattr(zone, "score_reasons", []) or [])
  if not score_reasons:
    return base[:]
  merged: list[str] = []
  inserted = False
  for reason in base:
    merged.append(reason)
    if not inserted and reason.lower().startswith("htf bias"):
      for score_reason in score_reasons:
        if score_reason not in merged:
          merged.append(score_reason)
      inserted = True
  if not inserted:
    for score_reason in score_reasons:
      if score_reason not in merged:
        merged.append(score_reason)
  return merged


def _finish(
  ctx: DetectionContext,
  setup: str,
  direction: str,
  level: float,
  zone: Zone,
  price: float,
  atr: float,
  reasons: list[str],
  mode: str = "with_trend",
  chop_tp_cap: bool = True,
  include_score_reasons: bool = True,
  factors: ConfluenceFactors | None = None,
  confirmation: str | None = None,
  *,
  structural_source: str | None = None,
  structural_id: str | None = None,
  structural_low: float | None = None,
  structural_high: float | None = None,
  structural_timeframe: str | None = None,
  structural_kind: str | None = None,
  confirmation_type: str | None = None,
  confirmation_bar_ts: str | None = None,
  touch_bar_ts: str | None = None,
  source_touches: int | None = None,
  source_score: float | None = None,
  bias_relationship: str | None = None,
) -> DetectionResult | None:
  if not _level_valid(level, price, direction):
    return None
  if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
    return None
  st = ctx.structures[ctx.tf]
  full_reasons = _merge_tp_anchor(
    ctx,
    reasons,
    st,
    price,
    direction,
    chop_tp_cap,
  )
  if include_score_reasons:
    full_reasons = _merge_score_reasons(full_reasons, zone)
  confluence = _confluence_from_zone(zone, factors)
  if confluence < ctx.settings.confluence_floor:
    return None
  return DetectionResult(
    setup=setup,
    direction=direction,
    key_level=float(level),
    entry_zone=zone,
    current_price=price,
    confluence=confluence,
    reasons=full_reasons,
    mode=mode,
    confirmation=confirmation,
    structural_source=structural_source,
    structural_id=structural_id,
    structural_low=structural_low,
    structural_high=structural_high,
    structural_timeframe=structural_timeframe or ctx.tf,
    structural_kind=structural_kind,
    confirmation_type=confirmation_type or confirmation,
    confirmation_bar_ts=confirmation_bar_ts,
    touch_bar_ts=touch_bar_ts,
    source_touches=source_touches,
    source_score=source_score,
    bias_relationship=bias_relationship,
  )


def _merge_tp_anchor(
  ctx: DetectionContext,
  reasons: list[str],
  st: StructureSet,
  price: float,
  direction: str,
  chop_tp_cap: bool = True,
) -> list[str]:
  if chop_tp_cap and _in_chop(ctx) and ctx.regime is not None:
    reasons = [
      reason for reason in reasons
      if not reason.startswith("TP anchor ")
    ]
    if direction == "BUY":
      edge_name = "range high"
      edge = ctx.regime.range_high
    else:
      edge_name = "range low"
      edge = ctx.regime.range_low
    return [*reasons, f"TP anchor {edge_name} {_number(edge)}"]

  anchor = _nearest_session_tp(st.session_levels, price, direction)
  if anchor is None:
    return reasons[:]
  reason = f"TP anchor {anchor.name}"
  if reason in reasons:
    return reasons[:]
  return [*reasons, reason]


def _nearest_session_tp(
  levels: list[SessionLevel],
  price: float,
  direction: str,
) -> SessionLevel | None:
  if direction == "BUY":
    candidates = [
      level for level in levels
      if not level.swept and _is_high_session_level(level.name) and level.price > price
    ]
  else:
    candidates = [
      level for level in levels
      if not level.swept and _is_low_session_level(level.name) and level.price < price
    ]
  if not candidates:
    return None
  return min(candidates, key=lambda level: abs(level.price - price))


def trend_pullback(ctx: DetectionContext) -> DetectionResult | None:
  """Recovery mission (2026-07-31): retrofitted onto the shared
  evaluate_structural_reaction confirmation path (same as demand/supply
  zone reactions - it draws from the identical st.zones/order_blocks
  pool), replacing the old bespoke _rejection(df, direction) check. That
  bespoke check never populated structural_id/touch_bar_ts/
  confirmation_bar_ts, which the legacy worker.py execution pipeline
  requires to treat M1 as optional (confirmation_policy_for.metadata_valid)
  - without it every setup would sit hard-gated behind an M1 pattern with
  no fallback, the same "M1 confirms everything" bug already fixed for
  the other reaction detectors.
  """
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  if _in_chop(ctx):
    return None
  direction = _confirmation_direction(ctx)
  htf_aligned = (
    direction is not None
    and ctx.htf_bias == _bias_for_direction(direction)
  )
  if (
    direction is None
    or not _pd_gate(st, direction, ctx.settings, ctx=ctx, setup="Trend Pullback")
    or (
      not ctx.settings.allow_counter_trend
      and st.bias != ctx.htf_bias
    )
  ):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  selected = _best_valid_zone(
    [
      zone for zone in _candidate_zones(st, direction)
      if _last_touches_zone(df, zone)
    ],
    price,
    atr,
    direction,
    ctx.settings,
  )
  if selected is None:
    return None
  zone, proximal = selected
  conf = evaluate_structural_reaction(
    df,
    direction=direction,
    low=float(zone.low),
    high=float(zone.high),
    lookback_bars=lookback,
    grabs=_zone_grabs_for(st, zone, direction),
    has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
  )
  if conf is None:
    return None
  level = _zone_key(zone, price, direction)
  reasons = [
    "with_bias" if htf_aligned else "counter_bias",
    "pullback into structure zone",
  ]
  reasons = _add_proximal_reason(reasons, proximal)
  if ctx.session_ok:
    reasons.append("session")
  factors = ConfluenceFactors(
    htf_aligned=htf_aligned,
    touches=zone.touches,
    wick_rejection=True,
    session_context=ctx.session_ok,
    structural_agreement=True,  # zone drawn from structural swing zones
  )
  return _structural_finish(
    ctx,
    setup="Trend Pullback",
    direction=direction,
    level=level,
    zone=zone,
    price=price,
    atr=atr,
    reasons=reasons,
    structural_source="supply_demand",
    structural_id=zone_structural_id(ctx.symbol, ctx.tf, zone),
    structural_low=float(zone.low),
    structural_high=float(zone.high),
    structural_kind="demand" if direction == "BUY" else "supply",
    confirmation=conf,
    source_touches=int(zone.touches),
    source_score=float(getattr(zone, "score", 0.0)),
    factors=factors,
  )


def break_retest(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  if _in_chop(ctx):
    return None
  direction = _confirmation_direction(ctx)
  if (
    direction is None
    or not _pd_gate(st, direction, ctx.settings, ctx=ctx, setup="Break & Retest")
    or not _rejection(df, direction)
  ):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  for line in sorted(
    st.trendlines,
    key=lambda item: abs(value_at(item, len(df) - 1) - price),
  ):
    if not _trendline_break_direction(line, direction):
      continue
    level_price = value_at(line, len(df) - 1)
    zone = _trendline_retest_zone(df, line, direction, atr, ctx.settings)
    if zone is None:
      continue
    reasons = [
      f"HTF bias {ctx.htf_bias}",
      "TL break+retest",
      f"TL {line.kind} ×{line.touches}",
      "retest rejection",
    ]
    result = _finish(
      ctx,
      "Break & Retest",
      direction,
      level_price,
      zone,
      price,
      atr,
      reasons,
      structural_source="trendline",
      structural_id=trendline_structural_id(ctx.symbol, ctx.tf, line),
      structural_low=zone.low,
      structural_high=zone.high,
      structural_timeframe=ctx.tf,
      structural_kind=line.kind,
    )
    if result is not None:
      return result
  levels = sorted(st.levels, key=lambda item: abs(item.price - price))
  for level in levels:
    if not _level_valid(level.price, price, direction):
      continue
    zone = find_retest(
      df,
      level.price,
      min_consecutive_closes=ctx.settings.breakout_accept_bars,
    )
    if zone is None:
      continue
    if direction == "BUY" and zone.kind != "retest_support":
      continue
    if direction == "SELL" and zone.kind != "retest_resistance":
      continue
    reasons = [f"HTF bias {ctx.htf_bias}", "break and retest", "retest rejection"]
    factors = ConfluenceFactors(
      htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
      touches=level.touches,
      wick_rejection=True,  # gated above via _rejection(df, direction)
      displacement_grade=_strong_body_break(
        df, st, direction, ctx.settings.momentum_body_frac,
      ),
      structural_agreement=True,  # zone.kind already matched direction above
    )
    result = _finish(
      ctx, "Break & Retest", direction, level.price, zone, price, atr, reasons,
      factors=factors,
      structural_source="key_level",
      structural_id=key_level_structural_id(ctx.symbol, ctx.tf, level),
      structural_low=zone.low,
      structural_high=zone.high,
      structural_timeframe=ctx.tf,
      structural_kind=level.kind,
    )
    if result is not None:
      return result
  return None


def _trendline_break_direction(line: Trendline, direction: str) -> bool:
  if not line.broken or line.break_index is None:
    return False
  if direction == "BUY":
    return line.kind == "resistance"
  return line.kind == "support"


def _trendline_retest_zone(
  df: pd.DataFrame,
  line: Trendline,
  direction: str,
  atr: float,
  settings: DetectorSettings,
) -> Zone | None:
  index = len(df) - 1
  if line.break_index is None or index <= line.break_index:
    return None
  level = value_at(line, index)
  tolerance = max(_EPS, max(0.0, settings.tl_tol_atr) * atr)
  row = df.iloc[-1]
  touched = (
    float(row["low"]) <= level + tolerance
    and float(row["high"]) >= level - tolerance
  )
  held = (
    float(row["close"]) >= level
    if direction == "BUY"
    else float(row["close"]) <= level
  )
  if not touched or not held:
    return None
  return _pseudo_level_zone(
    level,
    tolerance,
    direction,
    f"TL {line.kind} ×{line.touches}",
    source="trendline",
  )


def box_breakout(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  box = st.box_break
  if len(df) < 3 or box is None:
    return None
  direction = _confirmation_direction(ctx)
  expected = "up" if direction == "BUY" else "down" if direction == "SELL" else None
  if expected is None or box.direction != expected:
    return None
  age = len(df) - 1 - box.accept_index
  if age < 0 or age > max(0, ctx.settings.breakout_max_age_bars):
    return None

  price = _current_price(ctx, df)
  atr = _atr(ind)
  edge = box.box_high if direction == "BUY" else box.box_low
  entry_kind = _box_entry_kind(df, box, edge, direction, price, atr)
  if entry_kind is None:
    return None
  zone = _scored_box_zone(ctx, st, edge, direction, atr, box)
  measured = box.box_high - box.box_low
  signed_move = measured if direction == "BUY" else -measured
  reasons = [
    f"HTF bias {ctx.htf_bias}",
    f"box {_number(box.box_low)}-{_number(box.box_high)}",
    f"accepted ({box.acceptance})",
    f"{entry_kind} {_number(edge)}",
    f"measured {signed_move:+.1f}",
  ]
  tp1 = _box_tp1_reason(st, price, direction)
  if tp1 is not None:
    reasons.append(tp1)
  if box.coiling:
    reasons.append("coil")
  key_level = box.box_low if direction == "BUY" else box.box_high
  return _finish(
    ctx,
    "Box Breakout",
    direction,
    key_level,
    zone,
    price,
    atr,
    reasons,
    chop_tp_cap=False,
    include_score_reasons=False,
    structural_source="box_breakout",
    structural_id=box_structural_id(ctx.symbol, ctx.tf, box),
    structural_low=zone.low,
    structural_high=zone.high,
    structural_timeframe=ctx.tf,
    structural_kind=entry_kind,
  )


def _box_entry_kind(
  df: pd.DataFrame,
  box: BoxBreak,
  edge: float,
  direction: str,
  price: float,
  atr: float,
) -> str | None:
  current = len(df) - 1
  retest = find_retest(df, edge)
  expected_kind = "retest_support" if direction == "BUY" else "retest_resistance"
  if (
    retest is not None
    and retest.kind == expected_kind
    and retest.origin_index == current
    and current > box.accept_index
    and _rejection(df, direction)
  ):
    return "retest"
  if current != box.accept_index:
    return None
  row = df.iloc[-1]
  if not displacement_grade(row, atr, box.direction):
    return None
  if abs(price - edge) > REACTION_MAX_ATR * atr + _EPS:
    return None
  return "proximal"


def _scored_box_zone(
  ctx: DetectionContext,
  st: StructureSet,
  edge: float,
  direction: str,
  atr: float,
  box: BoxBreak,
) -> Zone:
  band = max(_EPS, ctx.settings.proximal_band_atr * max(0.0, atr))
  side = _BUY_ZONE_SIDE if direction == "BUY" else "supply"
  raw = Zone(
    edge - band,
    edge + band,
    side,
    origin_index=box.accept_index,
    source="box_breakout",
  )
  higher_zones = [
    zone
    for name, structure in ctx.structures.items()
    if name != ctx.tf
    for zone in structure.zones
  ]
  scored = score_zones(
    [raw],
    st.levels,
    st.liquidity_pools,
    ctx.settings.round_step,
    htf_zones=higher_zones,
    session_levels=st.session_levels,
    dealing_range=st.dealing_range,
    grabs=st.liquidity_grabs,
    trendlines=st.trendlines,
    bar_index=len(ctx.frames[ctx.tf]) - 1,
  )[0]
  if not box.coiling:
    return scored
  return replace(
    scored,
    score=scored.score + COIL_SCORE,
    score_reasons=[*scored.score_reasons, "coil"],
  )


def _box_tp1_reason(
  st: StructureSet,
  price: float,
  direction: str,
) -> str | None:
  session = _nearest_session_tp(st.session_levels, price, direction)
  if session is not None:
    return f"TP1 {session.name}"
  pool = _nearest_opposing_pool(st, price, direction)
  if pool is not None:
    return f"TP1 liquidity {_number(pool.level)}"
  return None


def snap_back(ctx: DetectionContext) -> DetectionResult | None:
  """Recovery mission (2026-07-31): retrofitted onto the shared
  evaluate_structural_reaction confirmation path, same reasoning as
  trend_pullback above - the old bespoke _rejection(df, direction) check
  never populated structural_id/touch_bar_ts/confirmation_bar_ts, which
  the legacy worker.py pipeline needs to treat M1 as optional rather than
  a hard, unconditional gate.
  """
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  direction = _confirmation_direction(ctx)
  if direction is None or not _pd_gate(st, direction, ctx.settings, ctx=ctx, setup="Snap-Back"):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  zones = _candidate_zones(st, direction)
  selected = _best_valid_zone(zones, price, atr, direction, ctx.settings)
  structural_source = "supply_demand"
  structural_kind = "demand" if direction == "BUY" else "supply"
  structural_id_value: str | None = None
  level = None
  proximal = False
  touches = 0
  structural_agreement = False
  if selected is not None:
    zone, proximal = selected
    distance = _zone_distance(zone, price, direction)
    level = _zone_key(zone, price, direction)
    touches = zone.touches
    structural_agreement = True  # zone drawn from structural swing zones
    structural_id_value = zone_structural_id(ctx.symbol, ctx.tf, zone)
  else:
    nearest = _nearest_level(st.levels, price, direction)
    if nearest is None:
      return None
    zone = entry_zone(df, nearest.price, direction)
    distance = _zone_distance(zone, price, direction)
    level = nearest.price
    touches = nearest.touches
    structural_source = "key_level"
    structural_kind = nearest.kind
    structural_id_value = key_level_structural_id(ctx.symbol, ctx.tf, nearest)
  if distance < atr * ctx.settings.snap_atr_mult:
    return None
  grab = _zone_grab(st, zone, direction)
  if grab is None or grab.grade not in {"A", "B"}:
    return None
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  conf = evaluate_structural_reaction(
    df,
    direction=direction,
    low=float(zone.low),
    high=float(zone.high),
    lookback_bars=lookback,
    grabs=[grab],
    has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
  )
  if conf is None:
    return None
  reasons = [
    "ATR extension",
    f"sweep {grab.grade}",
  ]
  reasons = _add_proximal_reason(reasons, proximal)
  factors = ConfluenceFactors(
    htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
    touches=touches,
    wick_rejection=True,
    displacement_grade=grab.grade == "A",
    structural_agreement=structural_agreement,
  )
  return _structural_finish(
    ctx,
    setup="Snap-Back",
    direction=direction,
    level=level,
    zone=zone,
    price=price,
    atr=atr,
    reasons=reasons,
    structural_source=structural_source,
    structural_id=structural_id_value,
    structural_low=float(zone.low),
    structural_high=float(zone.high),
    structural_kind=structural_kind,
    confirmation=conf,
    source_touches=touches,
    source_score=float(getattr(zone, "score", 0.0)),
    factors=factors,
  )


def momentum_ride(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  if _in_chop(ctx):
    return None
  direction = _confirmation_direction(ctx)
  if direction is None or not _pd_gate(st, direction, ctx.settings, ctx=ctx, setup="Momentum Ride"):
    return None
  if not _strong_body_break(df, st, direction, ctx.settings.momentum_body_frac):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  selected = _best_valid_zone(
    _candidate_zones(st, direction),
    price,
    atr,
    direction,
    ctx.settings,
  )
  if selected is not None:
    zone, proximal = selected
    level_price = _zone_key(zone, price, direction)
    reasons = [f"HTF bias {ctx.htf_bias}", "impulse break", "near scored zone"]
    reasons = _add_proximal_reason(reasons, proximal)
    factors = ConfluenceFactors(
      htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
      touches=zone.touches,
      displacement_grade=True,  # gated above via _strong_body_break(...)
      structural_agreement=True,  # zone drawn from structural swing zones
    )
    return _finish(
      ctx, "Momentum Ride", direction, level_price, zone, price, atr, reasons,
      factors=factors,
    )
  level = _nearest_level(st.levels, price, direction)
  if level is None:
    return None
  zone = entry_zone(df, level.price, direction)
  reasons = [f"HTF bias {ctx.htf_bias}", "impulse break", "near valid-side level"]
  factors = ConfluenceFactors(
    htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
    touches=level.touches,
    displacement_grade=True,  # gated above via _strong_body_break(...)
  )
  return _finish(
    ctx, "Momentum Ride", direction, level.price, zone, price, atr, reasons,
    factors=factors,
  )


def range_edge_scalp(ctx: DetectionContext) -> DetectionResult | None:
  """Confirmation retrofitted onto the shared evaluate_structural_reaction
  path (2026-08-04 recovery mission, same reasoning as trend_pullback/
  fade_scalp above). The old bespoke _range_edge_confirmation/
  _recent_rejection check required a strict single-candle wick-rejection
  shape inside a hard 3-bar (sweep_react_bars) window sized for M1
  granularity - but this detector runs on the M5 execution timeframe, and
  on real M5 data that exact shape essentially never landed in the last 3
  bars even when a barrier already had genuine, multi-touch wick-rejection
  history (touches/wick_rejections are already gated above, before
  confirmation is even checked). Result: Range Edge Scalp never fired in
  production despite RANGE_SCALP_ENABLED and a live, qualifying barrier -
  confirmed against live XAU M5 data showing zero fires in 200+ recent
  setups while sibling detectors on the same shared path fired normally.
  It also never populated touch_bar_ts/confirmation_bar_ts, the same gap
  trend_pullback's docstring describes for its own old bespoke check.

  Root cause of the miss, confirmed against live XAU M5 data: the barrier's
  own wick-rejection candle sat just outside the fixed
  structural_reaction_lookback_bars=3 confirmation window (4 bars back
  instead of 3) even though _barrier_touched_recently's separate,
  price-band-only check (no candle-shape requirement) still passed for the
  same barrier - a real, multi-touch/multi-wick-rejection level a few bars
  older than the window can otherwise never confirm. Unlike a fresh zone
  reaction, this barrier's touch/wick history was already independently
  vetted (by the earlier touches/wick_rejections gates) over
  scalp_ranges.py's own, much longer range-formation lookback - so the
  confirmation window here is widened to at least reach the barrier's own
  last recorded touch, instead of only trusting a fixed small constant
  sized for a same-bar reaction.
  """
  if not ctx.settings.range_scalp_enabled:
    return None
  df, ind, st = _exec(ctx)
  scalp_range = st.scalp_range
  if len(df) < 5 or scalp_range is None:
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  base_lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  candidates = [
    ("BUY", scalp_range.lower, scalp_range.upper.level),
    ("SELL", scalp_range.upper, scalp_range.lower.level),
  ]
  candidates = sorted(
    candidates,
    key=lambda item: (abs(item[1].level - price), -item[1].score, item[0]),
  )
  for direction, barrier, opposing_level in candidates:
    # Widen the confirmation window to at least reach this barrier's own
    # last recorded touch (capped at the range's own formation lookback) -
    # a barrier a few bars older than base_lookback must not be treated as
    # unconfirmable when _barrier_touched_recently's own band-only check
    # (no candle-shape requirement) already accepts it as current.
    recency = max(0, (len(df) - 1) - int(barrier.last_touch_index))
    lookback = min(
      max(base_lookback, recency + 1),
      max(base_lookback, int(ctx.settings.range_scalp_lookback)),
    )
    if not _barrier_touched_recently(df, barrier, lookback):
      continue
    if barrier.accepted_closes >= max(1, ctx.settings.range_scalp_break_closes):
      continue
    zone = _barrier_zone(barrier, direction)
    grab = _zone_grab(st, zone, direction)
    grade_a = grab is not None and grab.grade == "A"
    minimum_touches = max(2, ctx.settings.range_scalp_min_touches)
    if barrier.touches < minimum_touches and not (
      barrier.touches >= 2 and grade_a
    ):
      continue
    minimum_wicks = max(1, ctx.settings.range_scalp_min_wick_rejections)
    if barrier.wick_rejections < minimum_wicks and not grade_a:
      continue
    room_atr = abs(barrier.level - scalp_range.eq) / max(atr, _EPS)
    if room_atr < max(0.0, ctx.settings.range_scalp_min_room_atr):
      continue
    confirmation = evaluate_structural_reaction(
      df,
      direction=direction,
      low=float(zone.low),
      high=float(zone.high),
      lookback_bars=lookback,
      grabs=_zone_grabs_for(st, zone, direction),
      has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
    )
    if confirmation is None:
      continue
    edge = "lower" if direction == "BUY" else "upper"
    reasons = [
      f"local range {_number(scalp_range.lower.level)}-"
      f"{_number(scalp_range.upper.level)}",
      f"{edge} barrier ×{barrier.touches}",
      f"wick rejection ×{barrier.wick_rejections}",
      confirmation.confirmation_type,
      f"TP1 EQ {_number(scalp_range.eq)}",
      f"TP2 edge {_number(opposing_level)}",
    ]
    return _finish(
      ctx,
      "Range Edge Scalp",
      direction,
      barrier.level,
      zone,
      price,
      atr,
      reasons,
      mode="range_scalp",
      chop_tp_cap=False,
      confirmation=confirmation.confirmation_type,
      confirmation_bar_ts=confirmation.confirmation_bar_ts,
      touch_bar_ts=confirmation.touch_bar_ts,
      source_touches=barrier.touches,
      source_score=barrier.score,
    )
  return None


def _barrier_zone(barrier: ScalpBarrier, direction: str) -> Zone:
  return Zone(
    barrier.low,
    barrier.high,
    _BUY_ZONE_SIDE if direction == "BUY" else "supply",
    source="range_edge",
    score=max(STAR_TWO_SCORE, barrier.score),
    score_reasons=list(barrier.tags),
  )


def _barrier_touched_recently(
  df: pd.DataFrame,
  barrier: ScalpBarrier,
  bars: int,
) -> bool:
  for row in df.tail(max(1, bars)).itertuples(index=False):
    if float(row.low) <= barrier.high and float(row.high) >= barrier.low:
      return True
  return False


def fade_scalp(ctx: DetectionContext) -> DetectionResult | None:
  """Recovery mission (2026-07-31): retrofitted onto the shared
  evaluate_structural_reaction confirmation path, same reasoning as
  trend_pullback/snap_back above. Fade Scalp's family (range_reversion)
  is shared with Range Edge Scalp, whose hard M1 requirement is
  intentional (no separate M5 reaction exists for that setup) - so this
  detector is registered under _REACTION_STRATEGIES individually rather
  than by family, to give it the M1-optional treatment without touching
  Range Edge Scalp's.
  """
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  direction = _confirmation_direction(ctx)
  if direction is None or not _pd_gate(st, direction, ctx.settings, ctx=ctx, setup="Fade Scalp"):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  desired_kind = "equal_low" if direction == "BUY" else "equal_high"
  for level in st.equal_levels:
    if level.kind != desired_kind:
      continue
    grab = _level_grab(st, level, direction)
    if grab is None or grab.grade not in {"A", "B"}:
      continue
    zone = entry_zone(df, level.price, direction)
    if _in_chop(ctx) and (grab.grade != "A" or not _chop_edge_ok(ctx, zone, direction)):
      continue
    conf = evaluate_structural_reaction(
      df,
      direction=direction,
      low=float(zone.low),
      high=float(zone.high),
      lookback_bars=lookback,
      grabs=[grab],
      has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
    )
    if conf is None:
      continue
    reasons = [
      "equal level sweep",
      f"sweep {grab.grade}",
    ]
    range_reason = _chop_range_reason(ctx)
    if range_reason:
      reasons.append(range_reason)
    factors = ConfluenceFactors(
      htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
      touches=level.touches,
      wick_rejection=True,
      displacement_grade=grab.grade == "A",
    )
    result = _structural_finish(
      ctx,
      setup="Fade Scalp",
      direction=direction,
      level=level.price,
      zone=zone,
      price=price,
      atr=atr,
      reasons=reasons,
      structural_source="liquidity_pool",
      structural_id=equal_level_structural_id(ctx.symbol, ctx.tf, level),
      structural_low=float(zone.low),
      structural_high=float(zone.high),
      structural_kind=level.kind,
      confirmation=conf,
      source_touches=level.touches,
      source_score=float(getattr(zone, "score", 0.0)),
      factors=factors,
    )
    if result is not None:
      return result
  return None


def zone_reaction(ctx: DetectionContext) -> DetectionResult | None:
  if not ctx.settings.allow_counter_trend or ctx.htf_bias not in {"up", "down"}:
    return None
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  direction = "BUY" if ctx.htf_bias == "down" else "SELL"
  if not _counter_pd_gate(st, direction, ctx.settings):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  candidate = _counter_zone_candidate(ctx, df, st, direction, price, atr)
  if candidate is None:
    candidate = _counter_level_candidate(ctx, df, st, direction, price, atr)
  if candidate is None:
    return None

  zone, level, mode, reasons, confirmation_target = candidate
  if _in_chop(ctx) and not _chop_edge_ok(ctx, zone, direction):
    return None
  confirmation = _counter_confirmation(
    df,
    st,
    zone,
    direction,
    confirmation_target,
    ctx.settings,
  )
  if confirmation is None:
    return None
  if _in_chop(ctx) and confirmation != "sweep A":
    return None
  range_reason = _chop_range_reason(ctx)
  reasons = [
    f"HTF bias {ctx.htf_bias}",
    *reasons,
    confirmation,
    *([range_reason] if range_reason else []),
    _pd_reason(st),
    *_counter_target_reasons(st, price, direction, mode),
  ]
  return _finish(
    ctx,
    "Zone Reaction",
    direction,
    level,
    zone,
    price,
    atr,
    reasons,
    mode,
  )


def _counter_zone_candidate(
  ctx: DetectionContext,
  df: pd.DataFrame,
  st: StructureSet,
  direction: str,
  price: float,
  atr: float,
) -> tuple[Zone, float, str, list[str], Level | None] | None:
  zones = [
    zone for zone in _candidate_zones(st, direction)
    if (
      zone.touches == 0
      and float(getattr(zone, "score", 0.0)) >= ctx.settings.counter_min_zone_score
      and _last_touches_zone(df, zone)
    )
  ]
  selected = _best_valid_zone(zones, price, atr, direction, ctx.settings)
  if selected is None:
    return None
  zone, proximal = selected
  mode = "counter_swing" if _counter_swing_zone(zone) else "counter_reaction"
  reasons = ["fresh counter zone"]
  if mode == "counter_swing":
    reasons.append("fresh HTF OB")
  reasons = _add_proximal_reason(reasons, proximal)
  return zone, _zone_key(zone, price, direction), mode, reasons, None


def _counter_level_candidate(
  ctx: DetectionContext,
  df: pd.DataFrame,
  st: StructureSet,
  direction: str,
  price: float,
  atr: float,
) -> tuple[Zone, float, str, list[str], Level | None] | None:
  band = max(_EPS, ctx.settings.proximal_band_atr * max(0.0, atr))
  for level in sorted(st.levels, key=lambda item: abs(item.price - price)):
    if level.touches < ctx.settings.counter_level_min_touches:
      continue
    if not _level_touched_last(df, level.price, max(level.band, band)):
      continue
    zone = _pseudo_level_zone(level.price, band, direction, f"key {_number(level.price)} x{level.touches}")
    if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
      continue
    return (
      zone,
      _zone_key(zone, price, direction),
      "counter_reaction",
      [f"key {_number(level.price)} x{level.touches}"],
      level,
    )
  for line in sorted(
    st.trendlines,
    key=lambda item: abs(value_at(item, len(df) - 1) - price),
  ):
    if line.broken:
      continue
    if direction == "BUY" and line.kind != "support":
      continue
    if direction == "SELL" and line.kind != "resistance":
      continue
    line_price = value_at(line, len(df) - 1)
    if not _level_touched_last(df, line_price, band):
      continue
    reason = f"TL {line.kind} ×{line.touches}"
    zone = _pseudo_level_zone(
      line_price,
      band,
      direction,
      reason,
      source="trendline",
    )
    if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
      continue
    return (
      zone,
      _zone_key(zone, price, direction),
      "counter_reaction",
      [reason],
      None,
    )
  for session in sorted(st.session_levels, key=lambda item: abs(item.price - price)):
    if session.swept or not _counter_session_side(session.name, direction):
      continue
    if not _level_touched_last(df, session.price, band):
      continue
    zone = _pseudo_level_zone(session.price, band, direction, session.name)
    if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
      continue
    return (
      zone,
      _zone_key(zone, price, direction),
      "counter_reaction",
      [session.name],
      None,
    )
  return None


def _pseudo_level_zone(
  price: float,
  band: float,
  direction: str,
  reason: str,
  *,
  source: str = "level",
) -> Zone:
  side = _BUY_ZONE_SIDE if direction == "BUY" else "supply"
  return Zone(
    price - band,
    price + band,
    side,
    source=source,
    score=STAR_TWO_SCORE,
    score_reasons=[reason],
  )


def _counter_confirmation(
  df: pd.DataFrame,
  st: StructureSet,
  zone: Zone,
  direction: str,
  level: Level | None,
  settings: DetectorSettings,
) -> str | None:
  grab = _zone_grab(st, zone, direction)
  if grab is None and level is not None:
    grab = _level_grab(st, level, direction)
  if grab is not None and grab.grade == "A":
    return "sweep A"
  if _rejection(df, direction) and _recent_choch(st, direction, len(df), settings):
    return "rejection + CHoCH"
  return None


def _counter_pd_gate(
  st: StructureSet,
  direction: str,
  settings: DetectorSettings,
) -> bool:
  range_ = st.dealing_range
  if range_ is None:
    return False
  extreme = max(0.0, min(0.5, settings.counter_extreme_pd))
  if direction == "BUY":
    return range_.position <= extreme + _EPS
  return range_.position >= 1.0 - extreme - _EPS


def _pd_reason(st: StructureSet) -> str:
  if st.dealing_range is None:
    return "PD unknown"
  return f"PD {st.dealing_range.position:.2f}"


def _counter_swing_zone(zone: Zone) -> bool:
  sources = set(zone.sources or ([zone.source] if zone.source else []))
  has_structure = bool(sources & {"order_block", "breaker"})
  return has_structure and "HTF zone" in set(zone.score_reasons or [])


def _recent_choch(
  st: StructureSet,
  direction: str,
  bar_count: int,
  settings: DetectorSettings,
) -> bool:
  lookback = max(1, settings.sweep_react_bars)
  earliest = max(0, bar_count - lookback - 1)
  wanted = "up" if direction == "BUY" else "down"
  return any(
    item.kind == "CHoCH" and item.direction == wanted and item.index >= earliest
    for item in st.breaks
  )


def _level_touched_last(df: pd.DataFrame, price: float, band: float) -> bool:
  if df.empty:
    return False
  row = df.iloc[-1]
  return (
    float(row["low"]) <= price + max(0.0, band)
    and float(row["high"]) >= price - max(0.0, band)
  )


def _counter_session_side(name: str, direction: str) -> bool:
  if direction == "BUY":
    return _is_low_session_level(name)
  return _is_high_session_level(name)


def _counter_target_reasons(
  st: StructureSet,
  price: float,
  direction: str,
  mode: str,
) -> list[str]:
  if mode == "counter_swing":
    if st.dealing_range is not None:
      return [f"TP anchor EQ {_number(st.dealing_range.eq)}"]
    return ["TP anchor opposing HTF zone"]
  session = _nearest_session_tp(st.session_levels, price, direction)
  if session is not None:
    return [f"TP anchor {session.name}"]
  pool = _nearest_opposing_pool(st, price, direction)
  if pool is not None:
    return [f"TP anchor liquidity {_number(pool.level)}"]
  if st.dealing_range is not None:
    return [f"TP anchor EQ {_number(st.dealing_range.eq)}"]
  return []


def _nearest_opposing_pool(
  st: StructureSet,
  price: float,
  direction: str,
) -> Pool | None:
  if direction == "BUY":
    candidates = [
      pool for pool in st.liquidity_pools
      if pool.side == "buy" and pool.level > price
    ]
  else:
    candidates = [
      pool for pool in st.liquidity_pools
      if pool.side == "sell" and pool.level < price
    ]
  if not candidates:
    return None
  return min(candidates, key=lambda pool: abs(pool.level - price))


def _number(value: float) -> str:
  return f"{value:.2f}".rstrip("0").rstrip(".")


def _level_grab(
  st: StructureSet,
  level: Level,
  direction: str,
) -> Grab | None:
  wanted_direction = "bull" if direction == "BUY" else "bear"
  wanted_side = "sell" if direction == "BUY" else "buy"
  for grab in reversed(st.liquidity_grabs):
    if grab.direction != wanted_direction or grab.pool.side != wanted_side:
      continue
    if abs(grab.pool.level - level.price) <= max(grab.pool.band, level.band, _EPS):
      return grab
  return None


def _zone_grab(
  st: StructureSet,
  zone: Zone,
  direction: str,
) -> Grab | None:
  wanted_direction = "bull" if direction == "BUY" else "bear"
  wanted_side = "sell" if direction == "BUY" else "buy"
  for grab in reversed(st.liquidity_grabs):
    if grab.direction != wanted_direction or grab.pool.side != wanted_side:
      continue
    if _grab_points_into_zone(grab, zone):
      return grab
  return None


def _grab_points_into_zone(grab: Grab, zone: Zone) -> bool:
  width = max(zone.high - zone.low, 0.0)
  tolerance = max(grab.pool.band, width, 0.1)
  if zone.side == "demand" and grab.pool.side == "sell":
    return zone.low - tolerance <= grab.pool.level <= zone.high
  if zone.side == "supply" and grab.pool.side == "buy":
    return zone.low <= grab.pool.level <= zone.high + tolerance
  return False


def _is_high_session_level(name: str) -> bool:
  return name.endswith("_H") or name in {"PDH", "PWH"}


def _is_low_session_level(name: str) -> bool:
  return name.endswith("_L") or name in {"PDL", "PWL"}



def _recent_choch_flag(
  st: StructureSet,
  direction: str,
  bar_count: int,
  settings: DetectorSettings,
  lookback: int,
) -> bool:
  earliest = max(0, bar_count - max(1, lookback) - 1)
  wanted = "up" if direction == "BUY" else "down"
  return any(
    item.kind == "CHoCH" and item.direction == wanted and item.index >= earliest
    for item in st.breaks
  )


def _zone_grabs_for(
  st: StructureSet,
  zone: Zone,
  direction: str,
) -> list:
  grab = _zone_grab(st, zone, direction)
  return [grab] if grab is not None else []


def _level_grabs_for(
  st: StructureSet,
  level: Level,
  direction: str,
) -> list:
  grab = _level_grab(st, level, direction)
  return [grab] if grab is not None else []


def _structural_finish(
  ctx: DetectionContext,
  *,
  setup: str,
  direction: str,
  level: float,
  zone: Zone,
  price: float,
  atr: float,
  reasons: list[str],
  structural_source: str,
  structural_id: str,
  structural_low: float,
  structural_high: float,
  structural_kind: str,
  confirmation,
  source_touches: int | None = None,
  source_score: float | None = None,
  factors: ConfluenceFactors | None = None,
) -> DetectionResult | None:
  relationship = bias_relationship(ctx.htf_bias, direction)
  full_reasons = [
    f"HTF bias {ctx.htf_bias}",
    *reasons,
    confirmation.confirmation_type,
    f"bias {relationship}",
  ]
  return _finish(
    ctx,
    setup,
    direction,
    level,
    zone,
    price,
    atr,
    full_reasons,
    mode=relationship,
    factors=factors,
    confirmation=confirmation.confirmation_type,
    structural_source=structural_source,
    structural_id=structural_id,
    structural_low=structural_low,
    structural_high=structural_high,
    structural_timeframe=ctx.tf,
    structural_kind=structural_kind,
    confirmation_type=confirmation.confirmation_type,
    confirmation_bar_ts=confirmation.confirmation_bar_ts,
    touch_bar_ts=confirmation.touch_bar_ts,
    source_touches=source_touches,
    source_score=source_score,
    bias_relationship=relationship,
  )


def _opposing_zone_contradicts(
  st: StructureSet,
  *,
  band_low: float,
  band_high: float,
  naive_side: str,
) -> Zone | None:
  """A real, unmitigated opposing-side zone overlapping this level's own
  band contradicts the naive price-position guess below.

  key_levels() (levels.py) only ever produces kind="reaction"/"round" -
  never an explicit support/resistance label - so classify_key_level_role
  almost always falls through to "price above the level -> assume
  support -> BUY, price below -> assume resistance -> SELL" with no
  awareness of nearby structure at all. A supply/breaker zone sitting
  right at a level the naive guess called "support" means that guess is
  likely wrong. Don't flip the direction outright (a coin flip is not
  better than the current one) - just stop foreclosing the side that
  actually matches the zone, and let evaluate_structural_reaction (via
  the existing "try both, keep only if exactly one confirms" mechanism a
  few lines below) decide from real price action either way. The returned
  zone's own bounds - not just the level's narrow band - are what price
  actually has to react off of, so callers widen the reaction window to
  cover it: a plain bool here would leave the entry-validity check
  comparing price against the tiny level band and rejecting every
  contradicting-direction candidate outright.
  """
  opposing_side = "supply" if naive_side == "demand" else "demand"
  for zone in (*st.zones, *st.order_blocks):
    if zone.side != opposing_side or zone.mitigated:
      continue
    if zone.low <= band_high and zone.high >= band_low:
      return zone
  return None


def key_level_reaction(ctx: DetectionContext) -> DetectionResult | None:
  if not ctx.settings.key_level_reaction_enabled:
    return None
  df, ind, st = _exec(ctx)
  if len(df) < 3:
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  band = max(_EPS, ctx.settings.proximal_band_atr * max(0.0, atr))
  min_touches = max(1, int(ctx.settings.key_level_min_touches))
  best: DetectionResult | None = None
  for level in sorted(st.levels, key=lambda item: abs(item.price - price)):
    if level.touches < min_touches:
      continue
    zone_band = max(level.band, band)
    role = classify_key_level_role(
      kind=level.kind,
      level_price=level.price,
      band_low=level.price - zone_band,
      band_high=level.price + zone_band,
      closed_bars=df,
      breakout_accept_bars=ctx.settings.breakout_accept_bars,
    ).role
    # Accepted role flips are owned by Break & Retest. They cannot be
    # reinterpreted as an ordinary reaction in the opposite direction.
    if role in {ROLE_BROKEN_SUPPORT, ROLE_BROKEN_RESISTANCE}:
      continue
    band_low = level.price - zone_band
    band_high = level.price + zone_band
    react_low = band_low
    react_high = band_high
    contra_direction: str | None = None
    contra_level: float | None = None
    if role == ROLE_SUPPORT:
      directions = ("BUY",)
    elif role == ROLE_RESISTANCE:
      directions = ("SELL",)
    elif price > band_high:
      # No explicit kind/breakout evidence either way (ROLE_AMBIGUOUS), but
      # the level sits below current price - deterministic support
      # hypothesis, not a confluence-margin guess. Unless a real opposing
      # (supply) zone overlaps this same band - then that hypothesis is
      # contradicted by actual structure, not just a guess this detector
      # should override on its own. Widen the reaction window to the
      # opposing zone's own bounds too, and treat that zone's own edge -
      # not this key level's price, which sits below current price by
      # definition here - as the structural level a SELL is reacting off
      # of: _level_valid/_entry_valid both require the level/zone to be
      # at-or-above price for a SELL, which the original (lower) level
      # can never satisfy once price has already moved above it.
      opposing = _opposing_zone_contradicts(
        st, band_low=band_low, band_high=band_high, naive_side="demand",
      )
      if opposing is not None:
        directions = ("BUY", "SELL")
        react_low = min(band_low, opposing.low)
        react_high = max(band_high, opposing.high)
        contra_direction = "SELL"
        contra_level = opposing.high
      else:
        directions = ("BUY",)
    elif price < band_low:
      # Level sits above current price - deterministic resistance
      # hypothesis, same caveat mirrored for an opposing demand zone.
      opposing = _opposing_zone_contradicts(
        st, band_low=band_low, band_high=band_high, naive_side="supply",
      )
      if opposing is not None:
        directions = ("SELL", "BUY")
        react_low = min(band_low, opposing.low)
        react_high = max(band_high, opposing.high)
        contra_direction = "BUY"
        contra_level = opposing.low
      else:
        directions = ("SELL",)
    else:
      # Price is inside the level's own band - direction must come from
      # which side actually confirms an M5 reaction, never a raw-
      # confluence tiebreak. If both directions independently confirm,
      # that is a genuine contradiction, not a coin flip: the collection
      # loop below discards this level entirely rather than picking one.
      directions = ("BUY", "SELL")
    confirmed_here: list[DetectionResult] = []
    for direction in directions:
      level_price = (
        contra_level if direction == contra_direction else level.price
      )
      conf = evaluate_structural_reaction(
        df,
        direction=direction,
        low=react_low,
        high=react_high,
        lookback_bars=lookback,
        grabs=_level_grabs_for(st, level, direction),
        has_choch=_recent_choch_flag(
          st, direction, len(df), ctx.settings, lookback,
        ),
      )
      if conf is None:
        continue
      zone = Zone(
        react_low,
        react_high,
        _BUY_ZONE_SIDE if direction == "BUY" else "supply",
        source="level",
        score=STAR_TWO_SCORE,
        score_reasons=[f"key {level.kind} x{level.touches}"],
      )
      if not _entry_valid_for_settings(
        zone, price, atr, direction, ctx.settings,
      ):
        continue
      factors = ConfluenceFactors(
        htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
        touches=level.touches,
        wick_rejection=True,
        structural_agreement=True,
      )
      candidate = _structural_finish(
        ctx,
        setup="Key Level Reaction",
        direction=direction,
        level=level_price,
        zone=zone,
        price=price,
        atr=atr,
        reasons=[
          f"key {level.kind} {_number(level.price)} x{level.touches}",
          f"touches {level.touches}",
        ],
        structural_source="key_level",
        structural_id=key_level_structural_id(ctx.symbol, ctx.tf, level),
        structural_low=react_low,
        structural_high=react_high,
        structural_kind=level.kind,
        confirmation=conf,
        source_touches=level.touches,
        source_score=float(level.strength),
        factors=factors,
      )
      if candidate is not None:
        candidate = replace(candidate, key_level_role=role)
        confirmed_here.append(candidate)
    if len(confirmed_here) != 1:
      # Zero confirmations: nothing to keep. Two confirmations (only
      # reachable from the "price inside the band" branch above): both
      # sides independently confirmed a reaction off the same level in the
      # same bar - a genuine contradiction, not something a confluence-
      # score tiebreak should silently resolve. Neither survives; this
      # level produces no opportunity until price action resolves it.
      continue
    candidate = confirmed_here[0]
    if best is None or candidate.confluence > best.confluence:
      best = candidate
  return best


def _zone_has_source(zone: Zone, source: str) -> bool:
  sources = list(getattr(zone, "sources", None) or [])
  if not sources and getattr(zone, "source", None):
    sources = [str(zone.source)]
  return source in sources


def demand_zone_reaction(ctx: DetectionContext) -> DetectionResult | None:
  if not ctx.settings.demand_reaction_enabled:
    return None
  if not ctx.settings.zone_reaction_fallback_enabled:
    return None
  # Display name is zone-only (BUY/SELL carries the side). Legacy
  # "Demand Zone Reaction" remains accepted in taxonomy/policy maps.
  # Flip-tagged bands are owned by Flip Zone (not Zone Reaction).
  return _sd_zone_reaction(
    ctx,
    side="demand",
    direction="BUY",
    setup="Zone Reaction",
    exclude_sources=("flip_zone",),
  )


def supply_zone_reaction(ctx: DetectionContext) -> DetectionResult | None:
  if not ctx.settings.supply_reaction_enabled:
    return None
  if not ctx.settings.zone_reaction_fallback_enabled:
    return None
  return _sd_zone_reaction(
    ctx,
    side="supply",
    direction="SELL",
    setup="Zone Reaction",
    exclude_sources=("flip_zone",),
  )


def flip_demand_zone_reaction(ctx: DetectionContext) -> DetectionResult | None:
  if not ctx.settings.flip_zone_enabled:
    return None
  return _sd_zone_reaction(
    ctx,
    side="demand",
    direction="BUY",
    setup="Flip Zone",
    require_source="flip_zone",
    structural_source="flip_zone",
  )


def flip_supply_zone_reaction(ctx: DetectionContext) -> DetectionResult | None:
  if not ctx.settings.flip_zone_enabled:
    return None
  return _sd_zone_reaction(
    ctx,
    side="supply",
    direction="SELL",
    setup="Flip Zone",
    require_source="flip_zone",
    structural_source="flip_zone",
  )


def _sd_zone_reaction(
  ctx: DetectionContext,
  *,
  side: str,
  direction: str,
  setup: str,
  require_source: str | None = None,
  exclude_sources: tuple[str, ...] = (),
  structural_source: str = "supply_demand",
) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 3:
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  zones = []
  for zone in [*st.zones, *st.order_blocks]:
    if zone.side != side or zone.mitigated:
      continue
    if require_source is not None and not _zone_has_source(zone, require_source):
      continue
    if any(_zone_has_source(zone, excluded) for excluded in exclude_sources):
      continue
    zones.append(zone)
  selected = _best_valid_zone(zones, price, atr, direction, ctx.settings)
  if selected is None:
    # Still allow touch within lookback even if proximal helper misses.
    candidates = []
    for zone in zones:
      if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
        continue
      candidates.append(zone)
    if not candidates:
      return None
    zone = min(candidates, key=lambda item: abs(((item.low + item.high) / 2) - price))
  else:
    zone, _proximal = selected

  conf = evaluate_structural_reaction(
    df,
    direction=direction,
    low=float(zone.low),
    high=float(zone.high),
    lookback_bars=lookback,
    grabs=_zone_grabs_for(st, zone, direction),
    has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
  )
  if conf is None:
    return None
  factors = ConfluenceFactors(
    htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
    touches=int(zone.touches),
    wick_rejection=True,
    structural_agreement=True,
    displacement_grade=float(getattr(zone, "score", 0.0)) >= STAR_TWO_SCORE,
  )
  reasons = [
    f"{side} zone {_number(zone.low)}-{_number(zone.high)}",
    zone.source or side,
  ]
  if zone.touches:
    reasons.append(f"touches {zone.touches}")
  return _structural_finish(
    ctx,
    setup=setup,
    direction=direction,
    level=_zone_key(zone, price, direction),
    zone=zone,
    price=price,
    atr=atr,
    reasons=reasons,
    structural_source=structural_source,
    structural_id=zone_structural_id(ctx.symbol, ctx.tf, zone),
    structural_low=float(zone.low),
    structural_high=float(zone.high),
    structural_kind=side,
    confirmation=conf,
    source_touches=int(zone.touches),
    source_score=float(getattr(zone, "score", 0.0)),
    factors=factors,
  )


def session_level_reaction(ctx: DetectionContext) -> DetectionResult | None:
  if not ctx.settings.session_level_reaction_enabled:
    return None
  df, ind, st = _exec(ctx)
  if len(df) < 3:
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  band = max(_EPS, ctx.settings.proximal_band_atr * max(0.0, atr))
  best: DetectionResult | None = None
  for session in sorted(st.session_levels, key=lambda item: abs(item.price - price)):
    if _is_high_session_level(session.name):
      direction = "SELL"
    elif _is_low_session_level(session.name):
      direction = "BUY"
    else:
      continue
    # Swept levels remain valid only with a confirmed reclaim.
    conf = evaluate_structural_reaction(
      df,
      direction=direction,
      low=session.price - band,
      high=session.price + band,
      lookback_bars=lookback,
      grabs=[],
      has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
    )
    if conf is None:
      continue
    if session.swept and conf.confirmation_type not in {
      "sweep_reclaim", "strong_reclaim", "rejection_choch",
    }:
      continue
    zone = _pseudo_level_zone(session.price, band, direction, session.name)
    if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
      continue
    factors = ConfluenceFactors(
      htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
      touches=2,
      wick_rejection=True,
      structural_agreement=True,
    )
    candidate = _structural_finish(
      ctx,
      setup="Session Level Reaction",
      direction=direction,
      level=session.price,
      zone=zone,
      price=price,
      atr=atr,
      reasons=[f"session {session.name}", session.name],
      structural_source="session_level",
      structural_id=session_level_structural_id(ctx.symbol, ctx.tf, session),
      structural_low=session.price - band,
      structural_high=session.price + band,
      structural_kind=session.name,
      confirmation=conf,
      source_touches=2,
      factors=factors,
    )
    if candidate is not None and (
      best is None or candidate.confluence > best.confluence
    ):
      best = candidate
  return best


def trendline_reaction(ctx: DetectionContext) -> DetectionResult | None:
  if not ctx.settings.trendline_reaction_enabled:
    return None
  df, ind, st = _exec(ctx)
  if len(df) < 3:
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  lookback = max(1, int(ctx.settings.structural_reaction_lookback_bars))
  band = max(_EPS, ctx.settings.tl_tol_atr * max(0.0, atr))
  min_touches = max(2, int(ctx.settings.tl_min_touches))
  best: DetectionResult | None = None
  for line in sorted(
    st.trendlines,
    key=lambda item: abs(value_at(item, len(df) - 1) - price),
  ):
    if line.broken or line.touches < min_touches:
      continue
    if line.kind == "support":
      direction = "BUY"
    elif line.kind == "resistance":
      direction = "SELL"
    else:
      continue
    line_price = value_at(line, len(df) - 1)
    conf = evaluate_structural_reaction(
      df,
      direction=direction,
      low=line_price - band,
      high=line_price + band,
      lookback_bars=lookback,
      grabs=[],
      has_choch=_recent_choch_flag(st, direction, len(df), ctx.settings, lookback),
    )
    if conf is None:
      continue
    reason = f"TL {line.kind} ×{line.touches}"
    zone = _pseudo_level_zone(
      line_price, band, direction, reason, source="trendline",
    )
    if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
      continue
    factors = ConfluenceFactors(
      htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
      touches=line.touches,
      wick_rejection=True,
      structural_agreement=True,
    )
    candidate = _structural_finish(
      ctx,
      setup="Trendline Reaction",
      direction=direction,
      level=line_price,
      zone=zone,
      price=price,
      atr=atr,
      reasons=[reason, f"touches {line.touches}"],
      structural_source="trendline",
      structural_id=trendline_structural_id(ctx.symbol, ctx.tf, line),
      structural_low=line_price - band,
      structural_high=line_price + band,
      structural_kind=line.kind,
      confirmation=conf,
      source_touches=line.touches,
      factors=factors,
    )
    if candidate is not None and (
      best is None or candidate.confluence > best.confluence
    ):
      best = candidate
  return best



# Canonical execution families a detector's evidence maps into. Local
# string constants (not imported from app.autotrade.execution_policy's
# FAMILY_* constants of the same values) - detectors.py is a pure analysis
# module with no app.autotrade dependency today, and this registry must not
# introduce one.
FAMILY_KEY_LEVEL = "key_level"
FAMILY_SUPPLY_DEMAND = "supply_demand"
FAMILY_SESSION_LEVEL = "session_level"
FAMILY_TRENDLINE = "trendline"
FAMILY_RANGE_REVERSION = "range_reversion"
FAMILY_BREAKOUT_RETEST = "breakout_retest"
FAMILY_TREND_PULLBACK = "trend_pullback"
FAMILY_MOMENTUM_CONTINUATION = "momentum_continuation"
FAMILY_LIQUIDITY_REVERSAL = "liquidity_reversal"


@dataclass(frozen=True)
class DetectorRegistration:
  """One live-or-replay-only detector source and how it is governed.

  ``enabled`` is evaluated against a DetectorSettings instance (the same
  per-request settings object every detector already receives as
  ``ctx.settings``), not the raw app config - keeps this module's existing
  decoupling from app.core.config intact. ``replay_only_reason`` must be
  set whenever ``enabled`` can ever be False for the current default
  settings, so a disabled source is always visibly explained rather than
  silently missing (recovery mission requirement: "No enabled detector may
  exist as replay-only without an explicit config switch stating that it
  is replay-only").
  """

  name: str
  detector: SetupDetector
  canonical_family: str
  enabled: Callable[["DetectorSettings"], bool]
  replay_only_reason: str | None = None


# Deterministic order: this is the exact order detectors run in and the
# exact order DEFAULT_DETECTORS is built in when every entry is enabled.
LIVE_DETECTOR_REGISTRY: tuple[DetectorRegistration, ...] = (
  DetectorRegistration(
    "key_level_reaction", key_level_reaction, FAMILY_KEY_LEVEL,
    lambda cfg: cfg.key_level_reaction_enabled,
  ),
  DetectorRegistration(
    "confluence_zone_reaction", confluence_zone_reaction, FAMILY_SUPPLY_DEMAND,
    lambda cfg: cfg.confluence_zone_enabled,
  ),
  DetectorRegistration(
    "supply_demand_technique_reaction", supply_demand_technique_reaction,
    FAMILY_SUPPLY_DEMAND,
    lambda cfg: cfg.technique_sd_enabled,
  ),
  DetectorRegistration(
    "order_block_technique_reaction", order_block_technique_reaction,
    FAMILY_SUPPLY_DEMAND,
    lambda cfg: cfg.technique_ob_enabled,
  ),
  DetectorRegistration(
    "fvg_technique_reaction", fvg_technique_reaction, FAMILY_SUPPLY_DEMAND,
    lambda cfg: cfg.technique_fvg_enabled,
  ),
  DetectorRegistration(
    "ifvg_technique_reaction", ifvg_technique_reaction, FAMILY_SUPPLY_DEMAND,
    lambda cfg: cfg.technique_ifvg_enabled,
  ),
  DetectorRegistration(
    "crt_technique_reaction", crt_technique_reaction, FAMILY_SUPPLY_DEMAND,
    lambda cfg: cfg.technique_crt_enabled,
  ),
  DetectorRegistration(
    "demand_zone_reaction", demand_zone_reaction, FAMILY_SUPPLY_DEMAND,
    lambda cfg: (
      cfg.demand_reaction_enabled and cfg.zone_reaction_fallback_enabled
    ),
    replay_only_reason=(
      "legacy Zone Reaction publisher retired in favour of named technique "
      "detectors (Supply Demand, Order Block, etc.). Enable "
      "AUTO_TRADE_ZONE_REACTION_FALLBACK_ENABLED to restore"
    ),
  ),
  DetectorRegistration(
    "supply_zone_reaction", supply_zone_reaction, FAMILY_SUPPLY_DEMAND,
    lambda cfg: (
      cfg.supply_reaction_enabled and cfg.zone_reaction_fallback_enabled
    ),
    replay_only_reason=(
      "legacy Zone Reaction publisher retired in favour of named technique "
      "detectors. Enable AUTO_TRADE_ZONE_REACTION_FALLBACK_ENABLED to restore"
    ),
  ),
  DetectorRegistration(
    "flip_demand_zone_reaction", flip_demand_zone_reaction, FAMILY_SUPPLY_DEMAND,
    lambda cfg: cfg.flip_zone_enabled,
  ),
  DetectorRegistration(
    "flip_supply_zone_reaction", flip_supply_zone_reaction, FAMILY_SUPPLY_DEMAND,
    lambda cfg: cfg.flip_zone_enabled,
  ),
  DetectorRegistration(
    "session_level_reaction", session_level_reaction, FAMILY_SESSION_LEVEL,
    lambda cfg: cfg.session_level_reaction_enabled,
  ),
  DetectorRegistration(
    "trendline_reaction", trendline_reaction, FAMILY_TRENDLINE,
    lambda cfg: cfg.trendline_reaction_enabled,
  ),
  DetectorRegistration(
    "range_edge_scalp", range_edge_scalp, FAMILY_RANGE_REVERSION,
    lambda cfg: cfg.range_scalp_enabled,
  ),
  DetectorRegistration(
    "box_breakout", box_breakout, FAMILY_BREAKOUT_RETEST,
    lambda cfg: cfg.box_breakout_enabled,
    replay_only_reason=(
      "uses its own box-consolidation confirmation, not the shared "
      "evaluate_structural_reaction path every live zone-reaction detector "
      "uses. Band-kind classification and canonical BREAKOUT_RETEST family "
      "merge are verified now (structural_source/structural_id wiring + "
      "tests/test_detectors.py, tests/test_scanner.py) - stays off by "
      "config default pending a deliberate rollout decision, same as "
      "every other feature this pipeline ships dark by default"
    ),
  ),
  DetectorRegistration(
    "break_retest", break_retest, FAMILY_BREAKOUT_RETEST,
    lambda cfg: cfg.break_retest_enabled,
    replay_only_reason=(
      "same bespoke confirmation path as box_breakout (last-closed-bar "
      "retest+rejection, not evaluate_structural_reaction's lookback-"
      "window search). Band-kind classification and canonical family "
      "merge are verified now (structural_source/structural_id wiring + "
      "tests/test_detectors.py, tests/test_scanner.py) - stays off by "
      "config default pending a deliberate rollout decision, same as "
      "every other feature this pipeline ships dark by default"
    ),
  ),
  DetectorRegistration(
    "trend_pullback", trend_pullback, FAMILY_TREND_PULLBACK,
    lambda cfg: cfg.trend_pullback_enabled,
  ),
  DetectorRegistration(
    "momentum_ride", momentum_ride, FAMILY_MOMENTUM_CONTINUATION,
    lambda cfg: cfg.momentum_ride_enabled,
  ),
  DetectorRegistration(
    "snap_back", snap_back, FAMILY_LIQUIDITY_REVERSAL,
    lambda cfg: cfg.snap_back_enabled,
  ),
  DetectorRegistration(
    "fade_scalp", fade_scalp, FAMILY_LIQUIDITY_REVERSAL,
    lambda cfg: cfg.fade_scalp_enabled,
  ),
)


def build_default_detectors(
  settings: "DetectorSettings",
) -> tuple[SetupDetector, ...]:
  """The live detector tuple for the given settings, derived from
  LIVE_DETECTOR_REGISTRY so configuration and the live registry cannot
  silently disagree - a detector enabled in configuration is present here;
  one that isn't is either genuinely disabled or explicitly documented
  above as replay_only.
  """
  return tuple(
    registration.detector
    for registration in LIVE_DETECTOR_REGISTRY
    if registration.enabled(settings)
  )


def live_detector_report(
  settings: "DetectorSettings",
) -> tuple[dict[str, object], ...]:
  """One row per registry entry - enabled state, canonical family, and (for
  anything disabled) why. Used for startup logging and the funnel report;
  never silently omits a registered source.
  """
  return tuple(
    {
      "name": registration.name,
      "canonical_family": registration.canonical_family,
      "enabled": registration.enabled(settings),
      "replay_only_reason": (
        None if registration.enabled(settings)
        else registration.replay_only_reason
      ),
    }
    for registration in LIVE_DETECTOR_REGISTRY
  )


# Computed once at import time from DetectorSettings' own defaults (which
# match today's production config exactly: the five already-live sources
# plus range_edge_scalp are enabled, the six 2026-07-28 sources are
# registered but off pending re-verification - see DetectorSettings'
# comment above). app/analysis/scanner.py reads this as a plain module
# attribute (`detectors or DEFAULT_DETECTORS`), so it must stay a tuple.
DEFAULT_DETECTORS: tuple[SetupDetector, ...] = build_default_detectors(
  DetectorSettings()
)
