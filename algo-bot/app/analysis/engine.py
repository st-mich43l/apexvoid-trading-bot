"""Pure price-action analysis orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from types import SimpleNamespace
from typing import Any

import pandas as pd

from app.analysis.dealing_range import dealing_range
from app.analysis.fibonacci import FibLevel, fib_from_swings, nearest_fib
from app.analysis.levels import key_levels
from app.analysis.liquidity import liquidity_grabs, liquidity_pools
from app.analysis.momentum import MomentumState, momentum_state
from app.analysis.math_utils import atr_scalar, atr_series
from app.analysis.types import (
  Break,
  DealingRange,
  Grab,
  Leg,
  Level,
  Pool,
  SessionLevel,
  Swing,
  Zone,
)
from app.analysis.regime import BoxBreak, accepted_box_break
from app.analysis.scalp_ranges import ScalpBarrier, ScalpRange, build_scalp_structure
from app.analysis.session_liquidity import previous_week_levels, session_levels
from app.analysis.structure import market_structure, structure_breaks
from app.analysis.swings import find_swings
from app.analysis.trendlines import Trendline, trendlines as find_trendlines
from app.analysis.technique_geometry import (
  TechniqueGeometrySettings,
  TechniqueInstance,
  collect_technique_instances,
)
from app.analysis.zones import (
  ZONE_MERGE_OVERLAP,
  ZONE_MIN_WIDTH,
  breaker_blocks,
  displacement,
  flip_zones,
  fvg,
  mark_mitigation,
  merge_zones,
  order_blocks,
  reconcile_opposing,
  score_zones,
  supply_demand,
)

_TF_MINUTES = {
  "M1": 1,
  "M3": 3,
  "M5": 5,
  "M15": 15,
  "M30": 30,
  "H1": 60,
  "H4": 240,
  "D1": 1440,
}


def _nested_cfg_from_analysis_settings(settings: AnalysisSettings) -> Any:
  """Project the engine's flat ``AnalysisSettings`` DTO into the nested
  canonical shape expected by trendlines / session_liquidity / regime /
  scalp_ranges after Phase 2I-A.1.

  This is an internal composition adapter only — it is not a second
  configuration system and does not read ENV or legacy Settings.
  """
  def tree(data: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(**{
      key: tree(value) if isinstance(value, dict) else value
      for key, value in data.items()
    })

  return tree({
    "units": {
      "pip_size": settings.pip_size,
    },
    "analysis": {
      "trendlines": {
        "tolerance_atr": settings.tl_tol_atr,
        "pierce_tolerance_atr": settings.tl_pierce_tolerance_atr,
        "minimum_slope_atr": settings.tl_min_slope_atr,
        "maximum_slope_atr": settings.tl_max_slope_atr,
        "minimum_touches": settings.tl_min_touches,
        "maximum_touches": settings.tl_max_touches,
        "minimum_span_bars": settings.tl_min_span_bars,
        "minimum_touch_spacing_bars": settings.tl_min_touch_spacing_bars,
        "maximum_bars_since_last_touch": settings.tl_max_bars_since_last_touch,
        "maximum_fit_error_atr": settings.tl_max_fit_error_atr,
        "maximum_violations": settings.tl_max_violations,
      },
      "breakout": {
        "buffer_atr": settings.breakout_buffer_atr,
        "accept_bars": settings.breakout_accept_bars,
        "max_age_bars": settings.breakout_max_age_bars,
      },
      "levels": {
        "round_step": settings.round_step,
      },
    },
    "market_data": {
      "sessions": {
        "asia_start": settings.session_asia_start,
        "london_start": settings.session_london_start,
        "ny_start": settings.session_ny_start,
        "daily_rollover_utc_hour": settings.daily_rollover_utc_hour,
      },
    },
    "strategies": {
      "range_reversion": {
        "range_edge": {
          "lookback": settings.range_scalp_lookback,
          "cluster_atr": settings.range_scalp_cluster_atr,
          "cluster_min_abs": 0.0,
          "min_touches": settings.range_scalp_min_touches,
          "min_wick_frac": settings.range_scalp_min_wick_frac,
          "entry_tol_atr": settings.range_scalp_entry_tol_atr,
          "max_edge_width_atr": 0.75,
          "min_width_atr": settings.range_scalp_min_width_atr,
          "max_width_atr": settings.range_scalp_max_width_atr,
          "min_room_atr": settings.range_scalp_min_room_atr,
          "break_closes": settings.range_scalp_break_closes,
          "min_inside_closes": 3,
        },
      },
      "scalp": {
        "scalp_barrier_fallback_enabled": True,
        "scalp_barrier_fallback_min_confirmations": 1,
        "scalp_range_provisional_enabled": True,
        "scalp_post_impulse_range_enabled": True,
      },
    },
  })


@dataclass(frozen=True)
class AnalysisSettings:
  pip_size: float = 0.1
  atr_length: int = 14
  swing_fractal_n: int = 2
  zigzag_pct: float = 0.0
  zigzag_atr_mult: float = 1.0
  displacement_atr_mult: float = 1.5
  zone_width: str = "body"
  zone_merge_overlap: float = ZONE_MERGE_OVERLAP
  max_merged_zone_atr: float = 3.0
  equal_tol_atr: float = 0.15
  level_cluster_atr: float = 0.5
  round_step: float = 5.0
  key_level_min_touches: int = 2
  momentum_lookback: int = 8
  momentum_body_frac: float = 0.6
  momentum_velocity_lookback: int = 8
  momentum_velocity_bull_threshold: float = 0.15
  momentum_velocity_bear_threshold: float = -0.15
  momentum_va_gate_enabled: bool = False
  fibonacci_enabled: bool = True
  fibonacci_epsilon_atr: float = 0.15
  fibonacci_confluence_weight: float = 2.5
  fibonacci_deep_discount: float = 0.382
  fibonacci_deep_premium: float = 0.618
  session_asia_start: int = 22
  session_london_start: int = 7
  session_ny_start: int = 13
  daily_rollover_utc_hour: int = 21
  eq_band: float = 0.10
  sweep_body_frac: float = 0.5
  sweep_react_bars: int = 3
  inducement_band_atr: float = 0.3
  chop_filter_enabled: bool = True
  chop_range_atr: float = 4.0
  chop_lookback: int = 24
  tl_min_touches: int = 3
  tl_max_touches: int = 4
  tl_tol_atr: float = 0.3
  tl_pierce_tolerance_atr: float = 0.5
  tl_min_slope_atr: float = 0.02
  tl_max_slope_atr: float = 0.15
  tl_min_span_bars: int = 20
  tl_min_touch_spacing_bars: int = 3
  tl_max_bars_since_last_touch: int = 30
  tl_max_fit_error_atr: float = 0.15
  tl_max_violations: int = 2
  coil_contract: float = 0.8
  breakout_buffer_atr: float = 0.1
  breakout_accept_bars: int = 2
  breakout_max_age_bars: int = 6
  flip_zone_accept_bars: int | None = None
  flip_zone_max_break_age_bars: int = 48
  flip_band_body_fraction: float = 0.5
  range_scalp_lookback: int = 36
  range_scalp_cluster_atr: float = 0.20
  range_scalp_min_touches: int = 3
  range_scalp_min_wick_frac: float = 0.35
  range_scalp_entry_tol_atr: float = 0.15
  range_scalp_min_width_atr: float = 1.2
  range_scalp_max_width_atr: float = 6.0
  range_scalp_min_room_atr: float = 1.0
  range_scalp_break_closes: int = 2
  zone_reconcile_enabled: bool = True
  zone_reconcile_mode: str = "enforce"
  regime_direction_enabled: bool = False
  regime_direction_lookback: int = 120
  regime_min_directional_swings: int = 3
  regime_min_displacement_atr: float = 4.0
  crt_min_atr: float = 1.5
  crt_reclaim_bars: int = 6
  crt_entry_max_width_price: float = 5.0
  crt_h1_lookback_bars: int = 3
  fvg_entry_max_width_price: float = 5.0
  fvg_max_atr: float = 2.0
  technique_validation_enabled: bool = True
  causal_structure: bool = False
  max_cluster_span_multiple: float = 2.0


@dataclass(frozen=True)
class Regime:
  kind: str
  range_high: float
  range_low: float
  height_atr: float
  reasons: list[str]
  coiling: bool = False
  legacy_kind: str = "trend"
  new_kind: str = "trend"
  # Counterfactual detail for regime_compare DEBUG logs (always populated
  # when the directional test would override, even with the flag off).
  directional_detail: str = ""


@dataclass(frozen=True)
class TimeframeAnalysis:
  df: pd.DataFrame
  atr: pd.Series
  swings: list[Swing]
  structure: str
  breaks: list[Break]
  key_levels: list[Level]
  legs: list[Leg]
  supply_demand_zones: list[Zone]
  order_blocks: list[Zone]
  flip_zones: list[Zone]
  fvg_zones: list[Zone]
  zones: list[Zone]
  liquidity_pools: list[Pool]
  liquidity_grabs: list[Grab]
  momentum: str
  momentum_state: MomentumState | None = None
  fib_levels: list[FibLevel] = field(default_factory=list)
  nearest_fib: FibLevel | None = None
  session_levels: list[SessionLevel] = field(default_factory=list)
  dealing_range: DealingRange | None = None
  regime: Regime | None = None
  trendlines: list[Trendline] = field(default_factory=list)
  box_break: BoxBreak | None = None
  scalp_barriers: list[ScalpBarrier] = field(default_factory=list)
  scalp_range: ScalpRange | None = None
  # reconcile_opposing() diagnostics (zones.py) - dropped-zone count and
  # whether the circuit breaker discarded this pass's reconciliation.
  zone_reconcile_dropped: int = 0
  zone_reconcile_aborted: bool = False
  zone_reconcile_input: int = 0
  zone_reconcile_shadow_output: int = 0
  zone_reconcile_trimmed: int = 0
  zone_reconcile_candidate_difference_count: int = 0
  technique_instances: list[TechniqueInstance] = field(default_factory=list)
  technique_validation_rejects: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisContext:
  frames: dict[str, pd.DataFrame]
  per_tf: dict[str, TimeframeAnalysis]
  htf_bias: str
  dealing_range: DealingRange | None = None
  regime: Regime | None = None


def analyze(
  df_by_tf: dict[str, pd.DataFrame],
  settings: AnalysisSettings | None = None,
  htf_order: list[str] | None = None,
  *,
  symbol: str = "",
  metric_sink=None,
) -> AnalysisContext:
  settings = settings or AnalysisSettings()
  frames = {
    tf.upper(): df
    for tf, df in df_by_tf.items()
    if not df.empty
  }
  weekly_levels = _weekly_session_levels(frames)
  per_tf = {
    tf: _analyze_tf(
      df,
      settings,
      weekly_levels,
      timeframe=tf,
      symbol=symbol,
      metric_sink=metric_sink,
    )
    for tf, df in frames.items()
  }
  htf_order = htf_order or ["H1", "M15"]
  per_tf = _apply_mtf_zone_scores(per_tf, settings)
  per_tf = _attach_technique_instances(per_tf, settings)
  return AnalysisContext(
    frames={tf.upper(): df for tf, df in df_by_tf.items()},
    per_tf=per_tf,
    htf_bias=_htf_bias(per_tf, htf_order),
    dealing_range=_exec_dealing_range(per_tf),
    regime=_exec_regime(per_tf),
  )


def _attach_technique_instances(
  per_tf: dict[str, TimeframeAnalysis],
  settings: AnalysisSettings,
) -> dict[str, TimeframeAnalysis]:
  """Classify unmerged technique instances before map-only zone merge."""
  h1 = per_tf.get("H1")
  h1_df = h1.df if h1 is not None else None
  h1_atr = atr_scalar(h1.atr) if h1 is not None else 0.0
  reference_atr = h1_atr if h1_atr > 0 else max(
    (atr_scalar(item.atr) for item in per_tf.values()),
    default=0.0,
  )
  geom = TechniqueGeometrySettings(
    momentum_body_frac=settings.momentum_body_frac,
    confluence_min_overlap=settings.zone_merge_overlap,
    pip_size=max(settings.pip_size, 1e-12),
    zone_merge_max_width=(
      max(0.0, settings.max_merged_zone_atr) * reference_atr
    ),
    crt_min_atr=float(settings.crt_min_atr),
    crt_reclaim_bars=int(settings.crt_reclaim_bars),
    crt_entry_max_width_price=float(settings.crt_entry_max_width_price),
    crt_h1_lookback_bars=int(settings.crt_h1_lookback_bars),
    fvg_entry_max_width_price=float(settings.fvg_entry_max_width_price),
    fvg_max_atr=float(settings.fvg_max_atr),
  )
  updated: dict[str, TimeframeAnalysis] = {}
  for tf, analysis in per_tf.items():
    exec_atr = atr_scalar(analysis.atr)
    price = float(analysis.df.iloc[-1]["close"]) if not analysis.df.empty else 0.0
    instances, rejects = collect_technique_instances(
      sd_zones=analysis.supply_demand_zones,
      ob_zones=analysis.order_blocks,
      fvg_zones=analysis.fvg_zones,
      df=analysis.df,
      price=price,
      atr=exec_atr,
      h1_df=h1_df if tf.upper() != "H1" else None,
      h1_atr=h1_atr,
      exec_atr=exec_atr,
      settings=geom,
      validation_enabled=settings.technique_validation_enabled,
    )
    updated[tf] = replace(
      analysis,
      technique_instances=instances,
      technique_validation_rejects=rejects,
    )
  return updated


def _analyze_tf(
  df: pd.DataFrame,
  settings: AnalysisSettings,
  weekly_levels: list[SessionLevel] | None = None,
  *,
  timeframe: str = "",
  symbol: str = "",
  metric_sink=None,
) -> TimeframeAnalysis:
  atr = atr_series(df, settings.atr_length)
  swings = find_swings(
    df,
    settings.swing_fractal_n,
    settings.zigzag_pct,
    settings.zigzag_atr_mult,
    atr,
    as_of=len(df) - 1 if settings.causal_structure else None,
  )
  structure = market_structure(swings)
  breaks = structure_breaks(
    swings,
    df,
    causal=settings.causal_structure,
    fractal_n=settings.swing_fractal_n,
  )
  nested_cfg = _nested_cfg_from_analysis_settings(settings)
  diagonal_lines = find_trendlines(
    swings,
    df,
    atr,
    nested_cfg,
    symbol=symbol,
    timeframe=timeframe,
    metric_sink=metric_sink,
  )
  levels = key_levels(
    swings,
    atr,
    settings.level_cluster_atr,
    settings.round_step,
    settings.key_level_min_touches,
    settings.max_cluster_span_multiple,
  )
  legs = displacement(
    df,
    atr,
    settings.displacement_atr_mult,
    settings.momentum_body_frac,
  )
  sd_zones = supply_demand(df, legs)
  # A plain supply/demand zone is exactly as violable as an order block - a
  # demand zone price later closes decisively below is no longer demand, it's
  # broken structure that should act as supply on any later retest from
  # below (and vice versa). breaker_blocks already implements this "closed
  # through -> dead + flipped" rule generically (see _breaker_violation) and
  # was only ever wired to order_blocks; apply it here too so a stale,
  # already-broken zone never keeps opposing trades in its original,
  # long-since-invalidated direction.
  sd_zones = breaker_blocks(sd_zones, df)
  ob_zones = order_blocks(df, legs, breaks, settings.zone_width)
  ob_zones = breaker_blocks(ob_zones, df)
  flip_accept = (
    settings.breakout_accept_bars
    if settings.flip_zone_accept_bars is None
    else settings.flip_zone_accept_bars
  )
  flip = flip_zones(
    levels,
    breaks,
    df,
    accept_bars=flip_accept,
    max_break_age_bars=settings.flip_zone_max_break_age_bars,
    band_body_fraction=settings.flip_band_body_fraction,
    metric_sink=metric_sink,
    symbol=symbol,
    timeframe=timeframe,
  )
  fvg_zones = fvg(df)
  pools = liquidity_pools(
    swings,
    df,
    settings.equal_tol_atr,
    atr,
    settings.max_cluster_span_multiple,
  )
  sessions = [
    *session_levels(df, nested_cfg),
    *(weekly_levels or []),
  ]
  range_ = dealing_range(
    swings,
    float(df["close"].iloc[-1]),
    settings.eq_band,
    deep_discount=settings.fibonacci_deep_discount,
    deep_premium=settings.fibonacci_deep_premium,
  )
  regime_ = regime(df, atr, swings, structure, range_, settings)
  box_break = accepted_box_break(df, atr, regime_, nested_cfg)
  zones = merge_zones(
    [*sd_zones, *ob_zones, *flip, *fvg_zones],
    settings.zone_merge_overlap,
    atr_scalar(atr) * max(0.0, settings.max_merged_zone_atr),
  )
  zones = mark_mitigation(zones, df, cutoff=max(0, len(df) - 1))
  grabs = liquidity_grabs(
    df,
    pools,
    legs,
    zones,
    atr,
    settings.sweep_body_frac,
    settings.sweep_react_bars,
    settings.inducement_band_atr,
    settings.pip_size,
  )
  zones = score_zones(
    zones,
    levels,
    pools,
    settings.round_step,
    session_levels=sessions,
    dealing_range=range_,
    grabs=grabs,
    trendlines=diagonal_lines,
    bar_index=len(df) - 1,
    pip_size=settings.pip_size,
  )
  zone_reconcile_dropped = 0
  zone_reconcile_aborted = False
  zone_reconcile_input = len(zones)
  zone_reconcile_shadow_output = len(zones)
  zone_reconcile_trimmed = 0
  zone_reconcile_candidate_difference_count = 0
  reconcile_mode = (
    settings.zone_reconcile_mode.strip().lower()
    if settings.zone_reconcile_enabled else "off"
  )
  if reconcile_mode in {"shadow", "enforce"}:
    reconcile_stats: dict = {}
    # 2026-07-31: reconcile_opposing's circuit breaker (see zones.py's
    # ZONE_RECONCILE_MAX_FRACTION) was aborting on nearly every call in
    # production - replaying real live OHLC showed why: of a typical
    # ~69-zone set, only ~2 were still unmitigated (live). The other ~67
    # were historical zones price had already traded through - two dead,
    # long-since-irrelevant zones on opposite sides overlapping is normal
    # and expected over hundreds of bars, not a sign of a broken zone map,
    # but it was counted the same as a live conflict and tripped the
    # breaker on effectively every pass. Reconcile only the zones that are
    # still live; mitigated zones pass through untouched (nothing here
    # ever prunes them - detectors already filter zone.mitigated
    # themselves) and never count toward the circuit breaker's fraction.
    live_zones = [zone for zone in zones if not zone.mitigated]
    mitigated_zones = [zone for zone in zones if zone.mitigated]
    reconciled_live = reconcile_opposing(
      live_zones,
      min(0.3 * atr_scalar(atr), ZONE_MIN_WIDTH),
      stats=reconcile_stats,
    )
    reconciled = [*reconciled_live, *mitigated_zones]
    zone_reconcile_dropped = reconcile_stats.get("dropped", 0)
    zone_reconcile_aborted = reconcile_stats.get("aborted", False)
    zone_reconcile_shadow_output = len(reconciled)
    zone_reconcile_trimmed = int(reconcile_stats.get("trimmed", 0))
    original_geometry = {
      (zone.side, round(zone.low, 6), round(zone.high, 6))
      for zone in zones
    }
    reconciled_geometry = {
      (zone.side, round(zone.low, 6), round(zone.high, 6))
      for zone in reconciled
    }
    zone_reconcile_candidate_difference_count = len(
      original_geometry.symmetric_difference(reconciled_geometry)
    )
    if reconcile_mode == "enforce":
      zones = reconciled
  scalp_barriers, scalp_range = build_scalp_structure(
    df,
    atr,
    sessions,
    diagonal_lines,
    regime_,
    nested_cfg,
  )
  ob_zones, sd_zones, flip, fvg_zones = _zone_views(zones)
  close_price = float(df["close"].iloc[-1])
  atr_value = atr_scalar(atr)
  mom = momentum_state(
    df,
    atr,
    settings.momentum_velocity_lookback,
    settings.momentum_velocity_bull_threshold,
    settings.momentum_velocity_bear_threshold,
  )
  fib_levels: list[FibLevel] = []
  near_fib: FibLevel | None = None
  if settings.fibonacci_enabled:
    fib_levels = fib_from_swings(swings, close_price)
    near_fib = nearest_fib(
      fib_levels,
      close_price,
      atr_value,
      settings.fibonacci_epsilon_atr,
    )
  return TimeframeAnalysis(
    df=df,
    atr=atr,
    swings=swings,
    structure=structure,
    breaks=breaks,
    key_levels=levels,
    legs=legs,
    supply_demand_zones=sd_zones,
    order_blocks=ob_zones,
    flip_zones=flip,
    fvg_zones=fvg_zones,
    zones=zones,
    liquidity_pools=pools,
    liquidity_grabs=grabs,
    momentum=mom.state,
    momentum_state=mom,
    fib_levels=fib_levels,
    nearest_fib=near_fib,
    session_levels=sessions,
    dealing_range=range_,
    regime=regime_,
    trendlines=diagonal_lines,
    box_break=box_break,
    scalp_barriers=scalp_barriers,
    scalp_range=scalp_range,
    zone_reconcile_dropped=zone_reconcile_dropped,
    zone_reconcile_aborted=zone_reconcile_aborted,
    zone_reconcile_input=zone_reconcile_input,
    zone_reconcile_shadow_output=zone_reconcile_shadow_output,
    zone_reconcile_trimmed=zone_reconcile_trimmed,
    zone_reconcile_candidate_difference_count=(
      zone_reconcile_candidate_difference_count
    ),
  )


def regime(
  df: pd.DataFrame,
  atr: pd.Series,
  swings: list[Swing],
  structure: str,
  range_: DealingRange | None,
  settings: AnalysisSettings | None = None,
) -> Regime:
  settings = settings or AnalysisSettings()
  close = _last_close(df)
  coiling = _is_coiling(df, settings.chop_lookback, settings.coil_contract)
  if not settings.chop_filter_enabled:
    return Regime(
      "trend",
      close,
      close,
      math.inf,
      ["chop filter disabled"],
      coiling,
      "trend",
      "trend",
    )
  if range_ is None:
    return Regime(
      "trend",
      close,
      close,
      math.inf,
      ["no dealing range"],
      coiling,
      "trend",
      "trend",
    )

  range_high = float(range_.high)
  range_low = float(range_.low)
  height = max(0.0, range_high - range_low)
  atr_value = atr_scalar(atr)
  height_atr = height / atr_value if atr_value > 0 else math.inf
  reasons: list[str] = []
  if height_atr < max(0.0, settings.chop_range_atr):
    reasons.append(
      f"range height {height_atr:.2f} ATR < {settings.chop_range_atr:.2f}"
    )
  if structure == "range" and _closes_inside_range(
    df,
    range_low,
    range_high,
    settings.chop_lookback,
  ):
    reasons.append(f"range structure held {max(1, settings.chop_lookback)} bars")
  legacy_kind = "chop" if reasons else "trend"
  kind = legacy_kind
  new_kind = legacy_kind
  directional_detail = ""

  override = _directional_trend_override(
    df,
    swings,
    atr_value,
    settings,
  )
  if override is not None:
    pair_count, label, net_displacement, lookback = override
    directional_detail = (
      f"{pair_count} {label}, net {net_displacement:.1f} ATR"
    )
    override_reasons = [
      (
        f"trend (directional override): {pair_count} consecutive {label}, "
        f"net {net_displacement:.1f} ATR over {lookback} bars"
      ),
    ]
    if legacy_kind == "chop" and reasons:
      override_reasons.append(
        f"  [{reasons[0]} would have said chop]"
      )
    new_kind = "trend"
    if settings.regime_direction_enabled:
      kind = "trend"
      reasons = override_reasons

  if not reasons:
    reasons = ["range expanded or broke edge"]

  return Regime(
    kind,
    range_high,
    range_low,
    height_atr,
    reasons,
    coiling,
    legacy_kind,
    new_kind,
    directional_detail,
  )


def _directional_pairs(swings: list[Swing]) -> list[int]:
  """Classify adjacent swing pairs as bullish (+1) or bearish (-1)."""
  pairs: list[int] = []
  index = 0
  while index < len(swings) - 1:
    first, second = swings[index], swings[index + 1]
    labels = {first.label, second.label}
    if labels == {"LH", "LL"}:
      pairs.append(-1)
      index += 2
    elif labels == {"HH", "HL"}:
      pairs.append(1)
      index += 2
    else:
      index += 1
  return pairs


def directional_trend_override(
  df: pd.DataFrame,
  swings: list[Swing],
  atr_value: float,
  *,
  lookback: int = 120,
  min_directional_swings: int = 3,
  min_displacement_atr: float = 4.0,
) -> tuple[int, str, float, int] | None:
  """Return (pair_count, label, net_displacement_atr, lookback) when trending.

  A window is trending when it has enough same-direction swing pairs, at most
  one counter-direction pair, and net displacement clears the ATR floor.
  """
  if df.empty or atr_value <= 0:
    return None
  lookback = max(1, int(lookback))
  start_idx = max(0, len(df) - lookback)
  window = df.iloc[start_idx:]
  window_swings = [
    swing for swing in swings
    if int(swing.index) >= start_idx
  ]
  pairs = _directional_pairs(window_swings)
  bullish = sum(1 for pair in pairs if pair > 0)
  bearish = sum(1 for pair in pairs if pair < 0)
  first_close = float(window["close"].iloc[0])
  last_close = float(window["close"].iloc[-1])
  net_displacement = (last_close - first_close) / atr_value
  min_swings = max(1, int(min_directional_swings))
  min_disp = max(0.0, float(min_displacement_atr))

  if (
    bearish >= min_swings
    and bullish <= 1
    and net_displacement <= -min_disp
  ):
    return bearish, "LH/LL", net_displacement, lookback
  if (
    bullish >= min_swings
    and bearish <= 1
    and net_displacement >= min_disp
  ):
    return bullish, "HH/HL", net_displacement, lookback
  return None


def _directional_trend_override(
  df: pd.DataFrame,
  swings: list[Swing],
  atr_value: float,
  settings: AnalysisSettings,
) -> tuple[int, str, float, int] | None:
  return directional_trend_override(
    df,
    swings,
    atr_value,
    lookback=settings.regime_direction_lookback,
    min_directional_swings=settings.regime_min_directional_swings,
    min_displacement_atr=settings.regime_min_displacement_atr,
  )


def _is_coiling(df: pd.DataFrame, lookback: int, contract: float) -> bool:
  required = max(2, int(lookback))
  if len(df) < required:
    return False
  window = df.tail(required)
  split = len(window) // 2
  first = window.iloc[:split]
  second = window.iloc[split:]
  first_range = float(first["high"].max() - first["low"].min())
  second_range = float(second["high"].max() - second["low"].min())
  return first_range > 0 and second_range < max(0.0, contract) * first_range


def _last_close(df: pd.DataFrame) -> float:
  if df.empty:
    return 0.0
  value = float(df["close"].iloc[-1])
  return value if math.isfinite(value) else 0.0


def _closes_inside_range(
  df: pd.DataFrame,
  low: float,
  high: float,
  lookback: int,
) -> bool:
  if df.empty:
    return False
  required = max(1, lookback)
  if len(df) < required:
    return False
  closes = df["close"].tail(required)
  if closes.empty:
    return False
  return bool(((closes >= low) & (closes <= high)).all())


def _apply_mtf_zone_scores(
  per_tf: dict[str, TimeframeAnalysis],
  settings: AnalysisSettings,
) -> dict[str, TimeframeAnalysis]:
  updated = dict(per_tf)
  higher_zones: list[Zone] = []
  for tf in _ordered_tfs(updated):
    item = updated[tf]
    if higher_zones:
      zones = score_zones(
        item.zones,
        item.key_levels,
        item.liquidity_pools,
        settings.round_step,
        higher_zones,
        item.session_levels,
        item.dealing_range,
        item.liquidity_grabs,
        item.trendlines,
        len(item.df) - 1,
        settings.pip_size,
      )
      item = _with_zone_views(item, zones)
      updated[tf] = item
    higher_zones.extend(item.zones)
  return updated


def _ordered_tfs(per_tf: dict[str, TimeframeAnalysis]) -> list[str]:
  return sorted(per_tf, key=lambda tf: (-_tf_rank(tf), tf))


def _tf_rank(tf: str) -> int:
  tf = tf.upper()
  if tf in _TF_MINUTES:
    return _TF_MINUTES[tf]
  unit = tf[-1:]
  number = tf[:-1]
  if number.isdigit():
    value = int(number)
    if unit == "M":
      return value
    if unit == "H":
      return value * 60
    if unit == "D":
      return value * 1440
  return 0


def _with_zone_views(
  item: TimeframeAnalysis,
  zones: list[Zone],
) -> TimeframeAnalysis:
  ob_zones, sd_zones, flip, fvg_zones = _zone_views(zones)
  return replace(
    item,
    supply_demand_zones=sd_zones,
    order_blocks=ob_zones,
    flip_zones=flip,
    fvg_zones=fvg_zones,
    zones=zones,
  )


def _zone_views(
  zones: list[Zone],
) -> tuple[list[Zone], list[Zone], list[Zone], list[Zone]]:
  ob_zones = [zone for zone in zones if _has_source(zone, "order_block")]
  sd_zones = [zone for zone in zones if _has_source(zone, "supply_demand")]
  flip = [zone for zone in zones if _has_source(zone, "flip_zone")]
  fvg_zones = [
    zone for zone in zones
    if any(source.endswith("_fvg") for source in zone.sources)
  ]
  return ob_zones, sd_zones, flip, fvg_zones


def _has_source(zone: Zone, source: str) -> bool:
  return source in zone.sources or zone.source == source


def _weekly_session_levels(frames: dict[str, pd.DataFrame]) -> list[SessionLevel]:
  if not frames:
    return []
  tf = _ordered_frame_tfs(frames)[0]
  return previous_week_levels(frames[tf])


def _ordered_frame_tfs(frames: dict[str, pd.DataFrame]) -> list[str]:
  return sorted(frames, key=lambda tf: (-_tf_rank(tf), tf))


def _exec_dealing_range(
  per_tf: dict[str, TimeframeAnalysis],
) -> DealingRange | None:
  if not per_tf:
    return None
  tf = sorted(per_tf, key=lambda item: (_tf_rank(item), item))[0]
  return per_tf[tf].dealing_range


def _exec_regime(
  per_tf: dict[str, TimeframeAnalysis],
) -> Regime | None:
  if not per_tf:
    return None
  tf = sorted(per_tf, key=lambda item: (_tf_rank(item), item))[0]
  return per_tf[tf].regime


@dataclass(frozen=True)
class _TfLabelView:
  """Duck-typed stand-in for TimeframeAnalysis, carrying only the fields the
  label helpers read."""
  structure: str
  momentum: str
  regime: Regime | None


def analysis_labels(
  df_by_tf: dict[str, pd.DataFrame],
  settings: AnalysisSettings | None = None,
  htf_order: list[str] | None = None,
) -> tuple[str, str, str]:
  """Return ``(htf_bias, exec_structure, regime_kind)`` without building the
  full zone/technique stack. Values match ``analyze()`` exactly."""
  settings = settings or AnalysisSettings()
  frames = {
    tf.upper(): df
    for tf, df in df_by_tf.items()
    if isinstance(df, pd.DataFrame) and not df.empty
  }
  if not frames:
    return ("unknown", "unknown", "unknown")

  per_tf: dict[str, _TfLabelView] = {}
  for tf, df in frames.items():
    atr = atr_series(df, settings.atr_length)
    swings = find_swings(
      df,
      settings.swing_fractal_n,
      settings.zigzag_pct,
      settings.zigzag_atr_mult,
      atr,
      as_of=len(df) - 1 if settings.causal_structure else None,
    )
    structure = market_structure(swings)
    range_ = dealing_range(
      swings,
      float(df["close"].iloc[-1]),
      settings.eq_band,
      deep_discount=settings.fibonacci_deep_discount,
      deep_premium=settings.fibonacci_deep_premium,
    )
    regime_ = regime(df, atr, swings, structure, range_, settings)
    mom = momentum_state(
      df,
      atr,
      settings.momentum_velocity_lookback,
      settings.momentum_velocity_bull_threshold,
      settings.momentum_velocity_bear_threshold,
    )
    per_tf[tf] = _TfLabelView(
      structure=structure,
      momentum=mom.state,
      regime=regime_,
    )

  htf_bias = _htf_bias(per_tf, htf_order or ["H1", "M15"])
  exec_tf = sorted(per_tf, key=lambda item: (_tf_rank(item), item))[0]
  exec_structure = per_tf[exec_tf].structure
  r = _exec_regime(per_tf)
  regime_kind = r.kind if r is not None else "unknown"
  return htf_bias, exec_structure, regime_kind


def _htf_bias(
  per_tf: dict[str, TimeframeAnalysis],
  htf_order: list[str],
) -> str:
  for tf in htf_order:
    item = per_tf.get(tf.upper())
    if item is None:
      continue
    bias = _bias_from_tf(item)
    if bias != "range":
      return bias
  for tf in _ordered_tfs(per_tf):
    item = per_tf[tf]
    bias = _bias_from_tf(item)
    if bias != "range":
      return bias
  return "range"


def _bias_from_tf(item: TimeframeAnalysis) -> str:
  if item.structure == "up" and item.momentum != "bear":
    return "up"
  if item.structure == "down" and item.momentum != "bull":
    return "down"
  if item.momentum == "bull":
    return "up"
  if item.momentum == "bear":
    return "down"
  return "range"
