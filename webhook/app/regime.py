"""Pure consolidation-break acceptance helpers."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.pa_math import atr_at

BREAKOUT_BUFFER_ATR = 0.1
BREAKOUT_ACCEPT_BARS = 2
BREAKOUT_MAX_AGE_BARS = 6
DISPLACEMENT_BODY_FRAC = 0.6
DISPLACEMENT_RANGE_ATR = 1.0


@dataclass(frozen=True)
class BoxBreak:
  box_high: float
  box_low: float
  direction: str
  accept_index: int
  coiling: bool
  acceptance: str = "displacement"


def accepted_box_break(
  df: pd.DataFrame,
  atr: pd.Series | float,
  regime,
  cfg,
) -> BoxBreak | None:
  if df.empty or regime is None:
    return None
  box_high = float(regime.range_high)
  box_low = float(regime.range_low)
  if box_high <= box_low:
    return None
  buffer_atr = max(
    0.0,
    float(getattr(cfg, "breakout_buffer_atr", BREAKOUT_BUFFER_ATR)),
  )
  accept_bars = max(
    1,
    int(getattr(cfg, "breakout_accept_bars", BREAKOUT_ACCEPT_BARS)),
  )
  up_holds = 0
  down_holds = 0
  accepted: list[BoxBreak] = []
  for index in range(len(df)):
    row = df.iloc[index]
    atr_value = atr_at(atr, index)
    buffer = buffer_atr * atr_value
    close = float(row["close"])
    above = close > box_high + buffer
    below = close < box_low - buffer

    up_holds = up_holds + 1 if above else 0
    down_holds = down_holds + 1 if below else 0
    if up_holds == 1 and displacement_grade(row, atr_value, "up"):
      accepted.append(BoxBreak(
        box_high,
        box_low,
        "up",
        index,
        bool(getattr(regime, "coiling", False)),
        "displacement",
      ))
    elif up_holds == accept_bars:
      accepted.append(BoxBreak(
        box_high,
        box_low,
        "up",
        index,
        bool(getattr(regime, "coiling", False)),
        f"{accept_bars} closes",
      ))
    if down_holds == 1 and displacement_grade(row, atr_value, "down"):
      accepted.append(BoxBreak(
        box_high,
        box_low,
        "down",
        index,
        bool(getattr(regime, "coiling", False)),
        "displacement",
      ))
    elif down_holds == accept_bars:
      accepted.append(BoxBreak(
        box_high,
        box_low,
        "down",
        index,
        bool(getattr(regime, "coiling", False)),
        f"{accept_bars} closes",
      ))
  return accepted[-1] if accepted else None


def displacement_grade(
  row: pd.Series,
  atr: float,
  direction: str,
) -> bool:
  open_ = float(row["open"])
  high = float(row["high"])
  low = float(row["low"])
  close = float(row["close"])
  candle_range = high - low
  if candle_range <= 0 or atr <= 0:
    return False
  directional = close > open_ if direction == "up" else close < open_
  return (
    directional
    and abs(close - open_) >= DISPLACEMENT_BODY_FRAC * candle_range
    and candle_range >= DISPLACEMENT_RANGE_ATR * atr
  )
