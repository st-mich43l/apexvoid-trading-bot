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
  price_digits: int = 2,
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
        equal_highs.append(round(
          (first.price + second.price) / 2.0,
          max(0, int(price_digits)),
        ))
  for i, first in enumerate(low_swings):
    for second in low_swings[i + 1:]:
      if abs(first.price - second.price) <= equal_tol:
        equal_lows.append(round(
          (first.price + second.price) / 2.0,
          max(0, int(price_digits)),
        ))

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
  pullback_extreme_confirm_bars: int = 2,
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
    # The trigger bar is directional confirmation, not a confirmed structural
    # low. Keep the last confirmation bars out of the level sample and reject
    # when the running minimum is still in that unconfirmed tail.
    confirm_bars = max(1, int(pullback_extreme_confirm_bars))
    confirmed_end = len(window) - confirm_bars
    confirmed = lows_w.iloc[extreme_i:confirmed_end]
    tail = lows_w.iloc[max(extreme_i, confirmed_end):]
    if confirmed.empty or (not tail.empty and float(tail.min()) <= float(confirmed.min())):
      return {
        "rejected": True,
        "reason": "pullback_extreme_unconfirmed",
        "retracement": retracement,
      }
    pullback_extreme = float(confirmed.min())
    impulse = window.iloc[origin_i:extreme_i + 1]
    pullback_bars = window.iloc[extreme_i + 1:]
    impulse_body = (impulse["close"].astype(float) - impulse["open"].astype(float)).abs()
    impulse_range = (impulse["high"].astype(float) - impulse["low"].astype(float)).abs()
    pullback_body = (pullback_bars["close"].astype(float) - pullback_bars["open"].astype(float)).abs()
    mean_impulse_range = float(impulse_range.mean()) if not impulse_range.empty else 0.0
    mean_impulse_body = float(impulse_body.mean()) if not impulse_body.empty else 0.0
    mean_pullback_body = float(pullback_body.mean()) if not pullback_body.empty else 0.0
    return {
      "pattern": "impulse_pullback",
      "direction": "BUY",
      "bar_ts": _ts(window.index[-1]),
      "origin": origin,
      "extreme": extreme,
      "pullback_extreme": pullback_extreme,
      "retracement": retracement,
      "preferred": preferred_low <= retracement <= preferred_high,
      "close": current,
      "origin_index": origin_i,
      "extreme_index": extreme_i,
      "impulse_bars": max(1, extreme_i - origin_i + 1),
      "pullback_bars": len(pullback_bars),
      "impulse_len": impulse_len,
      "body_dominance": (
        mean_impulse_body / mean_impulse_range
        if mean_impulse_range > 0 else 0.0
      ),
      "mean_impulse_body": mean_impulse_body,
      "mean_pullback_body": mean_pullback_body,
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
  confirm_bars = max(1, int(pullback_extreme_confirm_bars))
  confirmed_end = len(window) - confirm_bars
  confirmed = highs_w.iloc[extreme_i:confirmed_end]
  tail = highs_w.iloc[max(extreme_i, confirmed_end):]
  if confirmed.empty or (not tail.empty and float(tail.max()) >= float(confirmed.max())):
    return {
      "rejected": True,
      "reason": "pullback_extreme_unconfirmed",
      "retracement": retracement,
    }
  pullback_extreme = float(confirmed.max())
  impulse = window.iloc[origin_i:extreme_i + 1]
  pullback_bars = window.iloc[extreme_i + 1:]
  impulse_body = (impulse["close"].astype(float) - impulse["open"].astype(float)).abs()
  impulse_range = (impulse["high"].astype(float) - impulse["low"].astype(float)).abs()
  pullback_body = (pullback_bars["close"].astype(float) - pullback_bars["open"].astype(float)).abs()
  mean_impulse_range = float(impulse_range.mean()) if not impulse_range.empty else 0.0
  mean_impulse_body = float(impulse_body.mean()) if not impulse_body.empty else 0.0
  mean_pullback_body = float(pullback_body.mean()) if not pullback_body.empty else 0.0
  return {
    "pattern": "impulse_pullback",
    "direction": "SELL",
    "bar_ts": _ts(window.index[-1]),
    "origin": origin,
    "extreme": extreme,
    "pullback_extreme": pullback_extreme,
    "retracement": retracement,
    "preferred": preferred_low <= retracement <= preferred_high,
    "close": current,
    "origin_index": origin_i,
    "extreme_index": extreme_i,
    "impulse_bars": max(1, origin_i - extreme_i + 1),
    "pullback_bars": len(pullback_bars),
    "impulse_len": impulse_len,
    "body_dominance": (
      mean_impulse_body / mean_impulse_range
      if mean_impulse_range > 0 else 0.0
    ),
    "mean_impulse_body": mean_impulse_body,
    "mean_pullback_body": mean_pullback_body,
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
  requirement -- this is only ever used as a veto against fading one).
  """
  if df is None or len(df) < lookback_bars or atr <= 0:
    return None
  window = df.tail(lookback_bars)
  displacement = float(window["close"].iloc[-1] - window["open"].iloc[0])
  if abs(displacement) / atr < min_displacement_atr:
    return None
  return "BUY" if displacement > 0 else "SELL"


def find_compression_box(
  df: pd.DataFrame,
  *,
  atr: float,
  min_box_bars: int = 8,
  max_box_bars: int = 20,
  box_max_atr: float = 1.5,
  min_touches_per_side: int = 2,
  touch_tol_atr: float = 0.20,
) -> dict[str, Any] | None:
  """Locate a recent M1 compression window (tight range + multi-touch).

  Used only by Breakout Retest — Range Sweep keeps ``active_range_*``.
  Prefers the most recent valid window that still leaves at least one bar
  after the box for a break/retest episode.
  """
  if df is None or atr is None or float(atr) <= 0:
    return None
  atr_f = float(atr)
  min_bars = max(3, int(min_box_bars))
  max_bars = max(min_bars, int(max_box_bars))
  # Need room after the box for break (+ ideally retest).
  if len(df) < min_bars + 2:
    return None
  tol = max(0.0, float(touch_tol_atr)) * atr_f
  max_width = max(0.0, float(box_max_atr)) * atr_f
  min_touches = max(1, int(min_touches_per_side))

  # end = last inclusive index of the box; leave >=1 bar after for break.
  for end in range(len(df) - 2, min_bars - 2, -1):
    for width in range(min_bars, min(max_bars, end + 1) + 1):
      start = end - width + 1
      if start < 0:
        continue
      window = df.iloc[start : end + 1]
      box_high = float(window["high"].max())
      box_low = float(window["low"].min())
      span = box_high - box_low
      if span <= 0 or span > max_width:
        continue
      hi_touches = 0
      lo_touches = 0
      for i in range(len(window)):
        bar = window.iloc[i]
        if float(bar["high"]) >= box_high - tol:
          hi_touches += 1
        if float(bar["low"]) <= box_low + tol:
          lo_touches += 1
      if hi_touches < min_touches or lo_touches < min_touches:
        continue
      return {
        "box_low": box_low,
        "box_high": box_high,
        "box_bars": int(width),
        "box_start_index": int(start),
        "box_end_index": int(end),
        "compression_atr": span / atr_f,
        "touch_count": int(hi_touches + lo_touches),
        "high_touches": int(hi_touches),
        "low_touches": int(lo_touches),
      }
  return None


def _breakout_rejection(bar: Any, *, side: str, level: float) -> bool:
  """Wick/touch through the broken level then close back in trade direction."""
  if side == "BUY":
    return float(bar["low"]) <= level and float(bar["close"]) > level
  return float(bar["high"]) >= level and float(bar["close"]) < level


def _breakout_touch(bar: Any, *, side: str, level: float) -> bool:
  if side == "BUY":
    return float(bar["low"]) <= level
  return float(bar["high"]) >= level


def detect_breakout_retest(
  df: pd.DataFrame,
  *,
  direction: str,
  box_high: float,
  box_low: float,
  min_displacement: float,
  retest_lookback_bars: int = 1,
  break_lookback_bars: int | None = None,
  require_retest_rejection: bool = True,
) -> dict[str, Any] | None:
  """Compression-level break → acceptance → rejection retest → hold.

  States: ``no_box`` | ``wait_break`` | ``wait_retest`` | ``failed_break`` |
  ``armed`` (pattern payload). See docs/scalping/OWN_BREAKOUT_TECHNIQUE.md.

  Retest lookback (2026-08-23) and break lookback (2026-08-25) lessons kept:
  touch/rejection scan is not newest-bar-only; break window defaults wide
  enough for break → pullback → hold on M1.
  """
  if df is None or len(df) < 5:
    return {"state": "no_box", "accepted": False, "reason": "insufficient_bars"}
  side = str(direction).upper()
  high = float(box_high)
  low = float(box_low)
  if high <= low:
    return {"state": "no_box", "accepted": False, "reason": "invalid_box"}

  retest_lb = max(1, int(retest_lookback_bars or 1))
  break_lb = (
    max(3, int(break_lookback_bars))
    if break_lookback_bars is not None
    else max(8, retest_lb + 4)
  )
  break_lb = min(break_lb, max(3, len(df) - 1))
  level = high if side == "BUY" else low
  min_disp = max(0.0, float(min_displacement))

  accepted_i = None
  for i in range(len(df) - break_lb, len(df)):
    if i < 1:
      continue
    bar = df.iloc[i]
    close = float(bar["close"])
    open_ = float(bar["open"])
    if side == "BUY":
      if close > high and (close - high) >= min_disp and close > open_:
        accepted_i = i
    else:
      if close < low and (low - close) >= min_disp and close < open_:
        accepted_i = i

  if accepted_i is None:
    return {
      "state": "wait_break",
      "accepted": False,
      "level": level,
      "box_high": high,
      "box_low": low,
    }

  # Failed break: any close after the break through the opposite box side.
  for i in range(accepted_i + 1, len(df)):
    close = float(df.iloc[i]["close"])
    if side == "BUY" and close < low:
      return {
        "state": "failed_break",
        "accepted": True,
        "accepted_index": accepted_i,
        "level": level,
        "reason": "opposite_side_close",
      }
    if side == "SELL" and close > high:
      return {
        "state": "failed_break",
        "accepted": True,
        "accepted_index": accepted_i,
        "level": level,
        "reason": "opposite_side_close",
      }

  # Acceptance: at least one post-break bar that does not fully reclaim
  # into the box (close remains beyond the broken boundary).
  accepted_hold = False
  for i in range(accepted_i + 1, len(df)):
    close = float(df.iloc[i]["close"])
    if side == "BUY" and close >= high:
      accepted_hold = True
      break
    if side == "SELL" and close <= low:
      accepted_hold = True
      break
  if not accepted_hold and accepted_i >= len(df) - 1:
    return {
      "state": "wait_retest",
      "accepted": True,
      "accepted_index": accepted_i,
      "level": level,
      "reason": "awaiting_acceptance",
    }
  if not accepted_hold:
    # Post-break bars exist but all closed back through the broken level.
    last_close = float(df.iloc[-1]["close"])
    if (side == "BUY" and last_close < high) or (side == "SELL" and last_close > low):
      return {
        "state": "failed_break",
        "accepted": True,
        "accepted_index": accepted_i,
        "level": level,
        "reason": "reclaimed_into_box",
      }
    return {
      "state": "wait_retest",
      "accepted": True,
      "accepted_index": accepted_i,
      "level": level,
    }

  bars_since_break = len(df) - accepted_i - 1
  window = max(1, min(retest_lb, bars_since_break))
  retest_i = None
  for offset in range(1, window + 1):
    idx = len(df) - offset
    if idx <= accepted_i:
      continue
    bar = df.iloc[idx]
    if require_retest_rejection:
      if _breakout_rejection(bar, side=side, level=level):
        retest_i = idx
        break
    elif _breakout_touch(bar, side=side, level=level):
      retest_i = idx
      break

  if retest_i is None:
    return {
      "state": "wait_retest",
      "accepted": True,
      "accepted_index": accepted_i,
      "level": level,
      "reason": (
        "awaiting_rejection_retest"
        if require_retest_rejection
        else "awaiting_retest_touch"
      ),
    }

  last = df.iloc[-1]
  last_close = float(last["close"])
  last_ts = _ts(df.index[-1])
  if side == "BUY":
    if last_close < high:
      return {
        "state": "failed_break",
        "accepted": True,
        "accepted_index": accepted_i,
        "level": level,
        "reason": "failed_hold",
      }
  else:
    if last_close > low:
      return {
        "state": "failed_break",
        "accepted": True,
        "accepted_index": accepted_i,
        "level": level,
        "reason": "failed_hold",
      }

  break_bar = df.iloc[accepted_i]
  break_disp = (
    float(break_bar["close"]) - high
    if side == "BUY"
    else low - float(break_bar["close"])
  )
  return {
    "state": "armed",
    "pattern": "breakout_retest",
    "direction": side,
    "bar_ts": last_ts,
    "level": level,
    "close": last_close,
    "accepted": True,
    "accepted_index": accepted_i,
    "retest_index": retest_i,
    "accepted_break": True,
    "correct_key_level_role": True,
    "retest_of_broken_level": True,
    "retest_rejection": bool(require_retest_rejection),
    "directionally_valid_close": True,
    "target_room_beyond_breakout": True,
    "break_displacement": break_disp,
    "box_high": high,
    "box_low": low,
  }
