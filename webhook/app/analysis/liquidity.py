"""Liquidity pools and wick-through grabs."""

from __future__ import annotations

import pandas as pd

from app.analysis.math_utils import atr_series, atr_scalar, body_fraction
from app.analysis.types import Grab, Leg, Pool, Swing, Zone


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


def liquidity_grabs(
  df: pd.DataFrame,
  pools: list[Pool],
  legs: list[Leg] | None = None,
  zones: list[Zone] | None = None,
  atr: pd.Series | None = None,
  sweep_body_frac: float = 0.5,
  sweep_react_bars: int = 3,
  inducement_band_atr: float = 0.3,
) -> list[Grab]:
  atr = atr if atr is not None else atr_series(df)
  grabs: list[Grab] = []
  for i, row in enumerate(df.itertuples()):
    for pool in pools:
      tol = max(pool.band, 0.0)
      if pool.side == "buy" and row.high > pool.level + tol:
        grade = _grab_grade(
          df,
          i,
          pool.level,
          "bear",
          legs or [],
          sweep_body_frac,
          sweep_react_bars,
        )
        if grade is not None:
          grabs.append(Grab(
            pool,
            i,
            "bear",
            df.index[i],
            grade,
            _has_reaction_displacement(i, "bear", legs or [], sweep_react_bars),
            _is_inducement(pool, zones or [], atr, inducement_band_atr),
          ))
      if pool.side == "sell" and row.low < pool.level - tol:
        grade = _grab_grade(
          df,
          i,
          pool.level,
          "bull",
          legs or [],
          sweep_body_frac,
          sweep_react_bars,
        )
        if grade is not None:
          grabs.append(Grab(
            pool,
            i,
            "bull",
            df.index[i],
            grade,
            _has_reaction_displacement(i, "bull", legs or [], sweep_react_bars),
            _is_inducement(pool, zones or [], atr, inducement_band_atr),
          ))
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


def _grab_grade(
  df: pd.DataFrame,
  index: int,
  level: float,
  direction: str,
  legs: list[Leg],
  sweep_body_frac: float,
  sweep_react_bars: int,
) -> str | None:
  row = df.iloc[index]
  close = float(row["close"])
  if direction == "bear":
    clean_close_back = close < level
    marginal = close <= level + _tol(row)
  else:
    clean_close_back = close > level
    marginal = close >= level - _tol(row)
  if clean_close_back:
    if body_fraction(row) >= sweep_body_frac and _has_reaction_displacement(
      index,
      direction,
      legs,
      sweep_react_bars,
    ):
      return "A"
    return "B"
  if marginal:
    return "C"
  return None


def _has_reaction_displacement(
  index: int,
  direction: str,
  legs: list[Leg],
  sweep_react_bars: int,
) -> bool:
  wanted = "up" if direction == "bull" else "down"
  for leg in legs:
    if leg.direction != wanted:
      continue
    if 0 <= leg.start - index <= max(0, int(sweep_react_bars)):
      return True
  return False


def _is_inducement(
  pool: Pool,
  zones: list[Zone],
  atr: pd.Series,
  inducement_band_atr: float,
) -> bool:
  if pool.touches != 2:
    return False
  if pool.band > atr_scalar(atr) * max(0.0, inducement_band_atr):
    return False
  for zone in zones:
    width = max(zone.high - zone.low, 0.0)
    tolerance = max(width, 0.1)
    if zone.side == "demand" and pool.side == "sell":
      if zone.low - tolerance <= pool.level <= zone.high:
        return True
    if zone.side == "supply" and pool.side == "buy":
      if zone.low <= pool.level <= zone.high + tolerance:
        return True
  return False


def _tol(row: pd.Series) -> float:
  return max(float(row["high"]) - float(row["low"]), 0.0) * 0.1
