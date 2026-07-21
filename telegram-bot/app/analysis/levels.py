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
) -> list[Level]:
  tolerance = atr_scalar(atr) * max(0.0, level_cluster_atr)
  clusters = _price_clusters(swings, tolerance)
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
  return deduped


def _price_clusters(swings: list[Swing], tolerance: float) -> list[list[Swing]]:
  clusters: list[list[Swing]] = []
  for swing in sorted(swings, key=lambda item: item.price):
    if clusters and abs(_avg(clusters[-1]) - swing.price) <= tolerance:
      clusters[-1].append(swing)
    else:
      clusters.append([swing])
  return clusters


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
