"""Pure price-action setup detectors for replayable scanner decisions."""

from dataclasses import dataclass, field, replace
import math
from typing import Callable, Protocol

import pandas as pd

from app.analysis.engine import AnalysisContext, AnalysisSettings, Regime, analyze
from app.analysis.indicators import atr as atr_indicator
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
from app.analysis.zones import score_zones

_EPS = 1e-9
_BUY_ZONE_SIDE = "de" + "mand"
STAR_THREE_SCORE = 12.0
STAR_TWO_SCORE = 8.0
COIL_SCORE = 1.5
REACTION_MAX_ATR = 1.0
RANGE_CONFIRMATION_LABELS = {
  "sweep_a": "sweep A",
  "sweep_reclaim": "sweep + reclaim",
  "rejection_choch": "rejection + micro CHoCH",
  "rejection_edge": "rejection at scored edge",
}


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
  regime_direction_enabled: bool = False
  regime_direction_lookback: int = 120
  regime_min_directional_swings: int = 3
  regime_min_displacement_atr: float = 4.0

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
      regime_direction_enabled=self.regime_direction_enabled,
      regime_direction_lookback=self.regime_direction_lookback,
      regime_min_directional_swings=self.regime_min_directional_swings,
      regime_min_displacement_atr=self.regime_min_displacement_atr,
    )


def detector_settings_from(config) -> DetectorSettings:
  """Build detector settings from the app config for every PA consumer."""
  return DetectorSettings(
    confluence_floor=config.scanner_confluence_floor,
    max_entry_atr=config.max_entry_atr,
    range_lookback=config.range_lookback,
    atr_length=config.atr_length,
    swing_fractal_n=config.swing_fractal_n,
    zigzag_pct=config.zigzag_pct,
    zigzag_atr_mult=config.zigzag_atr_mult,
    displacement_atr_mult=config.displacement_atr_mult,
    zone_width=config.zone_width,
    zone_merge_overlap=config.zone_merge_overlap,
    max_merged_zone_atr=config.max_merged_zone_atr,
    equal_tol_atr=config.equal_tol_atr,
    level_cluster_atr=config.level_cluster_atr,
    round_step=config.round_step,
    key_level_min_touches=config.key_level_min_touches,
    momentum_lookback=config.momentum_lookback,
    momentum_body_frac=config.momentum_body_frac,
    session_asia_start=config.session_asia_start,
    session_london_start=config.session_london_start,
    session_ny_start=config.session_ny_start,
    daily_rollover_utc_hour=config.daily_rollover_utc_hour,
    eq_band=config.eq_band,
    strict_pd_gate=config.strict_pd_gate,
    sweep_body_frac=config.sweep_body_frac,
    sweep_react_bars=config.sweep_react_bars,
    inducement_band_atr=config.inducement_band_atr,
    max_zone_width_atr=config.max_zone_width_atr,
    proximal_band_atr=config.proximal_band_atr,
    chop_filter_enabled=config.chop_filter_enabled,
    chop_range_atr=config.chop_range_atr,
    chop_lookback=config.chop_lookback,
    chop_edge_frac=config.chop_edge_frac,
    tl_min_touches=config.tl_min_touches,
    tl_tol_atr=config.tl_tol_atr,
    tl_max_slope_atr=config.tl_max_slope_atr,
    coil_contract=config.coil_contract,
    breakout_buffer_atr=config.breakout_buffer_atr,
    breakout_accept_bars=config.breakout_accept_bars,
    breakout_max_age_bars=config.breakout_max_age_bars,
    allow_counter_trend=config.allow_counter_trend,
    counter_min_zone_score=config.counter_min_zone_score,
    counter_extreme_pd=config.counter_extreme_pd,
    counter_level_min_touches=config.counter_level_min_touches,
    range_scalp_enabled=config.range_scalp_enabled,
    range_scalp_lookback=config.range_scalp_lookback,
    range_scalp_cluster_atr=config.range_scalp_cluster_atr,
    range_scalp_min_touches=config.range_scalp_min_touches,
    range_scalp_min_wick_frac=config.range_scalp_min_wick_frac,
    range_scalp_entry_tol_atr=config.range_scalp_entry_tol_atr,
    range_scalp_min_width_atr=config.range_scalp_min_width_atr,
    range_scalp_max_width_atr=config.range_scalp_max_width_atr,
    range_scalp_min_room_atr=config.range_scalp_min_room_atr,
    range_scalp_break_closes=config.range_scalp_break_closes,
    range_scalp_min_wick_rejections=config.range_scalp_min_wick_rejections,
    range_scalp_allow_rejection_only=config.range_scalp_allow_rejection_only,
    zone_reconcile_enabled=config.auto_trade_zone_reconcile_enabled,
    regime_direction_enabled=config.auto_trade_regime_direction_enabled,
    regime_direction_lookback=config.auto_trade_regime_direction_lookback,
    regime_min_directional_swings=config.auto_trade_regime_min_directional_swings,
    regime_min_displacement_atr=config.auto_trade_regime_min_displacement_atr,
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


class SetupDetector(Protocol):
  def __call__(self, ctx: DetectionContext) -> DetectionResult | None:
    ...


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
  return DetectionContext(
    symbol=symbol,
    tf=tf,
    frames=frames,
    indicators=indicator_sets,
    structures=structure_sets,
    htf_bias=analysis_ctx.htf_bias,
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
  if ctx.htf_bias == "up":
    return "BUY"
  if ctx.htf_bias == "down":
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
  return direction if ctx.htf_bias == _bias_for_direction(direction) else None


def _pd_gate(st: StructureSet, direction: str, settings: DetectorSettings) -> bool:
  range_ = st.dealing_range
  if range_ is None:
    return True
  if range_.zone == "eq":
    return False
  if direction == "BUY":
    if settings.strict_pd_gate:
      return range_.zone == "discount"
    return range_.zone != "premium"
  if settings.strict_pd_gate:
    return range_.zone == "premium"
  return range_.zone != "discount"


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
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  if _in_chop(ctx):
    return None
  direction = _confirmation_direction(ctx)
  if (
    direction is None
    or not _pd_gate(st, direction, ctx.settings)
    or st.bias != ctx.htf_bias
    or not _rejection(df, direction)
  ):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
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
  level = _zone_key(zone, price, direction)
  reasons = [
    f"HTF bias {ctx.htf_bias}",
    "pullback into structure zone",
    "rejection at support" if direction == "BUY" else "rejection at supply",
  ]
  reasons = _add_proximal_reason(reasons, proximal)
  if ctx.session_ok:
    reasons.append("session")
  factors = ConfluenceFactors(
    htf_aligned=True,  # gated above via st.bias == ctx.htf_bias
    touches=zone.touches,
    wick_rejection=True,  # gated above via _rejection(df, direction)
    session_context=ctx.session_ok,
    structural_agreement=True,  # zone drawn from structural swing zones
  )
  return _finish(
    ctx, "Trend Pullback", direction, level, zone, price, atr, reasons,
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
    or not _pd_gate(st, direction, ctx.settings)
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
    )
    if result is not None:
      return result
  levels = sorted(st.levels, key=lambda item: abs(item.price - price))
  for level in levels:
    if not _level_valid(level.price, price, direction):
      continue
    zone = find_retest(df, level.price)
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
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  direction = _confirmation_direction(ctx)
  if (
    direction is None
    or not _pd_gate(st, direction, ctx.settings)
    or not _rejection(df, direction)
  ):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  zones = _candidate_zones(st, direction)
  selected = _best_valid_zone(zones, price, atr, direction, ctx.settings)
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
  else:
    nearest = _nearest_level(st.levels, price, direction)
    if nearest is None:
      return None
    zone = entry_zone(df, nearest.price, direction)
    distance = _zone_distance(zone, price, direction)
    level = nearest.price
    touches = nearest.touches
  if distance < atr * ctx.settings.snap_atr_mult:
    return None
  grab = _zone_grab(st, zone, direction)
  if grab is None or grab.grade not in {"A", "B"}:
    return None
  reasons = [
    f"HTF bias {ctx.htf_bias}",
    "ATR extension",
    "reversal rejection",
    f"sweep {grab.grade}",
  ]
  reasons = _add_proximal_reason(reasons, proximal)
  factors = ConfluenceFactors(
    htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
    touches=touches,
    wick_rejection=True,  # gated above via _rejection(df, direction)
    displacement_grade=grab.grade == "A",
    structural_agreement=structural_agreement,
  )
  return _finish(
    ctx, "Snap-Back", direction, level, zone, price, atr, reasons, factors=factors,
  )


def momentum_ride(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  if _in_chop(ctx):
    return None
  direction = _confirmation_direction(ctx)
  if direction is None or not _pd_gate(st, direction, ctx.settings):
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
  if not ctx.settings.range_scalp_enabled:
    return None
  df, ind, st = _exec(ctx)
  scalp_range = st.scalp_range
  if len(df) < 5 or scalp_range is None:
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
  confirmation_bars = max(1, ctx.settings.sweep_react_bars)
  candidates = [
    ("BUY", scalp_range.lower, scalp_range.upper.level),
    ("SELL", scalp_range.upper, scalp_range.lower.level),
  ]
  candidates = sorted(
    candidates,
    key=lambda item: (abs(item[1].level - price), -item[1].score, item[0]),
  )
  for direction, barrier, opposing_level in candidates:
    if not _barrier_touched_recently(df, barrier, confirmation_bars):
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
    confirmation = _range_edge_confirmation(
      df,
      st,
      zone,
      barrier,
      direction,
      confirmation_bars,
      ctx.settings,
    )
    if confirmation is None:
      continue
    edge = "lower" if direction == "BUY" else "upper"
    reasons = [
      f"local range {_number(scalp_range.lower.level)}-"
      f"{_number(scalp_range.upper.level)}",
      f"{edge} barrier ×{barrier.touches}",
      f"wick rejection ×{barrier.wick_rejections}",
      RANGE_CONFIRMATION_LABELS[confirmation],
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
      confirmation=confirmation,
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


def _range_edge_confirmation(
  df: pd.DataFrame,
  st: StructureSet,
  zone: Zone,
  barrier: ScalpBarrier,
  direction: str,
  bars: int,
  settings: DetectorSettings,
) -> str | None:
  grab = _zone_grab(st, zone, direction)
  if grab is not None and grab.grade == "A":
    return "sweep_a"
  if _barrier_sweep_reclaim(df, barrier, direction, bars):
    return "sweep_reclaim"
  if _recent_rejection(df, direction, bars) and (
    _recent_choch(st, direction, len(df), settings)
    or _micro_choch(df, direction, bars)
  ):
    return "rejection_choch"
  if settings.range_scalp_allow_rejection_only and _recent_rejection(
    df, direction, bars
  ):
    return "rejection_edge"
  return None


def _barrier_sweep_reclaim(
  df: pd.DataFrame,
  barrier: ScalpBarrier,
  direction: str,
  bars: int,
) -> bool:
  for row in df.tail(max(1, bars)).itertuples(index=False):
    if direction == "SELL":
      if float(row.high) > barrier.high and float(row.close) < barrier.level:
        return True
    elif float(row.low) < barrier.low and float(row.close) > barrier.level:
      return True
  return False


def _recent_rejection(df: pd.DataFrame, direction: str, bars: int) -> bool:
  start = max(0, len(df) - max(1, bars))
  for index in range(start, len(df)):
    if _rejection(df.iloc[:index + 1], direction):
      return True
  return False


def _micro_choch(df: pd.DataFrame, direction: str, bars: int) -> bool:
  lookback = max(2, bars)
  if len(df) < lookback + 1:
    return False
  prior = df.iloc[-lookback - 1:-1]
  close = float(df["close"].iloc[-1])
  if direction == "BUY":
    return close > float(prior["high"].max())
  return close < float(prior["low"].min())


def fade_scalp(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5:
    return None
  direction = _confirmation_direction(ctx)
  if (
    direction is None
    or not _pd_gate(st, direction, ctx.settings)
    or not _rejection(df, direction)
  ):
    return None
  price = _current_price(ctx, df)
  atr = _atr(ind)
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
    reasons = [
      f"HTF bias {ctx.htf_bias}",
      "equal level sweep",
      "liquidity rejection",
      f"sweep {grab.grade}",
    ]
    range_reason = _chop_range_reason(ctx)
    if range_reason:
      reasons.append(range_reason)
    factors = ConfluenceFactors(
      htf_aligned=ctx.htf_bias == _bias_for_direction(direction),
      touches=level.touches,
      wick_rejection=True,  # gated above via _rejection(df, direction)
      displacement_grade=grab.grade == "A",
    )
    result = _finish(
      ctx, "Fade Scalp", direction, level.price, zone, price, atr, reasons,
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


DEFAULT_DETECTORS: tuple[SetupDetector, ...] = (
  box_breakout,
  trend_pullback,
  break_retest,
  snap_back,
  momentum_ride,
  range_edge_scalp,
  fade_scalp,
  zone_reaction,
)
