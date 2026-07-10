"""Pure price-action analysis orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.levels import key_levels
from app.liquidity import liquidity_grabs, liquidity_pools
from app.momentum import momentum
from app.pa_math import atr_series
from app.pa_types import Break, Grab, Leg, Level, Pool, Swing, Zone
from app.structure import market_structure, structure_breaks
from app.swings import find_swings
from app.zones import (
  displacement,
  flip_zones,
  fvg,
  mark_mitigation,
  order_blocks,
  supply_demand,
)


@dataclass(frozen=True)
class AnalysisSettings:
  swing_fractal_n: int = 2
  zigzag_pct: float = 0.0
  zigzag_atr_mult: float = 1.0
  displacement_atr_mult: float = 1.5
  zone_width: str = "body"
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
  return AnalysisContext(
    frames={tf.upper(): df for tf, df in df_by_tf.items()},
    per_tf=per_tf,
    htf_bias=_htf_bias(per_tf, htf_order or ["M30", "M15"]),
  )


def _analyze_tf(df: pd.DataFrame, settings: AnalysisSettings) -> TimeframeAnalysis:
  atr = atr_series(df)
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
  zones = mark_mitigation([*sd_zones, *ob_zones, *flip, *fvg_zones], df)
  ob_zones = [zone for zone in zones if zone.source == "order_block"]
  sd_zones = [zone for zone in zones if zone.source == "supply_demand"]
  flip = [zone for zone in zones if zone.source == "flip_zone"]
  fvg_zones = [zone for zone in zones if zone.source.endswith("_fvg")]
  pools = liquidity_pools(swings, df, settings.equal_tol_atr, atr)
  grabs = liquidity_grabs(df, pools)
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
  for item in per_tf.values():
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
