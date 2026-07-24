"""Shared closed-bar confirmation and stable IDs for first-class structural reactions.

Pure helpers — no detector registry imports — so detectors can call them without
circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

import pandas as pd

from app.analysis.types import Grab, Level, SessionLevel, Zone

CONFIRM_WICK_REJECTION = "wick_rejection"
CONFIRM_SWEEP_RECLAIM = "sweep_reclaim"
CONFIRM_REJECTION_CHOCH = "rejection_choch"
CONFIRM_STRONG_RECLAIM = "strong_reclaim"

STRUCTURAL_SETUPS = frozenset({
  "Key Level Reaction",
  "Demand Zone Reaction",
  "Supply Zone Reaction",
  "Session Level Reaction",
  "Trendline Reaction",
})

_EPS = 1e-12


@dataclass(frozen=True)
class ReactionConfirmation:
  confirmation_type: str
  touch_bar_ts: str
  confirmation_bar_ts: str
  touch_index: int
  confirmation_index: int


def bias_relationship(htf_bias: str, direction: str) -> str:
  bias = (htf_bias or "").casefold()
  side = (direction or "").upper()
  if bias not in {"up", "down"}:
    return "neutral"
  aligned = (bias == "up" and side == "BUY") or (bias == "down" and side == "SELL")
  return "with_bias" if aligned else "counter_bias"


def bar_ts(df: pd.DataFrame, index: int) -> str:
  if df.empty or index < 0 or index >= len(df):
    return ""
  stamp = df.index[index]
  try:
    return pd.Timestamp(stamp).isoformat()
  except (TypeError, ValueError):
    return str(stamp)


def structural_hash(*parts: object) -> str:
  raw = "|".join(str(part) for part in parts)
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def zone_structural_id(
  symbol: str,
  timeframe: str,
  zone: Zone,
) -> str:
  origin = getattr(zone, "origin_index", -1)
  source = zone.source or zone.side
  return structural_hash(
    symbol.upper(),
    timeframe.upper(),
    "supply_demand",
    zone.side,
    source,
    f"{float(zone.low):.5f}",
    f"{float(zone.high):.5f}",
    origin,
  )


def key_level_structural_id(
  symbol: str,
  timeframe: str,
  level: Level,
) -> str:
  return structural_hash(
    symbol.upper(),
    timeframe.upper(),
    "key_level",
    level.kind,
    f"{round(float(level.price), 2):.2f}",
  )


def session_level_structural_id(
  symbol: str,
  timeframe: str,
  level: SessionLevel,
) -> str:
  return structural_hash(
    symbol.upper(),
    timeframe.upper(),
    "session_level",
    level.name,
    f"{round(float(level.price), 2):.2f}",
  )


def trendline_structural_id(
  symbol: str,
  timeframe: str,
  line: Any,
) -> str:
  anchors = ",".join(str(int(idx)) for idx in getattr(line, "point_idx", ()))
  return structural_hash(
    symbol.upper(),
    timeframe.upper(),
    "trendline",
    getattr(line, "kind", ""),
    anchors,
    f"{float(getattr(line, 'slope', 0.0)):.8f}",
    f"{float(getattr(line, 'intercept', 0.0)):.5f}",
  )


def structural_thesis_id(
  *,
  symbol: str,
  strategy: str,
  direction: str,
  structural_source: str,
  structural_id: str,
  touch_bar_ts: str,
  confirmation_bar_ts: str,
  version: int = 1,
) -> str:
  return structural_hash(
    f"v{version}",
    symbol.upper(),
    strategy,
    direction.upper(),
    structural_source,
    structural_id,
    touch_bar_ts or "",
    confirmation_bar_ts or "",
  )


def band_touched(row: pd.Series, low: float, high: float) -> bool:
  return float(row["low"]) <= high + _EPS and float(row["high"]) >= low - _EPS


def level_band_touched(row: pd.Series, price: float, band: float) -> bool:
  return (
    float(row["low"]) <= price + max(0.0, band) + _EPS
    and float(row["high"]) >= price - max(0.0, band) - _EPS
  )


def wick_rejection_on_bar(row: pd.Series, direction: str) -> bool:
  open_ = float(row["open"])
  high = float(row["high"])
  low = float(row["low"])
  close = float(row["close"])
  candle_range = high - low
  if candle_range <= 0:
    return False
  body = abs(close - open_)
  upper = high - max(open_, close)
  lower = min(open_, close) - low
  lower_third = low + candle_range / 3
  upper_third = high - candle_range / 3
  if direction == "SELL":
    return upper >= body and close < open_ and close <= lower_third
  return lower >= body and close > open_ and close >= upper_third


def strong_reclaim_on_bar(
  row: pd.Series,
  *,
  direction: str,
  low: float,
  high: float,
) -> bool:
  """Wick pierces the structure then closes back through the near edge."""
  open_ = float(row["open"])
  high_px = float(row["high"])
  low_px = float(row["low"])
  close = float(row["close"])
  if direction == "BUY":
    swept = low_px < low - _EPS
    reclaimed = close >= low - _EPS and close > open_
    return swept and reclaimed
  swept = high_px > high + _EPS
  reclaimed = close <= high + _EPS and close < open_
  return swept and reclaimed


def evaluate_structural_reaction(
  df: pd.DataFrame,
  *,
  direction: str,
  low: float,
  high: float,
  lookback_bars: int,
  grabs: list[Grab] | None = None,
  has_choch: bool = False,
) -> ReactionConfirmation | None:
  """Find touch + confirmation within a closed-bar lookback window.

  Touch and confirmation may be on different bars; confirmation must be on or
  after the touch bar; both must fall inside the lookback from the latest bar.
  """
  if df.empty:
    return None
  lookback = max(1, int(lookback_bars))
  last = len(df) - 1
  earliest = max(0, last - lookback + 1)
  side = direction.upper()

  touch_indexes: list[int] = []
  for index in range(earliest, last + 1):
    if band_touched(df.iloc[index], low, high):
      touch_indexes.append(index)
  if not touch_indexes:
    return None

  grab_by_index = {
    int(grab.index): grab
    for grab in (grabs or [])
    if earliest <= int(grab.index) <= last
  }

  # Prefer the latest valid confirmation so stale touches without fresh
  # confirmation do not execute.
  for confirm_index in range(last, earliest - 1, -1):
    row = df.iloc[confirm_index]
    touches_here = [idx for idx in touch_indexes if idx <= confirm_index]
    if not touches_here:
      continue
    touch_index = touches_here[-1]
    confirmation: str | None = None

    grab = grab_by_index.get(confirm_index)
    if grab is not None and grab.grade in {"A", "B"}:
      confirmation = CONFIRM_SWEEP_RECLAIM
    elif wick_rejection_on_bar(row, side) and has_choch:
      confirmation = CONFIRM_REJECTION_CHOCH
    elif strong_reclaim_on_bar(row, direction=side, low=low, high=high):
      confirmation = CONFIRM_STRONG_RECLAIM
    elif wick_rejection_on_bar(row, side):
      confirmation = CONFIRM_WICK_REJECTION

    if confirmation is None:
      continue
    return ReactionConfirmation(
      confirmation_type=confirmation,
      touch_bar_ts=bar_ts(df, touch_index),
      confirmation_bar_ts=bar_ts(df, confirm_index),
      touch_index=touch_index,
      confirmation_index=confirm_index,
    )
  return None
