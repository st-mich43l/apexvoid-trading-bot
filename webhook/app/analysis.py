"""Pure price-action analysis orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math

import pandas as pd

from app.dealing_range import dealing_range
from app.levels import key_levels
from app.liquidity import liquidity_grabs, liquidity_pools
from app.momentum import momentum
from app.pa_math import atr_scalar, atr_series
from app.pa_types import (
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
from app.regime import BoxBreak, accepted_box_break
from app.scalp_ranges import ScalpBarrier, ScalpRange, build_scalp_structure
from app.session_liquidity import previous_week_levels, session_levels
from app.structure import market_structure, structure_breaks
from app.swings import find_swings
from app.trendlines import Trendline, trendlines as find_trendlines
from app.zones import (
  ZONE_MERGE_OVERLAP,
  breaker_blocks,
  displacement,
  flip_zones,
  fvg,
  mark_mitigation,
  merge_zones,
  order_blocks,
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


@dataclass(frozen=True)
class AnalysisSettings:
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
  tl_tol_atr: float = 0.3
  tl_max_slope_atr: float = 0.15
  coil_contract: float = 0.8
  breakout_buffer_atr: float = 0.1
  breakout_accept_bars: int = 2
  breakout_max_age_bars: int = 6
  range_scalp_lookback: int = 36
  range_scalp_cluster_atr: float = 0.20
  range_scalp_min_touches: int = 3
  range_scalp_min_wick_frac: float = 0.35
  range_scalp_entry_tol_atr: float = 0.15
  range_scalp_min_width_atr: float = 1.2
  range_scalp_max_width_atr: float = 6.0
  range_scalp_min_room_atr: float = 1.0
  range_scalp_break_closes: int = 2


@dataclass(frozen=True)
class Regime:
  kind: str
  range_high: float
  range_low: float
  height_atr: float
  reasons: list[str]
  coiling: bool = False


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
  session_levels: list[SessionLevel] = field(default_factory=list)
  dealing_range: DealingRange | None = None
  regime: Regime | None = None
  trendlines: list[Trendline] = field(default_factory=list)
  box_break: BoxBreak | None = None
  scalp_barriers: list[ScalpBarrier] = field(default_factory=list)
  scalp_range: ScalpRange | None = None


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
) -> AnalysisContext:
  settings = settings or AnalysisSettings()
  frames = {
    tf.upper(): df
    for tf, df in df_by_tf.items()
    if not df.empty
  }
  weekly_levels = _weekly_session_levels(frames)
  per_tf = {
    tf: _analyze_tf(df, settings, weekly_levels)
    for tf, df in frames.items()
  }
  htf_order = htf_order or ["M30", "M15"]
  per_tf = _apply_mtf_zone_scores(per_tf, settings)
  return AnalysisContext(
    frames={tf.upper(): df for tf, df in df_by_tf.items()},
    per_tf=per_tf,
    htf_bias=_htf_bias(per_tf, htf_order),
    dealing_range=_exec_dealing_range(per_tf),
    regime=_exec_regime(per_tf),
  )


def _analyze_tf(
  df: pd.DataFrame,
  settings: AnalysisSettings,
  weekly_levels: list[SessionLevel] | None = None,
) -> TimeframeAnalysis:
  atr = atr_series(df, settings.atr_length)
  swings = find_swings(
    df,
    settings.swing_fractal_n,
    settings.zigzag_pct,
    settings.zigzag_atr_mult,
    atr,
  )
  structure = market_structure(swings)
  breaks = structure_breaks(swings, df)
  diagonal_lines = find_trendlines(swings, df, atr, settings)
  levels = key_levels(
    swings,
    atr,
    settings.level_cluster_atr,
    settings.round_step,
    settings.key_level_min_touches,
  )
  legs = displacement(
    df,
    atr,
    settings.displacement_atr_mult,
    settings.momentum_body_frac,
  )
  sd_zones = supply_demand(df, legs)
  ob_zones = order_blocks(df, legs, breaks, settings.zone_width)
  ob_zones = breaker_blocks(ob_zones, df)
  flip = flip_zones(levels, breaks)
  fvg_zones = fvg(df)
  pools = liquidity_pools(swings, df, settings.equal_tol_atr, atr)
  sessions = [
    *session_levels(df, settings),
    *(weekly_levels or []),
  ]
  range_ = dealing_range(
    swings,
    float(df["close"].iloc[-1]),
    settings.eq_band,
  )
  regime_ = regime(df, atr, structure, range_, settings)
  box_break = accepted_box_break(df, atr, regime_, settings)
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
  )
  scalp_barriers, scalp_range = build_scalp_structure(
    df,
    atr,
    sessions,
    diagonal_lines,
    regime_,
    settings,
  )
  ob_zones, sd_zones, flip, fvg_zones = _zone_views(zones)
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
    momentum=momentum(df, atr, settings.momentum_lookback, settings.momentum_body_frac),
    session_levels=sessions,
    dealing_range=range_,
    regime=regime_,
    trendlines=diagonal_lines,
    box_break=box_break,
    scalp_barriers=scalp_barriers,
    scalp_range=scalp_range,
  )


def regime(
  df: pd.DataFrame,
  atr: pd.Series,
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
    )
  if range_ is None:
    return Regime("trend", close, close, math.inf, ["no dealing range"], coiling)

  range_high = float(range_.high)
  range_low = float(range_.low)
  height = max(0.0, range_high - range_low)
  atr_value = atr_scalar(atr)
  height_atr = height / atr_value if atr_value > 0 else math.inf
  reasons = []
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
  kind = "chop" if reasons else "trend"
  if not reasons:
    reasons = ["range expanded or broke edge"]
  return Regime(kind, range_high, range_low, height_atr, reasons, coiling)


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
