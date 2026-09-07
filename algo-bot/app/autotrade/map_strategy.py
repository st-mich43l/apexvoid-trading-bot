"""M1 reaction-lookback helpers shared by other guards, and the Market Map
Redis key/decode helpers `market_map_delivery.py`'s owner digest still uses.

2026-09 (Market Map purge stage 4): the nearest-actionable-zone selection
tree (`evaluate_market_map_strategy`/`_select_reaction_detailed` and their
exclusive helpers) is deleted - its `.match` result was confirmed dead in
production back in stage 1 (a prior H1->M15->M5 cutover already retired the
M1 touch/rejection reaction detector as a setup source), and no other
production path ever called the selection function itself.
`_reaction_in_lookback`/`_touches`/`_rejects` remain, since `worker.py`'s
overlap-thesis guard and `scale_context.py` still use them to disambiguate
an already-formed candidate's direction, which is a distinct concern from
originating a setup, and does not depend on Market Map's own data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from app.analysis.market_map import MapEntry, MarketMap, market_map_from_payload


MARKET_MAP_KEY_PREFIX = "auto_trade:market_map"
MARKET_MAP_DISPLAY_KEY_PREFIX = "auto_trade:market_map_display"


@dataclass(frozen=True)
class _LookbackReaction:
  reaction_type: str
  touch_bar_ts: str | None
  confirmation_bar_ts: str | None
  reaction_age_bars: int


def _bar_ts(index_value: object) -> str | None:
  if index_value is None:
    return None
  if hasattr(index_value, "isoformat"):
    try:
      return index_value.isoformat()
    except (TypeError, ValueError):
      return str(index_value)
  return str(index_value)


def _closes_away(
  row: pd.Series,
  entry: MapEntry,
  direction: str,
  tolerance: float,
) -> bool:
  close = float(row["close"])
  if direction == "SELL":
    return close < entry.lo - tolerance
  return close > entry.hi + tolerance


def _reaction_in_lookback(
  m1: pd.DataFrame,
  entry: MapEntry,
  direction: str,
  atr: float,
  tolerance: float,
  cfg: Any,
  price: float,
) -> _LookbackReaction | None:
  lookback = max(
    1, int(cfg.execution.mapped_zone.reaction_lookback_bars)
  )
  window = m1.tail(lookback)
  if window.empty:
    return None
  touch_positions = [
    offset
    for offset in range(len(window))
    if _touches(window.iloc[offset], entry, tolerance)
  ]
  if not touch_positions:
    return None
  # Prefer the most recent touch that still has a later confirmation.
  for touch_pos in reversed(touch_positions):
    touch_row = window.iloc[touch_pos]
    touch_ts = _bar_ts(window.index[touch_pos])
    age = len(window) - 1 - touch_pos
    if age >= lookback:
      continue
    same_bar_reject = _rejects(touch_row, direction, atr)
    if same_bar_reject:
      return _LookbackReaction(
        "rejection",
        touch_ts,
        touch_ts,
        age,
      )
    for confirm_pos in range(touch_pos + 1, len(window)):
      confirm_row = window.iloc[confirm_pos]
      confirm_ts = _bar_ts(window.index[confirm_pos])
      if _rejects(confirm_row, direction, atr):
        return _LookbackReaction(
          "rejection",
          touch_ts,
          confirm_ts,
          age,
        )
      if _closes_away(confirm_row, entry, direction, tolerance):
        reclaim = (
          direction == "BUY"
          and float(confirm_row["close"]) > entry.hi
        ) or (
          direction == "SELL"
          and float(confirm_row["close"]) < entry.lo
        )
        return _LookbackReaction(
          "reclaim" if reclaim else "close_away",
          touch_ts,
          confirm_ts,
          age,
        )
  # Touched inside lookback but never confirmed.
  last_touch = touch_positions[-1]
  return _LookbackReaction(
    "touch_only",
    _bar_ts(window.index[last_touch]),
    None,
    len(window) - 1 - last_touch,
  )


def _touches(row: pd.Series, entry: MapEntry, tolerance: float) -> bool:
  return (
    float(row["low"]) <= entry.hi + tolerance
    and float(row["high"]) >= entry.lo - tolerance
  )


def _rejects(row: pd.Series, direction: str, atr: float) -> bool:
  high = float(row["high"])
  low = float(row["low"])
  close = float(row["close"])
  candle_range = high - low
  if candle_range <= 0:
    return False
  minimum = max(0.15 * atr, 0.2 * candle_range)
  if direction == "SELL":
    return high - close >= minimum and close <= low + 0.6 * candle_range
  return close - low >= minimum and close >= high - 0.6 * candle_range


def market_map_key(symbol: str) -> str:
  return f"{MARKET_MAP_KEY_PREFIX}:{symbol.upper()}"


def market_map_display_key(symbol: str) -> str:
  return f"{MARKET_MAP_DISPLAY_KEY_PREFIX}:{symbol.upper()}"


def decode_market_map(raw: object) -> MarketMap | None:
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    return market_map_from_payload(text)
  except (KeyError, TypeError, ValueError):
    return None
