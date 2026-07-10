"""Liquidity pools and wick-through grabs."""

from __future__ import annotations

import pandas as pd

from app.pa_math import atr_series, atr_scalar
from app.pa_types import Grab, Pool, Swing


def liquidity_pools(
  swings: list[Swing],
  df: pd.DataFrame,
  equal_tol_atr: float = 0.15,
  atr: pd.Series | None = None,
) -> list[Pool]:
  if not swings:
    return []
  atr = atr if atr is not None else atr_series(df)
  band = atr_scalar(atr) * max(0.0, equal_tol_atr)
  pools = [
    *_cluster_pools([s for s in swings if s.kind == "high"], "buy", band),
    *_cluster_pools([s for s in swings if s.kind == "low"], "sell", band),
  ]
  highs = [s for s in swings if s.kind == "high"]
  lows = [s for s in swings if s.kind == "low"]
  if highs:
    extreme = max(highs, key=lambda item: item.price)
    _append_lone_extreme(pools, Pool("buy", extreme.price, band, 1))
  if lows:
    extreme = min(lows, key=lambda item: item.price)
    _append_lone_extreme(pools, Pool("sell", extreme.price, band, 1))
  return sorted(pools, key=lambda item: (item.level, item.side))


def liquidity_grabs(df: pd.DataFrame, pools: list[Pool]) -> list[Grab]:
  grabs: list[Grab] = []
  for i, row in enumerate(df.itertuples()):
    for pool in pools:
      tol = max(pool.band, 0.0)
      if pool.side == "buy" and row.high > pool.level + tol and row.close < pool.level:
        grabs.append(Grab(pool, i, "bear", df.index[i]))
      if pool.side == "sell" and row.low < pool.level - tol and row.close > pool.level:
        grabs.append(Grab(pool, i, "bull", df.index[i]))
  return grabs


def _cluster_pools(swings: list[Swing], side: str, band: float) -> list[Pool]:
  pools: list[Pool] = []
  ordered = sorted(swings, key=lambda item: item.price)
  clusters: list[list[Swing]] = []
  for swing in ordered:
    if clusters and abs(_avg(clusters[-1]) - swing.price) <= band:
      clusters[-1].append(swing)
    else:
      clusters.append([swing])
  for cluster in clusters:
    if len(cluster) >= 2:
      pools.append(Pool(side, _avg(cluster), band, len(cluster)))
  return pools


def _append_lone_extreme(pools: list[Pool], pool: Pool) -> None:
  if not any(abs(item.level - pool.level) <= max(item.band, pool.band) for item in pools):
    pools.append(pool)


def _avg(swings: list[Swing]) -> float:
  return sum(swing.price for swing in swings) / len(swings)
