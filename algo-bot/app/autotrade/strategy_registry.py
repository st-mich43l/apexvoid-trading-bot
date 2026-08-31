"""Single frozen strategy table — detector, execution, taxonomy, and enable paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CANONICAL_FAMILY_REACTION = "reaction"
CANONICAL_FAMILY_ZONE = "zone"
CANONICAL_FAMILY_LIQUIDITY = "liquidity"
CANONICAL_FAMILY_RANGE = "range"
CANONICAL_FAMILY_SCALP = "scalp"
CANONICAL_FAMILY_UNKNOWN = "unknown"

ARCHETYPE_REVERSAL = "reversal"
ARCHETYPE_RANGE_REVERSION = "range_reversion"
ARCHETYPE_TREND_PULLBACK = "trend_pullback"
ARCHETYPE_BREAKOUT_RETEST = "breakout_retest"
ARCHETYPE_MOMENTUM = "momentum"
ARCHETYPE_UNKNOWN = "unknown"

ACTIVATION_REACTION = "reaction_reversal"
ACTIVATION_BREAKOUT_RETEST = "breakout_retest"
ACTIVATION_TREND_PULLBACK = "trend_pullback"
ACTIVATION_MOMENTUM = "momentum_continuation"
ACTIVATION_UNKNOWN = "unknown"

FAMILY_EXEC_RANGE_REVERSION = "range_reversion"
FAMILY_EXEC_TREND_PULLBACK = "trend_pullback"
FAMILY_EXEC_BREAKOUT_RETEST = "breakout_retest"
FAMILY_EXEC_MOMENTUM_CONTINUATION = "momentum_continuation"
FAMILY_EXEC_LIQUIDITY_REVERSAL = "liquidity_reversal"
FAMILY_EXEC_MAPPED_ZONE_REACTION = "mapped_zone_reaction"
FAMILY_EXEC_KEY_LEVEL = "key_level"
FAMILY_EXEC_SUPPLY_DEMAND = "supply_demand"
FAMILY_EXEC_SESSION_LEVEL = "session_level"
FAMILY_EXEC_TRENDLINE = "trendline"
FAMILY_UNKNOWN = "unknown"

# Detector-family strings from app.analysis.detectors (avoid circular import).
FAMILY_DETECTOR_KEY_LEVEL = "key_level"
FAMILY_DETECTOR_SUPPLY_DEMAND = "supply_demand"
FAMILY_DETECTOR_SESSION_LEVEL = "session_level"
FAMILY_DETECTOR_TRENDLINE = "trendline"
FAMILY_DETECTOR_RANGE_REVERSION = "range_reversion"
FAMILY_DETECTOR_BREAKOUT_RETEST = "breakout_retest"
FAMILY_DETECTOR_TREND_PULLBACK = "trend_pullback"
FAMILY_DETECTOR_MOMENTUM_CONTINUATION = "momentum_continuation"
FAMILY_DETECTOR_LIQUIDITY_REVERSAL = "liquidity_reversal"

_SCALPING_MODE_SETTING = "strategies.scalping.mode"
_DEFAULT_ENABLE = "runtime.auto_trade.strategy_match_enabled"


@dataclass(frozen=True)
class StrategyRow:
  name: str
  detector_family: str
  execution_family: str
  canonical_family: str
  location_archetype: str
  activation_archetype: str
  enable_setting: str
  m5_authoritative: bool
  is_scalp: bool
  is_technique: bool
  detector_key: str | None = None
  enable_requires_live_mode: bool = False


def _reaction_row(
  name: str,
  *,
  detector_key: str,
  detector_family: str,
  execution_family: str,
  enable_setting: str,
) -> StrategyRow:
  return StrategyRow(
    name=name,
    detector_key=detector_key,
    detector_family=detector_family,
    execution_family=execution_family,
    canonical_family=CANONICAL_FAMILY_REACTION,
    location_archetype=ARCHETYPE_REVERSAL,
    activation_archetype=ACTIVATION_REACTION,
    enable_setting=enable_setting,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
  )


def _zone_row(
  name: str,
  *,
  detector_key: str | None = None,
  enable_setting: str,
  is_technique: bool = False,
) -> StrategyRow:
  return StrategyRow(
    name=name,
    detector_key=detector_key,
    detector_family=FAMILY_DETECTOR_SUPPLY_DEMAND,
    execution_family=FAMILY_EXEC_SUPPLY_DEMAND,
    canonical_family=CANONICAL_FAMILY_ZONE,
    location_archetype=ARCHETYPE_REVERSAL,
    activation_archetype=ACTIVATION_REACTION,
    enable_setting=enable_setting,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=is_technique,
  )


def _range_row(
  name: str,
  *,
  detector_key: str | None = None,
  enable_setting: str = "strategies.range_reversion.enabled",
) -> StrategyRow:
  return StrategyRow(
    name=name,
    detector_key=detector_key,
    detector_family=FAMILY_DETECTOR_RANGE_REVERSION,
    execution_family=FAMILY_EXEC_RANGE_REVERSION,
    canonical_family=CANONICAL_FAMILY_RANGE,
    location_archetype=ARCHETYPE_RANGE_REVERSION,
    activation_archetype=ACTIVATION_REACTION,
    enable_setting=enable_setting,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
  )


def _m1_scalp_row(name: str) -> StrategyRow:
  return StrategyRow(
    name=name,
    detector_family=FAMILY_DETECTOR_RANGE_REVERSION,
    execution_family=FAMILY_EXEC_RANGE_REVERSION,
    canonical_family=CANONICAL_FAMILY_SCALP,
    location_archetype=ARCHETYPE_RANGE_REVERSION,
    activation_archetype=ACTIVATION_REACTION,
    enable_setting=_SCALPING_MODE_SETTING,
    enable_requires_live_mode=True,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
  )


_STRATEGY_ROWS: tuple[StrategyRow, ...] = (
  _reaction_row(
    "Key Level Reaction",
    detector_key="key_level_reaction",
    detector_family=FAMILY_DETECTOR_KEY_LEVEL,
    execution_family=FAMILY_EXEC_KEY_LEVEL,
    enable_setting="strategies.reaction.key_level.enabled",
  ),
  _zone_row(
    "Confluence Zone",
    detector_key="confluence_zone_reaction",
    enable_setting="strategies.technique.confluence.enabled",
    is_technique=True,
  ),
  _zone_row(
    "Supply Demand",
    detector_key="supply_demand_technique_reaction",
    enable_setting="strategies.technique.sd.enabled",
    is_technique=True,
  ),
  _zone_row(
    "Order Block",
    detector_key="order_block_technique_reaction",
    enable_setting="strategies.technique.ob.enabled",
    is_technique=True,
  ),
  _zone_row(
    "FVG",
    detector_key="fvg_technique_reaction",
    enable_setting="strategies.technique.fvg.enabled",
    is_technique=True,
  ),
  _zone_row(
    "iFVG",
    detector_key="ifvg_technique_reaction",
    enable_setting="strategies.technique.ifvg.enabled",
    is_technique=True,
  ),
  _zone_row(
    "CRT",
    detector_key="crt_technique_reaction",
    enable_setting="strategies.technique.crt.enabled",
    is_technique=True,
  ),
  _zone_row(
    "Demand Zone Reaction",
    detector_key="demand_zone_reaction",
    enable_setting="strategies.reaction.demand.enabled",
  ),
  _zone_row(
    "Supply Zone Reaction",
    detector_key="supply_zone_reaction",
    enable_setting="strategies.reaction.supply.enabled",
  ),
  _zone_row(
    "Flip Zone",
    detector_key="flip_demand_zone_reaction",
    enable_setting="strategies.zone.flip.enabled",
  ),
  _reaction_row(
    "Session Level Reaction",
    detector_key="session_level_reaction",
    detector_family=FAMILY_DETECTOR_SESSION_LEVEL,
    execution_family=FAMILY_EXEC_SESSION_LEVEL,
    enable_setting="strategies.reaction.session_level.enabled",
  ),
  _reaction_row(
    "Trendline Reaction",
    detector_key="trendline_reaction",
    detector_family=FAMILY_DETECTOR_TRENDLINE,
    execution_family=FAMILY_EXEC_TRENDLINE,
    enable_setting="strategies.reaction.trendline.enabled",
  ),
  _range_row(
    "Range Edge Scalp",
    detector_key="range_edge_scalp",
    enable_setting="strategies.range_reversion.range_edge.enabled",
  ),
  StrategyRow(
    name="Box Breakout",
    detector_key="box_breakout",
    detector_family=FAMILY_DETECTOR_BREAKOUT_RETEST,
    execution_family=FAMILY_EXEC_BREAKOUT_RETEST,
    canonical_family=CANONICAL_FAMILY_UNKNOWN,
    location_archetype=ARCHETYPE_BREAKOUT_RETEST,
    activation_archetype=ACTIVATION_BREAKOUT_RETEST,
    enable_setting="strategies.selection.box_breakout_enabled",
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
  ),
  StrategyRow(
    name="Break & Retest",
    detector_key="break_retest",
    detector_family=FAMILY_DETECTOR_BREAKOUT_RETEST,
    execution_family=FAMILY_EXEC_BREAKOUT_RETEST,
    canonical_family=CANONICAL_FAMILY_UNKNOWN,
    location_archetype=ARCHETYPE_BREAKOUT_RETEST,
    activation_archetype=ACTIVATION_BREAKOUT_RETEST,
    enable_setting="strategies.breakout.break_retest_enabled",
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
  ),
  StrategyRow(
    name="Trend Pullback",
    detector_key="trend_pullback",
    detector_family=FAMILY_DETECTOR_TREND_PULLBACK,
    execution_family=FAMILY_EXEC_TREND_PULLBACK,
    canonical_family=CANONICAL_FAMILY_UNKNOWN,
    location_archetype=ARCHETYPE_TREND_PULLBACK,
    activation_archetype=ACTIVATION_TREND_PULLBACK,
    enable_setting="strategies.trend.pullback_enabled",
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
  ),
  StrategyRow(
    name="Momentum Ride",
    detector_key="momentum_ride",
    detector_family=FAMILY_DETECTOR_MOMENTUM_CONTINUATION,
    execution_family=FAMILY_EXEC_MOMENTUM_CONTINUATION,
    canonical_family=CANONICAL_FAMILY_UNKNOWN,
    location_archetype=ARCHETYPE_MOMENTUM,
    activation_archetype=ACTIVATION_MOMENTUM,
    enable_setting="strategies.selection.momentum_ride_enabled",
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
  ),
  StrategyRow(
    name="Snap-Back",
    detector_key="snap_back",
    detector_family=FAMILY_DETECTOR_LIQUIDITY_REVERSAL,
    execution_family=FAMILY_EXEC_LIQUIDITY_REVERSAL,
    canonical_family=CANONICAL_FAMILY_LIQUIDITY,
    location_archetype=ARCHETYPE_REVERSAL,
    activation_archetype=ACTIVATION_REACTION,
    enable_setting="strategies.selection.snap_back_enabled",
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
  ),
  _range_row(
    "Fade Scalp",
    detector_key="fade_scalp",
    enable_setting="strategies.scalp.fade_scalp_enabled",
  ),
  # Legacy / open-plan labels (no live detector key).
  _zone_row("Zone Reaction", enable_setting="strategies.reaction.enabled"),
  _zone_row("Demand Zone", enable_setting="strategies.zone.demand.enabled"),
  _zone_row("Supply Zone", enable_setting="strategies.zone.supply.enabled"),
  _range_row("Range Box Scalp"),
  _range_row("One-Sided Range Reaction"),
  _range_row("Chop Zone Reaction"),
  StrategyRow(
    name="Liquidity Sweep",
    detector_family=FAMILY_DETECTOR_LIQUIDITY_REVERSAL,
    execution_family=FAMILY_EXEC_LIQUIDITY_REVERSAL,
    canonical_family=CANONICAL_FAMILY_LIQUIDITY,
    location_archetype=ARCHETYPE_REVERSAL,
    activation_archetype=ACTIVATION_REACTION,
    enable_setting="strategies.reaction.liquidity_reversal.enabled",
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
  ),
  StrategyRow(
    name="Breakout Continuation",
    detector_family=FAMILY_DETECTOR_MOMENTUM_CONTINUATION,
    execution_family=FAMILY_EXEC_MOMENTUM_CONTINUATION,
    canonical_family=CANONICAL_FAMILY_UNKNOWN,
    location_archetype=ARCHETYPE_MOMENTUM,
    activation_archetype=ACTIVATION_MOMENTUM,
    enable_setting="strategies.selection.momentum_ride_enabled",
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
  ),
  StrategyRow(
    name="Mapped Zone Reaction",
    detector_family=FAMILY_DETECTOR_SUPPLY_DEMAND,
    execution_family=FAMILY_EXEC_MAPPED_ZONE_REACTION,
    canonical_family=CANONICAL_FAMILY_ZONE,
    location_archetype=ARCHETYPE_REVERSAL,
    activation_archetype=ACTIVATION_REACTION,
    enable_setting="strategies.mapped_zone.enabled",
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
  ),
  _m1_scalp_row("Range Sweep Scalp"),
  _m1_scalp_row("Impulse Pullback Scalp"),
  _m1_scalp_row("Breakout Retest Scalp"),
  _m1_scalp_row("Momentum Chase Scalp"),
)

# flip_supply shares the Flip Zone row (second detector registry entry).
_FLIP_ZONE_ROW = next(row for row in _STRATEGY_ROWS if row.name == "Flip Zone")

STRATEGY_BY_NAME: dict[str, StrategyRow] = {}
for _row in _STRATEGY_ROWS:
  STRATEGY_BY_NAME.setdefault(_row.name, _row)

STRATEGY_BY_DETECTOR_KEY: dict[str, StrategyRow] = {}
for _row in _STRATEGY_ROWS:
  if _row.detector_key:
    STRATEGY_BY_DETECTOR_KEY[_row.detector_key] = _row
STRATEGY_BY_DETECTOR_KEY["flip_supply_zone_reaction"] = _FLIP_ZONE_ROW


def lookup_row(strategy: str) -> StrategyRow | None:
  key = str(strategy or "").strip()
  if not key:
    return None
  return STRATEGY_BY_NAME.get(key)


def strategy_family(strategy: str) -> str:
  row = lookup_row(strategy)
  if row is not None:
    return row.execution_family
  return FAMILY_UNKNOWN


def canonical_family(name: str) -> str:
  row = lookup_row(name)
  if row is not None:
    return row.canonical_family
  return CANONICAL_FAMILY_UNKNOWN


def location_archetype(strategy: str) -> str:
  row = lookup_row(strategy)
  if row is not None:
    return row.location_archetype
  return ARCHETYPE_UNKNOWN


def activation_archetype(strategy: str) -> str:
  row = lookup_row(strategy)
  if row is not None:
    return row.activation_archetype
  return ACTIVATION_UNKNOWN


def _resolve_dotted_path(root: Any, path: str) -> Any:
  obj = root
  for part in path.split("."):
    if not hasattr(obj, part):
      raise AttributeError(f"enable_setting path {path!r} missing segment {part!r}")
    obj = getattr(obj, part)
  return obj


def resolve_enable_setting(path: str, cfg: Any) -> Any:
  """Traverse ``path`` on ``cfg``; raises if any segment is missing."""
  return _resolve_dotted_path(cfg, path)


def resolve_strategy_enabled(row: StrategyRow, cfg: Any) -> bool:
  value = resolve_enable_setting(row.enable_setting, cfg)
  if row.enable_requires_live_mode:
    return str(value or "").casefold() == "live"
  return bool(value)


def strategy_mode_enabled(strategy: str, cfg: Any) -> bool:
  row = lookup_row(strategy)
  if row is None:
    return bool(_resolve_dotted_path(cfg, _DEFAULT_ENABLE))
  return resolve_strategy_enabled(row, cfg)


def _assert_registry_complete() -> None:
  from app.analysis.detectors import LIVE_DETECTOR_REGISTRY
  from app.core.config import runtime_config

  missing = [
    registration.name
    for registration in LIVE_DETECTOR_REGISTRY
    if registration.name not in STRATEGY_BY_DETECTOR_KEY
  ]
  if missing:
    raise RuntimeError(
      "LIVE_DETECTOR_REGISTRY entries missing strategy_registry rows: "
      + ", ".join(sorted(missing))
    )

  for row in _STRATEGY_ROWS:
    resolve_enable_setting(row.enable_setting, runtime_config)


_assert_registry_complete()
