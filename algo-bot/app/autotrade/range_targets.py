"""Shared adaptive range-scalp target selection.

Single source of truth for turning available room into a take-profit
target. The ladder is configurable via AUTO_TRADE_RANGE_TARGETS_PIPS
(default 20,30,40,50,70). Callers must go through select_range_target().
"""

from __future__ import annotations

from app.core.config import runtime_config

_EPS = 1e-9

DEFAULT_RANGE_TARGETS_PIPS: tuple[int, ...] = (70, 50, 40, 30, 20, 15)
DEFAULT_RANGE_TP_BUFFER_PIPS = 3.0
DEFAULT_RANGE_MIN_TARGET_PIPS = 15.0
DEFAULT_RANGE_MIN_RR = 1.00


def configured_range_targets() -> tuple[int, ...]:
  """Parsed, deduplicated, descending-sorted AUTO_TRADE_RANGE_TARGETS_PIPS."""
  raw = runtime_config.execution.targeting.range_ladder_pips
  if not raw:
    return DEFAULT_RANGE_TARGETS_PIPS
  values: list[int] = []
  for chunk in str(raw).split(","):
    chunk = chunk.strip()
    if not chunk:
      continue
    try:
      value = int(float(chunk))
    except ValueError:
      continue
    if value > 0:
      values.append(value)
  if not values:
    return DEFAULT_RANGE_TARGETS_PIPS
  return tuple(sorted(set(values), reverse=True))


def range_tp_buffer_pips() -> float:
  value = runtime_config.execution.range.tp_buffer_pips
  if value is None:
    return DEFAULT_RANGE_TP_BUFFER_PIPS
  try:
    parsed = float(value)
  except (TypeError, ValueError):
    return DEFAULT_RANGE_TP_BUFFER_PIPS
  return parsed if parsed >= 0 else DEFAULT_RANGE_TP_BUFFER_PIPS


def range_min_target_pips() -> float:
  value = runtime_config.execution.range.min_target_pips
  if value is None:
    return DEFAULT_RANGE_MIN_TARGET_PIPS
  try:
    parsed = float(value)
  except (TypeError, ValueError):
    return DEFAULT_RANGE_MIN_TARGET_PIPS
  return parsed if parsed > 0 else DEFAULT_RANGE_MIN_TARGET_PIPS


def range_min_rr() -> float:
  value = runtime_config.execution.range.min_rr
  if value is None:
    return DEFAULT_RANGE_MIN_RR
  try:
    parsed = float(value)
  except (TypeError, ValueError):
    return DEFAULT_RANGE_MIN_RR
  return parsed if parsed > 0 else DEFAULT_RANGE_MIN_RR


def select_range_target(
  room_pips: float,
  *,
  targets: tuple[int, ...] | None = None,
  buffer_pips: float | None = None,
  stop_pips: float | None = None,
  min_rr: float | None = None,
) -> int | None:
  """Largest configured target whose (target + buffer) fits inside room_pips.

  When stop_pips is provided, also enforce minimum reward/risk so a tiny
  20-pip target is not selected into an oversized stop.
  """
  configured = configured_range_targets() if targets is None else targets
  ladder = tuple(sorted(set(configured), reverse=True))
  buffer = range_tp_buffer_pips() if buffer_pips is None else buffer_pips
  rr_floor = range_min_rr() if min_rr is None else min_rr
  for target in ladder:
    if room_pips + _EPS < target + buffer:
      continue
    if stop_pips is not None and stop_pips > 0:
      if target / stop_pips + _EPS < rr_floor:
        continue
    return target
  return None
