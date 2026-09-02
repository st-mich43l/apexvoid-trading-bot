"""Deterministic diagonal support and resistance over significant swings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from numbers import Integral

import pandas as pd

from app.analysis.math_utils import atr_scalar
from app.analysis.types import Swing

TL_MIN_TOUCHES = 3
TL_MAX_TOUCHES = 4
TL_TOL_ATR = 0.3
TL_PIERCE_TOL_ATR = 0.5
TL_MIN_SLOPE_ATR = 0.02
TL_MAX_SLOPE_ATR = 0.15
TL_MIN_SPAN_BARS = 20
TL_MIN_TOUCH_SPACING = 3
TL_MAX_BARS_SINCE_TOUCH = 30
TL_MAX_FIT_ERROR_ATR = 0.15
TL_MAX_VIOLATIONS = 2
TL_DEDUP_VALUE_ATR = 0.5
TL_DEDUP_SLOPE_PCT = 0.2
_EPS = 1e-12

MetricSink = Callable[[str, str, dict[str, str]], None]


@dataclass(frozen=True)
class Trendline:
  kind: str
  point_idx: tuple[int, ...]
  slope: float
  intercept: float
  touches: int
  broken: bool
  break_index: int | None
  fit_error_atr: float = 0.0
  violations: int = 0
  bars_since_last_touch: int = 0
  span_bars: int = 0
  exhausted: bool = False


def trendlines(
  swings: list[Swing],
  df: pd.DataFrame,
  atr: pd.Series | float,
  cfg,
  *,
  symbol: str = "",
  timeframe: str = "",
  metric_sink: MetricSink | None = None,
) -> list[Trendline]:
  if df.empty:
    return []
  if cfg is None:
    from app.core.config import runtime_config
    cfg = runtime_config
  tl_cfg = cfg.analysis.trendlines
  atr_value = atr_scalar(atr)
  touch_tolerance = (
    max(0.0, float(getattr(tl_cfg, "tolerance_atr", TL_TOL_ATR))) * atr_value
  )
  pierce_tolerance = (
    max(0.0, float(getattr(tl_cfg, "pierce_tolerance_atr", TL_PIERCE_TOL_ATR)))
    * atr_value
  )
  min_touches = max(2, int(getattr(tl_cfg, "minimum_touches", TL_MIN_TOUCHES)))
  max_touches = max(
    min_touches,
    int(getattr(tl_cfg, "maximum_touches", TL_MAX_TOUCHES)),
  )
  min_slope = (
    max(0.0, float(getattr(tl_cfg, "minimum_slope_atr", TL_MIN_SLOPE_ATR)))
    * atr_value
  )
  max_slope = (
    max(0.0, float(getattr(tl_cfg, "maximum_slope_atr", TL_MAX_SLOPE_ATR)))
    * atr_value
  )
  min_span = max(1, int(getattr(tl_cfg, "minimum_span_bars", TL_MIN_SPAN_BARS)))
  min_spacing = max(
    1,
    int(getattr(tl_cfg, "minimum_touch_spacing_bars", TL_MIN_TOUCH_SPACING)),
  )
  max_bars_since = max(
    0,
    int(getattr(tl_cfg, "maximum_bars_since_last_touch", TL_MAX_BARS_SINCE_TOUCH)),
  )
  max_fit_error = max(
    0.0,
    float(getattr(tl_cfg, "maximum_fit_error_atr", TL_MAX_FIT_ERROR_ATR)),
  )
  max_violations = max(
    0,
    int(getattr(tl_cfg, "maximum_violations", TL_MAX_VIOLATIONS)),
  )
  last_bar = len(df) - 1
  candidates: list[Trendline] = []
  for kind, line_kind in (("high", "resistance"), ("low", "support")):
    points = _swing_points(swings, df, kind)
    for left in range(len(points)):
      i, first_price = points[left]
      for right in range(left + 1, len(points)):
        j, second_price = points[right]
        if j <= i:
          continue
        slope = (second_price - first_price) / (j - i)
        if line_kind == "resistance" and slope > -min_slope:
          if metric_sink is not None:
            metric_sink(
              "trendline_rejected_counter_trend",
              symbol,
              {"tf": timeframe},
            )
          continue
        if line_kind == "support" and slope < min_slope:
          if metric_sink is not None:
            metric_sink(
              "trendline_rejected_counter_trend",
              symbol,
              {"tf": timeframe},
            )
          continue
        if abs(slope) > max_slope + _EPS:
          continue
        intercept = first_price - slope * i
        raw_touching = tuple(
          index for index, price in points
          if abs(price - (slope * index + intercept)) <= touch_tolerance + _EPS
        )
        touching = _collapse_touch_clusters(raw_touching, min_spacing)
        if len(touching) < min_touches:
          continue
        first_touch, last_touch = touching[0], touching[-1]
        span_bars = last_touch - first_touch
        if span_bars < min_span:
          continue
        bars_since_last_touch = last_bar - last_touch
        if bars_since_last_touch > max_bars_since:
          continue
        touch_prices = {
          index: price for index, price in points if index in touching
        }
        fit_error_atr = _fit_error_atr(
          touching, touch_prices, slope, intercept, atr_value,
        )
        if fit_error_atr > max_fit_error + _EPS:
          continue
        if not _contained(
          df,
          line_kind,
          slope,
          intercept,
          first_touch,
          last_touch,
          pierce_tolerance,
        ):
          continue
        violations = _count_violations(
          df,
          line_kind,
          slope,
          intercept,
          first_touch,
          pierce_tolerance,
        )
        if violations > max_violations:
          continue
        break_index = _break_index(
          df,
          line_kind,
          slope,
          intercept,
          last_touch + 1,
          pierce_tolerance,
        )
        touches = len(touching)
        candidates.append(Trendline(
          kind=line_kind,
          point_idx=touching,
          slope=slope,
          intercept=intercept,
          touches=touches,
          broken=break_index is not None,
          break_index=break_index,
          fit_error_atr=fit_error_atr,
          violations=violations,
          bars_since_last_touch=bars_since_last_touch,
          span_bars=span_bars,
          exhausted=touches >= max_touches,
        ))
  return _dedup(candidates, last_bar, atr_value, max_touches)


def value_at(line: Trendline, bar_index: int) -> float:
  return line.slope * bar_index + line.intercept


def _swing_points(
  swings: list[Swing],
  df: pd.DataFrame,
  kind: str,
) -> list[tuple[int, float]]:
  points: list[tuple[int, float]] = []
  for swing in swings:
    if swing.kind != kind:
      continue
    index = _bar_index(swing, df)
    price = float(swing.price)
    if index is None or not math.isfinite(price):
      continue
    points.append((index, price))
  return sorted(set(points))


def _bar_index(swing: Swing, df: pd.DataFrame) -> int | None:
  if isinstance(swing.index, Integral):
    index = int(swing.index)
  else:
    try:
      location = df.index.get_loc(swing.index)
    except KeyError:
      return None
    if not isinstance(location, Integral):
      return None
    index = int(location)
  return index if 0 <= index < len(df) else None


def _collapse_touch_clusters(
  indexes: tuple[int, ...],
  minimum_spacing: int,
) -> tuple[int, ...]:
  if not indexes:
    return ()
  kept = [indexes[0]]
  for index in indexes[1:]:
    if index - kept[-1] >= minimum_spacing:
      kept.append(index)
  return tuple(kept)


def _fit_error_atr(
  touching: tuple[int, ...],
  prices: dict[int, float],
  slope: float,
  intercept: float,
  atr_value: float,
) -> float:
  if not touching:
    return 0.0
  total = 0.0
  for index in touching:
    total += abs(prices[index] - (slope * index + intercept))
  mean_error = total / len(touching)
  if atr_value <= _EPS:
    return 0.0 if mean_error <= _EPS else float("inf")
  return mean_error / atr_value


def _contained(
  df: pd.DataFrame,
  kind: str,
  slope: float,
  intercept: float,
  start: int,
  end: int,
  pierce_tolerance: float,
) -> bool:
  return _break_index(
    df,
    kind,
    slope,
    intercept,
    start,
    pierce_tolerance,
    end=end,
  ) is None


def _break_index(
  df: pd.DataFrame,
  kind: str,
  slope: float,
  intercept: float,
  start: int,
  pierce_tolerance: float,
  *,
  end: int | None = None,
) -> int | None:
  stop = len(df) if end is None else min(len(df), end + 1)
  for index in range(max(0, start), stop):
    close = float(df["close"].iloc[index])
    line_value = slope * index + intercept
    if kind == "resistance" and close > line_value + pierce_tolerance:
      return index
    if kind == "support" and close < line_value - pierce_tolerance:
      return index
  return None


def _count_violations(
  df: pd.DataFrame,
  kind: str,
  slope: float,
  intercept: float,
  start: int,
  pierce_tolerance: float,
) -> int:
  count = 0
  for index in range(max(0, start), len(df)):
    line_value = slope * index + intercept
    close = float(df["close"].iloc[index])
    if kind == "resistance":
      high = float(df["high"].iloc[index])
      pierced = high > line_value + pierce_tolerance
      closed_through = close > line_value + pierce_tolerance
    else:
      low = float(df["low"].iloc[index])
      pierced = low < line_value - pierce_tolerance
      closed_through = close < line_value - pierce_tolerance
    if pierced and not closed_through:
      count += 1
  return count


def _dedup(
  lines: list[Trendline],
  last_bar: int,
  atr: float,
  maximum_touches: int = TL_MAX_TOUCHES,
) -> list[Trendline]:
  ranked = sorted(
    lines,
    key=lambda line: (
      -min(line.touches, maximum_touches),
      line.fit_error_atr,
      -line.span_bars,
      line.bars_since_last_touch,
      line.kind,
      line.slope,
      line.intercept,
    ),
  )
  kept: list[Trendline] = []
  for line in ranked:
    if any(_near_duplicate(line, other, last_bar, atr) for other in kept):
      continue
    kept.append(line)
  return sorted(
    kept,
    key=lambda line: (line.kind, value_at(line, last_bar), line.slope),
  )


def _near_duplicate(
  first: Trendline,
  second: Trendline,
  last_bar: int,
  atr: float,
) -> bool:
  if first.kind != second.kind:
    return False
  if abs(value_at(first, last_bar) - value_at(second, last_bar)) > (
    TL_DEDUP_VALUE_ATR * atr + _EPS
  ):
    return False
  scale = max(abs(first.slope), abs(second.slope), _EPS)
  return abs(first.slope - second.slope) / scale <= TL_DEDUP_SLOPE_PCT + _EPS
