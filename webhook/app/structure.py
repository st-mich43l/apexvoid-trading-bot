"""Pure structure layer plus compatibility helpers over closed OHLC bars."""

from __future__ import annotations

import pandas as pd

from app.levels import key_levels as cluster_key_levels
from app.pa_math import atr_series
from app.pa_types import Break, Level, Swing, Zone
from app.swings import find_swings


def market_structure(items: list[Swing]) -> str:
  highs = [s for s in items if s.kind == "high"]
  lows = [s for s in items if s.kind == "low"]
  if len(highs) >= 2 and len(lows) >= 2:
    if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
      return "up"
    if highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
      return "down"
  labels = {s.label for s in items[-4:]}
  if {"HH", "HL"} <= labels:
    return "up"
  if {"LH", "LL"} <= labels:
    return "down"
  return "range"


def structure_breaks(swings: list[Swing], df: pd.DataFrame) -> list[Break]:
  breaks: list[Break] = []
  trend = market_structure(swings)
  broken: set[tuple[str, float]] = set()
  for i in range(len(df)):
    close = float(df["close"].iloc[i])
    prior = [s for s in swings if int(s.index) < i]
    highs = [s for s in prior if s.kind == "high"]
    lows = [s for s in prior if s.kind == "low"]
    if highs:
      level = highs[-1].price
      key = ("up", level)
      if close > level and key not in broken:
        broken.add(key)
        breaks.append(Break(_break_kind(trend, "up"), "up", level, i, df.index[i]))
    if lows:
      level = lows[-1].price
      key = ("down", level)
      if close < level and key not in broken:
        broken.add(key)
        breaks.append(Break(_break_kind(trend, "down"), "down", level, i, df.index[i]))
  return breaks


def swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> list[Swing]:
  return find_swings(
    df,
    fractal_n=max(left, right),
    zigzag_pct=0.0,
    zigzag_atr_mult=0.0,
  )


def key_levels(df: pd.DataFrame) -> list[Level]:
  atr = atr_series(df)
  return cluster_key_levels(swings(df, 2, 2), atr, min_touches=1)


def equal_highs_lows(df: pd.DataFrame) -> list[Level]:
  from app.liquidity import liquidity_pools

  pivots = swings(df, 1, 1)
  pools = liquidity_pools(pivots, df)
  levels: list[Level] = []
  for pool in pools:
    if pool.touches < 2:
      continue
    kind = "equal_high" if pool.side == "buy" else "equal_low"
    levels.append(Level(pool.level, kind, pool.touches, pool.band, float(pool.touches)))
  return levels


def order_blocks(df: pd.DataFrame) -> list[Zone]:
  from app.zones import displacement, mark_mitigation, order_blocks as find_order_blocks

  atr = atr_series(df)
  pivots = find_swings(df, 2, 0.0, 0.0, atr)
  breaks = structure_breaks(pivots, df)
  legs = displacement(df, atr)
  zones = find_order_blocks(df, legs, breaks)
  if zones:
    return mark_mitigation(zones, df)
  return _legacy_order_blocks(df)


def fvg(df: pd.DataFrame) -> list[Zone]:
  from app.zones import fvg as find_fvg

  return find_fvg(df)


def flip_zones(df: pd.DataFrame) -> list[Zone]:
  return [
    zone for level in key_levels(df)
    if (zone := find_retest(df, level.price)) is not None
  ]


def liquidity_sweep(df: pd.DataFrame, level: float | Level) -> str | None:
  if df.empty:
    return None
  price = level.price if isinstance(level, Level) else float(level)
  row = df.iloc[-1]
  if float(row["high"]) > price and float(row["close"]) < price:
    return "sell"
  if float(row["low"]) < price and float(row["close"]) > price:
    return "buy"
  return None


def is_break(df: pd.DataFrame, level: float | Level) -> str | None:
  if len(df) < 2:
    return None
  price = level.price if isinstance(level, Level) else float(level)
  prev_close = float(df["close"].iloc[-2])
  close = float(df["close"].iloc[-1])
  if prev_close <= price < close:
    return "up"
  if prev_close >= price > close:
    return "down"
  return None


def find_retest(df: pd.DataFrame, level: float | Level) -> Zone | None:
  if len(df) < 3:
    return None
  price = level.price if isinstance(level, Level) else float(level)
  tolerance = _tol(df)
  closes = df["close"].astype(float)
  break_idx: int | None = None
  direction: str | None = None
  for i in range(1, len(df) - 1):
    if closes.iloc[i - 1] <= price < closes.iloc[i]:
      break_idx, direction = i, "buy"
    elif closes.iloc[i - 1] >= price > closes.iloc[i]:
      break_idx, direction = i, "sell"
  if break_idx is None or direction is None:
    return None
  for i in range(break_idx + 1, len(df)):
    row = df.iloc[i]
    touched = float(row["low"]) - tolerance <= price <= float(row["high"]) + tolerance
    if not touched:
      continue
    if direction == "buy" and float(row["close"]) >= price:
      return Zone(
        price - tolerance,
        price + tolerance,
        "demand",
        i,
        df.index[i],
        source="retest_support",
      )
    if direction == "sell" and float(row["close"]) <= price:
      return Zone(
        price - tolerance,
        price + tolerance,
        "supply",
        i,
        df.index[i],
        source="retest_resistance",
      )
  return None


def entry_zone(
  df: pd.DataFrame,
  level: float | Level,
  direction: str,
) -> Zone:
  price = level.price if isinstance(level, Level) else float(level)
  tolerance = _tol(df)
  zones = order_blocks(df) + flip_zones(df) + fvg(df)
  for zone in reversed(zones):
    if zone.low - tolerance <= price <= zone.high + tolerance:
      return zone
  side = "demand" if direction.upper() == "BUY" else "supply"
  return Zone(price - tolerance, price + tolerance, side, source=side)


def _break_kind(trend: str, direction: str) -> str:
  if trend == "up":
    return "BOS" if direction == "up" else "CHoCH"
  if trend == "down":
    return "BOS" if direction == "down" else "CHoCH"
  return "BOS"


def _tol(df: pd.DataFrame) -> float:
  if df.empty:
    return 0.0
  span = float(df["high"].max() - df["low"].min())
  return max(span * 0.003, 0.1)


def _legacy_order_blocks(df: pd.DataFrame) -> list[Zone]:
  zones: list[Zone] = []
  avg_range = (df["high"] - df["low"]).rolling(10, min_periods=1).mean()
  for i in range(1, len(df)):
    prev = df.iloc[i - 1]
    cur = df.iloc[i]
    impulse = abs(float(cur["close"] - cur["open"]))
    if impulse < float(avg_range.iloc[i]):
      continue
    if cur["close"] > cur["open"] and prev["close"] < prev["open"]:
      zones.append(Zone(float(prev["low"]), float(prev["high"]), "demand", i - 1, df.index[i - 1], source="bullish_ob"))
    if cur["close"] < cur["open"] and prev["close"] > prev["open"]:
      zones.append(Zone(float(prev["low"]), float(prev["high"]), "supply", i - 1, df.index[i - 1], source="bearish_ob"))
  return zones
