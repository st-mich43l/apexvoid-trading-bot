"""Explicit strategy-family registry — exact names only, no substring matching."""

from __future__ import annotations

from typing import Any

from app.autotrade.strategy_names import (
  CANONICAL_FAMILY_BREAKOUT_RETEST,
  CANONICAL_FAMILY_LIQUIDITY,
  CANONICAL_FAMILY_MOMENTUM,
  CANONICAL_FAMILY_RANGE,
  CANONICAL_FAMILY_REACTION,
  CANONICAL_FAMILY_SCALP,
  CANONICAL_FAMILY_TREND_PULLBACK,
  CANONICAL_FAMILY_UNKNOWN,
  CANONICAL_FAMILY_ZONE,
  BREAKOUT_RETEST_SCALP,
  STRATEGY_NAMES,
  names_for_family,
)

REACTION_STRATEGIES = names_for_family(CANONICAL_FAMILY_REACTION)
ZONE_STRATEGIES = names_for_family(CANONICAL_FAMILY_ZONE)
TECHNIQUE_STRATEGIES = frozenset(
  entry.canonical for entry in STRATEGY_NAMES
  if entry.detector_id and entry.detector_id.endswith("_technique_reaction")
)
CONFLUENCE_STRATEGIES = frozenset({
  entry.canonical for entry in STRATEGY_NAMES
  if entry.detector_id == "confluence_zone_reaction"
})
LIQUIDITY_STRATEGIES = names_for_family(CANONICAL_FAMILY_LIQUIDITY)
RANGE_STRATEGIES = names_for_family(CANONICAL_FAMILY_RANGE)
M1_SCALP_STRATEGIES = names_for_family(CANONICAL_FAMILY_SCALP)
BREAKOUT_RETEST_SCALP_STRATEGIES = frozenset({BREAKOUT_RETEST_SCALP})

_SCALP_FAMILIES = frozenset({"scalp", "range", "range_reversion"})
_SCALP_MODES = frozenset({"scalp_m1", "range_scalp", "auto_box_scalp"})
_M1_SCALP_MODES = frozenset({"scalp_m1"})
_M1_SCALP_FAMILIES = frozenset({CANONICAL_FAMILY_SCALP})


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
  """True for canonical M1 scalping display names."""
  return str(name or "") in M1_SCALP_STRATEGIES


def is_breakout_retest_scalp_strategy(name: str) -> bool:
  """M1 breakout-retest scalps — enter inside the retest band only."""
  return str(name or "") in BREAKOUT_RETEST_SCALP_STRATEGIES


def is_m1_scalp_match(match: Any) -> bool:
  """True when a StrategyMatch belongs to the M1 scalping lane."""
  strategy = str(getattr(match, "strategy", "") or "")
  family = str(getattr(match, "family", "") or "").casefold()
  mode = str(getattr(match, "strategy_mode", "") or "").casefold()
  source = str(getattr(match, "structural_source", "") or "").casefold()
  return (
    is_m1_scalp_strategy(strategy)
    or family in _M1_SCALP_FAMILIES
    or mode in _M1_SCALP_MODES
    or source == "scalp"
  )


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
  """Scalp may ignore HTF map opposing when native room owns the trade."""
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
