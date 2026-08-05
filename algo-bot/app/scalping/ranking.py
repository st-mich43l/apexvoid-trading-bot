"""Opportunity ranking after hard gates."""

from __future__ import annotations

from typing import Any

from app.scalping.models import (
  ScalpContextSnapshot,
  ScalpDecision,
  ScalpOpportunity,
  ScalpScore,
)


def score_opportunity(
  opportunity: ScalpOpportunity,
  context: ScalpContextSnapshot,
  decision: ScalpDecision,
  *,
  spread_pips: float,
) -> ScalpScore:
  penalties: list[str] = []
  location = 1.0
  pos = decision.measured.get("location_position", opportunity.location_position)
  if pos is not None:
    if opportunity.direction == "BUY":
      location = max(0.0, 1.0 - float(pos))
    else:
      location = max(0.0, float(pos))
  else:
    location = 0.4
    penalties.append("missing_location")

  trigger = 0.8 if opportunity.trigger_type else 0.2
  structure = 0.7
  if opportunity.archetype in context.permitted_archetypes:
    structure = 0.9
  if context.htf_bias == "unknown":
    structure -= 0.1
    penalties.append("unknown_htf_bias")

  room = min(1.0, float(opportunity.expected_target_pips) / 30.0)
  freshness = 0.9
  cost = max(0.0, 1.0 - float(spread_pips) / 5.0)
  if spread_pips > 3.0:
    penalties.append("wide_spread")

  total = (
    0.25 * location
    + 0.20 * trigger
    + 0.20 * structure
    + 0.15 * room
    + 0.10 * freshness
    + 0.10 * cost
  )
  return ScalpScore(
    total=round(total, 4),
    location=round(location, 4),
    trigger=round(trigger, 4),
    structure=round(structure, 4),
    room=round(room, 4),
    freshness=round(freshness, 4),
    cost=round(cost, 4),
    penalties=tuple(penalties),
  )


def rank_opportunities(
  items: list[tuple[ScalpOpportunity, ScalpDecision, ScalpScore]],
  *,
  maximum: int,
) -> list[tuple[ScalpOpportunity, ScalpDecision, ScalpScore]]:
  allowed = [item for item in items if item[1].allowed and not item[1].hard_block]
  allowed.sort(key=lambda row: row[2].total, reverse=True)
  return allowed[: max(0, int(maximum))]
