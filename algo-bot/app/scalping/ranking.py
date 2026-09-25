"""Opportunity ranking after hard gates.

Uses the unified mathematical score when score_inputs / feature vector are
present; falls back to the legacy pip-heuristic score otherwise.

MAD is not applied here — owner rule: MAD does not drive scalping.
Accumulation soft favor lives only on technique Range Edge Scalp.
"""

from __future__ import annotations

from typing import Any

from app.scalping.math_features import unified_scalp_score
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
  math_inputs = decision.measured.get("math_score_inputs")
  if isinstance(math_inputs, dict) and math_inputs:
    total = unified_scalp_score(
      location=float(math_inputs.get("location", 0.0)),
      trigger=float(math_inputs.get("trigger", 0.0)),
      momentum=float(math_inputs.get("momentum", 0.0)),
      structure=float(math_inputs.get("structure", 0.0)),
      room=float(math_inputs.get("room", 0.0)),
      cost=float(math_inputs.get("cost", 0.0)),
      exhaustion=float(math_inputs.get("exhaustion", 0.0)),
    )
    session_quality = max(0.0, min(1.0, float(decision.measured.get("session_quality", 1.0))))
    total *= 0.85 + 0.15 * session_quality
    return ScalpScore(
      total=round(total, 4),
      location=round(float(math_inputs.get("location", 0.0)), 4),
      trigger=round(float(math_inputs.get("trigger", 0.0)), 4),
      structure=round(float(math_inputs.get("structure", 0.0)), 4),
      room=round(float(math_inputs.get("room", 0.0)), 4),
      freshness=round(float(math_inputs.get("momentum", 0.0)), 4),
      cost=round(1.0 - float(math_inputs.get("cost", 0.0)), 4),
      penalties=(),
    )

  return _legacy_score(opportunity, context, decision, spread_pips=spread_pips)


def _legacy_score(
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

  # Prefer ATR room when context carries it; else legacy /30 pip normalizer.
  room_atr = decision.measured.get("room_atr")
  if room_atr is not None:
    room = min(1.0, float(room_atr) / 1.0)
  else:
    room = min(1.0, float(opportunity.expected_target_pips) / 30.0)
  freshness = 0.9
  atr = float(context.atr or 0.0)
  if atr > 0 and spread_pips > 0:
    pip_size = float(
      decision.measured.get("pip_size")
      or opportunity.measured.get("pip_size")
      or 0.1
    )
    cost_atr = (float(spread_pips) * pip_size) / atr
    cost = max(0.0, 1.0 - min(1.0, cost_atr))
  else:
    cost = max(0.0, 1.0 - float(spread_pips) / 5.0)
  if spread_pips > 3.0:
    penalties.append("wide_spread")

  total = unified_scalp_score(
    location=location,
    trigger=trigger,
    momentum=freshness,
    structure=structure,
    room=room,
    cost=1.0 - cost,
    exhaustion=0.0,
  )
  session_quality = max(0.0, min(1.0, float(decision.measured.get("session_quality", 1.0))))
  total *= 0.85 + 0.15 * session_quality
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
  # Hard gate first: never rank blocked setups.
  allowed = [item for item in items if item[1].allowed and not item[1].hard_block]
  limit = max(0, int(maximum))
  if limit <= 0:
    return []

  # One archetype must not starve the others simply by producing a higher
  # generic score. Keep the old global ordering for non-scalp test/consumer
  # records, then use a deterministic round-robin across the three independent
  # scalp techniques.
  scalp_archetypes = {"range_sweep", "impulse_pullback", "breakout_retest"}
  if not any(item[0].archetype in scalp_archetypes for item in allowed):
    allowed.sort(key=lambda row: row[2].total, reverse=True)
    return allowed[:limit]

  groups: dict[str, list[tuple[ScalpOpportunity, ScalpDecision, ScalpScore]]] = {}
  for item in allowed:
    groups.setdefault(item[0].archetype, []).append(item)
  for group in groups.values():
    group.sort(key=lambda row: row[2].total, reverse=True)
  ranked: list[tuple[ScalpOpportunity, ScalpDecision, ScalpScore]] = []
  while len(ranked) < limit:
    progressed = False
    for archetype in sorted(groups):
      group = groups[archetype]
      if group:
        ranked.append(group.pop(0))
        progressed = True
        if len(ranked) >= limit:
          break
    if not progressed:
      break
  return ranked
