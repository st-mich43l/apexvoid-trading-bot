"""Per-strategy behavior flags — exact names only, no substring matching.

Each set below lists strategies that individually opt into one behavior in
``strategy_catalog``. A set is a convenience for asking "does this strategy
opt in?"; it is not a strategy family and it never supplies a strategy's
technical rules. The legacy plan/event label is a record label and is
deliberately not an input here.
"""

from __future__ import annotations

from typing import Any

from app.autotrade.strategy_catalog import names_with

REACTION_STRATEGIES = names_with("reaction")
ZONE_STRATEGIES = names_with("zone")
TECHNIQUE_STRATEGIES = frozenset(
  name for name in names_with("is_technique") if name not in names_with("confluence")
)
CONFLUENCE_STRATEGIES = names_with("confluence")
RANGE_STRATEGIES = names_with("range_lane")
M1_SCALP_STRATEGIES = names_with("m1_scalp")
BREAKOUT_RETEST_SCALP_STRATEGIES = frozenset({"Breakout Retest Scalp"})
# Scalps that enter only while the quote is inside the zone printed on the card.
# Range Edge owns a confirmed zone, so a quote past its edge is a missed entry,
# never a market chase at a price the card does not show (production 2026-10-08:
# card 4,125-4,127, order at 4124.19, stopped out in 8 minutes).
# XAU strategies that retest a level and so scale in like the zone strategies
# (shallow at the near edge, deeper inside) instead of one market order. The
# catalog keeps them market/single for FX, whose entry is one precise price.
# Production 2026-10-08: a Break & Retest zone of 0.23 (4114.91-4115.14) was
# entered as one market order and the card printed "Entry Zone 4,115 - 4,115".
XAU_RETEST_LADDER_STRATEGIES = frozenset({"Break & Retest"})
RETEST_ONLY_SCALP_STRATEGIES = BREAKOUT_RETEST_SCALP_STRATEGIES | frozenset(
  {"Range Edge Scalp"},
)

_SCALP_MODES = frozenset({"scalp_m1", "range_scalp", "auto_box_scalp"})
_M1_SCALP_MODES = frozenset({"scalp_m1"})


def is_reaction_strategy(name: str) -> bool:
  return str(name or "") in REACTION_STRATEGIES


def is_zone_strategy(name: str) -> bool:
  return str(name or "") in ZONE_STRATEGIES


def is_technique_or_confluence(name: str) -> bool:
  key = str(name or "")
  return key in TECHNIQUE_STRATEGIES or key in CONFLUENCE_STRATEGIES


def is_range_strategy(name: str) -> bool:
  return str(name or "") in RANGE_STRATEGIES


def is_m1_scalp_strategy(name: str) -> bool:
  """True for canonical M1 scalping display names."""
  return str(name or "") in M1_SCALP_STRATEGIES


def is_breakout_retest_scalp_strategy(name: str) -> bool:
  """M1 breakout-retest scalps — enter inside the retest band only."""
  return str(name or "") in BREAKOUT_RETEST_SCALP_STRATEGIES


def is_xau_retest_ladder_strategy(name: str) -> bool:
  """Level-retest strategies that use the XAU shallow/deep ladder."""
  return str(name or "") in XAU_RETEST_LADDER_STRATEGIES


def is_retest_only_scalp_strategy(name: str) -> bool:
  """Scalps that never chase: executable only with the quote inside the card zone."""
  return str(name or "") in RETEST_ONLY_SCALP_STRATEGIES


def is_m1_scalp_match(match: Any) -> bool:
  """True when a StrategyMatch belongs to the M1 scalping lane."""
  strategy = str(getattr(match, "strategy", "") or "")
  mode = str(getattr(match, "strategy_mode", "") or "").casefold()
  source = str(getattr(match, "structural_source", "") or "").casefold()
  return (
    is_m1_scalp_strategy(strategy)
    or mode in _M1_SCALP_MODES
    or source == "scalp"
  )


def is_scalp_strategy(
  name: str,
  *,
  strategy_mode: str | None = None,
) -> bool:
  """Range Box / Range Edge / M1 scalp — own native room, not HTF opposing."""
  if is_range_strategy(name) or is_m1_scalp_strategy(name):
    return True
  if str(strategy_mode or "").casefold() in _SCALP_MODES:
    return True
  return False


def bypasses_opposing_structure_gates(
  name: str,
  *,
  full_take_profit_pips: int | float | None = None,
  strategy_mode: str | None = None,
) -> bool:
  """Scalp may ignore HTF map opposing when native room owns the trade."""
  if is_m1_scalp_strategy(name):
    return True
  if str(strategy_mode or "").casefold() in _M1_SCALP_MODES:
    return True
  if not is_scalp_strategy(name, strategy_mode=strategy_mode):
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
    strategy_mode=getattr(match, "strategy_mode", None),
  )
