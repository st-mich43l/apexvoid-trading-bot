"""Pure price-action analysis orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math

import pandas as pd

from app.analysis.dealing_range import dealing_range
from app.analysis.levels import key_levels
from app.analysis.liquidity import liquidity_grabs, liquidity_pools
from app.analysis.momentum import momentum
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
  zone_reconcile_enabled: bool = True
  zone_reconcile_mode: str = "enforce"
  regime_direction_enabled: bool = False
  regime_direction_lookback: int = 120
  regime_min_directional_swings: int = 3
  regime_min_displacement_atr: float = 4.0


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
  regime_ = regime(df, atr, swings, structure, range_, settings)
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
    reconciled = reconcile_opposing(
      zones,
      min(0.3 * atr_scalar(atr), ZONE_MIN_WIDTH),
      stats=reconcile_stats,
    )
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
