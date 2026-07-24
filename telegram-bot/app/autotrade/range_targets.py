"""Shared adaptive range-scalp target selection (30/40/50-pip ladder).

Single source of truth for turning available room into a take-profit
target. Before this module existed, the same "largest of {50,70} that
fits with a 5-pip buffer" policy was duplicated independently in
scanner.py, gate.py, strategy_match.py and worker.py, all hardcoded -
any setup with 0-49 pips of room (the common case) silently produced no
executable target anywhere in that chain. Every caller must go through
select_range_target() instead of re-deriving its own ladder.
"""

from __future__ import annotations

from app.core.config import settings

_EPS = 1e-9

DEFAULT_RANGE_TARGETS_PIPS: tuple[int, ...] = (50, 40, 30)
DEFAULT_RANGE_TP_BUFFER_PIPS = 5.0
DEFAULT_RANGE_MIN_TARGET_PIPS = 30.0


def configured_range_targets() -> tuple[int, ...]:
  """Parsed, deduplicated, descending-sorted AUTO_TRADE_RANGE_TARGETS_PIPS."""
  raw = getattr(settings, "auto_trade_range_targets_pips", None)
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
  # Sorted descending so "largest target that fits" is a simple linear scan,
  # and deduplicated so a typo'd repeat in the env var can't double-count.
  return tuple(sorted(set(values), reverse=True))


def range_tp_buffer_pips() -> float:
  value = getattr(settings, "auto_trade_range_tp_buffer_pips", None)
  if value is None:
    return DEFAULT_RANGE_TP_BUFFER_PIPS
  try:
    parsed = float(value)
  except (TypeError, ValueError):
    return DEFAULT_RANGE_TP_BUFFER_PIPS
  return parsed if parsed >= 0 else DEFAULT_RANGE_TP_BUFFER_PIPS


def range_min_target_pips() -> float:
  value = getattr(settings, "auto_trade_range_min_target_pips", None)
  if value is None:
    return DEFAULT_RANGE_MIN_TARGET_PIPS
  try:
    parsed = float(value)
  except (TypeError, ValueError):
    return DEFAULT_RANGE_MIN_TARGET_PIPS
  return parsed if parsed > 0 else DEFAULT_RANGE_MIN_TARGET_PIPS


def select_range_target(
  room_pips: float,
  *,
  targets: tuple[int, ...] | None = None,
  buffer_pips: float | None = None,
) -> int | None:
  """Largest configured target whose (target + buffer) fits inside room_pips.

  Returns None only when no configured target fits at all - the caller
  must record this as an insufficient_target_room reason, never a bare
  silent rejection.
  """
  ladder = configured_range_targets() if targets is None else targets
  buffer = range_tp_buffer_pips() if buffer_pips is None else buffer_pips
  for target in ladder:
    if room_pips + _EPS >= target + buffer:
      return target
  return None
