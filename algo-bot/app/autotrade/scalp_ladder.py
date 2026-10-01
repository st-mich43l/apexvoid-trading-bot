"""Execution-side target ladder for already-created scalp opportunities."""

from __future__ import annotations

from typing import Any


def scalp_target_ladder(
  opportunity: Any,
  cfg: Any | None = None,
) -> tuple[int, tuple[int, ...]]:
  """Return final target pips and the published target ladder.

  This consumes a typed opportunity emitted by the authoritative strategy
  producer.  It does not discover, validate, or reshape the technical setup.
  """
  del cfg  # retained for the compatibility signature used by old callers
  final_pips = max(1, int(round(float(opportunity.expected_target_pips))))
  stop = max(1, int(round(float(opportunity.expected_stop_pips))))
  try:
    rr = float(opportunity.expected_reward_risk)
  except (TypeError, ValueError):
    rr = (final_pips / stop) if stop else 1.0
  if rr <= 1.05 or final_pips <= stop:
    return final_pips, (final_pips,)
  first = stop
  last = max(first, min(final_pips, stop * 2))
  if last <= first:
    return last, (last,)
  return last, (first, last)
