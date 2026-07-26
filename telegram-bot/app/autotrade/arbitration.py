"""Cross-engine arbitration for one autonomous execution cycle."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionIntent:
  intent_id: str
  source: str
  strategy: str
  direction: str
  confluence: int
  tier: str
  freshness: float
  distance_pips: float


@dataclass(frozen=True)
class ArbitrationResult:
  ordered: tuple[ExecutionIntent, ...]
  suppressed: tuple[ExecutionIntent, ...]
  reason_code: str


_SOURCE_PRIORITY = {
  "scanner_strategy_match": 0,
  "market_map_strategy": 1,
  "private_trend": 2,
  "private_range": 3,
}


def _rank(intent: ExecutionIntent) -> tuple:
  return (
    0 if intent.tier.upper() == "A" else 1,
    -intent.confluence,
    -intent.freshness,
    intent.distance_pips,
    _SOURCE_PRIORITY.get(intent.source, 9),
    intent.intent_id,
  )


def arbitrate_execution_intents(
  intents: list[ExecutionIntent],
  *,
  conflict_margin: float = 1.0,
) -> ArbitrationResult:
  """Return one-direction publication order for this M1 confirmation cycle.

  At most one caller may publish. The ordered tail exists only as fallback
  when a higher-ranked intent fails its own execution checks.
  """
  if not intents:
    return ArbitrationResult((), (), "no_intent")
  ordered = sorted(intents, key=_rank)
  top = ordered[0]
  opposing = [item for item in ordered if item.direction != top.direction]
  if opposing:
    strongest_opposing = opposing[0]
    same_tier = strongest_opposing.tier.upper() == top.tier.upper()
    decisive = (
      not same_tier
      or top.confluence - strongest_opposing.confluence
        >= max(1.0, float(conflict_margin))
    )
    if not decisive:
      return ArbitrationResult(
        (),
        tuple(ordered),
        "opposite_direction_conflict",
      )
  selected_direction = tuple(
    item for item in ordered if item.direction == top.direction
  )
  suppressed = tuple(
    item for item in ordered if item.direction != top.direction
  )
  return ArbitrationResult(
    selected_direction,
    suppressed,
    "ranked_single_direction",
  )
