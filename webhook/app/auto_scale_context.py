"""Closed-bar PA context required by the cTrader scale-in executor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

import pandas as pd

from app.auto_scalp_gate import AutoScalpDecision
from app.pa_math import atr_series
from app.structure import structure_breaks
from app.swings import find_swings
from app.zones import displacement


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


def build_auto_scale_context(
  frames: dict[str, pd.DataFrame],
  decision: AutoScalpDecision,
  *,
  spot_price: float,
  cfg: Any,
) -> AutoScaleContext | None:
  direction = str(decision.direction or "").upper()
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

  target = decision.target
  opposing_distance_atr = None
  if target is not None:
    distance = (
      float(target.low) - spot_price
      if direction == "BUY"
      else spot_price - float(target.high)
    )
    opposing_distance_atr = max(0.0, distance) / atr_value

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
