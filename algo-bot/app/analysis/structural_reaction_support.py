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
from app.core.symbols import digits_for
from app.runtime.price_identity import price_token

CONFIRM_WICK_REJECTION = "wick_rejection"
CONFIRM_SWEEP_RECLAIM = "sweep_reclaim"
CONFIRM_REJECTION_CHOCH = "rejection_choch"
CONFIRM_STRONG_RECLAIM = "strong_reclaim"
CONFIRM_ENGULFING = "engulfing"

STRUCTURAL_SETUPS = frozenset({
  "Key Level Reaction",
  "Zone Reaction",
  "Flip Zone",
  # Legacy labels still treated as structural for open/historical setups:
  "Demand Zone Reaction",
  "Supply Zone Reaction",
  "Session Level Reaction",
  "Trendline Reaction",
  # Atomic technique publishers + confluence band:
  "Supply Demand",
  "Order Block",
  "FVG",
  "iFVG",
  "CRT",
  "Confluence Zone",
})

_ZONE_REACTION_ALIASES = frozenset({
  "Zone Reaction",
  "Demand Zone Reaction",
  "Supply Zone Reaction",
})

_EPS = 1e-12


def canonical_structural_setup(setup: str) -> str:
  """Map legacy Demand/Supply Zone Reaction labels to canonical Zone Reaction."""
  key = str(setup or "")
  return "Zone Reaction" if key in _ZONE_REACTION_ALIASES else key


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


def _price_id(symbol: str, value: float) -> str:
  return price_token(value, digits=digits_for(symbol))


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
    _price_id(symbol, zone.low),
    _price_id(symbol, zone.high),
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
    _price_id(symbol, level.price),
  )


def equal_level_structural_id(
  symbol: str,
  timeframe: str,
  level: Level,
) -> str:
  """Equal-highs/equal-lows liquidity pool identity (Fade Scalp).

  Tagged distinctly from key_level_structural_id so an equal-level pool at
  the same price as an unrelated key level never collides in identity.
  """
  return structural_hash(
    symbol.upper(),
    timeframe.upper(),
    "equal_level",
    level.kind,
    _price_id(symbol, level.price),
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
    _price_id(symbol, level.price),
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
    _price_id(symbol, getattr(line, "intercept", 0.0)),
  )


def box_structural_id(
  symbol: str,
  timeframe: str,
  box: Any,
) -> str:
  """Accepted consolidation-box identity (Box Breakout).

  Keyed on the box's own edges, direction, and acceptance bar - the same
  accepted box always yields the same id across the proximal-entry poll and
  every later retest poll, so a proximal fire and a subsequent retest fire
  on the same box collapse into one confluence zone instead of two.
  """
  return structural_hash(
    symbol.upper(),
    timeframe.upper(),
    "box_breakout",
    getattr(box, "direction", ""),
    _price_id(symbol, getattr(box, "box_low", 0.0)),
    _price_id(symbol, getattr(box, "box_high", 0.0)),
    int(getattr(box, "accept_index", -1)),
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
    canonical_structural_setup(strategy),
    direction.upper(),
    structural_source,
    structural_id,
    touch_bar_ts or "",
    confirmation_bar_ts or "",
  )


def v7_thesis_id(
  *,
  symbol: str,
  strategy_family: str,
  direction: str,
  structural_id: str,
  version: int = 1,
) -> str:
  """Stable TradePlan V7 thesis identity (docs/adr-trade-plan-v7-boundary.md).

  Deliberately narrower than structural_thesis_id() above: this excludes
  touch_bar_ts/confirmation_bar_ts on purpose. Those timestamps make
  structural_thesis_id() (and match_id, which reuses it) change on every
  new confirmation of the same structural reaction - correct for V6's
  per-event dedup, but exactly what a V7 thesis must NOT do, since "a new
  confirmation timestamp alone must not create a new thesis" is a hard
  requirement. A thesis is identified purely by what structure it is: the
  same (symbol, strategy_family, direction, structural_id) across any
  number of re-confirmations is the same thesis.
  """
  return structural_hash(
    "v7-thesis",
    f"v{version}",
    symbol.upper(),
    strategy_family,
    direction.upper(),
    structural_id,
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


def engulfing_on_bar(
  row: pd.Series,
  prior_row: pd.Series,
  direction: str,
) -> bool:
  """Bullish/bearish engulfing: this bar's body fully engulfs the prior
  bar's body, closing in the reaction direction.

  A slow multi-hour grind at a zone often never produces a single dramatic
  rejection wick (what wick_rejection_on_bar/strong_reclaim_on_bar require)
  even after repeated genuine touches - small-bodied consolidation candles
  absorbing at the level, then one decisive engulfing candle, is the
  textbook confirmation for exactly that shape of reaction. Already
  implemented and tested for M1 execution timing
  (app.analysis.m1_trigger._engulfing); mirrored here, without the M1
  config dependency, as a first-class M5 structural reaction confirmation
  - additive only, checked after every existing (stricter) pattern.
  """
  open_ = float(row["open"])
  close = float(row["close"])
  prior_open = float(prior_row["open"])
  prior_close = float(prior_row["close"])
  body_low = min(open_, close)
  body_high = max(open_, close)
  prior_low = min(prior_open, prior_close)
  prior_high = max(prior_open, prior_close)
  if body_low > prior_low or body_high < prior_high:
    return False
  if direction == "BUY":
    return close > open_
  return close < open_


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
    elif (
      confirm_index > 0
      and engulfing_on_bar(row, df.iloc[confirm_index - 1], side)
    ):
      confirmation = CONFIRM_ENGULFING

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
