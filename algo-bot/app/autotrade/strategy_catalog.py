"""The strategy catalog: one explicit row per strategy.

There is no strategy family. Every strategy below owns, as literal values, the
execution policy it is admitted under, the behaviors it opts into, and the
configuration switch that enables it. Two strategies may carry the same number
today; changing one never changes the other, and nothing in the code resolves
a strategy's behavior through a group it belongs to.

Shared mathematical and execution primitives (zone clipping, ladder maths,
stop envelopes by instrument) live elsewhere and are called *by* strategies;
they do not decide for them.

legacy_family_label is the historical strategy_family string that
TradePlan V8 and persisted events carry. It is a record-compatibility label,
read only where a plan or event is serialised (go_opportunity_policy
writes it; the C# executor echoes it). No trading decision may read it —
test_strategy_independence enforces that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.autotrade.strategy_names import resolve_strategy

_SCALPING_MODE_SETTING = "auto_algo.strategies.scalping.mode"
_DEFAULT_ENABLE = "runtime.auto_trade.strategy_match_enabled"


@dataclass(frozen=True)
class ExecutionProfile:
  """The execution policy one strategy is admitted under."""

  min_confluence: int
  max_entry_drift_atr: float
  max_entry_drift_pips: float
  max_zone_width_atr: float
  min_target_room_atr: float
  min_reward_risk: float
  risk_multiplier: float
  order_type_preference: str  # limit | market | either
  permitted_regimes: tuple[str, ...]
  entry_distribution: str = "either"  # single | zone_scale | either
  # Which instrument execution section (execution.range / .trend /
  # .mapped_zone) supplies this strategy's drift and minimum-pip settings;
  # None when the strategy reads none of them.
  drift_section: str | None = None
  hard_drift_default_pips: float | None = None
  # This strategy's minimum reward/risk comes from execution.range.min_rr.
  min_rr_from_range_config: bool = False
  # This strategy's risk multiplier ceiling comes from sizing.range_max_risk_multiplier.
  range_risk_ceiling: bool = False


@dataclass(frozen=True)
class StrategyProfile:
  name: str
  legacy_family_label: str
  detector_key: str | None
  enable_setting: str
  enable_requires_live_mode: bool
  m5_authoritative: bool
  is_scalp: bool
  is_technique: bool
  # Behaviors this strategy opts into, each its own flag.
  reaction: bool  # Key Level / Session Level / Trendline: market-with-limit-scale routing
  zone: bool
  range_lane: bool
  m1_scalp: bool  # own native room: HTF opposing structure does not gate it
  confluence: bool
  # Legacy Python-detector confirmation contract this strategy was published
  # under: zone_reaction | level_reaction | continuation | None (M1 required).
  # Go-origin matches never read it - Go owns every confirmation.
  confirmation: str | None
  execution: ExecutionProfile


STRATEGY_PROFILES: tuple[StrategyProfile, ...] = (
  StrategyProfile(
    name='Key Level',
    legacy_family_label='key_level',
    detector_key='key_level_reaction',
    enable_setting='auto_algo.strategies.reaction.key_level.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=True,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='level_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=1.5, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Confluence Zone',
    legacy_family_label='supply_demand',
    detector_key='confluence_zone_reaction',
    enable_setting='auto_algo.strategies.technique.confluence.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=True,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=True,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Supply Demand',
    legacy_family_label='supply_demand',
    detector_key='supply_demand_technique_reaction',
    enable_setting='auto_algo.strategies.technique.sd.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=True,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Order Block',
    legacy_family_label='supply_demand',
    detector_key='order_block_technique_reaction',
    enable_setting='auto_algo.strategies.technique.ob.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=True,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='FVG',
    legacy_family_label='supply_demand',
    detector_key='fvg_technique_reaction',
    enable_setting='auto_algo.strategies.technique.fvg.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=True,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='iFVG',
    legacy_family_label='supply_demand',
    detector_key='ifvg_technique_reaction',
    enable_setting='auto_algo.strategies.technique.ifvg.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=True,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='CRT',
    legacy_family_label='supply_demand',
    detector_key='crt_technique_reaction',
    enable_setting='auto_algo.strategies.technique.crt.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=True,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Demand Zone Reaction',
    legacy_family_label='supply_demand',
    detector_key='demand_zone_reaction',
    enable_setting='auto_algo.strategies.reaction.demand.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Supply Zone Reaction',
    legacy_family_label='supply_demand',
    detector_key='supply_zone_reaction',
    enable_setting='auto_algo.strategies.reaction.supply.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Flip Zone',
    legacy_family_label='supply_demand',
    detector_key='flip_demand_zone_reaction',
    enable_setting='auto_algo.strategies.zone.flip.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Session Level',
    legacy_family_label='session_level',
    detector_key='session_level_reaction',
    enable_setting='auto_algo.strategies.reaction.session_level.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=True,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='level_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=1.5, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Trendline',
    legacy_family_label='trendline',
    detector_key='trendline_reaction',
    enable_setting='auto_algo.strategies.reaction.trendline.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=True,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='level_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.55, max_entry_drift_pips=14.0,
      max_zone_width_atr=1.5, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='trend', hard_drift_default_pips=30.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Range Edge Scalp',
    legacy_family_label='range_reversion',
    detector_key='range_edge_scalp',
    enable_setting='auto_algo.strategies.range_reversion.range_edge.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=True,
    m1_scalp=False,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
  StrategyProfile(
    name='Box Breakout',
    legacy_family_label='breakout_retest',
    detector_key='box_breakout',
    enable_setting='auto_algo.strategies.selection.box_breakout_enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.85, max_entry_drift_pips=18.0,
      max_zone_width_atr=2.5, min_target_room_atr=0.7, min_reward_risk=1.2,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('trend', 'breakout', 'unknown'),
      entry_distribution='either', drift_section='trend', hard_drift_default_pips=30.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Break & Retest',
    legacy_family_label='breakout_retest',
    detector_key='break_retest',
    enable_setting='auto_algo.strategies.breakout.break_retest_enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.85, max_entry_drift_pips=18.0,
      max_zone_width_atr=2.5, min_target_room_atr=0.7, min_reward_risk=1.2,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('trend', 'breakout', 'unknown'),
      entry_distribution='either', drift_section='trend', hard_drift_default_pips=30.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Trend Pullback',
    legacy_family_label='trend_pullback',
    detector_key=None,
    enable_setting='auto_algo.strategies.trend.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='level_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.75, max_entry_drift_pips=15.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.6, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('trend', 'breakout', 'unknown'),
      entry_distribution='either', drift_section='trend', hard_drift_default_pips=30.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Momentum Ride',
    legacy_family_label='momentum_continuation',
    detector_key='momentum_ride',
    enable_setting='auto_algo.strategies.selection.momentum_ride_enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='continuation',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=1.0, max_entry_drift_pips=20.0,
      max_zone_width_atr=3.0, min_target_room_atr=0.8, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('trend', 'breakout', 'unknown'),
      entry_distribution='either', drift_section='trend', hard_drift_default_pips=30.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Snap-Back',
    legacy_family_label='liquidity_reversal',
    detector_key='snap_back',
    enable_setting='auto_algo.strategies.selection.snap_back_enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='level_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.45, max_entry_drift_pips=10.0,
      max_zone_width_atr=1.5, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'trend', 'unknown'),
      entry_distribution='either', drift_section=None, hard_drift_default_pips=None,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Fade Scalp',
    legacy_family_label='range_reversion',
    detector_key='fade_scalp',
    enable_setting='auto_algo.strategies.scalp.fade_scalp_enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=True,
    m1_scalp=False,
    confluence=False,
    confirmation='level_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
  StrategyProfile(
    name='Zone Reaction',
    legacy_family_label='supply_demand',
    detector_key=None,
    enable_setting='auto_algo.strategies.reaction.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Demand Zone',
    legacy_family_label='supply_demand',
    detector_key=None,
    enable_setting='auto_algo.strategies.zone.demand.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Supply Zone',
    legacy_family_label='supply_demand',
    detector_key=None,
    enable_setting='auto_algo.strategies.zone.supply.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=True,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='zone_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.5, max_entry_drift_pips=12.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='limit', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='zone_scale', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Range Box Scalp',
    legacy_family_label='range_reversion',
    detector_key=None,
    enable_setting='auto_algo.strategies.range_reversion.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=True,
    m1_scalp=False,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
  StrategyProfile(
    name='One-Sided Range Reaction',
    legacy_family_label='range_reversion',
    detector_key=None,
    enable_setting='auto_algo.strategies.range_reversion.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=True,
    m1_scalp=False,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
  StrategyProfile(
    name='Chop Zone Reaction',
    legacy_family_label='range_reversion',
    detector_key=None,
    enable_setting='auto_algo.strategies.range_reversion.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=True,
    m1_scalp=False,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
  StrategyProfile(
    name='Liquidity Sweep',
    legacy_family_label='liquidity_reversal',
    detector_key=None,
    enable_setting='auto_algo.strategies.reaction.liquidity_reversal.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=False,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='level_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.45, max_entry_drift_pips=10.0,
      max_zone_width_atr=1.5, min_target_room_atr=0.55, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'trend', 'unknown'),
      entry_distribution='either', drift_section=None, hard_drift_default_pips=None,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Breakout Continuation',
    legacy_family_label='momentum_continuation',
    detector_key=None,
    enable_setting='auto_algo.strategies.selection.momentum_ride_enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='continuation',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=1.0, max_entry_drift_pips=20.0,
      max_zone_width_atr=3.0, min_target_room_atr=0.8, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('trend', 'breakout', 'unknown'),
      entry_distribution='either', drift_section='trend', hard_drift_default_pips=30.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Mapped Zone Reaction',
    legacy_family_label='mapped_zone_reaction',
    detector_key=None,
    enable_setting='auto_algo.strategies.mapped_zone.enabled',
    enable_requires_live_mode=False,
    m5_authoritative=True,
    is_scalp=False,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=False,
    confluence=False,
    confirmation='level_reaction',
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.4, max_entry_drift_pips=10.0,
      max_zone_width_atr=2.0, min_target_room_atr=0.6, min_reward_risk=1.15,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'trend', 'breakout', 'unknown'),
      entry_distribution='either', drift_section='mapped_zone', hard_drift_default_pips=20.0,
      min_rr_from_range_config=False, range_risk_ceiling=False,
    ),
  ),
  StrategyProfile(
    name='Range Sweep Scalp',
    legacy_family_label='range_reversion',
    detector_key=None,
    enable_setting='auto_algo.strategies.scalping.mode',
    enable_requires_live_mode=True,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=True,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
  StrategyProfile(
    name='Impulse Pullback Scalp',
    legacy_family_label='range_reversion',
    detector_key=None,
    enable_setting='auto_algo.strategies.scalping.mode',
    enable_requires_live_mode=True,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=True,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
  StrategyProfile(
    name='Breakout Retest Scalp',
    legacy_family_label='range_reversion',
    detector_key=None,
    enable_setting='auto_algo.strategies.scalping.mode',
    enable_requires_live_mode=True,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=True,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
  StrategyProfile(
    name='Momentum Chase Scalp',
    legacy_family_label='range_reversion',
    detector_key=None,
    enable_setting='auto_algo.strategies.scalping.mode',
    enable_requires_live_mode=True,
    m5_authoritative=False,
    is_scalp=True,
    is_technique=False,
    reaction=False,
    zone=False,
    range_lane=False,
    m1_scalp=True,
    confluence=False,
    confirmation=None,
    execution=ExecutionProfile(
      min_confluence=2, max_entry_drift_atr=0.35, max_entry_drift_pips=8.0,
      max_zone_width_atr=1.0, min_target_room_atr=0.5, min_reward_risk=1.1,
      risk_multiplier=1.0, order_type_preference='market', permitted_regimes=('chop', 'range', 'unknown'),
      entry_distribution='either', drift_section='range', hard_drift_default_pips=20.0,
      min_rr_from_range_config=True, range_risk_ceiling=True,
    ),
  ),
)

STRATEGY_BY_NAME: dict[str, StrategyProfile] = {}
for _profile in STRATEGY_PROFILES:
  STRATEGY_BY_NAME.setdefault(_profile.name, _profile)

STRATEGY_BY_DETECTOR_KEY: dict[str, StrategyProfile] = {
  profile.detector_key: profile
  for profile in STRATEGY_PROFILES
  if profile.detector_key
}
# Flip Zone's second (sell-side) detector key resolves to the same profile.
STRATEGY_BY_DETECTOR_KEY["flip_supply_zone_reaction"] = STRATEGY_BY_NAME["Flip Zone"]


def lookup_profile(strategy: str) -> StrategyProfile | None:
  key = str(strategy or "").strip()
  if not key:
    return None
  resolved = resolve_strategy(key)
  return STRATEGY_BY_NAME.get(resolved.canonical if resolved else key)


def names_with(trait: str) -> frozenset[str]:
  """Strategy names whose profile sets the boolean flag trait."""
  return frozenset(p.name for p in STRATEGY_PROFILES if getattr(p, trait))


def _resolve_dotted_path(root: Any, path: str) -> Any:
  obj = root
  for part in path.split("."):
    if not hasattr(obj, part):
      raise AttributeError(f"enable_setting path {path!r} missing segment {part!r}")
    obj = getattr(obj, part)
  return obj


def resolve_enable_setting(path: str, cfg: Any) -> Any:
  """Traverse path on cfg; raises if any segment is missing."""
  return _resolve_dotted_path(cfg, path)


def resolve_strategy_enabled(profile: StrategyProfile, cfg: Any) -> bool:
  value = resolve_enable_setting(profile.enable_setting, cfg)
  if profile.enable_requires_live_mode:
    return str(value or "").casefold() == "live"
  return bool(value)


def strategy_mode_enabled(strategy: str, cfg: Any) -> bool:
  profile = lookup_profile(strategy)
  if profile is None:
    return bool(_resolve_dotted_path(cfg, _DEFAULT_ENABLE))
  return resolve_strategy_enabled(profile, cfg)


def _assert_catalog_complete() -> None:
  from app.core.config import runtime_config

  if len(STRATEGY_BY_NAME) != len(STRATEGY_PROFILES):
    raise RuntimeError("strategy catalog names must be unique")
  for profile in STRATEGY_PROFILES:
    if not profile.name.strip():
      raise RuntimeError("strategy catalog row missing a name")
    resolve_enable_setting(profile.enable_setting, runtime_config)


_assert_catalog_complete()
