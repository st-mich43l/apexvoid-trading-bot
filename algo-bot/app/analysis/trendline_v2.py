"""Causal construction and live-state evaluation for Trendline V2.

V1 fitted a line through any two historical pivots and subsequently counted
nearby pivots as touches.  V2 keeps the two defining anchors immutable: every
later touch is measured against that original projection and must show a real
reaction before it becomes an independent validation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from numbers import Integral
from typing import Any

import pandas as pd

from app.analysis.math_utils import atr_scalar
from app.analysis.trendlines import (
  Trendline,
  TrendlineInteraction,
  TrendlineValidationTouch,
  value_at,
)
from app.analysis.types import Swing


_EPS = 1e-12
MetricSink = Callable[[str, str, dict[str, str]], None]


@dataclass(frozen=True)
class _Pivot:
  index: int
  price: float
  confirmed_index: int
  timestamp: str | None
  confirmed_timestamp: str | None


@dataclass(frozen=True)
class _Health:
  state: str
  broken: bool
  break_index: int | None
  wick_violations: int
  close_violations: int
  max_penetration: float
  latest_penetration: float
  violation_index: int | None
  violation_reclaimed: bool
  consecutive_violations: int


def build_causal_trendlines(
  swings: list[Swing],
  df: pd.DataFrame,
  atr: pd.Series | float,
  cfg: Any,
  *,
  symbol: str = "",
  timeframe: str = "",
  metric_sink: MetricSink | None = None,
) -> list[Trendline]:
  """Return V2 lines built from immutable A/B anchors and later evidence.

  A pivot is considered only from its ``confirmed_index`` onward.  The loop
  deliberately never chooses a later point as an anchor and then re-counts an
  earlier middle pivot: C can validate A->B, but cannot redraw it.
  """
  if df.empty:
    return []
  atr_value = atr_scalar(atr)
  if not math.isfinite(atr_value) or atr_value <= _EPS:
    return []

  settings = _settings(cfg, atr_value)
  last_bar = len(df) - 1
  candidates: list[Trendline] = []
  for swing_kind, line_kind in (("low", "support"), ("high", "resistance")):
    points = _confirmed_points(swings, df, swing_kind, last_bar)
    # A later pivot may validate the current A/B projection, but it must not
    # be promoted into an A/C anchor that redraws history around B.  A pivot
    # that is too close simply starts a new prospective pair with its next
    # chronological neighbour.
    for anchor_a, anchor_b in zip(points, points[1:]):
      if anchor_b.index - anchor_a.index < settings["minimum_spacing"]:
        continue
      line = _candidate_from_anchors(
        line_kind,
        anchor_a,
        anchor_b,
        points,
        df,
        atr_value,
        settings,
      )
      if line is None:
        continue
      candidates.append(line)

  lines = _deduplicate(candidates, last_bar, atr_value, settings)
  if metric_sink is not None:
    for line in lines:
      metric_sink(
        f"trendline_v2_{line.state}",
        symbol,
        {"tf": timeframe, "kind": line.kind},
      )
  return lines


def evaluate_live_interaction(
  line: Trendline,
  df: pd.DataFrame,
  atr: float,
  cfg: Any,
) -> TrendlineInteraction:
  """Classify the latest closed M5 relation to a V2 dynamic level.

  This is intentionally an interaction classifier, not an entry signal.  A
  reclaimed M5 test still needs a new M1 trigger in the execution lifecycle.
  """
  if df.empty:
    return TrendlineInteraction(
      "NO_DATA", 0.0, 0.0, 0.0, False, 0.0, 0, None, None, "no_m5_data",
    )
  atr_value = max(float(atr), _EPS)
  interaction_band = max(
    0.0,
    float(getattr(cfg, "interaction_band_atr", 0.2)),
  ) * atr_value
  close_violation = max(
    0.0,
    float(getattr(cfg, "close_violation_atr", 0.15)),
  ) * atr_value
  minimum_approach = max(
    0.0,
    float(getattr(cfg, "approach_min_distance_atr", 0.1)),
  ) * atr_value
  index = len(df) - 1
  line_price = value_at(line, index)
  row = df.iloc[index]
  low = float(row["low"])
  high = float(row["high"])
  close = float(row["close"])
  valid_side_close = _side_distance(line.kind, close, line_price)
  touch = low <= line_price + interaction_band and high >= line_price - interaction_band
  prior_distance = 0.0
  approach_valid = False
  if index > 0:
    prior_line = value_at(line, index - 1)
    prior_close = float(df["close"].iloc[index - 1])
    prior_distance = _side_distance(line.kind, prior_close, prior_line)
    approach_valid = prior_distance >= minimum_approach
  timestamp = _timestamp(df, index)
  if valid_side_close < -close_violation:
    return TrendlineInteraction(
      "FAILED_SUPPORT" if line.kind == "support" else "FAILED_RESISTANCE",
      line_price,
      line_price - interaction_band,
      line_price + interaction_band,
      approach_valid,
      prior_distance / atr_value,
      1 if index > 0 else 0,
      index,
      timestamp,
      "close_violation",
    )
  if not touch:
    state = "ABOVE_LINE" if line.kind == "support" else "BELOW_LINE"
    if 0.0 <= valid_side_close <= interaction_band * 2.0:
      state = "APPROACHING_SUPPORT" if line.kind == "support" else "APPROACHING_RESISTANCE"
    return TrendlineInteraction(
      state,
      line_price,
      line_price - interaction_band,
      line_price + interaction_band,
      approach_valid,
      prior_distance / atr_value,
      1 if index > 0 else 0,
      None,
      None,
    )
  if not approach_valid:
    return TrendlineInteraction(
      "FAILED_SUPPORT" if line.kind == "support" else "FAILED_RESISTANCE",
      line_price,
      line_price - interaction_band,
      line_price + interaction_band,
      False,
      prior_distance / atr_value,
      1 if index > 0 else 0,
      index,
      timestamp,
      "invalid_approach_direction",
    )
  if valid_side_close >= 0.0:
    return TrendlineInteraction(
      "RECLAIMED_SUPPORT" if line.kind == "support" else "RECLAIMED_RESISTANCE",
      line_price,
      line_price - interaction_band,
      line_price + interaction_band,
      True,
      prior_distance / atr_value,
      1,
      index,
      timestamp,
    )
  return TrendlineInteraction(
    "TESTING_SUPPORT" if line.kind == "support" else "TESTING_RESISTANCE",
    line_price,
    line_price - interaction_band,
    line_price + interaction_band,
    True,
    prior_distance / atr_value,
    1,
    index,
    timestamp,
    "testing_without_reaction",
  )


def _candidate_from_anchors(
  kind: str,
  anchor_a: _Pivot,
  anchor_b: _Pivot,
  points: list[_Pivot],
  df: pd.DataFrame,
  atr: float,
  settings: dict[str, float | int],
) -> Trendline | None:
  slope = (anchor_b.price - anchor_a.price) / (anchor_b.index - anchor_a.index)
  if kind == "support" and slope < float(settings["minimum_slope"]) - _EPS:
    return None
  if kind == "resistance" and slope > -float(settings["minimum_slope"]) + _EPS:
    return None
  if abs(slope) > float(settings["maximum_slope"]) + _EPS:
    return None
  intercept = anchor_a.price - slope * anchor_a.index
  validations: list[TrendlineValidationTouch] = []
  validation_indexes: list[int] = []
  previous_interaction = anchor_b.index
  for pivot in points:
    if pivot.index <= anchor_b.index or pivot.confirmed_index >= len(df):
      continue
    if pivot.index - previous_interaction < int(settings["minimum_validation_spacing"]):
      continue
    measured = _measure_validation(
      kind,
      pivot,
      slope,
      intercept,
      df,
      atr,
      settings,
    )
    if measured is None:
      continue
    validations.append(measured)
    validation_indexes.append(pivot.index)
    previous_interaction = pivot.index

  last_touch = validation_indexes[-1] if validation_indexes else anchor_b.index
  span_bars = last_touch - anchor_a.index
  health = _health(
    kind,
    slope,
    intercept,
    df,
    atr,
    anchor_b.index + 1,
    settings,
  )
  validation_errors = [item.touch_error_atr for item in validations]
  spacing = tuple(
    later - earlier
    for earlier, later in zip(
      (anchor_b.index, *validation_indexes[:-1]),
      validation_indexes,
    )
  )
  validation_count = len(validations)
  exhausted = validation_count >= int(settings["exhaustion_validations"])
  state = health.state
  if state not in {"broken", "degraded"}:
    if exhausted:
      state = "exhausted"
    elif (
      validation_count >= int(settings["minimum_validations"])
      and span_bars >= int(settings["minimum_span"])
    ):
      state = "confirmed"
    else:
      state = "tentative"
  point_indexes = (anchor_a.index, anchor_b.index, *validation_indexes)
  return Trendline(
    kind=kind,
    point_idx=point_indexes,
    slope=slope,
    intercept=intercept,
    touches=len(point_indexes),
    broken=health.broken,
    break_index=health.break_index,
    fit_error_atr=(
      sum(validation_errors) / len(validation_errors)
      if validation_errors else 0.0
    ),
    violations=health.wick_violations,
    bars_since_last_touch=(len(df) - 1) - last_touch,
    span_bars=span_bars,
    exhausted=exhausted,
    version="v2",
    state=state,
    anchor_idx=(anchor_a.index, anchor_b.index),
    anchor_prices=(anchor_a.price, anchor_b.price),
    anchor_timestamps=(anchor_a.timestamp, anchor_b.timestamp),
    anchor_confirmed_idx=(anchor_a.confirmed_index, anchor_b.confirmed_index),
    anchor_confirmed_timestamps=(
      anchor_a.confirmed_timestamp,
      anchor_b.confirmed_timestamp,
    ),
    validation_touches=tuple(validations),
    anchor_count=2,
    validation_touch_count=validation_count,
    total_touch_count=2 + validation_count,
    slope_atr_per_bar=slope / atr,
    anchor_fit_error_atr=0.0,
    mean_validation_error_atr=(
      sum(validation_errors) / len(validation_errors)
      if validation_errors else None
    ),
    median_validation_error_atr=_median(validation_errors),
    max_validation_error_atr=max(validation_errors) if validation_errors else None,
    touch_spacing_bars=spacing,
    wick_violations=health.wick_violations,
    close_violations=health.close_violations,
    max_penetration_price=health.max_penetration,
    max_penetration_atr=health.max_penetration / atr,
    latest_penetration_price=health.latest_penetration,
    latest_penetration_atr=health.latest_penetration / atr,
    bars_since_violation=(
      None if health.violation_index is None
      else (len(df) - 1) - health.violation_index
    ),
    violation_reclaimed=health.violation_reclaimed,
    violation_index=health.violation_index,
    consecutive_violations=health.consecutive_violations,
  )


def _measure_validation(
  kind: str,
  pivot: _Pivot,
  slope: float,
  intercept: float,
  df: pd.DataFrame,
  atr: float,
  settings: dict[str, float | int],
) -> TrendlineValidationTouch | None:
  line_at_pivot = slope * pivot.index + intercept
  actual_extreme = pivot.price
  error = abs(actual_extreme - line_at_pivot)
  tolerance = float(settings["validation_tolerance"])
  if error > tolerance + _EPS:
    return None
  reaction_end = (
    pivot.confirmed_index + int(settings["validation_reaction_bars"])
  )
  # A partial reaction window would make a line appear confirmed before all
  # evidence that defines the reaction has closed.  Defer it to the next bar.
  if reaction_end >= len(df):
    return None
  window = df.iloc[pivot.index:reaction_end + 1]
  expected = [slope * index + intercept for index in range(pivot.index, reaction_end + 1)]
  if kind == "support":
    penetration = max(
      0.0,
      max(line - float(low) for line, low in zip(expected, window["low"])),
    )
    favorable = max(float(window["high"].max()) - line_at_pivot, 0.0)
    adverse = penetration
  else:
    penetration = max(
      0.0,
      max(float(high) - line for line, high in zip(expected, window["high"])),
    )
    favorable = max(line_at_pivot - float(window["low"].min()), 0.0)
    adverse = penetration
  close_line = expected[-1]
  close = float(window["close"].iloc[-1])
  close_distance = _side_distance(kind, close, close_line)
  prior_distance = 0.0
  approach_valid = False
  if pivot.index > 0:
    prior_line = slope * (pivot.index - 1) + intercept
    prior_distance = _side_distance(kind, float(df["close"].iloc[pivot.index - 1]), prior_line)
    approach_valid = prior_distance >= float(settings["approach_min_distance"])
  reclaimed = close_distance >= 0.0
  structure_confirmed = bool(
    approach_valid
    and reclaimed
    and penetration <= float(settings["invalidation_penetration"]) + _EPS
    and favorable >= float(settings["minimum_favorable_excursion"]) - _EPS
  )
  if not structure_confirmed:
    return None
  reaction_bars = reaction_end - pivot.index + 1
  strength = min(
    1.0,
    (favorable / atr) + max(0.0, close_distance / atr) - (penetration / atr),
  )
  return TrendlineValidationTouch(
    bar_index=pivot.index,
    timestamp=pivot.timestamp,
    projected_line_price=line_at_pivot,
    actual_extreme=actual_extreme,
    touch_error_price=error,
    touch_error_atr=error / atr,
    penetration_price=penetration,
    penetration_atr=penetration / atr,
    close_distance_from_line=close_distance,
    close_distance_atr=close_distance / atr,
    favorable_excursion_price=favorable,
    favorable_excursion_atr=favorable / atr,
    adverse_excursion_price=adverse,
    adverse_excursion_atr=adverse / atr,
    reaction_bars=reaction_bars,
    reclaimed=reclaimed,
    rejection_strength=strength,
    structure_confirmed=structure_confirmed,
    approach_direction_valid=approach_valid,
  )


def _health(
  kind: str,
  slope: float,
  intercept: float,
  df: pd.DataFrame,
  atr: float,
  start: int,
  settings: dict[str, float | int],
) -> _Health:
  wick_violations = 0
  close_violations = 0
  max_penetration = 0.0
  latest_penetration = 0.0
  violation_index: int | None = None
  unresolved_close: int | None = None
  recovered = False
  consecutive = 0
  max_consecutive = 0
  for index in range(max(0, start), len(df)):
    line = slope * index + intercept
    row = df.iloc[index]
    penetration = (
      max(0.0, line - float(row["low"]))
      if kind == "support"
      else max(0.0, float(row["high"]) - line)
    )
    close_distance = _side_distance(kind, float(row["close"]), line)
    max_penetration = max(max_penetration, penetration)
    if penetration > float(settings["invalidation_penetration"]) + _EPS:
      wick_violations += 1
      latest_penetration = penetration
      violation_index = index
      consecutive += 1
      max_consecutive = max(max_consecutive, consecutive)
      if close_distance >= 0.0:
        recovered = True
    else:
      consecutive = 0
    if close_distance < -float(settings["close_violation"]) - _EPS:
      close_violations += 1
      unresolved_close = index
      latest_penetration = max(latest_penetration, -close_distance)
      violation_index = index
      recovered = False
    elif unresolved_close is not None and close_distance >= 0.0:
      recovered = True
      unresolved_close = None
  broken = unresolved_close is not None
  if broken:
    state = "broken"
  elif wick_violations > int(settings["maximum_wick_violations"]):
    state = "degraded"
  elif close_violations:
    state = "degraded"
  else:
    state = "candidate"
  return _Health(
    state=state,
    broken=broken,
    break_index=unresolved_close,
    wick_violations=wick_violations,
    close_violations=close_violations,
    max_penetration=max_penetration,
    latest_penetration=latest_penetration,
    violation_index=violation_index,
    violation_reclaimed=recovered,
    consecutive_violations=max_consecutive,
  )


def _confirmed_points(
  swings: list[Swing],
  df: pd.DataFrame,
  kind: str,
  last_bar: int,
) -> list[_Pivot]:
  points: list[_Pivot] = []
  for swing in swings:
    if swing.kind != kind:
      continue
    index = _bar_index(swing, df)
    if index is None:
      continue
    confirmed_index = swing.confirmed_index
    if confirmed_index is None:
      # Older cached/replay Swing payloads did not carry availability.  The
      # pivot can still be used, but never before its own bar.
      confirmed_index = index
    if not isinstance(confirmed_index, Integral) or not 0 <= int(confirmed_index) <= last_bar:
      continue
    price = float(swing.price)
    if not math.isfinite(price):
      continue
    points.append(_Pivot(
      index=index,
      price=price,
      confirmed_index=int(confirmed_index),
      timestamp=_timestamp(df, index),
      confirmed_timestamp=_timestamp(df, int(confirmed_index)),
    ))
  return sorted({(point.index, point.price, point.confirmed_index): point for point in points}.values(), key=lambda item: item.index)


def _bar_index(swing: Swing, df: pd.DataFrame) -> int | None:
  if isinstance(swing.index, Integral):
    index = int(swing.index)
  else:
    try:
      location = df.index.get_loc(swing.index)
    except KeyError:
      return None
    if not isinstance(location, Integral):
      return None
    index = int(location)
  return index if 0 <= index < len(df) else None


def _deduplicate(
  lines: list[Trendline],
  last_bar: int,
  atr: float,
  settings: dict[str, float | int],
) -> list[Trendline]:
  # Near-identical projections are one structural line.  Preserve the oldest
  # anchor pair so B/C cannot replace A/B and erase the latter's validation,
  # violation, or exhaustion lifecycle.
  ranked = sorted(
    lines,
    key=lambda line: (
      line.anchor_idx[0] if line.anchor_idx else min(line.point_idx),
      line.anchor_idx[1] if len(line.anchor_idx) > 1 else max(line.point_idx),
      -line.validation_touch_count,
    ),
  )
  kept: list[Trendline] = []
  for line in ranked:
    if any(_near_duplicate(line, other, last_bar, atr, settings) for other in kept):
      continue
    kept.append(line)
  return sorted(kept, key=lambda line: (line.kind, value_at(line, last_bar), line.slope))


def _near_duplicate(
  first: Trendline,
  second: Trendline,
  last_bar: int,
  atr: float,
  settings: dict[str, float | int],
) -> bool:
  if first.kind != second.kind:
    return False
  if abs(value_at(first, last_bar) - value_at(second, last_bar)) > float(settings["dedup_value_atr"]) * atr:
    return False
  denominator = max(abs(first.slope), abs(second.slope), _EPS)
  return abs(first.slope - second.slope) / denominator <= float(settings["dedup_slope_percent"]) + _EPS


def _settings(cfg: Any, atr: float) -> dict[str, float | int]:
  get = lambda name, default: getattr(cfg, name, default)
  return {
    "minimum_slope": max(0.0, float(get("minimum_slope_atr", 0.02))) * atr,
    "maximum_slope": max(0.0, float(get("maximum_slope_atr", 0.15))) * atr,
    "minimum_spacing": max(1, int(get("minimum_touch_spacing_bars", 3))),
    "minimum_span": max(1, int(get("minimum_span_bars", 20))),
    "minimum_validation_spacing": max(1, int(get("minimum_validation_touch_spacing_bars", 5))),
    "validation_tolerance": max(0.0, float(get("validation_touch_tolerance_atr", get("tolerance_atr", 0.3)))) * atr,
    "invalidation_penetration": max(0.0, float(get("invalidation_penetration_atr", get("pierce_tolerance_atr", 0.5)))) * atr,
    "close_violation": max(0.0, float(get("close_violation_atr", 0.15))) * atr,
    "approach_min_distance": max(0.0, float(get("approach_min_distance_atr", 0.1))) * atr,
    "minimum_favorable_excursion": max(0.0, float(get("minimum_validation_favorable_excursion_atr", 0.1))) * atr,
    "validation_reaction_bars": max(0, int(get("validation_reaction_bars", 2))),
    "minimum_validations": max(1, int(get("minimum_validation_touches", 1))),
    "exhaustion_validations": max(1, int(get("exhaustion_validation_touches", get("maximum_touches", 4) - 2))),
    "maximum_wick_violations": max(0, int(get("maximum_wick_violations", get("maximum_violations", 2)))),
    "dedup_value_atr": max(0.0, float(get("dedup_value_atr", 0.5))),
    "dedup_slope_percent": max(0.0, float(get("dedup_slope_percent", 0.2))),
  }


def _side_distance(kind: str, price: float, line: float) -> float:
  return price - line if kind == "support" else line - price


def _timestamp(df: pd.DataFrame, index: int) -> str | None:
  if not 0 <= index < len(df):
    return None
  value = df.index[index]
  return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _median(values: list[float]) -> float | None:
  if not values:
    return None
  ordered = sorted(values)
  middle = len(ordered) // 2
  if len(ordered) % 2:
    return ordered[middle]
  return (ordered[middle - 1] + ordered[middle]) / 2.0
