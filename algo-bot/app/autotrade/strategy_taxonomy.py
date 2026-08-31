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

# Canonical M1 scalp display names (no "HFS" product tag).
SCALP_M1_STRATEGIES = frozenset({
  "Range Sweep Scalp",
  "Impulse Pullback Scalp",
  "Breakout Retest Scalp",
  "Momentum Chase Scalp",
})

# M1 scalp set including legacy ``HFS *`` open-plan / historical labels.
M1_SCALP_STRATEGIES = frozenset({
  *SCALP_M1_STRATEGIES,
  "HFS Range Sweep",
  "HFS Impulse Pullback",
  "HFS Breakout Retest",
  "HFS Momentum Chase",
})

BREAKOUT_RETEST_SCALP_STRATEGIES = frozenset({
  "Breakout Retest Scalp",
  "HFS Breakout Retest",
})
# Back-compat alias — prefer M1_SCALP_STRATEGIES / is_m1_scalp_strategy.
HFS_STRATEGIES = M1_SCALP_STRATEGIES

CANONICAL_FAMILY_REACTION = "reaction"
CANONICAL_FAMILY_ZONE = "zone"
CANONICAL_FAMILY_LIQUIDITY = "liquidity"
CANONICAL_FAMILY_RANGE = "range"
CANONICAL_FAMILY_SCALP = "scalp"
CANONICAL_FAMILY_HFS = "hfs"  # legacy family stamp on open plans
CANONICAL_FAMILY_SCALP_LEGACY_HFS = CANONICAL_FAMILY_HFS
CANONICAL_FAMILY_UNKNOWN = "unknown"

_SCALP_FAMILIES = frozenset({"scalp", "hfs", "range", "range_reversion"})
_SCALP_MODES = frozenset({
  "scalp_m1", "hfs_scalp", "range_scalp", "auto_box_scalp",
})
_M1_SCALP_MODES = frozenset({"scalp_m1", "hfs_scalp"})
_M1_SCALP_FAMILIES = frozenset({CANONICAL_FAMILY_SCALP, CANONICAL_FAMILY_SCALP_LEGACY_HFS})


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


def is_m1_scalp_strategy(name: str) -> bool:
  """True for M1 scalp archetypes (canonical + legacy ``HFS *`` labels)."""
  key = str(name or "")
  return key in M1_SCALP_STRATEGIES or key.startswith("HFS ")


def is_breakout_retest_scalp_strategy(name: str) -> bool:
  """M1 breakout-retest scalps — enter inside the retest band only."""
  return str(name or "") in BREAKOUT_RETEST_SCALP_STRATEGIES


# Back-compat — prefer is_m1_scalp_strategy.
is_hfs_strategy = is_m1_scalp_strategy


def is_scalp_strategy(
  name: str,
  *,
  family: str | None = None,
  strategy_mode: str | None = None,
) -> bool:
  """Range Box / Range Edge / M1 scalp — own native room, not HTF opposing."""
  if is_range_strategy(name) or is_m1_scalp_strategy(name):
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
  """Scalp may ignore HTF map opposing when native room owns the trade.

  M1 scalp always bypasses — its episode already sized stop/target; map
  ``actionable_entries`` / HTF zones must not silence the scalp loop.

  Range Box / Range Edge still require fitted ``full_take_profit_pips``
  (select_range_target / configured floor). Raw reaction ladders alone
  do not unlock the bypass.
  """
  if is_m1_scalp_strategy(name):
    return True
  if str(strategy_mode or "").casefold() in _M1_SCALP_MODES:
    return True
  if str(family or "").casefold() in _M1_SCALP_FAMILIES:
    return True
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
  from app.autotrade.strategy_registry import canonical_family as registry_canonical

  return registry_canonical(name)
