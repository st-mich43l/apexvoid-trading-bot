"""Closed-bar PA context required by the cTrader scale-in executor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

import pandas as pd

from app.analysis.math_utils import atr_series
from app.analysis.structure import structure_breaks
from app.analysis.swings import find_swings
from app.analysis.zones import displacement
from app.autotrade.map_strategy import _rejects


@dataclass(frozen=True)
class AutoScaleContext:
  bar_ts: int
  atr: float
  structure_swing: float
  displacement_direction: str | None
  displacement_age_bars: int | None
  bos_direction: str | None
  bos_ts: int | None
  opposing_level_distance_atr: float | None
  # Pullback scale-in add (ScaleInTriggerPlanner P1-P4 in ctrader-engine) -
  # counter_bos_ts/extreme_price/extreme_ts follow the same "position-
  # agnostic market observation, gated by the caller's own GroupOpenedAt"
  # pattern bos_ts already uses (AutoTradeEngine.ValidateAddTriggers does
  # the gating): this module has no notion of which group is open.
  counter_bos_ts: int | None = None
  extreme_price: float | None = None
  extreme_ts: int | None = None
  rejection_confirmed: bool = False


def build_auto_scale_context(
  frames: dict[str, pd.DataFrame],
  direction: str,
  *,
  spot_price: float,
  cfg: Any,
  target_low: float | None = None,
  target_high: float | None = None,
) -> AutoScaleContext | None:
  direction = str(direction or "").upper()
  frame = frames.get("M1")
  if direction not in {"BUY", "SELL"} or frame is None or frame.empty:
    return None
  required = ["open", "high", "low", "close"]
  if any(column not in frame.columns for column in required):
    return None
  m1 = frame.copy()
  for column in required:
    m1[column] = pd.to_numeric(m1[column], errors="coerce")
  m1 = m1.dropna(subset=required)
  if len(m1) < 8:
    return None
  atr = atr_series(m1, max(2, int(getattr(cfg, "atr_length", 14))))
  atr_value = float(atr.iloc[-1])
  if not math.isfinite(atr_value) or atr_value <= 0:
    return None
  swings = find_swings(
    m1,
    max(1, int(getattr(cfg, "swing_fractal_n", 2))),
    max(0.0, float(getattr(cfg, "zigzag_pct", 0.0))),
    max(0.0, float(getattr(cfg, "zigzag_atr_mult", 1.0))),
    atr,
  )
  swing_kind = "low" if direction == "BUY" else "high"
  matching_swings = [item for item in swings if item.kind == swing_kind]
  if not matching_swings:
    return None
  structure_swing = float(matching_swings[-1].price)

  legs = displacement(
    m1,
    atr,
    max(0.1, float(getattr(cfg, "displacement_atr_mult", 1.5))),
    max(0.0, float(getattr(cfg, "momentum_body_frac", 0.6))),
  )
  pa_direction = "up" if direction == "BUY" else "down"
  counter_direction = "down" if pa_direction == "up" else "up"
  matching_legs = [item for item in legs if item.direction == pa_direction]
  latest_leg = matching_legs[-1] if matching_legs else None
  displacement_age = (
    len(m1) - 1 - int(latest_leg.start)
    if latest_leg is not None else None
  )

  breaks = structure_breaks(swings, m1)
  matching_breaks = [
    item for item in breaks
    if item.direction == pa_direction and item.kind == "BOS"
  ]
  latest_break = matching_breaks[-1] if matching_breaks else None
  bos_ts = _epoch(latest_break.ts) if latest_break is not None else None

  counter_breaks = [
    item for item in breaks
    if item.direction == counter_direction and item.kind == "BOS"
  ]
  latest_counter_break = counter_breaks[-1] if counter_breaks else None
  counter_bos_ts = (
    _epoch(latest_counter_break.ts) if latest_counter_break is not None else None
  )

  opposing_distance_atr = None
  if target_low is not None and target_high is not None:
    distance = (
      float(target_low) - spot_price
      if direction == "BUY"
      else spot_price - float(target_high)
    )
    opposing_distance_atr = max(0.0, distance) / atr_value

  # "Best price reached" (P2's retrace denominator): the highest high for a
  # BUY, lowest low for a SELL, over the whole visible M1 window. Bounded
  # by that window (see _load_frames' 240-bar/4h default) - a group open
  # longer than the window understates the true extreme, which
  # AutoTradeEngine.ValidateAddTriggers treats as "insufficient history"
  # (gated against GroupOpenedAt) rather than guess past what's visible.
  if direction == "BUY":
    extreme_idx = m1["high"].idxmax()
    extreme_price = float(m1["high"].max())
  else:
    extreme_idx = m1["low"].idxmin()
    extreme_price = float(m1["low"].min())
  extreme_ts = _epoch(extreme_idx)

  rejection_confirmed = bool(_rejects(m1.iloc[-1], direction, atr_value))

  return AutoScaleContext(
    bar_ts=_epoch(m1.index[-1]) or 0,
    atr=atr_value,
    structure_swing=structure_swing,
    displacement_direction=(
      pa_direction if latest_leg is not None else None
    ),
    displacement_age_bars=displacement_age,
    bos_direction=(pa_direction if latest_break is not None else None),
    bos_ts=bos_ts,
    opposing_level_distance_atr=opposing_distance_atr,
    counter_bos_ts=counter_bos_ts,
    extreme_price=extreme_price,
    extreme_ts=extreme_ts,
    rejection_confirmed=rejection_confirmed,
  )


def _epoch(value) -> int | None:
  if value is None:
    return None
  if hasattr(value, "to_pydatetime"):
    value = value.to_pydatetime()
  if isinstance(value, datetime):
    return int(value.timestamp())
  try:
    return int(value)
  except (TypeError, ValueError):
    return None
