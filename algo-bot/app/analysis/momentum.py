"""Price-only momentum classification and ATR-normalized velocity/acceleration."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.analysis.math_utils import atr_at, atr_series, body_fraction


@dataclass(frozen=True)
class MomentumState:
  state: str
  velocity: float
  acceleration: float
  lookback: int


def momentum(
  df: pd.DataFrame,
  atr: pd.Series | None = None,
  lookback: int = 8,
  body_frac: float = 0.6,
) -> str:
  """Legacy body-count classifier (kept for compatibility / dual-run)."""
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


def momentum_state(
  df: pd.DataFrame,
  atr: pd.Series | None = None,
  lookback: int = 8,
  bull_threshold: float = 0.15,
  bear_threshold: float = -0.15,
) -> MomentumState:
  """Discrete price derivatives: v = ΔC/(n·ATR), a = Δv."""
  n = max(1, int(lookback))
  if df is None or len(df) < 2:
    return MomentumState("neutral", 0.0, 0.0, n)
  atr = atr if atr is not None else atr_series(df)
  closes = df["close"].astype(float)
  idx = len(closes) - 1
  v = _velocity_at(closes, atr, idx, n)
  prev_idx = idx - n
  a = 0.0
  if prev_idx >= n:
    v_prev = _velocity_at(closes, atr, prev_idx, n)
    a = v - v_prev
  if v >= float(bull_threshold):
    state = "bull"
  elif v <= float(bear_threshold):
    state = "bear"
  else:
    state = "neutral"
  return MomentumState(state=state, velocity=v, acceleration=a, lookback=n)


def _velocity_at(
  closes: pd.Series,
  atr: pd.Series | float,
  index: int,
  n: int,
) -> float:
  if index < n or index < 0 or index >= len(closes):
    return 0.0
  atr_value = atr_at(atr, index, fallback=0.0)
  if atr_value <= 0:
    return 0.0
  delta = float(closes.iloc[index]) - float(closes.iloc[index - n])
  return delta / (float(n) * atr_value)


def _rising(values: pd.Series) -> bool:
  clean = values.dropna()
  if len(clean) < 2:
    return True
  return float(clean.iloc[-1]) >= float(clean.iloc[0])
