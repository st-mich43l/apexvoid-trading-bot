"""Hybrid fractal + zigzag swing primitive."""

from __future__ import annotations

import pandas as pd

from app.analysis.math_utils import atr_at, atr_series
from app.analysis.types import Swing


def find_swings(
  df: pd.DataFrame,
  fractal_n: int = 2,
  zigzag_pct: float = 0.0,
  zigzag_atr_mult: float = 1.0,
  atr: pd.Series | None = None,
  as_of: int | None = None,
) -> list[Swing]:
  if len(df) < (fractal_n * 2) + 1:
    return []
  atr = atr if atr is not None else atr_series(df)
  candidates = _fractal_candidates(df, fractal_n, as_of)
  filtered = _zigzag_filter(candidates, atr, zigzag_pct, zigzag_atr_mult)
  return _label(filtered)


def _fractal_candidates(
  df: pd.DataFrame,
  n: int,
  as_of: int | None = None,
) -> list[Swing]:
  candidates: list[Swing] = []
  for i in range(n, len(df) - n):
    if as_of is not None and i + n > as_of:
      continue
    window = df.iloc[i - n:i + n + 1]
    row = df.iloc[i]
    high = float(row["high"])
    low = float(row["low"])
    ts = df.index[i]
    max_high = float(window["high"].max())
    if high == max_high and all(float(df.iloc[j]["high"]) < high for j in range(i - n, i)):
      candidates.append(Swing(i, "high", high, ts=ts))
    min_low = float(window["low"].min())
    if low == min_low and all(float(df.iloc[j]["low"]) > low for j in range(i - n, i)):
      candidates.append(Swing(i, "low", low, ts=ts))
  return sorted(candidates, key=lambda item: (int(item.index), item.kind))


def _zigzag_filter(
  candidates: list[Swing],
  atr: pd.Series,
  zigzag_pct: float,
  zigzag_atr_mult: float,
) -> list[Swing]:
  confirmed: list[Swing] = []
  for candidate in candidates:
    if not confirmed:
      confirmed.append(candidate)
      continue
    last = confirmed[-1]
    if candidate.kind == last.kind:
      if _more_extreme(candidate, last):
        confirmed[-1] = candidate
      continue
    threshold = max(
      abs(last.price) * max(0.0, zigzag_pct),
      atr_at(atr, int(candidate.index), fallback=0.0) * max(0.0, zigzag_atr_mult),
    )
    if abs(candidate.price - last.price) >= threshold:
      confirmed.append(candidate)
  return confirmed


def _more_extreme(candidate: Swing, current: Swing) -> bool:
  if candidate.kind == "high":
    return candidate.price > current.price
  return candidate.price < current.price


def _label(swings: list[Swing]) -> list[Swing]:
  last_high: float | None = None
  last_low: float | None = None
  result: list[Swing] = []
  for swing in swings:
    if swing.kind == "high":
      label = "HH" if last_high is None or swing.price > last_high else "LH"
      last_high = swing.price
    else:
      label = "HL" if last_low is None or swing.price > last_low else "LL"
      last_low = swing.price
    result.append(Swing(swing.index, swing.kind, swing.price, label, swing.ts))
  return result
