"""Explicit strategy-family registry — exact names only, no substring matching."""

from __future__ import annotations

REACTION_STRATEGIES = frozenset({
  "Key Level Reaction",
  "Session Level Reaction",
  "Trendline Reaction",
})

ZONE_STRATEGIES = frozenset({
  "Demand Zone",
  "Supply Zone",
  # Canonical live name (side is BUY/SELL, not the strategy label):
  "Zone Reaction",
  # Broken key-level role flip (source flip_zone); same Zone family:
  "Flip Zone",
  # production legacy names that must remain Zone-family, NOT Reaction:
  "Demand Zone Reaction",
  "Supply Zone Reaction",
})

LIQUIDITY_STRATEGIES = frozenset({
  "Liquidity Sweep",
  "Snap-Back",
})

RANGE_STRATEGIES = frozenset({
  "Range Box Scalp",
  "Range Edge Scalp",
  "One-Sided Range Reaction",
  "Fade Scalp",
  "Chop Zone Reaction",
})

CANONICAL_FAMILY_REACTION = "reaction"
CANONICAL_FAMILY_ZONE = "zone"
CANONICAL_FAMILY_LIQUIDITY = "liquidity"
CANONICAL_FAMILY_RANGE = "range"
CANONICAL_FAMILY_UNKNOWN = "unknown"


def is_reaction_strategy(name: str) -> bool:
  return str(name or "") in REACTION_STRATEGIES


def is_zone_strategy(name: str) -> bool:
  return str(name or "") in ZONE_STRATEGIES


def is_liquidity_strategy(name: str) -> bool:
  return str(name or "") in LIQUIDITY_STRATEGIES


def is_range_strategy(name: str) -> bool:
  return str(name or "") in RANGE_STRATEGIES


def bypasses_opposing_structure_gates(
  name: str,
  *,
  full_take_profit_pips: int | float | None = None,
) -> bool:
  """Range/scalp may enter inside HTF opposing only when target room fits.

  Requires a successful native range target (full_take_profit_pips from
  select_range_target / EQ room). Ladder floor is currently 15 pips — a
  fitted 15p target is enough to open. Without a fitted target, opposing
  gates still apply.
  """
  if not is_range_strategy(name):
    return False
  try:
    return full_take_profit_pips is not None and float(full_take_profit_pips) > 0
  except (TypeError, ValueError):
    return False


def canonical_family(name: str) -> str:
  """Classify by exact registered name only — never by substring."""
  key = str(name or "")
  if key in REACTION_STRATEGIES:
    return CANONICAL_FAMILY_REACTION
  if key in ZONE_STRATEGIES:
    return CANONICAL_FAMILY_ZONE
  if key in LIQUIDITY_STRATEGIES:
    return CANONICAL_FAMILY_LIQUIDITY
  if key in RANGE_STRATEGIES:
    return CANONICAL_FAMILY_RANGE
  return CANONICAL_FAMILY_UNKNOWN
