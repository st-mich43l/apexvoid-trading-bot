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

TECHNIQUE_STRATEGIES = frozenset({
  "Supply Demand",
  "Order Block",
  "FVG",
  "iFVG",
  "CRT",
})

CONFLUENCE_STRATEGIES = frozenset({
  "Confluence Zone",
})

# Union for convenience — technique + confluence are zone-family publishers.
ZONE_STRATEGIES = frozenset({
  *ZONE_STRATEGIES,
  *TECHNIQUE_STRATEGIES,
  *CONFLUENCE_STRATEGIES,
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

HFS_STRATEGIES = frozenset({
  "HFS Range Sweep",
  "HFS Impulse Pullback",
  "HFS Breakout Retest",
  "HFS Momentum Chase",
})

CANONICAL_FAMILY_REACTION = "reaction"
CANONICAL_FAMILY_ZONE = "zone"
CANONICAL_FAMILY_LIQUIDITY = "liquidity"
CANONICAL_FAMILY_RANGE = "range"
CANONICAL_FAMILY_HFS = "hfs"
CANONICAL_FAMILY_UNKNOWN = "unknown"

_SCALP_FAMILIES = frozenset({"hfs", "range", "range_reversion"})
_SCALP_MODES = frozenset({"hfs_scalp", "range_scalp", "auto_box_scalp"})


def is_reaction_strategy(name: str) -> bool:
  return str(name or "") in REACTION_STRATEGIES


def is_zone_strategy(name: str) -> bool:
  return str(name or "") in ZONE_STRATEGIES


def is_technique_strategy(name: str) -> bool:
  return str(name or "") in TECHNIQUE_STRATEGIES


def is_confluence_strategy(name: str) -> bool:
  return str(name or "") in CONFLUENCE_STRATEGIES


def is_technique_or_confluence(name: str) -> bool:
  key = str(name or "")
  return key in TECHNIQUE_STRATEGIES or key in CONFLUENCE_STRATEGIES


def is_liquidity_strategy(name: str) -> bool:
  return str(name or "") in LIQUIDITY_STRATEGIES


def is_range_strategy(name: str) -> bool:
  return str(name or "") in RANGE_STRATEGIES


def is_hfs_strategy(name: str) -> bool:
  key = str(name or "")
  return key in HFS_STRATEGIES or key.startswith("HFS ")


def is_scalp_strategy(
  name: str,
  *,
  family: str | None = None,
  strategy_mode: str | None = None,
) -> bool:
  """Range Box / Range Edge / HFS — own native room, not HTF opposing."""
  if is_range_strategy(name) or is_hfs_strategy(name):
    return True
  if str(family or "").casefold() in _SCALP_FAMILIES:
    return True
  if str(strategy_mode or "").casefold() in _SCALP_MODES:
    return True
  return False


def bypasses_opposing_structure_gates(
  name: str,
  *,
  full_take_profit_pips: int | float | None = None,
  family: str | None = None,
  strategy_mode: str | None = None,
) -> bool:
  """Scalp may enter inside HTF opposing only when native target room fits.

  Covers Range Box / Range Edge / HFS. Requires a fitted room evidence
  (``full_take_profit_pips`` from select_range_target / HFS expected target).
  Raw reaction ladders alone do not unlock this — without a fitted target,
  opposing gates still apply.
  """
  if not is_scalp_strategy(
    name, family=family, strategy_mode=strategy_mode,
  ):
    return False
  try:
    return full_take_profit_pips is not None and float(full_take_profit_pips) > 0
  except (TypeError, ValueError):
    return False


def match_bypasses_opposing_structure(match: object) -> bool:
  """Read StrategyMatch / PrivatePolicySubject fields for scalp opposing skip."""
  return bypasses_opposing_structure_gates(
    str(getattr(match, "strategy", "") or ""),
    full_take_profit_pips=getattr(match, "full_take_profit_pips", None),
    family=getattr(match, "family", None),
    strategy_mode=getattr(match, "strategy_mode", None),
  )


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
  if is_hfs_strategy(key):
    return CANONICAL_FAMILY_HFS
  return CANONICAL_FAMILY_UNKNOWN
