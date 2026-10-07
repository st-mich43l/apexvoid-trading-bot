"""Multi-strategy match storage and selection helpers."""

from __future__ import annotations

import json
from typing import Any, Iterable

from app.autotrade.strategy_match import StrategyMatch


STRATEGY_MATCHES_KEY_PREFIX = "auto_trade:strategy_matches"


def strategy_matches_key(symbol: str) -> str:
  return f"{STRATEGY_MATCHES_KEY_PREFIX}:{symbol.upper()}"


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
  """Representative match for status reporting only.

  Execution selection is ``arbitration.arbitrate_execution_intents``; this
  never decides what publishes.
  """
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
      -(item.quality_overall or 0.0),
      -item.confluence,
      item.match_id,
    ),
  )
