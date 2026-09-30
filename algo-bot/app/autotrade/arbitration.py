"""Cross-engine arbitration for one autonomous execution cycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
  tier: str
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
  current_price: float | None = None
  target_model: str = "fill_relative"
  targets_pips: tuple[int, ...] = ()
  absolute_target_price: float | None = None
  target_reference_price: str = "broker_fill"
  proposed_group_id: str | None = None
  cycle_id: str | None = None
  # Go's real per-instance StrategyQuality.Overall (see strategy_match.py's
  # own doc comment). None only for a hypothetical non-Go source; every
  # live source today ("go_analysis_engine") always sets this.
  quality_overall: float | None = None
  # Go's cross-strategy arbitration decision for this intent's match
  # (Phase 2, analysis.opportunity.arbitration.v1 — threaded from
  # StrategyMatch.arbitration_status/arbitration_reason_code). None until
  # Go publishes a decision for this opportunity.
  arbitration_status: str | None = None
  arbitration_reason_code: str | None = None
  # False when the executable quote is outside this intent's entry contract, so
  # it can only wait for a retest this cycle. A waiting intent must not create a
  # direction conflict with an intent that can execute now: the two-sided
  # picture "demand below price, supply above it, both waiting" is not a
  # BUY-vs-SELL conflict. Defaults True so callers that do not know keep the
  # legacy behavior of treating every intent as a live competitor.
  executable_now: bool = True


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


def _rank(intent: ExecutionIntent, *, use_quality: bool) -> tuple:
  if use_quality:
    # Go's real per-instance quality when present; a scaled-down confluence
    # fallback otherwise (only reachable for a hypothetical non-Go source —
    # every live source today always sets quality_overall). The /10.0 keeps
    # the fallback well below any real quality score's [0, 1] range rather
    # than letting an int proxy silently dominate a real signal.
    quality_key = -(
      intent.quality_overall
      if intent.quality_overall is not None
      else float(intent.confluence) / 10.0
    )
  else:
    quality_key = -intent.confluence
  return (
    0 if intent.tier.upper() == "A" else 1,
    quality_key,
    -intent.freshness,
    intent.distance_pips,
    _SOURCE_PRIORITY.get(intent.source, 9),
    intent.intent_id,
  )


def arbitrate_execution_intents(
  intents: list[ExecutionIntent],
  *,
  conflict_margin: float = 1.0,
  use_quality_ranking: bool = False,
  conflict_margin_quality: float = 0.15,
) -> ArbitrationResult:
  """Return one-direction publication order for this M1 confirmation cycle.

  At most one caller may publish. The ordered tail exists only as fallback
  when a higher-ranked intent fails its own execution checks.

  ``use_quality_ranking`` switches the rank/decisiveness signal from the
  legacy evidence-code ``confluence`` count (constant per strategy family,
  not a real per-instance signal) to Go's real ``quality_overall`` score.
  Defaults off so existing behavior is reproduced exactly until the
  rollout flag (``actionability.scanner_gates.use_quality_ranking``) is
  flipped. ``conflict_margin`` stays confluence-integer-scaled;
  ``conflict_margin_quality`` is the analogous margin on the [0, 1]
  quality scale — the two are not interchangeable units.
  """
  if not intents:
    return ArbitrationResult((), (), "no_intent")
  ordered = sorted(
    intents, key=lambda intent: _rank(intent, use_quality=use_quality_ranking),
  )
  # The direction decision is made among intents that can execute right now;
  # only when none can does it fall back to the whole set (the legacy rule).
  # Waiting intents of the chosen direction still stay in ``ordered`` so their
  # retest state keeps advancing and they can publish the moment price enters.
  decision_pool = [item for item in ordered if item.executable_now] or ordered
  top = decision_pool[0]
  opposing = [item for item in decision_pool if item.direction != top.direction]
  if opposing:
    strongest_opposing = opposing[0]
    same_tier = strongest_opposing.tier.upper() == top.tier.upper()
    if (
      use_quality_ranking
      and top.quality_overall is not None
      and strongest_opposing.quality_overall is not None
    ):
      decisive = (
        not same_tier
        or top.quality_overall - strongest_opposing.quality_overall
          >= float(conflict_margin_quality)
      )
    else:
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


def select_go_arbitrated_intent(intents: list[ExecutionIntent]) -> ArbitrationResult:
  """Phase 2: read Go's already-decided winner instead of ranking here.

  ``intents`` must already be admission-filtered — the same staleness/
  spot/regime gate (``_admit_strategy_intent_for_cycle``) every caller of
  ``arbitrate_execution_intents`` already ran before arbitration. Each
  intent's ``arbitration_status`` is threaded from its StrategyMatch (see
  ``go_opportunity_policy.on_arbitration_decision``) — analysis-engine's
  own ``internal/arbitration.Arbitrate`` decision for this symbol's live
  set. This function's job is purely "read the answer, never invent one":
  it never promotes a lower-ranked or non-winner intent itself when no
  winner is present or a caller passes stale/mixed data — it returns
  nothing, and waits for Go's next re-evaluation to publish an updated
  decision instead.
  """
  if not intents:
    return ArbitrationResult((), (), "no_intent")
  winners = sorted(
    (item for item in intents if item.arbitration_status == "winner"),
    key=lambda item: item.intent_id,
  )
  if not winners:
    return ArbitrationResult((), tuple(intents), "go_arbitration_no_winner")
  # Go's own per-symbol Arbitrate() yields at most one winner per
  # direction-conflict-group; more than one here would mean a stale or
  # mixed read across unrelated theses — the lowest intent_id keeps this
  # deterministic rather than raising, matching _rank's own tie-break.
  winner = winners[0]
  suppressed = tuple(item for item in intents if item.intent_id != winner.intent_id)
  reason = winner.arbitration_reason_code or "ranked_single_direction"
  return ArbitrationResult((winner,), suppressed, reason)
