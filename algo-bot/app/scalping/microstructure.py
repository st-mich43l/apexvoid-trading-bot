"""Closed-bar M1 microstructure for scalping."""

from __future__ import annotations

import statistics
from typing import Any, Sequence

import pandas as pd

from app.scalping.models import (
  BR_ACCEPTED,
  BR_ARMED,
  BR_BREAK_DETECTED,
  BR_CONFIRMATION,
  BR_EXPIRED,
  BR_FAILED_BREAK,
  BR_INVALID_RETEST,
  BR_NO_CONTINUATION,
  BR_REASON_BREAK_NOT_AFTER_SOURCE,
  BR_REASON_FAILED_ACCEPTANCE,
  BR_REASON_IMMEDIATE_RECLAIM,
  BR_REASON_NO_CANDIDATE_LEVEL,
  BR_REASON_NO_CONFIRMATION,
  BR_REASON_NO_ROLE_FLIP,
  BR_REASON_OPPOSITE_STRUCTURE_BREAK,
  BR_REASON_RETEST_TOO_DEEP,
  BR_REASON_RETEST_TOO_LATE,
  BR_RETESTED,
  BR_SOURCE_COMPRESSION_BOX,
  BR_SOURCE_EQH,
  BR_SOURCE_EQL,
  BR_SOURCE_M1_SWING_HIGH,
  BR_SOURCE_M1_SWING_LOW,
  BR_SOURCE_M5_SWING_HIGH,
  BR_SOURCE_M5_SWING_LOW,
  BR_SUBTYPE_LIQUIDITY_LEVEL_BREAK,
  BR_SUBTYPE_RANGE_BREAK,
  BR_SUBTYPE_STRUCTURE_FLIP,
  BR_WAIT_RETEST,
  BR_WATCH_LEVEL,
  BreakoutLevelCandidate,
  MicroStructure,
  MicroSwing,
)


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
  box_end_index: int | None = None,
) -> dict[str, Any] | None:
  """Compression-level break → acceptance → rejection retest → hold.

  States: ``no_box`` | ``wait_break`` | ``wait_retest`` | ``failed_break`` |
  ``armed`` (pattern payload).

  Retest lookback (2026-08-23) and break lookback (2026-08-25) lessons kept:
  touch/rejection scan is not newest-bar-only; break window defaults wide
  enough for break → pullback → hold on M1.

  Owner-directed 2026-09-16 rebuild - two correctness fixes, both
  backward-compatible for every existing caller:

  1. The break-acceptance scan below now stops at the FIRST bar that
     qualifies (``break``) instead of letting every later independently-
     qualifying bar keep overwriting ``accepted_i``. A later continuation
     candle must never silently replace the true breakout candle.
  2. ``box_end_index`` (optional, default ``None``) lets a caller bind the
     scan to a specific compression episode: bars at or before it can never
     be treated as the break, since a breakout cannot predate the box it
     broke out of. Omitted, this behaves exactly as before (unbounded scan
     over the trailing ``break_lookback_bars`` window) - see the generic
     multi-source engine below (``evaluate_breakout_retest_episode``) for
     the fully generalized, tolerant-retest replacement used by live
     discovery; this function remains the compression-only legacy path
     still read directly by ``diagnose_breakout_reject`` telemetry and by
     the tests in tests/scalping/test_scalp_core.py.
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
    if box_end_index is not None and i <= box_end_index:
      continue
    bar = df.iloc[i]
    close = float(bar["close"])
    open_ = float(bar["open"])
    if side == "BUY":
      if close > high and (close - high) >= min_disp and close > open_:
        accepted_i = i
        break
    else:
      if close < low and (low - close) >= min_disp and close < open_:
        accepted_i = i
        break

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


# ============================================================================
# Breakout Retest V2 - generic BREAKOUT -> ACCEPTANCE -> RETEST -> ROLE FLIP
# -> CONTINUATION engine. Owner-directed 2026-09-16 rebuild.
#
# The functions above (find_compression_box, detect_breakout_retest) are
# untouched apart from the two surgical bug fixes already applied, and
# remain the compression-only legacy path used by diagnose_breakout_reject
# telemetry. Everything below is new, additive, single-level/single-side
# machinery: a BreakoutLevelCandidate (compression box edge, M1/M5 swing, or
# EQH/EQL) is evaluated by evaluate_breakout_retest_episode, which composes
# the small pure helpers below rather than being one large function.
# ============================================================================


def robust_mad_volatility(
  values: Sequence[float],
  *,
  min_floor: float = 0.0,
) -> float:
  """Median absolute deviation, scaled to be std-dev comparable (x1.4826).

  Robust to a handful of outlier bars in a way a mean-based measure is not.
  Floors at ``min_floor`` so flat/degenerate input never yields 0 (which
  would blow up any ``distance / mad`` caller) or NaN/inf.
  """
  floor = max(0.0, float(min_floor))
  cleaned = [float(value) for value in values if value == value]  # drop NaN
  if not cleaned:
    return floor
  median = statistics.median(cleaned)
  mad = statistics.median(abs(value - median) for value in cleaned)
  robust = 1.4826 * mad
  return robust if robust > floor else floor


def m1_mad_volatility(
  df: pd.DataFrame,
  *,
  lookback: int = 14,
  min_floor: float = 0.0,
) -> float:
  """Robust MAD volatility from the last ``lookback`` M1 bar ranges."""
  if df is None or df.empty:
    return max(0.0, float(min_floor))
  ranges = (df["high"].astype(float) - df["low"].astype(float)).tail(max(1, lookback))
  return robust_mad_volatility(ranges.tolist(), min_floor=min_floor)


def is_true_level_cross(
  *,
  side: str,
  prev_close: float,
  close: float,
  level: float,
  cross_tolerance: float = 0.0,
  breakout_margin: float = 0.0,
) -> bool:
  """A real crossing of ``level``, not "already far beyond it".

  BUY:  prev_close <= level + cross_tolerance AND close > level + breakout_margin
  SELL: prev_close >= level - cross_tolerance AND close < level - breakout_margin
  """
  side = side.upper()
  tol = max(0.0, float(cross_tolerance))
  margin = max(0.0, float(breakout_margin))
  if side == "BUY":
    return prev_close <= level + tol and close > level + margin
  return prev_close >= level - tol and close < level - margin


def candidate_from_compression_box(
  box: dict[str, Any],
  *,
  side: str,
  timeframe: str = "M1",
) -> BreakoutLevelCandidate:
  """Wrap a find_compression_box() result as a BreakoutLevelCandidate.

  Binds ``source_index`` to ``box_end_index`` so the shared engine can
  never select a break at or before the box - a breakout cannot predate the
  compression episode it broke out of (rebuild Problem B).
  """
  side = side.upper()
  box_low = float(box["box_low"])
  box_high = float(box["box_high"])
  level = box_high if side == "BUY" else box_low
  invalidation_level = box_low if side == "BUY" else box_high
  compression_atr = box.get("compression_atr")
  return BreakoutLevelCandidate(
    level=level,
    side=side,
    source=BR_SOURCE_COMPRESSION_BOX,
    subtype=BR_SUBTYPE_RANGE_BREAK,
    timeframe=timeframe,
    source_index=int(box["box_end_index"]),
    source_time=None,
    strength=float(compression_atr) if compression_atr is not None else None,
    metadata={
      "box_start_index": int(box["box_start_index"]),
      "box_end_index": int(box["box_end_index"]),
      "box_low": box_low,
      "box_high": box_high,
      "invalidation_level": invalidation_level,
      "touch_count": box.get("touch_count"),
    },
  )


def structure_flip_candidates(
  micro: MicroStructure,
  *,
  side: str,
  current_index: int,
  timeframe: str = "M1",
  min_age_bars: int = 3,
  max_age_bars: int = 240,
  atr: float = 0.0,
  min_level_spacing_atr: float = 0.3,
) -> list[BreakoutLevelCandidate]:
  """M1 structural swing-high/low candidates from an already-built MicroStructure.

  Reuses ``micro.swings`` (built once upstream by build_micro_structure) -
  no new swing computation. Filters by age (a swing too young hasn't had
  time to matter; one too old is stale) and drops swings sitting within
  ``min_level_spacing_atr`` of a more recent same-side swing already kept,
  so one cluster of nearby pivots doesn't produce redundant candidates.
  """
  side = side.upper()
  kind = "high" if side == "BUY" else "low"
  source = BR_SOURCE_M1_SWING_HIGH if side == "BUY" else BR_SOURCE_M1_SWING_LOW
  spacing = max(0.0, min_level_spacing_atr) * max(0.0, atr)
  swings = sorted(
    (s for s in micro.swings if s.kind == kind),
    key=lambda s: s.index,
    reverse=True,
  )
  kept: list[MicroSwing] = []
  for swing in swings:
    age = current_index - swing.index
    if age < max(0, min_age_bars) or age > max(0, max_age_bars):
      continue
    if spacing > 0 and any(abs(swing.price - k.price) < spacing for k in kept):
      continue
    kept.append(swing)
  return [
    BreakoutLevelCandidate(
      level=swing.price,
      side=side,
      source=source,
      subtype=BR_SUBTYPE_STRUCTURE_FLIP,
      timeframe=timeframe,
      source_index=swing.index,
      source_time=swing.bar_ts,
      strength=None,
      metadata={"swing_kind": swing.kind},
    )
    for swing in kept
  ]


def m5_structure_flip_candidates(
  key_levels: Sequence[dict[str, Any]],
  *,
  side: str,
  current_price: float,
  min_touches: int = 2,
) -> list[BreakoutLevelCandidate]:
  """M5 structural candidates from an already-computed ScalpContextSnapshot.key_levels.

  Reuses the M5 key-level clustering already done upstream by
  scalp_structure()/key_levels() - no new M5 swing computation here. Only
  levels on the correct side of the current price are directionally
  breakable (a level already behind price cannot be "broken").
  """
  side = side.upper()
  source = BR_SOURCE_M5_SWING_HIGH if side == "BUY" else BR_SOURCE_M5_SWING_LOW
  out: list[BreakoutLevelCandidate] = []
  for entry in key_levels:
    try:
      price = float(entry.get("price"))
    except (TypeError, ValueError, AttributeError):
      continue
    touches = int(entry.get("touches") or 0)
    if touches < max(1, min_touches):
      continue
    if side == "BUY" and price <= current_price:
      continue
    if side == "SELL" and price >= current_price:
      continue
    score = entry.get("score")
    out.append(BreakoutLevelCandidate(
      level=price,
      side=side,
      source=source,
      subtype=BR_SUBTYPE_STRUCTURE_FLIP,
      timeframe="M5",
      source_index=None,
      source_time=None,
      strength=float(score) if score is not None else None,
      metadata={"touches": touches},
    ))
  return out


def liquidity_level_candidates(
  micro: MicroStructure,
  *,
  side: str,
  timeframe: str = "M1",
) -> list[BreakoutLevelCandidate]:
  """EQH/EQL candidates from an already-built MicroStructure.

  Reuses ``micro.equal_highs``/``micro.equal_lows`` - no new EQH/EQL
  computation. Session high/low liquidity levels are intentionally not
  wired here yet: they need a multi-hour/session-spanning frame that
  discover_breakout_retest's M1-only window doesn't carry today (see PR
  description) - this stays a real gap, not silently faked.
  """
  side = side.upper()
  if side == "BUY":
    prices, source = micro.equal_highs, BR_SOURCE_EQH
  else:
    prices, source = micro.equal_lows, BR_SOURCE_EQL
  return [
    BreakoutLevelCandidate(
      level=float(price),
      side=side,
      source=source,
      subtype=BR_SUBTYPE_LIQUIDITY_LEVEL_BREAK,
      timeframe=timeframe,
      source_index=None,
      source_time=None,
      strength=None,
      metadata={},
    )
    for price in prices
  ]


def evaluate_breakout_acceptance(
  df: pd.DataFrame,
  *,
  side: str,
  level: float,
  break_index: int,
  acceptance_bars: int = 1,
  acceptance_required_closes: int = 1,
  invalidation_level: float | None = None,
  atr: float = 0.0,
  mad: float = 0.0,
) -> dict[str, Any]:
  """Evidence price is actually being accepted beyond ``level``.

  Within the ``acceptance_bars`` bars immediately after ``break_index``, at
  least ``acceptance_required_closes`` closes must remain beyond level. A
  close on the very first post-break bar that fails to hold beyond level
  fails acceptance immediately (immediate_reclaim) rather than waiting out
  the rest of the window. If ``invalidation_level`` is given (the box's far
  edge for a compression candidate), any close through it anywhere in the
  window is a stronger, immediate opposite_structure_break failure.
  """
  side = side.upper()
  n = len(df)
  window_len = max(1, int(acceptance_bars))
  window = list(range(break_index + 1, min(n, break_index + 1 + window_len)))
  bars_elapsed = len(window)
  closes_beyond = 0
  immediate_reclaim = False
  last_close: float | None = None
  for offset, i in enumerate(window):
    close = float(df.iloc[i]["close"])
    last_close = close
    if invalidation_level is not None:
      breached = close < invalidation_level if side == "BUY" else close > invalidation_level
      if breached:
        return {
          "accepted": False,
          "pending": False,
          "acceptance_bars_elapsed": offset + 1,
          "acceptance_distance_atr": None,
          "acceptance_distance_mad": None,
          "failure_reason": BR_REASON_OPPOSITE_STRUCTURE_BREAK,
        }
    beyond = close > level if side == "BUY" else close < level
    if beyond:
      closes_beyond += 1
    elif offset == 0:
      immediate_reclaim = True
  required = max(1, int(acceptance_required_closes))
  if closes_beyond >= required:
    distance = (last_close - level) if side == "BUY" else (level - last_close)
    return {
      "accepted": True,
      "pending": False,
      "acceptance_bars_elapsed": bars_elapsed,
      "acceptance_distance_atr": (distance / atr) if atr > 0 else None,
      "acceptance_distance_mad": (distance / mad) if mad > 0 else None,
      "failure_reason": None,
    }
  if immediate_reclaim:
    return {
      "accepted": False,
      "pending": False,
      "acceptance_bars_elapsed": bars_elapsed,
      "acceptance_distance_atr": None,
      "acceptance_distance_mad": None,
      "failure_reason": BR_REASON_IMMEDIATE_RECLAIM,
    }
  if bars_elapsed < window_len:
    return {
      "accepted": False,
      "pending": True,
      "acceptance_bars_elapsed": bars_elapsed,
      "acceptance_distance_atr": None,
      "acceptance_distance_mad": None,
      "failure_reason": None,
    }
  return {
    "accepted": False,
    "pending": False,
    "acceptance_bars_elapsed": bars_elapsed,
    "acceptance_distance_atr": None,
    "acceptance_distance_mad": None,
    "failure_reason": BR_REASON_FAILED_ACCEPTANCE,
  }


def evaluate_retest(
  df: pd.DataFrame,
  *,
  side: str,
  level: float,
  break_index: int,
  min_retest_delay_bars: int = 0,
  max_retest_delay_bars: int = 20,
  retest_front_run_tolerance: float = 0.0,
  max_retest_penetration: float = 0.0,
  atr: float = 0.0,
  mad: float = 0.0,
) -> dict[str, Any]:
  """Price returns toward ``level`` within a bounded, tolerant band.

  Unlike an exact-touch/reject test, a bar counts as interacting with the
  level if its wick reaches to within ``retest_front_run_tolerance`` of it
  (so a retest that front-runs and never quite reaches the level still
  qualifies) or penetrates through it. Penetration beyond
  ``max_retest_penetration`` invalidates the retest outright
  (retest_too_deep) rather than silently degrading it. No interaction
  found by ``max_retest_delay_bars`` after the break is retest_too_late;
  still within that window with no bars left to check yet is "pending".
  """
  side = side.upper()
  n = len(df)
  tolerance = max(0.0, float(retest_front_run_tolerance))
  max_pen = max(0.0, float(max_retest_penetration))
  earliest = break_index + 1 + max(0, int(min_retest_delay_bars))
  latest = break_index + max(1, int(max_retest_delay_bars))
  for i in range(max(earliest, break_index + 1), min(n, latest + 1)):
    bar = df.iloc[i]
    high = float(bar["high"])
    low = float(bar["low"])
    if side == "BUY":
      touched = low <= level + tolerance
      penetration = max(0.0, level - low)
      distance = abs(low - level)
    else:
      touched = high >= level - tolerance
      penetration = max(0.0, high - level)
      distance = abs(high - level)
    if not touched:
      continue
    if penetration > max_pen:
      return {
        "retest_index": None,
        "bars_until_retest": None,
        "retest_distance": None,
        "retest_penetration": penetration,
        "retest_penetration_atr": (penetration / atr) if atr > 0 else None,
        "retest_penetration_mad": (penetration / mad) if mad > 0 else None,
        "failure_reason": BR_REASON_RETEST_TOO_DEEP,
      }
    return {
      "retest_index": i,
      "bars_until_retest": i - break_index,
      "retest_distance": distance,
      "retest_penetration": penetration,
      "retest_penetration_atr": (penetration / atr) if atr > 0 else None,
      "retest_penetration_mad": (penetration / mad) if mad > 0 else None,
      "failure_reason": None,
    }
  pending = (n - 1) < latest
  return {
    "retest_index": None,
    "bars_until_retest": None,
    "retest_distance": None,
    "retest_penetration": None,
    "retest_penetration_atr": None,
    "retest_penetration_mad": None,
    "failure_reason": None if pending else BR_REASON_RETEST_TOO_LATE,
  }


def evaluate_retest_confirmation(
  df: pd.DataFrame,
  *,
  side: str,
  level: float,
  retest_index: int,
  confirmation_mode: str = "reclaim_close",
) -> dict[str, Any]:
  """A retest is not automatically an entry - require a confirming signal.

  Mandatory (never optional, regardless of ``confirmation_mode``): the
  retest bar's own close must already sit back beyond ``level`` (the role
  flip itself - old resistance holding as new support, or vice versa). A
  wick that merely touches the band without the bar's close reclaiming the
  level is a weak/no retest, not a confirmed one.

  ``confirmation_mode="retest_high_break"`` additionally requires the next
  bar to break the retest bar's own high (BUY) / low (SELL) - a stricter,
  opt-in quality bar, not layered onto every mode at once.
  """
  side = side.upper()
  n = len(df)
  retest_bar = df.iloc[retest_index]
  retest_close = float(retest_bar["close"])
  role_flip = retest_close > level if side == "BUY" else retest_close < level
  if not role_flip:
    return {
      "confirmed": False,
      "pending": False,
      "confirmation_type": None,
      "confirmation_index": None,
      "failure_reason": BR_REASON_NO_ROLE_FLIP,
    }
  if confirmation_mode != "retest_high_break":
    return {
      "confirmed": True,
      "pending": False,
      "confirmation_type": "retest_close_reclaim",
      "confirmation_index": retest_index,
      "failure_reason": None,
    }
  if retest_index + 1 >= n:
    return {
      "confirmed": False,
      "pending": True,
      "confirmation_type": None,
      "confirmation_index": None,
      "failure_reason": None,
    }
  confirm_bar = df.iloc[retest_index + 1]
  if side == "BUY" and float(confirm_bar["high"]) > float(retest_bar["high"]):
    return {
      "confirmed": True,
      "pending": False,
      "confirmation_type": "retest_high_break",
      "confirmation_index": retest_index + 1,
      "failure_reason": None,
    }
  if side == "SELL" and float(confirm_bar["low"]) < float(retest_bar["low"]):
    return {
      "confirmed": True,
      "pending": False,
      "confirmation_type": "retest_low_break",
      "confirmation_index": retest_index + 1,
      "failure_reason": None,
    }
  return {
    "confirmed": False,
    "pending": False,
    "confirmation_type": None,
    "confirmation_index": None,
    "failure_reason": BR_REASON_NO_CONFIRMATION,
  }


def evaluate_breakout_retest_episode(
  df: pd.DataFrame,
  candidate: BreakoutLevelCandidate,
  *,
  atr: float,
  mad: float = 0.0,
  cross_tolerance: float = 0.0,
  breakout_margin: float = 0.0,
  max_break_delay_bars: int | None = None,
  acceptance_bars: int = 1,
  acceptance_required_closes: int = 1,
  min_retest_delay_bars: int = 0,
  max_retest_delay_bars: int = 20,
  retest_front_run_tolerance: float = 0.0,
  max_retest_penetration: float = 0.0,
  confirmation_mode: str = "reclaim_close",
) -> dict[str, Any]:
  """Run one BreakoutLevelCandidate through the full V2 state machine.

  WATCH_LEVEL -> BREAK_DETECTED -> ACCEPTED -> WAIT_RETEST -> RETESTED ->
  CONFIRMATION -> ARMED, with FAILED_BREAK/EXPIRED/INVALID_RETEST/
  INVALIDATED/NO_CONTINUATION failure states. Pure function - no lookahead
  beyond ``df``'s own rows (every index used is < len(df)), no side
  effects. Composes the small helpers above rather than doing everything
  itself. Returns a telemetry dict shaped for
  ScalpOpportunity.measured["v2"] (rebuild spec section 11); the caller
  (discover_breakout_retest in strategies.py) is responsible for quality
  scoring and turning an ARMED result into a ScalpOpportunity.
  """
  side = candidate.side.upper()
  level = float(candidate.level)
  n = len(df)
  result: dict[str, Any] = {
    "strategy": "breakout_retest",
    "version": "v2",
    "subtype": candidate.subtype,
    "direction": side,
    "break_source": candidate.source,
    "break_source_timeframe": candidate.timeframe,
    "broken_level": level,
    "state": BR_WATCH_LEVEL,
    "failure_reason": None,
  }
  if candidate.subtype == BR_SUBTYPE_RANGE_BREAK:
    result["box_start_index"] = candidate.metadata.get("box_start_index")
    result["box_end_index"] = candidate.metadata.get("box_end_index")

  if n < 2:
    result["failure_reason"] = BR_REASON_NO_CANDIDATE_LEVEL
    return result

  # A breakout cannot predate the level it broke - never scan at or before
  # the candidate's own source bar (rebuild Problem B, generalized to every
  # source, not just compression).
  earliest_break_index = (
    max(1, int(candidate.source_index) + 1)
    if candidate.source_index is not None
    else 1
  )

  break_index: int | None = None
  for i in range(earliest_break_index, n):
    prev_close = float(df.iloc[i - 1]["close"])
    close = float(df.iloc[i]["close"])
    if is_true_level_cross(
      side=side, prev_close=prev_close, close=close, level=level,
      cross_tolerance=cross_tolerance, breakout_margin=breakout_margin,
    ):
      break_index = i
      break  # first true crossing wins - immutable once selected.

  if break_index is None:
    return result

  if (
    max_break_delay_bars is not None
    and candidate.source_index is not None
    and break_index > int(candidate.source_index) + int(max_break_delay_bars)
  ):
    result["state"] = BR_EXPIRED
    result["failure_reason"] = BR_REASON_BREAK_NOT_AFTER_SOURCE
    return result

  break_bar = df.iloc[break_index]
  break_close = float(break_bar["close"])
  break_distance = (break_close - level) if side == "BUY" else (level - break_close)
  body = abs(break_close - float(break_bar["open"]))
  bar_range = max(1e-9, float(break_bar["high"]) - float(break_bar["low"]))
  result.update({
    "state": BR_BREAK_DETECTED,
    "break_index": break_index,
    "break_time": _ts(df.index[break_index]),
    "break_distance": break_distance,
    "break_distance_atr": (break_distance / atr) if atr > 0 else None,
    "break_distance_mad": (break_distance / mad) if mad > 0 else None,
    "break_body_ratio": body / bar_range,
  })

  acceptance = evaluate_breakout_acceptance(
    df, side=side, level=level, break_index=break_index,
    acceptance_bars=acceptance_bars,
    acceptance_required_closes=acceptance_required_closes,
    invalidation_level=candidate.metadata.get("invalidation_level"),
    atr=atr, mad=mad,
  )
  result["accepted"] = acceptance["accepted"]
  result["acceptance_bars"] = acceptance["acceptance_bars_elapsed"]
  result["acceptance_distance_atr"] = acceptance["acceptance_distance_atr"]
  result["acceptance_distance_mad"] = acceptance["acceptance_distance_mad"]
  if not acceptance["accepted"]:
    if acceptance["pending"]:
      return result  # stays BREAK_DETECTED
    result["state"] = BR_FAILED_BREAK
    result["failure_reason"] = acceptance["failure_reason"]
    return result
  result["state"] = BR_ACCEPTED

  retest = evaluate_retest(
    df, side=side, level=level, break_index=break_index,
    min_retest_delay_bars=min_retest_delay_bars,
    max_retest_delay_bars=max_retest_delay_bars,
    retest_front_run_tolerance=retest_front_run_tolerance,
    max_retest_penetration=max_retest_penetration,
    atr=atr, mad=mad,
  )
  result.update({
    "retest_index": retest["retest_index"],
    "bars_until_retest": retest["bars_until_retest"],
    "retest_distance": retest["retest_distance"],
    "retest_penetration": retest["retest_penetration"],
    "retest_penetration_atr": retest["retest_penetration_atr"],
    "retest_penetration_mad": retest["retest_penetration_mad"],
  })
  if retest["retest_index"] is None:
    if retest["failure_reason"] is None:
      result["state"] = BR_WAIT_RETEST
      return result
    result["state"] = (
      BR_INVALID_RETEST
      if retest["failure_reason"] == BR_REASON_RETEST_TOO_DEEP
      else BR_EXPIRED
    )
    result["failure_reason"] = retest["failure_reason"]
    return result
  result["state"] = BR_RETESTED
  result["retest_time"] = _ts(df.index[retest["retest_index"]])

  confirmation = evaluate_retest_confirmation(
    df, side=side, level=level, retest_index=retest["retest_index"],
    confirmation_mode=confirmation_mode,
  )
  result["confirmation_type"] = confirmation["confirmation_type"]
  result["confirmation_index"] = confirmation["confirmation_index"]
  if not confirmation["confirmed"]:
    if confirmation["pending"]:
      return result  # stays RETESTED
    result["state"] = BR_NO_CONTINUATION
    result["failure_reason"] = confirmation["failure_reason"]
    return result
  result["state"] = BR_CONFIRMATION

  last_close = float(df.iloc[-1]["close"])
  continuation_distance = (last_close - level) if side == "BUY" else (level - last_close)
  result["continuation_distance"] = continuation_distance
  result["continuation_distance_atr"] = (
    continuation_distance / atr if atr > 0 else None
  )
  result["continuation_distance_mad"] = (
    continuation_distance / mad if mad > 0 else None
  )
  result["state"] = BR_ARMED
  result["accepted_break"] = True
  result["retest_of_broken_level"] = True
  result["directionally_valid_close"] = (
    last_close > level if side == "BUY" else last_close < level
  )
  return result
