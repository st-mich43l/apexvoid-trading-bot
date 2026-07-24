"""Multi-strategy match storage, deduplication, and selection helpers."""

from __future__ import annotations

import json
import math
from typing import Any, Iterable

from app.autotrade.execution_policy import TIER_C, classify_tier
from app.autotrade.strategy_match import StrategyMatch


STRATEGY_MATCHES_KEY_PREFIX = "auto_trade:strategy_matches"
_EPS = 1e-9


def strategy_matches_key(symbol: str) -> str:
  return f"{STRATEGY_MATCHES_KEY_PREFIX}:{symbol.upper()}"


def same_thesis(left: StrategyMatch, right: StrategyMatch, *, atr: float) -> bool:
  """True when two matches represent materially the same trade thesis."""
  if left.direction != right.direction:
    return False
  if left.symbol != right.symbol:
    return False
  if left.strategy != right.strategy:
    return False
  if left.family and right.family and left.family != right.family:
    return False

  # Mapped Zone Reaction: same reaction_id is identical; same thesis_id is the
  # same structural occupancy (dedupe before publish — claim still enforces).
  if left.reaction_id and right.reaction_id and left.reaction_id == right.reaction_id:
    return True
  if left.thesis_id and right.thesis_id and left.thesis_id == right.thesis_id:
    return True
  if left.reaction_id and right.reaction_id:
    return False
  if left.reaction_id or right.reaction_id:
    return False

  structural_left = left.structural_zone_id or left.zone_id
  structural_right = right.structural_zone_id or right.zone_id
  if (
    left.strategy_mode == "mapped_zone_reaction"
    or right.strategy_mode == "mapped_zone_reaction"
  ):
    if structural_left and structural_right and structural_left == structural_right:
      if left.touch_bar_ts and right.touch_bar_ts:
        return (
          left.touch_bar_ts == right.touch_bar_ts
          and left.confirmation_bar_ts == right.confirmation_bar_ts
          and left.reaction_type == right.reaction_type
        )
      from app.autotrade.reaction_identity import zones_materially_equivalent
      left_lo = left.structural_zone_low if left.structural_zone_low is not None else left.entry_low
      left_hi = left.structural_zone_high if left.structural_zone_high is not None else left.entry_high
      right_lo = right.structural_zone_low if right.structural_zone_low is not None else right.entry_low
      right_hi = right.structural_zone_high if right.structural_zone_high is not None else right.entry_high
      return zones_materially_equivalent(
        left_lo,
        left_hi,
        right_lo,
        right_hi,
        atr=atr,
      )
    from app.autotrade.reaction_identity import zones_materially_equivalent
    return (
      left.touch_bar_ts == right.touch_bar_ts
      and left.confirmation_bar_ts == right.confirmation_bar_ts
      and left.reaction_type == right.reaction_type
      and zones_materially_equivalent(
        left.entry_low,
        left.entry_high,
        right.entry_low,
        right.entry_high,
        atr=atr,
      )
    )

  # Event-based scanner strategies keep timestamp identity.
  if left.event_ts != right.event_ts:
    return False
  if left.range_id != right.range_id:
    return False
  if left.targets_pips != right.targets_pips:
    return False
  return (
    math.isclose(left.key_level, right.key_level, abs_tol=_EPS)
    and math.isclose(left.entry_low, right.entry_low, abs_tol=_EPS)
    and math.isclose(left.entry_high, right.entry_high, abs_tol=_EPS)
  )


def merge_confluence(primary: StrategyMatch, secondary: StrategyMatch) -> StrategyMatch:
  reasons = tuple(dict.fromkeys([*primary.reasons, *secondary.reasons]))
  tags = tuple(dict.fromkeys([
    *primary.tags,
    *secondary.tags,
    f"confluence:{secondary.strategy}",
  ]))
  confluence = max(primary.confluence, secondary.confluence) + (
    1 if secondary.confluence >= primary.confluence else 0
  )
  tier = classify_tier(
    confluence=confluence,
    strategy=primary.strategy,
  )
  payload = primary.to_json()
  data = json.loads(payload)
  data["reasons"] = list(reasons)
  data["tags"] = list(tags)
  data["confluence"] = confluence
  data["tier"] = tier
  data["risk_multiplier"] = primary.risk_multiplier
  merged = StrategyMatch.from_json(json.dumps(data, separators=(",", ":")))
  return merged or primary


def dedupe_matches(
  matches: Iterable[StrategyMatch],
  *,
  atr: float,
) -> tuple[list[StrategyMatch], list[dict[str, str]]]:
  """Keep distinct theses; merge same-thesis into the higher-quality match."""
  kept: list[StrategyMatch] = []
  events: list[dict[str, str]] = []
  for match in sorted(
    matches,
    key=lambda item: (-item.confluence, item.strategy, item.direction),
  ):
    if (match.tier or "").upper() == TIER_C:
      events.append({
        "match_id": match.match_id,
        "event": "detector_not_matched",
        "reason": "tier_c_analysis_only",
      })
      continue
    merged_into = None
    for index, existing in enumerate(kept):
      if same_thesis(existing, match, atr=atr):
        kept[index] = merge_confluence(existing, match)
        merged_into = existing.match_id
        break
    if merged_into is not None:
      events.append({
        "match_id": match.match_id,
        "event": "merged_confluence",
        "into": merged_into,
      })
      continue
    kept.append(match)
    events.append({
      "match_id": match.match_id,
      "event": "tracked",
      "strategy": match.strategy,
    })
  return kept, events


def serialize_matches(matches: Iterable[StrategyMatch]) -> str:
  return json.dumps(
    [json.loads(match.to_json()) for match in matches],
    separators=(",", ":"),
  )


def deserialize_matches(raw: object) -> list[StrategyMatch]:
  if raw is None:
    return []
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
  except (TypeError, ValueError, json.JSONDecodeError):
    return []
  if isinstance(payload, dict):
    payload = [payload]
  if not isinstance(payload, list):
    return []
  result: list[StrategyMatch] = []
  for item in payload:
    match = StrategyMatch.from_json(json.dumps(item, separators=(",", ":")))
    if match is not None:
      result.append(match)
  return result


def select_primary(
  matches: Iterable[StrategyMatch],
  *,
  prefer_direction: str | None = None,
) -> StrategyMatch | None:
  items = list(matches)
  if not items:
    return None
  if prefer_direction:
    sided = [m for m in items if m.direction == prefer_direction.upper()]
    if sided:
      items = sided
  return min(
    items,
    key=lambda item: (
      0 if (item.tier or "B").upper() == "A" else 1,
      -item.confluence,
      item.strategy,
      item.direction,
    ),
  )


def zones_contradict(left: StrategyMatch, right: StrategyMatch, atr: float) -> bool:
  if left.direction == right.direction:
    return False
  tol = max(atr * 0.25, 0.3) if atr > 0 and math.isfinite(atr) else 0.3
  overlap = (
    min(left.entry_high, right.entry_high) - max(left.entry_low, right.entry_low)
  )
  return overlap > tol
