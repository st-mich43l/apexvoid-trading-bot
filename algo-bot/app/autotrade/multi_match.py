"""Multi-strategy match storage, deduplication, and selection helpers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from app.autotrade.execution_policy import (
  FAMILY_RANGE_REVERSION,
  TIER_C,
  classify_tier,
  risk_multiplier_for_tier,
  strategy_family,
)
from app.autotrade.strategy_match import StrategyMatch
from app.autotrade.strategy_identity import (
  STRUCTURAL_SETUPS,
)


STRATEGY_MATCHES_KEY_PREFIX = "auto_trade:strategy_matches"


def _freshness(match: StrategyMatch) -> float:
  for raw in (
    match.confirmation_bar_ts,
    match.touch_bar_ts,
    match.event_ts,
  ):
    if not raw:
      continue
    text = str(raw).strip()
    try:
      value = float(text)
      if value > 1e12:
        value /= 1000
      return value
    except ValueError:
      try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
      except ValueError:
        continue
  return float(match.issued_at)


def strategy_matches_key(symbol: str) -> str:
  return f"{STRATEGY_MATCHES_KEY_PREFIX}:{symbol.upper()}"


def _structural_strategy_rank(match: StrategyMatch) -> int:
  if match.strategy in STRUCTURAL_SETUPS:
    return 0
  if match.strategy == "Break & Retest":
    return 1
  if match.strategy in {"Trend Pullback", "Range Edge Scalp", "Fade Scalp"}:
    return 2
  return 3


def merge_confluence(
  primary: StrategyMatch,
  secondary: StrategyMatch,
  *,
  cfg: Any | None = None,
) -> StrategyMatch:
  same_match_replay = primary.match_id == secondary.match_id
  reasons = tuple(dict.fromkeys([*primary.reasons, *secondary.reasons]))
  tags = tuple(dict.fromkeys([
    *primary.tags,
    *secondary.tags,
    *(
      ()
      if same_match_replay
      else (
        f"confluence:{secondary.strategy}",
        f"contributor:{primary.match_id}",
        f"contributor:{secondary.match_id}",
      )
    ),
  ]))
  # A replay/update of one detector match is not independent evidence. Keep
  # the strongest already-assembled score so a fresh replay cannot erase
  # legitimate contributors, but never award its own +1.
  confluence = (
    max(primary.confluence, secondary.confluence)
    if same_match_replay
    else max(primary.confluence, secondary.confluence) + (
      1 if secondary.confluence >= primary.confluence else 0
    )
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
  data["risk_multiplier"] = risk_multiplier_for_tier(
    tier,
    cfg,
    post_impulse=bool(primary.range_state == "post_impulse_range"),
    one_sided=bool(primary.strategy == "One-Sided Range Reaction"),
    range_scalp=(
      strategy_family(primary.strategy) == FAMILY_RANGE_REVERSION
    ),
  )
  merged = StrategyMatch.from_json(json.dumps(data, separators=(",", ":")))
  return merged or primary


def _same_go_thesis(
  left: StrategyMatch, right: StrategyMatch,
) -> bool:
  """Correlate only the thesis identity published by Go arbitration."""
  return (
    left.go_thesis_id is not None
    and right.go_thesis_id is not None
    and left.go_thesis_id == right.go_thesis_id
  )


def dedupe_matches(
  matches: Iterable[StrategyMatch],
  *,
  atr: float,
  cfg: Any | None = None,
) -> tuple[list[StrategyMatch], list[dict[str, str]]]:
  """Keep distinct theses; merge same-thesis into the higher-quality match."""
  kept: list[StrategyMatch] = []
  events: list[dict[str, str]] = []
  for match in sorted(
    matches,
    key=lambda item: (
      -_freshness(item),
      -item.confluence,
      item.strategy,
      item.direction,
    ),
  ):
    if (match.tier or "").upper() == TIER_C:
      events.append({
        "match_id": match.match_id,
        "event": "preference_telemetry",
        "reason": "tier_c_analysis_only",
      })
      # Tier C is preference telemetry — keep the match executable.
    merged_into = None
    for index, existing in enumerate(kept):
      if _same_go_thesis(existing, match):
        primary, secondary = existing, match
        if _freshness(match) > _freshness(existing):
          primary, secondary = match, existing
        elif (
          _freshness(match) == _freshness(existing)
          and _structural_strategy_rank(match)
            < _structural_strategy_rank(existing)
        ):
          primary, secondary = match, existing
        kept[index] = merge_confluence(primary, secondary, cfg=cfg)
        merged_into = existing.match_id
        break
    if merged_into is not None:
      events.append({
        "match_id": match.match_id,
        "event": (
          "replay_updated"
          if match.match_id == merged_into
          else "merged_confluence"
        ),
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
