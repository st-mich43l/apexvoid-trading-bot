"""Pure price-action setup detectors for replayable scanner decisions."""

from dataclasses import dataclass, field
import math
from typing import Callable, Protocol

import pandas as pd

from app.analysis import AnalysisSettings, analyze
from app.indicators import atr as atr_indicator
from app.structure import (
  Level,
  Swing,
  Zone,
  entry_zone,
  equal_highs_lows,
  find_retest,
  fvg,
  key_levels,
  liquidity_sweep,
  market_structure,
  order_blocks,
  swings,
)

_EPS = 1e-9
_BUY_ZONE_SIDE = "de" + "mand"


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


@dataclass(frozen=True)
class DetectorSettings:
  confluence_floor: int = 2
  max_entry_atr: float = 2.0
  range_lookback: int = 50
  snap_atr_mult: float = 1.5
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

  def analysis_settings(self) -> AnalysisSettings:
    return AnalysisSettings(
      swing_fractal_n=self.swing_fractal_n,
      zigzag_pct=self.zigzag_pct,
      zigzag_atr_mult=self.zigzag_atr_mult,
      displacement_atr_mult=self.displacement_atr_mult,
      zone_width=self.zone_width,
      equal_tol_atr=self.equal_tol_atr,
      level_cluster_atr=self.level_cluster_atr,
      round_step=self.round_step,
      key_level_min_touches=self.key_level_min_touches,
      momentum_lookback=self.momentum_lookback,
      momentum_body_frac=self.momentum_body_frac,
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


@dataclass(frozen=True)
class DetectionResult:
  setup: str
  direction: str
  key_level: float
  entry_zone: Zone
  current_price: float
  confluence: int
  reasons: list[str]


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
    name: _indicator_set(df)
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
  )


def _indicator_set(df: pd.DataFrame) -> IndicatorSet:
  return IndicatorSet(atr=atr_indicator(df, 14))


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
    )
  return result


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


def _range_position(df: pd.DataFrame, lookback: int) -> float:
  recent = df.tail(max(2, int(lookback)))
  high = float(recent["high"].max())
  low = float(recent["low"].min())
  if high <= low:
    return 0.5
  return (float(recent["close"].iloc[-1]) - low) / (high - low)


def _mid_range(df: pd.DataFrame, settings: DetectorSettings) -> bool:
  pos = _range_position(df, settings.range_lookback)
  return 0.40 <= pos <= 0.60


def _last(series: pd.Series, default: float = 0.0) -> float:
  clean = series.dropna()
  value = float(clean.iloc[-1]) if not clean.empty else default
  return value if math.isfinite(value) and value > 0 else default


def _atr(ind: IndicatorSet, fallback: float = 1.0) -> float:
  return _last(ind.atr, fallback)


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


def _nearest_valid_zone(
  zones: list[Zone],
  price: float,
  atr: float,
  direction: str,
  settings: DetectorSettings,
) -> Zone | None:
  valid = [
    zone for zone in zones
    if _entry_valid_for_settings(zone, price, atr, direction, settings)
  ]
  if not valid:
    return None
  if direction == "BUY":
    return min(valid, key=lambda zone: abs(price - zone.high))
  return min(valid, key=lambda zone: abs(zone.low - price))


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


def _finish(
  ctx: DetectionContext,
  setup: str,
  direction: str,
  level: float,
  zone: Zone,
  price: float,
  atr: float,
  reasons: list[str],
) -> DetectionResult | None:
  if not _level_valid(level, price, direction):
    return None
  if not _entry_valid_for_settings(zone, price, atr, direction, ctx.settings):
    return None
  score = min(3, len(reasons))
  if score < ctx.settings.confluence_floor:
    return None
  return DetectionResult(
    setup=setup,
    direction=direction,
    key_level=float(level),
    entry_zone=zone,
    current_price=price,
    confluence=score,
    reasons=reasons[:],
  )


def trend_pullback(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df, ctx.settings):
    return None
  direction = _confirmation_direction(ctx)
  if direction is None or st.bias != ctx.htf_bias or not _rejection(df, direction):
    return None
  price = float(df["close"].iloc[-1])
  atr = _atr(ind)
  zone = _nearest_valid_zone(
    [
      zone for zone in _candidate_zones(st, direction)
      if _last_touches_zone(df, zone)
    ],
    price,
    atr,
    direction,
    ctx.settings,
  )
  if zone is None:
    return None
  level = _zone_key(zone, price, direction)
  reasons = [
    f"HTF bias {ctx.htf_bias}",
    "pullback into structure zone",
    "rejection at support" if direction == "BUY" else "rejection at supply",
  ]
  if ctx.session_ok:
    reasons.append("session")
  return _finish(ctx, "Trend Pullback", direction, level, zone, price, atr, reasons)


def break_retest(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df, ctx.settings):
    return None
  direction = _confirmation_direction(ctx)
  if direction is None or not _rejection(df, direction):
    return None
  price = float(df["close"].iloc[-1])
  atr = _atr(ind)
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
    result = _finish(ctx, "Break & Retest", direction, level.price, zone, price, atr, reasons)
    if result is not None:
      return result
  return None


def snap_back(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df, ctx.settings):
    return None
  direction = _confirmation_direction(ctx)
  if direction is None or not _rejection(df, direction):
    return None
  price = float(df["close"].iloc[-1])
  atr = _atr(ind)
  zones = _candidate_zones(st, direction)
  zone = _nearest_valid_zone(zones, price, atr, direction, ctx.settings)
  level = None
  if zone is not None:
    distance = _zone_distance(zone, price, direction)
    level = _zone_key(zone, price, direction)
  else:
    nearest = _nearest_level(st.levels, price, direction)
    if nearest is None:
      return None
    zone = entry_zone(df, nearest.price, direction)
    distance = _zone_distance(zone, price, direction)
    level = nearest.price
  if distance < atr * ctx.settings.snap_atr_mult:
    return None
  reasons = [f"HTF bias {ctx.htf_bias}", "ATR extension", "reversal rejection"]
  return _finish(ctx, "Snap-Back", direction, level, zone, price, atr, reasons)


def momentum_ride(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df, ctx.settings):
    return None
  direction = _confirmation_direction(ctx)
  if direction is None:
    return None
  if not _strong_body_break(df, st, direction, ctx.settings.momentum_body_frac):
    return None
  price = float(df["close"].iloc[-1])
  atr = _atr(ind)
  level = _nearest_level(st.levels, price, direction)
  if level is None:
    return None
  zone = entry_zone(df, level.price, direction)
  reasons = [f"HTF bias {ctx.htf_bias}", "impulse break", "near valid-side level"]
  return _finish(ctx, "Momentum Ride", direction, level.price, zone, price, atr, reasons)


def fade_scalp(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df, ctx.settings):
    return None
  direction = _confirmation_direction(ctx)
  if direction is None or not _rejection(df, direction):
    return None
  price = float(df["close"].iloc[-1])
  atr = _atr(ind)
  desired_kind = "equal_low" if direction == "BUY" else "equal_high"
  for level in st.equal_levels:
    if level.kind != desired_kind:
      continue
    sweep = liquidity_sweep(df, level)
    if sweep != direction.lower():
      continue
    zone = entry_zone(df, level.price, direction)
    reasons = [
      f"HTF bias {ctx.htf_bias}",
      "equal level sweep",
      "liquidity rejection",
    ]
    result = _finish(ctx, "Fade Scalp", direction, level.price, zone, price, atr, reasons)
    if result is not None:
      return result
  return None


DEFAULT_DETECTORS: tuple[SetupDetector, ...] = (
  trend_pullback,
  break_retest,
  snap_back,
  momentum_ride,
  fade_scalp,
)
