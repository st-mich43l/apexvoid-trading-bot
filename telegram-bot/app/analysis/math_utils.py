"""Small pure OHLC math helpers for price-action modules."""

from __future__ import annotations

import math

import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
  high = df["high"].astype(float)
  low = df["low"].astype(float)
  close = df["close"].astype(float)
  prev_close = close.shift(1)
  return pd.concat([
    high - low,
    (high - prev_close).abs(),
    (low - prev_close).abs(),
  ], axis=1).max(axis=1)


def atr_series(df: pd.DataFrame, length: int = 14) -> pd.Series:
  tr = true_range(df)
  return tr.rolling(length, min_periods=1).mean()


def atr_at(atr: pd.Series | float | int | None, index: int, fallback: float = 1.0) -> float:
  if atr is None:
    return fallback
  if isinstance(atr, int | float):
    value = float(atr)
  elif atr.empty:
    value = fallback
  else:
    safe_index = max(0, min(index, len(atr) - 1))
    value = float(atr.iloc[safe_index])
  if not math.isfinite(value) or value <= 0:
    return fallback
  return value


def atr_scalar(atr: pd.Series | float | int | None, fallback: float = 1.0) -> float:
  if atr is None:
    return fallback
  if isinstance(atr, int | float):
    value = float(atr)
  else:
    clean = atr.dropna()
    value = float(clean.median()) if not clean.empty else fallback
  if not math.isfinite(value) or value <= 0:
    return fallback
  return value


def candle_direction(row: pd.Series) -> str | None:
  open_ = float(row["open"])
  close = float(row["close"])
  if close > open_:
    return "up"
  if close < open_:
    return "down"
  return None


def body_fraction(row: pd.Series) -> float:
  high = float(row["high"])
  low = float(row["low"])
  span = high - low
  if span <= 0:
    return 0.0
  return abs(float(row["close"]) - float(row["open"])) / span
