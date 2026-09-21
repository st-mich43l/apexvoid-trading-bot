"""Key level clustering from significant swings."""

from __future__ import annotations

import math

import pandas as pd

from app.analysis.math_utils import atr_at, atr_scalar
from app.analysis.types import Level, Swing


def key_levels(
  swings: list[Swing],
  atr: pd.Series | float,
  level_cluster_atr: float = 0.5,
  round_step: float = 5.0,
  min_touches: int = 2,
  max_cluster_span_multiple: float = 2.0,
  bars: pd.DataFrame | None = None,
) -> list[Level]:
  tolerance = atr_scalar(atr) * max(0.0, level_cluster_atr)
  clusters = _price_clusters(swings, tolerance, max_cluster_span_multiple)
  levels = [
    Level(
      price=sum(item.price for item in cluster) / len(cluster),
      kind="reaction",
      touches=len(cluster),
      band=tolerance,
      strength=float(len(cluster)),
    )
    for cluster in clusters
    if len(cluster) >= min_touches
  ]
  levels.extend(_round_levels(swings, atr, round_step, tolerance, min_touches))
  deduped: list[Level] = []
  for level in sorted(levels, key=lambda item: item.price):
    if deduped and abs(deduped[-1].price - level.price) <= max(level.band, tolerance):
      prev = deduped[-1]
      touches = max(prev.touches, level.touches)
      deduped[-1] = Level(
        price=(prev.price + level.price) / 2,
        kind=prev.kind if prev.touches >= level.touches else level.kind,
        touches=touches,
        band=max(prev.band, level.band),
        strength=max(prev.strength, level.strength),
      )
    else:
      deduped.append(level)
  if bars is not None and not bars.empty:
    deduped = [_with_wick_touches(level, bars, tolerance) for level in deduped]
  return deduped


def wick_touch_episodes(bars: pd.DataFrame, price: float, band: float) -> int:
  """Distinct times a bar's wick (high/low, not just its close) reached the
  band around ``price`` without closing decisively through it.

  Owner 2026-09-21: touches were only ever counted from fractal swing
  points, so a wick that poked a level and was rejected without forming a
  confirmed fractal never counted, and a level was under-counted or missed.
  A bar counts when its range overlaps the band, unless it opened on one
  side and closed beyond the far edge (a break, not a touch). Consecutive
  qualifying bars are one episode, so a grind inside the band counts once.
  """
  if bars.empty or band < 0:
    return 0
  lo, hi = price - band, price + band
  highs = bars["high"].to_numpy(dtype=float)
  lows = bars["low"].to_numpy(dtype=float)
  opens = bars["open"].to_numpy(dtype=float)
  closes = bars["close"].to_numpy(dtype=float)
  episodes = 0
  in_episode = False
  for high, low, open_, close in zip(highs, lows, opens, closes):
    touched = low <= hi and high >= lo
    if touched:
      if open_ > hi and close < lo:
        touched = False
      elif open_ < lo and close > hi:
        touched = False
    if touched and not in_episode:
      episodes += 1
    in_episode = touched
  return episodes


def _with_wick_touches(level: Level, bars: pd.DataFrame, tolerance: float) -> Level:
  band = max(level.band, tolerance)
  episodes = wick_touch_episodes(bars, level.price, band)
  if episodes <= level.touches:
    return level
  return Level(
    price=level.price,
    kind=level.kind,
    touches=episodes,
    band=level.band,
    strength=max(level.strength, float(episodes)),
  )


def _price_clusters(
  swings: list[Swing],
  tolerance: float,
  max_cluster_span_multiple: float = 2.0,
) -> list[list[Swing]]:
  clusters: list[list[Swing]] = []
  max_span = tolerance * max(0.0, max_cluster_span_multiple)
  for swing in sorted(swings, key=lambda item: item.price):
    if clusters and _can_join_cluster(
      clusters[-1],
      swing,
      tolerance,
      max_span,
    ):
      clusters[-1].append(swing)
    else:
      clusters.append([swing])
  return clusters


def _can_join_cluster(
  cluster: list[Swing],
  swing: Swing,
  tolerance: float,
  max_span: float,
) -> bool:
  prices = [item.price for item in cluster] + [swing.price]
  if max(prices) - min(prices) > max_span:
    return False
  return all(abs(item.price - swing.price) <= tolerance for item in cluster)


def _round_levels(
  swings: list[Swing],
  atr: pd.Series | float,
  round_step: float,
  tolerance: float,
  min_touches: int,
) -> list[Level]:
  if not swings or round_step <= 0:
    return []
  prices = [swing.price for swing in swings]
  low = math.floor(min(prices) / round_step) * round_step
  high = math.ceil(max(prices) / round_step) * round_step
  levels: list[Level] = []
  steps = int((high - low) / round_step) + 1
  for step in range(steps):
    price = low + (step * round_step)
    touches = sum(
      1 for swing in swings
      if abs(swing.price - price) <= max(tolerance, atr_at(atr, int(swing.index)) * 0.25)
    )
    if touches >= min_touches:
      levels.append(Level(price, "round", touches, tolerance, float(touches)))
  return levels


def _avg(swings: list[Swing]) -> float:
  return sum(swing.price for swing in swings) / len(swings)
