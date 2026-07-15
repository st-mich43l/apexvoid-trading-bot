"""Deterministic diagonal support and resistance over significant swings."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral

import pandas as pd

from app.pa_math import atr_scalar
from app.pa_types import Swing

TL_MIN_TOUCHES = 3
TL_TOL_ATR = 0.3
TL_MAX_SLOPE_ATR = 0.15
TL_DEDUP_VALUE_ATR = 0.5
TL_DEDUP_SLOPE_PCT = 0.2
_EPS = 1e-12


@dataclass(frozen=True)
class Trendline:
  kind: str
  point_idx: tuple[int, ...]
  slope: float
  intercept: float
  touches: int
  broken: bool
  break_index: int | None


def trendlines(
  swings: list[Swing],
  df: pd.DataFrame,
  atr: pd.Series | float,
  cfg,
) -> list[Trendline]:
  if df.empty:
    return []
  atr_value = atr_scalar(atr)
  tolerance = max(0.0, float(getattr(cfg, "tl_tol_atr", TL_TOL_ATR))) * atr_value
  min_touches = max(2, int(getattr(cfg, "tl_min_touches", TL_MIN_TOUCHES)))
  max_slope = (
    max(0.0, float(getattr(cfg, "tl_max_slope_atr", TL_MAX_SLOPE_ATR)))
    * atr_value
  )
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
        if abs(slope) > max_slope + _EPS:
          continue
        intercept = first_price - slope * i
        touching = tuple(
          index for index, price in points
          if abs(price - (slope * index + intercept)) <= tolerance + _EPS
        )
        if len(touching) < min_touches:
          continue
        first_touch, last_touch = touching[0], touching[-1]
        if not _contained(
          df,
          line_kind,
          slope,
          intercept,
          first_touch,
          last_touch,
          tolerance,
        ):
          continue
        break_index = _break_index(
          df,
          line_kind,
          slope,
          intercept,
          last_touch + 1,
          tolerance,
        )
        candidates.append(Trendline(
          kind=line_kind,
          point_idx=touching,
          slope=slope,
          intercept=intercept,
          touches=len(touching),
          broken=break_index is not None,
          break_index=break_index,
        ))
  return _dedup(candidates, len(df) - 1, atr_value)


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


def _contained(
  df: pd.DataFrame,
  kind: str,
  slope: float,
  intercept: float,
  start: int,
  end: int,
  tolerance: float,
) -> bool:
  return _break_index(
    df,
    kind,
    slope,
    intercept,
    start,
    tolerance,
    end=end,
  ) is None


def _break_index(
  df: pd.DataFrame,
  kind: str,
  slope: float,
  intercept: float,
  start: int,
  tolerance: float,
  *,
  end: int | None = None,
) -> int | None:
  stop = len(df) if end is None else min(len(df), end + 1)
  for index in range(max(0, start), stop):
    close = float(df["close"].iloc[index])
    line_value = slope * index + intercept
    if kind == "resistance" and close > line_value + tolerance:
      return index
    if kind == "support" and close < line_value - tolerance:
      return index
  return None


def _dedup(
  lines: list[Trendline],
  last_bar: int,
  atr: float,
) -> list[Trendline]:
  ranked = sorted(
    lines,
    key=lambda line: (
      -line.touches,
      -(line.point_idx[-1] - line.point_idx[0]),
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
