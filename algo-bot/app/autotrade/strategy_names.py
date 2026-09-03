"""Canonical strategy names and historical aliases.

This module is deliberately independent from detector and configuration
modules.  It is the naming contract shared by taxonomy, persistence, and
detector publishers.
"""

from __future__ import annotations

from dataclasses import dataclass


CANONICAL_FAMILY_REACTION = "reaction"
CANONICAL_FAMILY_ZONE = "zone"
CANONICAL_FAMILY_LIQUIDITY = "liquidity"
CANONICAL_FAMILY_RANGE = "range"
CANONICAL_FAMILY_SCALP = "scalp"
CANONICAL_FAMILY_BREAKOUT_RETEST = "breakout_retest"
CANONICAL_FAMILY_MOMENTUM = "momentum_continuation"
CANONICAL_FAMILY_TREND_PULLBACK = "trend_pullback"
CANONICAL_FAMILY_UNKNOWN = "unknown"

# Detector IDs are kept here as strings to avoid importing the analysis
# module (which would create an analysis/configuration import cycle).
@dataclass(frozen=True)
class StrategyName:
  canonical: str
  family: str
  detector_id: str | None
  aliases: frozenset[str]
  retired: bool = False


def _name(
  canonical: str,
  family: str,
  detector_id: str | None = None,
  *,
  aliases: tuple[str, ...] = (),
  retired: bool = False,
) -> StrategyName:
  return StrategyName(
    canonical=canonical,
    family=family,
    detector_id=detector_id,
    aliases=frozenset(alias.casefold() for alias in aliases),
    retired=retired,
  )


# Canonical display constants used by detector and UI publishers.
KEY_LEVEL_REACTION = "Key Level Reaction"
CONFLUENCE_ZONE = "Confluence Zone"
SUPPLY_DEMAND = "Supply Demand"
ORDER_BLOCK = "Order Block"
FVG = "FVG"
IFVG = "iFVG"
CRT = "CRT"
DEMAND_ZONE_REACTION = "Demand Zone Reaction"
SUPPLY_ZONE_REACTION = "Supply Zone Reaction"
FLIP_ZONE = "Flip Zone"
SESSION_LEVEL_REACTION = "Session Level Reaction"
TRENDLINE_REACTION = "Trendline Reaction"
RANGE_EDGE_SCALP = "Range Edge Scalp"
BOX_BREAKOUT = "Box Breakout"
BREAK_AND_RETEST = "Break & Retest"
TREND_PULLBACK = "Trend Pullback"
MOMENTUM_RIDE = "Momentum Ride"
SNAP_BACK = "Snap-Back"
FADE_SCALP = "Fade Scalp"

ZONE_REACTION = "Zone Reaction"
DEMAND_ZONE = "Demand Zone"
SUPPLY_ZONE = "Supply Zone"
RANGE_BOX_SCALP = "Range Box Scalp"
ONE_SIDED_RANGE_REACTION = "One-Sided Range Reaction"
CHOP_ZONE_REACTION = "Chop Zone Reaction"
LIQUIDITY_SWEEP = "Liquidity Sweep"
BREAKOUT_CONTINUATION = "Breakout Continuation"
MAPPED_ZONE_REACTION = "Mapped Zone Reaction"
RANGE_SWEEP_SCALP = "Range Sweep Scalp"
IMPULSE_PULLBACK_SCALP = "Impulse Pullback Scalp"
BREAKOUT_RETEST_SCALP = "Breakout Retest Scalp"
MOMENTUM_CHASE_SCALP = "Momentum Chase Scalp"
GOLDEN_FIBO = "Golden Fibo"


STRATEGY_NAMES: tuple[StrategyName, ...] = (
  _name(KEY_LEVEL_REACTION, CANONICAL_FAMILY_REACTION, "key_level_reaction", aliases=("key-level", "key level")),
  _name(CONFLUENCE_ZONE, CANONICAL_FAMILY_ZONE, "confluence_zone_reaction", aliases=("confluence", "confulence")),
  _name(SUPPLY_DEMAND, CANONICAL_FAMILY_ZONE, "supply_demand_technique_reaction", aliases=("supply demand reaction", "supply", "demand")),
  _name(ORDER_BLOCK, CANONICAL_FAMILY_ZONE, "order_block_technique_reaction", aliases=("order block reaction", "ob")),
  _name(FVG, CANONICAL_FAMILY_ZONE, "fvg_technique_reaction", aliases=("fvg reaction",)),
  _name(IFVG, CANONICAL_FAMILY_ZONE, "ifvg_technique_reaction", aliases=("ifvg reaction",)),
  _name(CRT, CANONICAL_FAMILY_ZONE, "crt_technique_reaction", aliases=("crt reaction",)),
  _name(DEMAND_ZONE_REACTION, CANONICAL_FAMILY_ZONE, retired=True),
  _name(SUPPLY_ZONE_REACTION, CANONICAL_FAMILY_ZONE, retired=True),
  _name(FLIP_ZONE, CANONICAL_FAMILY_ZONE, "flip_demand_zone_reaction", aliases=("flip-zone",)),
  _name(SESSION_LEVEL_REACTION, CANONICAL_FAMILY_REACTION, "session_level_reaction", aliases=("session level", "session-level")),
  _name(TRENDLINE_REACTION, CANONICAL_FAMILY_REACTION, "trendline_reaction", aliases=("trendline",)),
  _name(RANGE_EDGE_SCALP, CANONICAL_FAMILY_RANGE, "range_edge_scalp"),
  _name(BOX_BREAKOUT, CANONICAL_FAMILY_BREAKOUT_RETEST, "box_breakout"),
  _name(BREAK_AND_RETEST, CANONICAL_FAMILY_BREAKOUT_RETEST, "break_retest"),
  _name(TREND_PULLBACK, CANONICAL_FAMILY_TREND_PULLBACK, retired=True),
  _name(MOMENTUM_RIDE, CANONICAL_FAMILY_MOMENTUM, "momentum_ride"),
  _name(SNAP_BACK, CANONICAL_FAMILY_LIQUIDITY, "snap_back"),
  _name(FADE_SCALP, CANONICAL_FAMILY_RANGE, "fade_scalp"),
  # Legacy plan/report names.  They remain resolvable but are emitted by no
  # current detector, so they must not be mistaken for live sources.
  _name(ZONE_REACTION, CANONICAL_FAMILY_ZONE, retired=True),
  _name(DEMAND_ZONE, CANONICAL_FAMILY_ZONE, retired=True),
  _name(SUPPLY_ZONE, CANONICAL_FAMILY_ZONE, retired=True),
  _name(RANGE_BOX_SCALP, CANONICAL_FAMILY_RANGE, retired=True),
  _name(ONE_SIDED_RANGE_REACTION, CANONICAL_FAMILY_RANGE, retired=True),
  _name(CHOP_ZONE_REACTION, CANONICAL_FAMILY_RANGE, retired=True),
  _name(LIQUIDITY_SWEEP, CANONICAL_FAMILY_LIQUIDITY, retired=True),
  _name(BREAKOUT_CONTINUATION, CANONICAL_FAMILY_MOMENTUM, retired=True),
  _name(MAPPED_ZONE_REACTION, CANONICAL_FAMILY_UNKNOWN, retired=True),
  _name(RANGE_SWEEP_SCALP, CANONICAL_FAMILY_SCALP, aliases=("range sweep", "hfs range sweep")),
  _name(IMPULSE_PULLBACK_SCALP, CANONICAL_FAMILY_SCALP, aliases=("impulse pullback", "hfs impulse pullback")),
  _name(BREAKOUT_RETEST_SCALP, CANONICAL_FAMILY_SCALP, aliases=("breakout retest", "breakout-retest")),
  _name(MOMENTUM_CHASE_SCALP, CANONICAL_FAMILY_SCALP, aliases=("momentum", "hfs momentum chase"), retired=True),
  _name(GOLDEN_FIBO, CANONICAL_FAMILY_UNKNOWN, aliases=("golden-fibo",), retired=True),
)


def _validate() -> None:
  canonicals = [entry.canonical.casefold() for entry in STRATEGY_NAMES]
  if len(canonicals) != len(set(canonicals)):
    raise RuntimeError("strategy canonical names must be unique")
  detector_ids = [entry.detector_id for entry in STRATEGY_NAMES if entry.detector_id]
  if len(detector_ids) != len(set(detector_ids)):
    raise RuntimeError("strategy detector IDs must be unique")
  canonical_set = set(canonicals)
  aliases: dict[str, str] = {}
  for entry in STRATEGY_NAMES:
    for alias in entry.aliases:
      if alias in canonical_set:
        raise RuntimeError(f"strategy alias collides with canonical: {alias!r}")
      previous = aliases.setdefault(alias, entry.canonical)
      if previous != entry.canonical:
        raise RuntimeError(f"strategy alias collides: {alias!r}")


_validate()

BY_CANONICAL: dict[str, StrategyName] = {
  entry.canonical: entry for entry in STRATEGY_NAMES
}
BY_ALIAS: dict[str, StrategyName] = {
  alias: entry
  for entry in STRATEGY_NAMES
  for alias in entry.aliases
}

# Canonical casefold keys are represented in the compatibility map as well.
# They are canonical hits, not aliases, and therefore do not weaken the
# collision invariant above.
SETUP_TYPE_ALIASES: dict[str, str] = {
  **{entry.canonical.casefold(): entry.canonical for entry in STRATEGY_NAMES},
  **{alias: entry.canonical for alias, entry in BY_ALIAS.items()},
}


def resolve_strategy(raw: str | None) -> StrategyName | None:
  if raw is None:
    return None
  key = str(raw).strip().casefold()
  if not key:
    return None
  return next(
    (entry for entry in STRATEGY_NAMES if entry.canonical.casefold() == key),
    None,
  ) or BY_ALIAS.get(key)


def names_for_family(family: str, *, include_retired: bool = True) -> frozenset[str]:
  return frozenset(
    entry.canonical for entry in STRATEGY_NAMES
    if entry.family == family and (include_retired or not entry.retired)
  )


def strategy_for_detector(detector_id: str) -> StrategyName | None:
  legacy_canonical = {
    "demand_zone_reaction": ZONE_REACTION,
    "supply_zone_reaction": ZONE_REACTION,
    "flip_supply_zone_reaction": FLIP_ZONE,
  }.get(detector_id)
  if legacy_canonical is not None:
    return BY_CANONICAL[legacy_canonical]
  return next(
    (entry for entry in STRATEGY_NAMES if entry.detector_id == detector_id),
    None,
  )
