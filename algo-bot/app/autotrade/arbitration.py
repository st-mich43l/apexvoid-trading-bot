"""Arbitration for one autonomous execution cycle.

Selection is best-first and deterministic. Opportunities that argue for the
same trade (same symbol and direction, same thesis/structure or overlapping
entry corridor) compete: exactly one executable winner is published and the
rest are suppressed. The rank hierarchy is, in order:

1. execution eligibility (can the quote enter the entry contract now)
2. strategy quality (Go ``quality.overall``)
3. confluence
4. structural/source quality (detector confluence raw score)
5. freshness of the confirmation
6. intent id (deterministic tie-break)

No strategy, family or source is favoured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.autotrade.entry_overlap import corridors_overlap


PublicationStatus = Literal[
  "published",
  "duplicate_candidate",
  "route_in_progress",
  "cycle_conflict",
  "duplicate_reaction",
  "duplicate_thesis",
  "terminal_reject",
  "publication_unavailable",
]


@dataclass(frozen=True)
class CandidatePublicationResult:
  """Typed outcome controlling ranked-intent fallback.

  Only ``terminal_reject`` is allowed to expose a lower-ranked intent. Every
  ownership/concurrency/availability result preserves the selected route's
  priority for the current cycle.
  """

  candidate_id: str | None
  status: PublicationStatus
  terminal: bool
  blocks_lower_ranked_intents: bool
  reason_code: str | None = None

  @classmethod
  def published(cls, candidate_id: str) -> "CandidatePublicationResult":
    return cls(candidate_id, "published", False, True, "candidate_published")

  @classmethod
  def terminal_reject(cls, reason_code: str) -> "CandidatePublicationResult":
    return cls(None, "terminal_reject", True, False, reason_code)

  @classmethod
  def blocked(
    cls,
    status: PublicationStatus,
    reason_code: str | None = None,
  ) -> "CandidatePublicationResult":
    return cls(None, status, False, True, reason_code or status)


@dataclass(frozen=True)
class ExecutionIntent:
  intent_id: str
  source: str
  strategy: str
  direction: str
  confluence: int
  freshness: float
  distance_pips: float
  symbol: str = "XAU"
  timeframe: str = "M1"
  family: str = ""
  entry_low: float = 0.0
  entry_high: float = 0.0
  structural_id: str = ""
  match_id: str | None = None
  reaction_id: str | None = None
  thesis_id: str | None = None
  # Go's cross-strategy thesis group (shared by opportunities on one structure).
  go_thesis_id: str | None = None
  current_price: float | None = None
  target_model: str = "fill_relative"
  targets_pips: tuple[int, ...] = ()
  absolute_target_price: float | None = None
  target_reference_price: str = "broker_fill"
  proposed_group_id: str | None = None
  cycle_id: str | None = None
  # Go's per-instance StrategyQuality.Overall in [0, 1].
  quality_overall: float | None = None
  # Detector confluence raw score (structural/source quality), tie-break only.
  structural_quality: float | None = None
  # ATR used to pad entry corridors when deciding whether two intents share a
  # thesis. Zero collapses the pad to exact zone intersection.
  atr: float = 0.0
  # Go-owned HTF relationship, consumed here only after admission. This is an
  # execution tie-break, not a Python technical-analysis reconstruction.
  bias_relationship: str | None = None
  # False when the executable quote is outside this intent's entry contract, so
  # it can only wait for a retest this cycle. A waiting intent must not create a
  # direction conflict with an intent that can execute now: the two-sided
  # picture "demand below price, supply above it, both waiting" is not a
  # BUY-vs-SELL conflict.
  executable_now: bool = True


@dataclass(frozen=True)
class ArbitrationResult:
  ordered: tuple[ExecutionIntent, ...]
  suppressed: tuple[ExecutionIntent, ...]
  reason_code: str
  # Same-direction intents that lost to a better intent on the same thesis.
  # Subset of ``suppressed``; mapped to the winning intent id.
  thesis_losers: dict[str, str] = field(default_factory=dict)


def _rank(intent: ExecutionIntent) -> tuple:
  return (
    0 if intent.executable_now else 1,
    -(intent.quality_overall or 0.0),
    -intent.confluence,
    -(intent.structural_quality or 0.0),
    -intent.freshness,
    intent.intent_id,
  )


def same_thesis(left: ExecutionIntent, right: ExecutionIntent) -> bool:
  """True when two intents argue for the same trade.

  Opposite directions never share a thesis. Same-direction intents do when they
  carry the same Go thesis group or structural identity, or their entry corridors
  overlap after an ATR pad.
  """
  if left.symbol != right.symbol or left.direction != right.direction:
    return False
  if left.go_thesis_id and left.go_thesis_id == right.go_thesis_id:
    return True
  if left.structural_id and left.structural_id == right.structural_id:
    return True
  if not (left.entry_high >= left.entry_low > 0 and right.entry_high >= right.entry_low > 0):
    return False
  return corridors_overlap(
    left.entry_low, left.entry_high, right.entry_low, right.entry_high,
    max(left.atr, right.atr),
  )


def _collapse_theses(
  ranked: list[ExecutionIntent],
) -> tuple[list[ExecutionIntent], dict[str, str]]:
  """Keep the best-ranked intent of every same-thesis group.

  ``ranked`` is already best-first, so an intent joins the group of the first
  winner it shares a thesis with.
  """
  winners: list[ExecutionIntent] = []
  losers: dict[str, str] = {}
  for item in ranked:
    owner = next((w for w in winners if same_thesis(w, item)), None)
    if owner is None:
      winners.append(item)
    else:
      losers[item.intent_id] = owner.intent_id
  return winners, losers


def arbitrate_execution_intents(
  intents: list[ExecutionIntent],
  *,
  conflict_margin_quality: float = 0.15,
) -> ArbitrationResult:
  """Return the best-first publication order for one confirmation cycle.

  At most one caller may publish. Every ordered intent belongs to a different
  thesis and the tail exists only as fallback when a higher-ranked intent fails
  its own execution checks. Directions that cannot be separated by
  ``conflict_margin_quality`` (the quality gap) are held back unless exactly
  one of them agrees with the higher-timeframe bias.
  """
  if not intents:
    return ArbitrationResult((), (), "no_intent")
  ranked = sorted(intents, key=_rank)
  # The direction decision is made among intents that can execute right now;
  # only when none can does it fall back to the whole set. Waiting intents of
  # the chosen direction still stay in ``ordered`` so their retest state keeps
  # advancing and they can publish the moment price enters.
  decision_pool = [item for item in ranked if item.executable_now] or ranked
  top = decision_pool[0]
  opposing = [item for item in decision_pool if item.direction != top.direction]
  if opposing:
    gap = (top.quality_overall or 0.0) - (opposing[0].quality_overall or 0.0)
    if gap < float(conflict_margin_quality):
      aligned_directions = {
        item.direction
        for item in decision_pool
        if str(item.bias_relationship or "").casefold() == "with_bias"
      }
      if len(aligned_directions) == 1:
        aligned_direction = next(iter(aligned_directions))
        top = next(
          item for item in decision_pool if item.direction == aligned_direction
        )
      else:
        return ArbitrationResult((), tuple(ranked), "opposite_direction_conflict")
  same_direction = [item for item in ranked if item.direction == top.direction]
  winners, losers = _collapse_theses(same_direction)
  suppressed = tuple(
    item for item in ranked
    if item.direction != top.direction or item.intent_id in losers
  )
  return ArbitrationResult(
    tuple(winners), suppressed, "ranked_single_direction", losers,
  )
