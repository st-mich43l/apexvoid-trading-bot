"""Canonical strategy names and historical aliases.

This module is deliberately independent from detector and configuration
modules.  It is the naming contract shared by taxonomy, persistence, and
detector publishers.
"""

from __future__ import annotations

from dataclasses import dataclass


# Detector IDs are kept here as strings to avoid importing the analysis
# module (which would create an analysis/configuration import cycle).
@dataclass(frozen=True)
class StrategyName:
  canonical: str
  detector_id: str | None
  aliases: frozenset[str]
  retired: bool = False


def _name(
  canonical: str,
  detector_id: str | None = None,
  *,
  aliases: tuple[str, ...] = (),
  retired: bool = False,
) -> StrategyName:
  return StrategyName(
    canonical=canonical,
    detector_id=detector_id,
    aliases=frozenset(alias.casefold() for alias in aliases),
    retired=retired,
  )


# Canonical display constants used by detector and UI publishers.
KEY_LEVEL = "Key Level"
CONFLUENCE_ZONE = "Confluence Zone"
SUPPLY_DEMAND = "Supply Demand"
ORDER_BLOCK = "Order Block"
FVG = "FVG"
IFVG = "iFVG"
CRT = "CRT"
DEMAND_ZONE_REACTION = "Demand Zone Reaction"
SUPPLY_ZONE_REACTION = "Supply Zone Reaction"
FLIP_ZONE = "Flip Zone"
SESSION_LEVEL = "Session Level"
TRENDLINE = "Trendline"
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
  _name(KEY_LEVEL, "key_level_reaction", aliases=("key-level", "key level reaction")),
  _name(CONFLUENCE_ZONE, "confluence_zone_reaction", aliases=("confluence", "confulence")),
  _name(SUPPLY_DEMAND, "supply_demand_technique_reaction", aliases=("supply demand reaction", "supply", "demand")),
  _name(ORDER_BLOCK, "order_block_technique_reaction", aliases=("order block reaction", "ob")),
  _name(FVG, "fvg_technique_reaction", aliases=("fvg reaction",)),
  _name(IFVG, "ifvg_technique_reaction", aliases=("ifvg reaction",)),
  _name(CRT, "crt_technique_reaction", aliases=("crt reaction",)),
  _name(DEMAND_ZONE_REACTION, retired=True),
  _name(SUPPLY_ZONE_REACTION, retired=True),
  _name(FLIP_ZONE, "flip_demand_zone_reaction", aliases=("flip-zone",)),
  _name(SESSION_LEVEL, "session_level_reaction", aliases=("session-level", "session level reaction")),
  _name(TRENDLINE, "trendline_reaction", aliases=("trendline reaction",)),
  _name(RANGE_EDGE_SCALP, "range_edge_scalp"),
  _name(BOX_BREAKOUT, "box_breakout"),
  # Independent M5 structural thesis restored by the Go analysis engine.
  # Box Breakout and Breakout Retest Scalp remain separate strategies.
  _name(BREAK_AND_RETEST),
  _name(TREND_PULLBACK, retired=True),
  _name(MOMENTUM_RIDE, "momentum_ride"),
  _name(SNAP_BACK, "snap_back"),
  # Retired automatic thesis: Range Edge and Snap-Back own its former range
  # reversion roles without a second detector competing for the same move.
  _name(FADE_SCALP, retired=True),
  # Legacy plan/report names.  They remain resolvable but are emitted by no
  # current detector, so they must not be mistaken for live sources.
  _name(ZONE_REACTION, retired=True),
  _name(DEMAND_ZONE, retired=True),
  _name(SUPPLY_ZONE, retired=True),
  _name(RANGE_BOX_SCALP, retired=True),
  _name(ONE_SIDED_RANGE_REACTION, retired=True),
  _name(CHOP_ZONE_REACTION, retired=True),
  # No longer retired: the reviewed Go liquidity_sweep adapter
  # (go_opportunity_policy.REVIEWED_SCOPES) now publishes live matches under
  # exactly this display name, distinct from the still-live Python "Fade
  # Scalp" M1 technique below (a separate strategy with its own execution
  # profile).
  _name(LIQUIDITY_SWEEP, "go:liquidity_sweep"),
  _name(BREAKOUT_CONTINUATION, retired=True),
  _name(MAPPED_ZONE_REACTION, retired=True),
  _name(RANGE_SWEEP_SCALP, aliases=("range sweep", "hfs range sweep")),
  _name(IMPULSE_PULLBACK_SCALP, aliases=("impulse pullback", "hfs impulse pullback")),
  _name(BREAKOUT_RETEST_SCALP, aliases=("breakout retest", "breakout-retest")),
  _name(MOMENTUM_CHASE_SCALP, aliases=("momentum", "hfs momentum chase"), retired=True),
  _name(GOLDEN_FIBO, aliases=("golden-fibo",), retired=True),
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

