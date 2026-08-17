"""Closed-bar M1 microstructure for scalping."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.scalping.models import MicroStructure, MicroSwing


def _ts(index_value: Any) -> int:
  return int(pd.Timestamp(index_value).timestamp())


def build_micro_structure(
  df: pd.DataFrame,
  *,
  swing_lookback: int = 3,
  equal_tol: float = 0.05,
) -> MicroStructure:
  if df is None or df.empty:
    return MicroStructure(
      structure="empty",
      swings=(),
      last_break_direction=None,
      last_break_price=None,
      last_break_ts=None,
      equal_highs=(),
      equal_lows=(),
    )
  swings: list[MicroSwing] = []
  highs = df["high"].astype(float)
  lows = df["low"].astype(float)
  lb = max(1, int(swing_lookback))
  for i in range(lb, len(df) - lb):
    h = float(highs.iloc[i])
    l = float(lows.iloc[i])
    if h >= float(highs.iloc[i - lb: i + lb + 1].max()):
      swings.append(MicroSwing("high", h, _ts(df.index[i]), i))
    if l <= float(lows.iloc[i - lb: i + lb + 1].min()):
      swings.append(MicroSwing("low", l, _ts(df.index[i]), i))

  equal_highs: list[float] = []
  equal_lows: list[float] = []
  high_swings = [s for s in swings if s.kind == "high"]
  low_swings = [s for s in swings if s.kind == "low"]
  for i, first in enumerate(high_swings):
    for second in high_swings[i + 1:]:
      if abs(first.price - second.price) <= equal_tol:
        equal_highs.append(round((first.price + second.price) / 2.0, 2))
  for i, first in enumerate(low_swings):
    for second in low_swings[i + 1:]:
      if abs(first.price - second.price) <= equal_tol:
        equal_lows.append(round((first.price + second.price) / 2.0, 2))

  last_break_direction = None
  last_break_price = None
  last_break_ts = None
  structure = "range"
  if len(high_swings) >= 2 and len(low_swings) >= 2:
    if high_swings[-1].price > high_swings[-2].price and low_swings[-1].price > low_swings[-2].price:
      structure = "bullish"
      last_break_direction = "BUY"
      last_break_price = high_swings[-1].price
      last_break_ts = high_swings[-1].bar_ts
    elif high_swings[-1].price < high_swings[-2].price and low_swings[-1].price < low_swings[-2].price:
      structure = "bearish"
      last_break_direction = "SELL"
      last_break_price = low_swings[-1].price
      last_break_ts = low_swings[-1].bar_ts

  return MicroStructure(
    structure=structure,
    swings=tuple(swings),
    last_break_direction=last_break_direction,
    last_break_price=last_break_price,
    last_break_ts=last_break_ts,
    equal_highs=tuple(sorted(set(equal_highs))),
    equal_lows=tuple(sorted(set(equal_lows))),
    measured={"swing_count": len(swings)},
  )


def detect_sweep_reclaim(
  df: pd.DataFrame,
  *,
  direction: str,
  edge_price: float,
  tolerance: float,
  lookback_bars: int = 1,
) -> dict[str, Any] | None:
  """Edge touch / false-break that closes back inside the range.

  Owner 2026-08-06: requiring ``low < edge`` skipped bars that only wicked
  *to* the edge. Accept touch-or-through within ``tolerance``, close
  reclaimed inside, directional close. Scans newest ``lookback_bars`` so
  discovery matches activation age (slow M5 rebuild must not skip the only
  reclaim bar forever).
  """
  if df is None or len(df) < 1:
    return None
  side = str(direction).upper()
  edge = float(edge_price)
  tol = max(0.0, float(tolerance))
  window = max(1, min(int(lookback_bars or 1), len(df)))

  for offset in range(1, window + 1):
    bar = df.iloc[-offset]
    open_ = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])
    bar_ts = _ts(df.index[-offset])

    if side == "BUY":
      # Touch or pierce support, close back at/above edge, bullish bar.
      if low > edge + tol:
        continue
      if close < edge:
        continue
      if close <= open_:
        continue
      return {
        "pattern": "sweep_reclaim",
        "direction": "BUY",
        "bar_ts": bar_ts,
        "extreme": low,
        "close": close,
        "edge": edge,
      }

    # Touch or pierce resistance, close back at/below edge, bearish bar.
    if high < edge - tol:
      continue
    if close > edge:
      continue
    if close >= open_:
      continue
    return {
      "pattern": "sweep_reclaim",
      "direction": "SELL",
      "bar_ts": bar_ts,
      "extreme": high,
      "close": close,
      "edge": edge,
    }
  return None


def detect_impulse_pullback(
  df: pd.DataFrame,
  *,
  direction: str,
  min_retracement: float = 0.25,
  max_retracement: float = 0.75,
  preferred_low: float = 0.382,
  preferred_high: float = 0.618,
) -> dict[str, Any] | None:
  """Measure pullback against the most recent impulse leg."""
  if df is None or len(df) < 8:
    return None
  side = str(direction).upper()
  closes = df["close"].astype(float)
  highs = df["high"].astype(float)
  lows = df["low"].astype(float)
  window = df.tail(30)
  if side == "BUY":
    impulse_start_idx = int(window["low"].astype(float).idxmin()) if False else None
    # Use positional min/max in last 30 bars
    lows_w = window["low"].astype(float)
    highs_w = window["high"].astype(float)
    origin_i = int(lows_w.values.argmin())
    extreme_slice = highs_w.iloc[origin_i:]
    if extreme_slice.empty:
      return None
    extreme_i = origin_i + int(extreme_slice.values.argmax())
    origin = float(lows_w.iloc[origin_i])
    extreme = float(highs_w.iloc[extreme_i])
    impulse_len = extreme - origin
    if impulse_len <= 0:
      return None
    current = float(window["close"].iloc[-1])
    pullback = extreme - current
    retracement = pullback / impulse_len
    if retracement < min_retracement:
      return {"rejected": True, "reason": "pullback_too_shallow", "retracement": retracement}
    if retracement > max_retracement:
      return {"rejected": True, "reason": "pullback_too_deep", "retracement": retracement}
    # Continuation evidence: last bar bullish
    last = window.iloc[-1]
    if float(last["close"]) <= float(last["open"]):
      return None
    if abs(current - extreme) / impulse_len < 0.05:
      return {"rejected": True, "reason": "continuation_overextended", "retracement": retracement}
    return {
      "pattern": "impulse_pullback",
      "direction": "BUY",
      "bar_ts": _ts(window.index[-1]),
      "origin": origin,
      "extreme": extreme,
      "retracement": retracement,
      "preferred": preferred_low <= retracement <= preferred_high,
      "close": current,
    }

  highs_w = window["high"].astype(float)
  lows_w = window["low"].astype(float)
  origin_i = int(highs_w.values.argmax())
  extreme_slice = lows_w.iloc[origin_i:]
  if extreme_slice.empty:
    return None
  extreme_i = origin_i + int(extreme_slice.values.argmin())
  origin = float(highs_w.iloc[origin_i])
  extreme = float(lows_w.iloc[extreme_i])
  impulse_len = origin - extreme
  if impulse_len <= 0:
    return None
  current = float(window["close"].iloc[-1])
  pullback = current - extreme
  retracement = pullback / impulse_len
  if retracement < min_retracement:
    return {"rejected": True, "reason": "pullback_too_shallow", "retracement": retracement}
  if retracement > max_retracement:
    return {"rejected": True, "reason": "pullback_too_deep", "retracement": retracement}
  last = window.iloc[-1]
  if float(last["close"]) >= float(last["open"]):
    return None
  if abs(current - extreme) / impulse_len < 0.05:
    return {"rejected": True, "reason": "continuation_overextended", "retracement": retracement}
  return {
    "pattern": "impulse_pullback",
    "direction": "SELL",
    "bar_ts": _ts(window.index[-1]),
    "origin": origin,
    "extreme": extreme,
    "retracement": retracement,
    "preferred": preferred_low <= retracement <= preferred_high,
    "close": current,
  }


def macro_momentum_direction(
  df: pd.DataFrame,
  *,
  atr: float,
  min_displacement_atr: float = 2.5,
  lookback_bars: int = 60,
) -> str | None:
  """Net directional bias over a wide recent window, or None if unclear.

  Live 2026-08-06: an impulse_pullback SELL faded the top of a "range"
  whose own high/low were the pre-crash level and flash-crash low from
  under an hour earlier -- price wasn't topping out at stable resistance,
  it was mid-reclaim of the entire crash with the freshest, strongest
  momentum on the chart. impulse_pullback's own lookback is only the last
  30 bars, too narrow to see a move that size; m5_structure and htf_bias
  had nothing to say either ("range" / "unknown"). This is a wider,
  displacement-only read (no directional-bar-count, no freshness
  requirement -- unlike detect_momentum_ignition, which is for *entering*
  a chase, this is only ever used as a veto against fading one).
  """
  if df is None or len(df) < lookback_bars or atr <= 0:
    return None
  window = df.tail(lookback_bars)
  displacement = float(window["close"].iloc[-1] - window["open"].iloc[0])
  if abs(displacement) / atr < min_displacement_atr:
    return None
  return "BUY" if displacement > 0 else "SELL"


def detect_momentum_ignition(
  df: pd.DataFrame,
  *,
  direction: str,
  atr: float,
  min_displacement_atr: float = 1.0,  # owner-tuned 2026-08-11, was 1.2
  lookback_bars: int = 5,
  min_directional_bars: int = 4,
) -> dict[str, Any] | None:
  """A live, still-accelerating thrust -- chase it, don't wait for a pullback.

  impulse_pullback deliberately waits for a 25-75% retracement before
  entering. That leaves a straight, uninterrupted run with nothing to catch
  it: the market can travel the entire distance a scalp would have wanted
  before ever handing back the pullback impulse_pullback is waiting for.
  This fires while the thrust is still in progress -- most of the last
  ``lookback_bars`` bars directional, net displacement past
  ``min_displacement_atr``, and the newest bar still making a fresh extreme
  (not basing/stalling, which is impulse_pullback's job, not this one's).
  """
  if df is None or len(df) < lookback_bars or atr <= 0:
    return None
  side = str(direction).upper()
  window = df.tail(lookback_bars)
  opens = window["open"].astype(float)
  closes = window["close"].astype(float)
  highs = window["high"].astype(float)
  lows = window["low"].astype(float)
  last = window.iloc[-1]

  # Diagnostic (2026-08-11): owner report - momentum_chase fires rarely
  # even on visibly impulsive candles. Every rejection path below used to
  # return a bare None, indistinguishable from every other - the caller's
  # "not_matched" telemetry couldn't tell "wrong number of directional
  # bars" from "displacement fell short" from "last bar isn't a fresh
  # extreme" (momentum_stalling, the only one that already carried a
  # reason). All four now carry a reason plus the measured value that
  # missed, so a real distribution can be read from production instead of
  # guessing which of these four independent, all-mandatory conditions is
  # actually the bottleneck.
  if side == "BUY":
    directional = int((closes > opens).sum())
    if directional < min_directional_bars:
      return {
        "rejected": True, "reason": "insufficient_directional_bars",
        "directional_bars": directional, "min_directional_bars": min_directional_bars,
      }
    displacement = float(closes.iloc[-1] - opens.iloc[0])
    if displacement <= 0 or displacement / atr < min_displacement_atr:
      return {
        "rejected": True, "reason": "insufficient_displacement",
        "displacement_atr": displacement / atr if atr > 0 else 0.0,
        "min_displacement_atr": min_displacement_atr,
      }
    if float(last["close"]) <= float(last["open"]):
      return {"rejected": True, "reason": "last_bar_not_directional"}
    if float(last["high"]) < float(highs.iloc[:-1].max()):
      return {"rejected": True, "reason": "momentum_stalling"}
    return {
      "pattern": "momentum_ignition",
      "direction": "BUY",
      "bar_ts": _ts(window.index[-1]),
      "extreme": float(lows.min()),
      "close": float(closes.iloc[-1]),
      "directional_bars": directional,
      "displacement_atr": displacement / atr,
    }

  directional = int((closes < opens).sum())
  if directional < min_directional_bars:
    return {
      "rejected": True, "reason": "insufficient_directional_bars",
      "directional_bars": directional, "min_directional_bars": min_directional_bars,
    }
  displacement = float(opens.iloc[0] - closes.iloc[-1])
  if displacement <= 0 or displacement / atr < min_displacement_atr:
    return {
      "rejected": True, "reason": "insufficient_displacement",
      "displacement_atr": displacement / atr if atr > 0 else 0.0,
      "min_displacement_atr": min_displacement_atr,
    }
  if float(last["close"]) >= float(last["open"]):
    return {"rejected": True, "reason": "last_bar_not_directional"}
  if float(last["low"]) > float(lows.iloc[:-1].min()):
    return {"rejected": True, "reason": "momentum_stalling"}
  return {
    "pattern": "momentum_ignition",
    "direction": "SELL",
    "bar_ts": _ts(window.index[-1]),
    "extreme": float(highs.max()),
    "close": float(closes.iloc[-1]),
    "directional_bars": directional,
    "displacement_atr": displacement / atr,
  }


def detect_breakout_retest(
  df: pd.DataFrame,
  *,
  direction: str,
  box_high: float,
  box_low: float,
  min_displacement: float,
) -> dict[str, Any] | None:
  """Require accepted break → return → hold, not wick-only break."""
  if df is None or len(df) < 5:
    return None
  side = str(direction).upper()
  high = float(box_high)
  low = float(box_low)
  if high <= low:
    return None

  accepted_i = None
  for i in range(len(df) - 3, len(df)):
    if i < 1:
      continue
    bar = df.iloc[i]
    close = float(bar["close"])
    open_ = float(bar["open"])
    if side == "BUY":
      if close > high and (close - high) >= min_displacement and close > open_:
        accepted_i = i
    else:
      if close < low and (low - close) >= min_displacement and close < open_:
        accepted_i = i
  if accepted_i is None or accepted_i >= len(df) - 1:
    return {"state": "wait_retest", "accepted": False}

  # After accept, look for return to level and reclaim
  last = df.iloc[-1]
  last_ts = _ts(df.index[-1])
  if side == "BUY":
    # Retest: wick or body tagged broken resistance, closed back above
    if float(last["low"]) > high:
      return {"state": "wait_retest", "accepted": True, "accepted_index": accepted_i}
    if float(last["close"]) < high:
      return None  # failed hold
    if float(last["close"]) <= float(last["open"]):
      return None
    return {
      "pattern": "breakout_retest",
      "direction": "BUY",
      "bar_ts": last_ts,
      "level": high,
      "close": float(last["close"]),
      "accepted_break": True,
      "correct_key_level_role": True,
      "retest_of_broken_level": True,
      "directionally_valid_close": True,
      "target_room_beyond_breakout": True,
    }

  if float(last["high"]) < low:
    return {"state": "wait_retest", "accepted": True, "accepted_index": accepted_i}
  if float(last["close"]) > low:
    return None
  if float(last["close"]) >= float(last["open"]):
    return None
  return {
    "pattern": "breakout_retest",
    "direction": "SELL",
    "bar_ts": last_ts,
    "level": low,
    "close": float(last["close"]),
    "accepted_break": True,
    "correct_key_level_role": True,
    "retest_of_broken_level": True,
    "directionally_valid_close": True,
    "target_room_beyond_breakout": True,
  }
