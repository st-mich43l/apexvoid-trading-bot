"""Pure price-action analysis orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from app.levels import key_levels
from app.liquidity import liquidity_grabs, liquidity_pools
from app.momentum import momentum
from app.pa_math import atr_series
from app.pa_types import Break, Grab, Leg, Level, Pool, Swing, Zone
from app.structure import market_structure, structure_breaks
from app.swings import find_swings
from app.zones import (
  ZONE_MERGE_OVERLAP,
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
  equal_tol_atr: float = 0.15
  level_cluster_atr: float = 0.5
  round_step: float = 5.0
  key_level_min_touches: int = 2
  momentum_lookback: int = 8
  momentum_body_frac: float = 0.6


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


@dataclass(frozen=True)
class AnalysisContext:
  frames: dict[str, pd.DataFrame]
  per_tf: dict[str, TimeframeAnalysis]
  htf_bias: str


def analyze(
  df_by_tf: dict[str, pd.DataFrame],
  settings: AnalysisSettings | None = None,
  htf_order: list[str] | None = None,
) -> AnalysisContext:
  settings = settings or AnalysisSettings()
  per_tf = {
    tf.upper(): _analyze_tf(df, settings)
    for tf, df in df_by_tf.items()
    if not df.empty
  }
  htf_order = htf_order or ["M30", "M15"]
  per_tf = _apply_mtf_zone_scores(per_tf, settings)
  return AnalysisContext(
    frames={tf.upper(): df for tf, df in df_by_tf.items()},
    per_tf=per_tf,
    htf_bias=_htf_bias(per_tf, htf_order),
  )


def _analyze_tf(df: pd.DataFrame, settings: AnalysisSettings) -> TimeframeAnalysis:
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
  flip = flip_zones(levels, breaks)
  fvg_zones = fvg(df)
  pools = liquidity_pools(swings, df, settings.equal_tol_atr, atr)
  grabs = liquidity_grabs(df, pools)
  zones = merge_zones(
    [*sd_zones, *ob_zones, *flip, *fvg_zones],
    settings.zone_merge_overlap,
  )
  zones = mark_mitigation(zones, df, cutoff=max(0, len(df) - 1))
  zones = score_zones(zones, levels, pools, settings.round_step)
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
  )


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
