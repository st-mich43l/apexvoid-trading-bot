"""Price-only momentum classification."""

from __future__ import annotations

import pandas as pd

from app.pa_math import atr_series, body_fraction


def momentum(
  df: pd.DataFrame,
  atr: pd.Series | None = None,
  lookback: int = 8,
  body_frac: float = 0.6,
) -> str:
  if df.empty:
    return "neutral"
  atr = atr if atr is not None else atr_series(df)
  window = df.tail(max(1, lookback))
  strong_up = 0
  strong_down = 0
  for _, row in window.iterrows():
    if body_fraction(row) < body_frac:
      continue
    if float(row["close"]) > float(row["open"]):
      strong_up += 1
    elif float(row["close"]) < float(row["open"]):
      strong_down += 1
  rising_atr = _rising(atr.tail(len(window)))
  threshold = max(1, len(window) // 2)
  if rising_atr and strong_up >= threshold and strong_up > strong_down:
    return "bull"
  if rising_atr and strong_down >= threshold and strong_down > strong_up:
    return "bear"
  return "neutral"


def _rising(values: pd.Series) -> bool:
  clean = values.dropna()
  if len(clean) < 2:
    return True
  return float(clean.iloc[-1]) >= float(clean.iloc[0])
