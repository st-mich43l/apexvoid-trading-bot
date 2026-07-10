"""Pure price-action setup detectors for replayable scanner decisions."""

from dataclasses import dataclass
import math
from typing import Callable, Protocol

import pandas as pd

from app import indicators
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


@dataclass(frozen=True)
class IndicatorSet:
  ema_fast: pd.Series
  ema_slow: pd.Series
  atr: pd.Series
  mfi: pd.Series
  bbands: pd.DataFrame
  wae: pd.DataFrame


@dataclass(frozen=True)
class StructureSet:
  swings: list[Swing]
  bias: str
  levels: list[Level]
  equal_levels: list[Level]
  fvg_zones: list[Zone]
  order_blocks: list[Zone]


@dataclass(frozen=True)
class DetectorSettings:
  confluence_floor: int = 2
  wae_fast: int = 20
  wae_slow: int = 40
  wae_sensitivity: float = 150.0
  wae_bb_length: int = 20
  wae_bb_mult: float = 2.0
  snap_atr_mult: float = 1.5


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
  indicator_sets = {
    name: _indicator_set(df, settings)
    for name, df in frames.items()
  }
  structure_sets = {
    name: _structure_set(df)
    for name, df in frames.items()
  }
  htf_bias = "range"
  for name in htf_order:
    bias = structure_sets.get(name, StructureSet([], "range", [], [], [], [])).bias
    if bias != "range":
      htf_bias = bias
      break
  if htf_bias == "range":
    htf_bias = structure_sets.get(tf, StructureSet([], "range", [], [], [], [])).bias
  return DetectionContext(
    symbol=symbol,
    tf=tf,
    frames=frames,
    indicators=indicator_sets,
    structures=structure_sets,
    htf_bias=htf_bias,
    settings=settings,
  )


def _indicator_set(df: pd.DataFrame, settings: DetectorSettings) -> IndicatorSet:
  return IndicatorSet(
    ema_fast=indicators.ema(df, settings.wae_fast),
    ema_slow=indicators.ema(df, settings.wae_slow),
    atr=indicators.atr(df, 14),
    mfi=indicators.mfi(df, 14),
    bbands=indicators.bbands(df, settings.wae_bb_length, settings.wae_bb_mult),
    wae=indicators.wae(
      df,
      settings.wae_fast,
      settings.wae_slow,
      settings.wae_sensitivity,
      settings.wae_bb_length,
      settings.wae_bb_mult,
    ),
  )


def _structure_set(df: pd.DataFrame) -> StructureSet:
  items = swings(df, 2, 2)
  return StructureSet(
    swings=items,
    bias=market_structure(items),
    levels=key_levels(df),
    equal_levels=equal_highs_lows(df),
    fvg_zones=fvg(df),
    order_blocks=order_blocks(df),
  )


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


def _range_position(df: pd.DataFrame) -> float:
  high = float(df["high"].max())
  low = float(df["low"].min())
  if high <= low:
    return 0.5
  return (float(df["close"].iloc[-1]) - low) / (high - low)


def _mid_range(df: pd.DataFrame) -> bool:
  pos = _range_position(df)
  return 0.42 <= pos <= 0.58


def _last(series: pd.Series, default: float = 0.0) -> float:
  clean = series.dropna()
  return float(clean.iloc[-1]) if not clean.empty else default


def _prev(series: pd.Series, default: float = 0.0) -> float:
  clean = series.dropna()
  return float(clean.iloc[-2]) if len(clean) >= 2 else default


def _atr(ind: IndicatorSet, fallback: float = 1.0) -> float:
  value = _last(ind.atr, fallback)
  return value if value > 0 else fallback


def _nearest_level(
  levels: list[Level],
  price: float,
  direction: str,
) -> Level | None:
  if not levels:
    return None
  if direction == "BUY":
    candidates = [level for level in levels if level.price <= price]
  else:
    candidates = [level for level in levels if level.price >= price]
  if not candidates:
    candidates = levels
  return min(candidates, key=lambda level: abs(level.price - price))


def _zone_contains_price(zone: Zone, price: float) -> bool:
  return zone.low <= price <= zone.high


def _finish(
  ctx: DetectionContext,
  setup: str,
  direction: str,
  level: float,
  zone: Zone,
  reasons: list[str],
) -> DetectionResult | None:
  score = min(3, len(reasons))
  if score < ctx.settings.confluence_floor:
    return None
  return DetectionResult(
    setup=setup,
    direction=direction,
    key_level=float(level),
    entry_zone=zone,
    confluence=score,
    reasons=reasons[:],
  )


def trend_pullback(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df):
    return None
  direction = _direction(ctx)
  if direction is None:
    return None
  close = float(df["close"].iloc[-1])
  atr = _atr(ind)
  ema_fast = _last(ind.ema_fast, close)
  ema_slow = _last(ind.ema_slow, close)
  level = _nearest_level(st.levels, close, direction)
  if level is None:
    return None
  zone = entry_zone(df, level.price, direction)
  ema_aligned = (
    ema_fast >= ema_slow
    if direction == "BUY"
    else ema_fast <= ema_slow
  )
  near_ema = abs(close - ema_slow) <= atr * 1.1
  in_zone = _zone_contains_price(zone, close)
  wae_col = "trend_up" if direction == "BUY" else "trend_down"
  wae_reset = _last(ind.wae[wae_col]) > _prev(ind.wae[wae_col])
  if not (ema_aligned and (near_ema or in_zone) and wae_reset):
    return None
  reasons = [f"HTF bias {ctx.htf_bias}"]
  reasons.append("EMA aligned")
  if near_ema or in_zone:
    reasons.append("pullback into EMA/zone")
  if wae_reset:
    reasons.append("WAE reset")
  if ctx.session_ok:
    reasons.append("session")
  return _finish(ctx, "Trend Pullback", direction, level.price, zone, reasons)


def break_retest(ctx: DetectionContext) -> DetectionResult | None:
  df, _ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df):
    return None
  direction = _direction(ctx)
  if direction is None:
    return None
  for level in sorted(st.levels, key=lambda item: abs(item.price - df["close"].iloc[-1])):
    zone = find_retest(df, level.price)
    if zone is None:
      continue
    if direction == "BUY" and zone.kind != "retest_support":
      continue
    if direction == "SELL" and zone.kind != "retest_resistance":
      continue
    reasons = [f"HTF bias {ctx.htf_bias}", "break and retest", "key level"]
    return _finish(ctx, "Break & Retest", direction, level.price, zone, reasons)
  return None


def snap_back(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, _st = _exec(ctx)
  if len(df) < 5 or _mid_range(df):
    return None
  direction = _direction(ctx)
  if direction is None:
    return None
  close = float(df["close"].iloc[-1])
  open_ = float(df["open"].iloc[-1])
  mean = _last(ind.ema_slow, close)
  atr = _atr(ind)
  distance = abs(close - mean)
  bullish_reversal = close > open_ and float(df["low"].iloc[-1]) < mean
  bearish_reversal = close < open_ and float(df["high"].iloc[-1]) > mean
  if direction == "BUY" and not (close < mean and bullish_reversal):
    return None
  if direction == "SELL" and not (close > mean and bearish_reversal):
    return None
  if distance < atr * ctx.settings.snap_atr_mult:
    return None
  zone = entry_zone(df, mean, direction)
  reasons = [f"HTF bias {ctx.htf_bias}", "ATR extension", "reversal bar"]
  return _finish(ctx, "Snap-Back", direction, mean, zone, reasons)


def momentum_ride(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df):
    return None
  direction = _direction(ctx)
  if direction is None:
    return None
  wae_col = "trend_up" if direction == "BUY" else "trend_down"
  wae_value = _last(ind.wae[wae_col])
  explosion = _last(ind.wae["explosion"])
  dead_zone = _last(ind.wae["dead_zone"])
  mfi_value = _last(ind.mfi, 50)
  volume_avg = float(df["volume"].rolling(20).mean().iloc[-1] or 0)
  if not math.isfinite(volume_avg):
    volume_avg = 0
  volume_ok = volume_avg <= 0 or float(df["volume"].iloc[-1]) >= volume_avg
  mfi_ok = mfi_value >= 55 if direction == "BUY" else mfi_value <= 45
  if wae_value <= max(dead_zone, explosion * 0.2):
    return None
  if not (mfi_ok and volume_ok):
    return None
  close = float(df["close"].iloc[-1])
  level = _nearest_level(st.levels, close, direction)
  price = level.price if level else close
  zone = entry_zone(df, price, direction)
  reasons = [f"HTF bias {ctx.htf_bias}", "WAE explosion", "MFI/volume confirm"]
  return _finish(ctx, "Momentum Ride", direction, price, zone, reasons)


def fade_scalp(ctx: DetectionContext) -> DetectionResult | None:
  df, ind, st = _exec(ctx)
  if len(df) < 5 or _mid_range(df):
    return None
  direction = _direction(ctx)
  if direction is None:
    return None
  desired_kind = "equal_low" if direction == "BUY" else "equal_high"
  wae_col = "trend_up" if direction == "BUY" else "trend_down"
  wae_weakening = _last(ind.wae[wae_col]) <= _prev(ind.wae[wae_col], 0)
  for level in st.equal_levels:
    if level.kind != desired_kind:
      continue
    sweep = liquidity_sweep(df, level)
    if sweep == direction.lower() and wae_weakening:
      zone = entry_zone(df, level.price, direction)
      reasons = [f"HTF bias {ctx.htf_bias}", "equal level sweep", "WAE weakening"]
      return _finish(ctx, "Fade Scalp", direction, level.price, zone, reasons)
  return None


DEFAULT_DETECTORS: tuple[SetupDetector, ...] = (
  trend_pullback,
  break_retest,
  snap_back,
  momentum_ride,
  fade_scalp,
)
