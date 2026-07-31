"""Setup-aware execution policy, quality tiers, and strategy families."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from app.autotrade.execution_route import resolve_execution_route_plan
from app.autotrade.protective_stop import (
  ProtectiveStopError,
  opposing_zone_context_from_values,
  opposing_zone_context_measured,
  plan_group_protective_stop,
  plan_protective_stop,
  stop_bounds_for_strategy,
)

GUARD_MODE_OBSERVE = "observe"
GUARD_MODE_BALANCED = "balanced"
GUARD_MODE_STRICT = "strict"
_GUARD_MODES = (GUARD_MODE_OBSERVE, GUARD_MODE_BALANCED, GUARD_MODE_STRICT)

# Version of the entry-plan contract (`planned_execution_route`,
# `planned_entry_price`, `planned_leg_entry_prices`) shared with the executor.
ENTRY_PLAN_VERSION = 1

ROUTE_MARKET = "market"
ROUTE_SINGLE_LIMIT = "single_limit"
ROUTE_ZONE_SPLIT = "zone_split"
# The policy allows either route: the executor picks deterministically and is
# not held to one planned entry.
ROUTE_EITHER = "either"

OUTCOME_ALLOW = "allow"
OUTCOME_ALLOW_WITH_WARNING = "allow_with_warning"
OUTCOME_ADJUST_TARGET = "adjust_target"
OUTCOME_WAIT = "wait"
OUTCOME_BLOCK = "block"

# Preference / quality signals that must never terminal-reject or consume a
# setup. They stay on the measured payload for ranking and ops telemetry.
PREFERENCE_TELEMETRY_REASONS = frozenset({
  "policy_regime_not_permitted",
  "policy_reward_risk_insufficient",
  "policy_confluence_below_minimum",
  "policy_zone_too_wide",
  "policy_target_room_insufficient",
  "tier_c_analysis_only",
  "all_matches_tier_c",
  "insufficient_target_room",
  "zone_width_contract_rejected",
  "zone_too_wide",
  "zone_too_narrow",
  "source_level_exhausted",
  "round_without_structural_anchor",
  "low_confluence_counter_bias_in_range",
  "counter_bias_disabled",
  "counter_bias_target_barrier",
  "target_room_insufficient",
  "context_degraded",
  "contested_corridor",
  "nearby_opposing_structure",
  "htf_veto",
  "opposing_barrier",
  "opposing_barrier_no_target",
  "opposing_major_no_room",
  "opposing_entry_overlap",
  "opposing_entry_contained",
  "entry_inside_opposing_zone",
  "opposing_ahead",
  "overlap_veto",
  "overlapping_zone_conflict",
  "ambiguous_waiting_confirmation",
  "opposing_zone_ahead",
  "zone_cooldown",
  "m1_trigger_wait",
  "m1_trigger_expired",
  "news_window_active",
  "news_guard_unavailable",
  "rr_pre_gate",
  "opposing_barrier_rr_insufficient",
})


def is_preference_telemetry(reason_code: str | None) -> bool:
  return str(reason_code or "").strip() in PREFERENCE_TELEMETRY_REASONS


@dataclass(frozen=True)
class StructuralBarrier:
  """One price structure evaluated by an execution guard.

  ``barrier_id`` is deliberately stable enough to compare with the
  candidate source identity.  The worker may still build a deterministic
  fallback id from geometry for older StrategyMatch payloads.
  """
  barrier_id: str
  source_type: str
  side: str
  low: float
  high: float
  level_kind: str = ""
  timeframe: str = ""
  touches: int = 0
  score: float = 0.0
  is_primary_source: bool = False
  is_supporting_source: bool = False


@dataclass(frozen=True)
class StructuralSourceIdentity:
  strategy: str
  strategy_family: str
  structural_source: str
  zone_id: str | None
  level_id: str | None
  key_level: float | None
  low: float
  high: float


@dataclass(frozen=True)
class ExecutionGuardDecision:
  """Typed result of a structural-quality guard evaluation. ``hard_block``
  (not ``outcome`` alone) is the single source of truth for whether a
  caller may delete/consume a match or terminal-reject a candidate -
  ``outcome`` is presentation/observability detail.
  """
  guard: str
  outcome: str
  reason_code: str
  message: str
  hard_block: bool
  measured: dict[str, Any] = field(default_factory=dict)
  barrier: StructuralBarrier | None = None


# Compatibility for the first replay commit on this branch.  New code should
# use the explicit public name above.
GuardOutcome = ExecutionGuardDecision


def classify_barrier_relationship(
  *,
  strategy: str,
  direction: str,
  entry_reference: float,
  target_reference: float | None,
  source_identity: StructuralSourceIdentity,
  barrier: StructuralBarrier,
) -> str:
  """Classify a barrier relative to one concrete trade thesis.

  The identity check is intentionally stronger than generic band overlap:
  exact ids win, while legacy matches may identify their source by the
  selected key level plus entry band.  This prevents an unrelated,
  overlapping opposing zone from being incorrectly discarded as "own
  source".
  """
  direction = direction.upper()
  exact_id = bool(
    (source_identity.zone_id and source_identity.zone_id == barrier.barrier_id)
    or (
      source_identity.level_id
      and source_identity.level_id == barrier.barrier_id
    )
  )
  source_overlap = (
    barrier.low <= source_identity.high
    and barrier.high >= source_identity.low
  )
  key_matches = (
    source_identity.key_level is not None
    and barrier.low <= source_identity.key_level <= barrier.high
  )
  side_supports = (
    direction == "BUY" and barrier.side in {"demand", "support"}
    or direction == "SELL" and barrier.side in {"supply", "resistance"}
  )
  if barrier.is_primary_source or exact_id or (
    source_overlap and key_matches and side_supports
  ):
    return "primary_source"
  if barrier.is_supporting_source or (
    side_supports and barrier.low <= entry_reference <= barrier.high
  ):
    return "supportive"

  if direction == "BUY":
    if barrier.high < entry_reference:
      return "behind_entry"
    opposing = barrier.side in {"supply", "resistance"}
    ahead = barrier.low > entry_reference
  else:
    if barrier.low > entry_reference:
      return "behind_entry"
    opposing = barrier.side in {"demand", "support"}
    ahead = barrier.high < entry_reference

  contains_entry = barrier.low <= entry_reference <= barrier.high
  if contains_entry:
    return (
      "overlapping_ambiguous"
      if barrier.side == "neutral" or not opposing
      else "overlapping_ambiguous"
    )
  if opposing and ahead:
    if target_reference is None:
      return "opposing_ahead"
    target_crosses = (
      direction == "BUY" and target_reference >= barrier.low
      or direction == "SELL" and target_reference <= barrier.high
    )
    return "opposing_ahead" if target_crosses else "irrelevant"
  if barrier.side == "neutral" and ahead:
    return "opposing_ahead"
  return "irrelevant"


def resolve_guard_mode(cfg: Any) -> str:
  mode = str(getattr(cfg, "auto_trade_structural_guard_mode", GUARD_MODE_BALANCED))
  mode = mode.strip().lower()
  return mode if mode in _GUARD_MODES else GUARD_MODE_BALANCED


def classify_guard_severity(
  guard: str,
  condition: str,
  reason: str,
  *,
  guard_mode: str,
  hard_geometry: bool = False,
) -> ExecutionGuardDecision:
  """Map a detected structural condition to a typed, mode-aware outcome.

  Preference / quality signals are always telemetry. ``hard_geometry`` marks
  true zero-room contract failures that are not on the preference list.
  """
  if is_preference_telemetry(condition):
    return ExecutionGuardDecision(
      guard, OUTCOME_ALLOW_WITH_WARNING, condition, reason, False,
    )
  if hard_geometry:
    return ExecutionGuardDecision(
      guard, OUTCOME_BLOCK, condition, reason, True,
    )
  if guard_mode == GUARD_MODE_STRICT:
    return ExecutionGuardDecision(
      guard, OUTCOME_BLOCK, condition, reason, True,
    )
  if guard_mode == GUARD_MODE_OBSERVE:
    return ExecutionGuardDecision(
      guard, OUTCOME_ALLOW_WITH_WARNING, condition, reason, False,
    )
  # balanced: buffer/ATR-based "ahead of entry" and other soft conditions
  # become warnings. Hard zero-room geometry already returned above.
  return ExecutionGuardDecision(
    guard, OUTCOME_ALLOW_WITH_WARNING, condition, reason, False,
  )


TIER_A = "A"
TIER_B = "B"
TIER_C = "C"

FAMILY_RANGE_REVERSION = "range_reversion"
FAMILY_TREND_PULLBACK = "trend_pullback"
FAMILY_BREAKOUT_RETEST = "breakout_retest"
FAMILY_MOMENTUM_CONTINUATION = "momentum_continuation"
FAMILY_LIQUIDITY_REVERSAL = "liquidity_reversal"
FAMILY_MAPPED_ZONE_REACTION = "mapped_zone_reaction"
FAMILY_KEY_LEVEL = "key_level"
FAMILY_SUPPLY_DEMAND = "supply_demand"
FAMILY_SESSION_LEVEL = "session_level"
FAMILY_TRENDLINE = "trendline"
FAMILY_RANGE = "range"
FAMILY_TREND = "trend"
FAMILY_UNKNOWN = "unknown"

_STRATEGY_FAMILY = {
  "Range Box Scalp": FAMILY_RANGE_REVERSION,
  "Range Edge Scalp": FAMILY_RANGE_REVERSION,
  "One-Sided Range Reaction": FAMILY_RANGE_REVERSION,
  "Fade Scalp": FAMILY_RANGE_REVERSION,
  "Zone Reaction": FAMILY_RANGE_REVERSION,
  "Chop Zone Reaction": FAMILY_RANGE_REVERSION,
  "Trend Pullback": FAMILY_TREND_PULLBACK,
  "Break & Retest": FAMILY_BREAKOUT_RETEST,
  "Box Breakout": FAMILY_BREAKOUT_RETEST,
  "Breakout Continuation": FAMILY_MOMENTUM_CONTINUATION,
  "Momentum Ride": FAMILY_MOMENTUM_CONTINUATION,
  "Mapped Zone Reaction": FAMILY_MAPPED_ZONE_REACTION,
  "Liquidity Sweep": FAMILY_LIQUIDITY_REVERSAL,
  "Snap-Back": FAMILY_LIQUIDITY_REVERSAL,
  "Key Level Reaction": FAMILY_KEY_LEVEL,
  "Demand Zone Reaction": FAMILY_SUPPLY_DEMAND,
  "Supply Zone Reaction": FAMILY_SUPPLY_DEMAND,
  "Session Level Reaction": FAMILY_SESSION_LEVEL,
  "Trendline Reaction": FAMILY_TRENDLINE,
}


@dataclass(frozen=True)
class ExecutionPolicy:
  family: str
  min_confluence: int
  max_entry_drift_atr: float
  max_entry_drift_pips: float
  max_zone_width_atr: float
  min_target_room_atr: float
  min_reward_risk: float
  risk_multiplier: float
  order_type_preference: str  # limit | market | either
  permitted_regimes: tuple[str, ...]
  entry_distribution: str = "either"  # single | zone_split | either


@dataclass(frozen=True)
class ExecutionPolicyEvaluation:
  allowed: bool
  reason_code: str
  message: str
  terminal: bool
  measured: dict[str, Any]
  policy: ExecutionPolicy | None = None


def preference_allow(
  reason_code: str,
  message: str,
  measured: dict[str, Any],
  policy: ExecutionPolicy | None = None,
) -> ExecutionPolicyEvaluation:
  """Record a preference miss without denying publication."""
  payload = {
    **dict(measured),
    "preference_telemetry": True,
    "preference_reason_code": reason_code,
    "preference_message": message,
  }
  return ExecutionPolicyEvaluation(
    True,
    reason_code,
    message,
    False,
    payload,
    policy,
  )


_DEFAULT_POLICIES: dict[str, ExecutionPolicy] = {
  FAMILY_RANGE_REVERSION: ExecutionPolicy(
    FAMILY_RANGE_REVERSION, 2, 0.35, 8.0, 1.0, 0.5, 1.10, 1.0,
    "market", ("chop", "range", "unknown"),
  ),
  FAMILY_TREND_PULLBACK: ExecutionPolicy(
    FAMILY_TREND_PULLBACK, 2, 0.75, 15.0, 2.0, 0.6, 1.15, 1.0,
    "limit", ("trend", "breakout", "unknown"),
  ),
  FAMILY_BREAKOUT_RETEST: ExecutionPolicy(
    FAMILY_BREAKOUT_RETEST, 2, 0.85, 18.0, 2.5, 0.7, 1.20, 1.0,
    "market", ("trend", "breakout", "unknown"),
  ),
  FAMILY_MOMENTUM_CONTINUATION: ExecutionPolicy(
    FAMILY_MOMENTUM_CONTINUATION, 2, 1.0, 20.0, 3.0, 0.8, 1.15, 1.0,
    "market", ("trend", "breakout", "unknown"),
  ),
  FAMILY_LIQUIDITY_REVERSAL: ExecutionPolicy(
    FAMILY_LIQUIDITY_REVERSAL, 2, 0.45, 10.0, 1.5, 0.55, 1.15, 0.75,
    "market", ("chop", "range", "trend", "unknown"),
  ),
  FAMILY_MAPPED_ZONE_REACTION: ExecutionPolicy(
    FAMILY_MAPPED_ZONE_REACTION, 2, 0.40, 10.0, 2.0, 0.6, 1.15, 1.0,
    "market", ("chop", "range", "trend", "breakout", "unknown"),
  ),
  # Strict stop contracts require a concrete route. Key/Session/Trendline
  # reaction families use market_with_limit_scale (L1 market 70% + L2 deeper
  # limit 30%) when AUTO_TRADE_REACTION_SCALE_ENABLED. Demand/Supply keep the
  # classic DCA limit_ladder zone_scale path and must not auto-select
  # market_with_limit_scale.
  FAMILY_KEY_LEVEL: ExecutionPolicy(
    FAMILY_KEY_LEVEL, 2, 0.50, 12.0, 1.5, 0.55, 1.15, 1.0,
    "limit", ("chop", "range", "trend", "breakout", "unknown"),
    entry_distribution="zone_scale",
  ),
  FAMILY_SUPPLY_DEMAND: ExecutionPolicy(
    FAMILY_SUPPLY_DEMAND, 2, 0.50, 12.0, 2.0, 0.55, 1.15, 1.0,
    "limit", ("chop", "range", "trend", "breakout", "unknown"),
    entry_distribution="zone_scale",
  ),
  FAMILY_SESSION_LEVEL: ExecutionPolicy(
    FAMILY_SESSION_LEVEL, 2, 0.50, 12.0, 1.5, 0.55, 1.15, 1.0,
    "limit", ("chop", "range", "trend", "breakout", "unknown"),
    entry_distribution="zone_scale",
  ),
  FAMILY_TRENDLINE: ExecutionPolicy(
    FAMILY_TRENDLINE, 2, 0.55, 14.0, 1.5, 0.55, 1.15, 1.0,
    "limit", ("chop", "range", "trend", "breakout", "unknown"),
    entry_distribution="zone_scale",
  ),
}


def strategy_family(strategy: str) -> str:
  # An unknown detector label is a contract error, not a trend pullback.
  # Falling back here silently grants an unreviewed setup the pullback
  # policy, including its drift and risk allowances.
  return _STRATEGY_FAMILY.get(strategy, FAMILY_UNKNOWN)


def policy_for(strategy: str, cfg: Any | None = None) -> ExecutionPolicy:
  family = strategy_family(strategy)
  if family == FAMILY_UNKNOWN:
    raise ValueError(f"unknown execution strategy: {strategy}")
  base = _DEFAULT_POLICIES[family]
  if cfg is None:
    return base
  drift_overrides = {
    FAMILY_RANGE_REVERSION: float(getattr(
      cfg, "auto_trade_range_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
    FAMILY_TREND_PULLBACK: float(getattr(
      cfg, "auto_trade_trend_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
    FAMILY_BREAKOUT_RETEST: float(getattr(
      cfg, "auto_trade_trend_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
    FAMILY_MOMENTUM_CONTINUATION: float(getattr(
      cfg, "auto_trade_trend_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
    FAMILY_MAPPED_ZONE_REACTION: float(getattr(
      cfg, "auto_trade_map_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
    FAMILY_KEY_LEVEL: float(getattr(
      cfg, "auto_trade_map_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
    FAMILY_SUPPLY_DEMAND: float(getattr(
      cfg, "auto_trade_map_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
    FAMILY_SESSION_LEVEL: float(getattr(
      cfg, "auto_trade_map_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
    FAMILY_TRENDLINE: float(getattr(
      cfg, "auto_trade_trend_max_entry_drift_atr", base.max_entry_drift_atr,
    )),
  }
  return ExecutionPolicy(
    family=base.family,
    min_confluence=base.min_confluence,
    max_entry_drift_atr=drift_overrides.get(family, base.max_entry_drift_atr),
    max_entry_drift_pips=base.max_entry_drift_pips,
    max_zone_width_atr=base.max_zone_width_atr,
    min_target_room_atr=base.min_target_room_atr,
    min_reward_risk=float(getattr(
      cfg, "auto_trade_range_min_rr", base.min_reward_risk,
    )) if family == FAMILY_RANGE_REVERSION else base.min_reward_risk,
    risk_multiplier=base.risk_multiplier,
    order_type_preference=base.order_type_preference,
    permitted_regimes=base.permitted_regimes,
    entry_distribution=base.entry_distribution,
  )


def planned_execution_route(
  *,
  order_type_preference: str,
  entry_distribution: str,
  allow_either: bool = True,
) -> str:
  """Legacy route-name helper for parity fixtures and non-strict candidates.

  Strict autonomous publication must use resolve_execution_route_plan so
  `either` is resolved to a concrete route before stop planning.
  """
  preference = (order_type_preference or "").strip().lower()
  distribution = (entry_distribution or "").strip().lower()
  if preference == "market":
    return ROUTE_MARKET
  if preference == "limit":
    if distribution in {"zone_split", "zone_scale"}:
      return ROUTE_ZONE_SPLIT
    if distribution == "single":
      return ROUTE_SINGLE_LIMIT
  if allow_either:
    return ROUTE_EITHER
  return ROUTE_MARKET


def evaluate_execution_policy(
  match: Any,
  *,
  spot_price: float,
  regime: str | None,
  pip_size: float,
  cfg: Any | None = None,
  opposing_zone_low: float | None = None,
  opposing_zone_high: float | None = None,
  opposing_zone_id: str | None = None,
  executable_quote: float | None = None,
  trigger_wick_extreme: float | None = None,
) -> ExecutionPolicyEvaluation:
  """Enforce every declared setup policy before candidate publication."""
  try:
    policy = policy_for(str(getattr(match, "strategy", "")), cfg)
  except ValueError as exc:
    return ExecutionPolicyEvaluation(
      False,
      "unknown_strategy_policy",
      str(exc),
      True,
      {},
      None,
    )
  atr = float(getattr(match, "atr", 0.0) or 0.0)
  low = float(getattr(match, "entry_low", 0.0))
  high = float(getattr(match, "entry_high", 0.0))
  direction = str(getattr(match, "direction", "")).upper()
  pip = pip_size if pip_size > 0 else 0.1
  confluence = int(getattr(match, "confluence", 0) or 0)
  zone_width_atr = (
    (high - low) / atr if atr > 0 and math.isfinite(atr) else float("inf")
  )
  targets = tuple(int(value) for value in getattr(match, "targets_pips", ()) or ())
  target_model = str(
    getattr(match, "target_model", "") or (
      "hybrid"
      if getattr(match, "target_price", None) is not None and targets
      else "absolute"
      if getattr(match, "target_price", None) is not None
      else "fill_relative"
    )
  ).strip().lower()
  absolute_target = getattr(match, "absolute_target_price", None)
  if absolute_target is None:
    absolute_target = getattr(match, "target_price", None)
  # Policy is evaluated against the actual planned entry, not the detector
  # tick. A fill-relative ladder is anchored later to the broker fill and
  # therefore must never be converted into a detection-relative price here.
  entry_distribution = (
    policy.entry_distribution
    if policy.entry_distribution != "either"
    else "zone_split"
    if policy.order_type_preference == "limit" and zone_width_atr >= 0.5
    else "single"
  )
  quote = float(
    spot_price if executable_quote is None else executable_quote
  )
  digits = int(getattr(cfg, "auto_trade_xau_price_digits", 2) or 2)
  route_plan = resolve_execution_route_plan(
    direction=direction,
    order_type_preference=policy.order_type_preference,
    entry_distribution=entry_distribution,
    executable_quote=quote,
    zone_low=low,
    zone_high=high,
    atr=atr,
    zone_fill_enabled=bool(
      getattr(cfg, "auto_trade_zone_fill_enabled", False)
    ),
    zone_fill_min_atr=float(
      getattr(cfg, "auto_trade_zone_fill_min_atr", 0.5) or 0.5
    ),
    inside_zone_market_entry_enabled=bool(
      getattr(cfg, "auto_trade_inside_zone_market_entry_enabled", True)
    ),
    zone_fill_fallback_enabled=bool(
      getattr(cfg, "auto_trade_zone_fill_fallback_enabled", True)
    ),
    digits=digits,
    allow_either=False,
    scale_first_leg_fraction=float(
      getattr(cfg, "auto_trade_zone_scale_first_leg_fraction", 0.70) or 0.70
    ),
    scale_step_atr=float(
      getattr(cfg, "auto_trade_zone_scale_step_atr", 0.5) or 0.5
    ),
    reaction_scale_enabled=bool(
      getattr(cfg, "auto_trade_reaction_scale_enabled", True)
    ),
    reaction_market_fraction=float(
      getattr(cfg, "auto_trade_reaction_market_fraction", 0.70) or 0.70
    ),
    reaction_scale_fraction=float(
      getattr(cfg, "auto_trade_reaction_scale_fraction", 0.30) or 0.30
    ),
    reaction_scale_step_atr=float(
      getattr(cfg, "auto_trade_reaction_scale_step_atr", 0.5) or 0.5
    ),
    reaction_scale_invalid_policy=str(
      getattr(
        cfg, "auto_trade_reaction_scale_invalid_policy", "single_market",
      ) or "single_market"
    ),
    strategy=str(getattr(match, "strategy", "") or ""),
    strategy_family=str(
      getattr(match, "family", None)
      or getattr(match, "strategy_family", None)
      or strategy_family(str(getattr(match, "strategy", "") or ""))
    ),
  )
  if not route_plan.valid:
    return ExecutionPolicyEvaluation(
      False,
      "execution_route_unresolved",
      route_plan.reject_reason or "execution route could not be resolved",
      True,
      {
        "order_type_preference": policy.order_type_preference,
        "entry_distribution": entry_distribution,
        "routing_reason": route_plan.routing_reason,
      },
      policy,
    )
  planned_route = route_plan.route
  planned_entry = float(route_plan.planned_entry_price)
  ladder_room_price = max(targets) * pip if targets else 0.0
  absolute_room_price = 0.0
  if absolute_target is not None and math.isfinite(float(absolute_target)):
    absolute_room_price = (
      float(absolute_target) - planned_entry
      if direction == "BUY"
      else planned_entry - float(absolute_target)
    )
  if target_model == "fill_relative":
    remaining_price = ladder_room_price
  elif target_model == "absolute":
    remaining_price = absolute_room_price
  elif target_model == "hybrid":
    remaining_price = min(ladder_room_price, absolute_room_price)
  else:
    remaining_price = -1.0
  remaining_pips = max(0.0, remaining_price / pip)
  remaining_room_atr = (
    max(0.0, remaining_price) / atr
    if atr > 0 and math.isfinite(atr) else 0.0
  )
  stop_plan = None
  stop_plan_error: str | None = None
  stop_plan_error_measured: dict[str, Any] = {}
  opposing_zone = None
  try:
    minimum_stop_pips, maximum_stop_pips = stop_bounds_for_strategy(
      strategy=str(getattr(match, "strategy", "")),
      pip_size=pip,
      cfg=cfg,
    )
    sweep_extreme = (
      trigger_wick_extreme
      if trigger_wick_extreme is not None
      else getattr(
        match,
        "sweep_low" if direction == "BUY" else "sweep_high",
        None,
      )
    )
    zone_low = (
      opposing_zone_low
      if opposing_zone_low is not None
      else getattr(match, "opposing_zone_low", None)
    )
    zone_high = (
      opposing_zone_high
      if opposing_zone_high is not None
      else getattr(match, "opposing_zone_high", None)
    )
    zone_id = (
      opposing_zone_id
      if opposing_zone_id is not None
      else getattr(match, "opposing_zone_id", None)
      or getattr(match, "zone_id", None)
    )
    opposing_zone = opposing_zone_context_from_values(
      opposing_zone_low=zone_low,
      opposing_zone_high=zone_high,
      opposing_zone_id=(
        str(zone_id) if zone_id is not None else None
      ),
      direction=direction,
      atr=atr,
      pip_size=pip,
      cfg=cfg,
    )
    digits = int(getattr(cfg, "auto_trade_xau_price_digits", 2))
    structure_buffer_atr = getattr(
      cfg, "auto_trade_add_stop_buffer_atr", 0.3,
    )
    wick_buffer_atr = getattr(
      cfg, "auto_trade_wick_stop_buffer_atr", 0.15,
    )
    leg_prices = list(route_plan.planned_leg_entry_prices or ())
    leg_ratios = list(route_plan.planned_leg_volume_ratios or ())
    use_group_stop = (
      entry_distribution == "zone_scale"
      and len(leg_prices) >= 2
      and len(leg_ratios) == len(leg_prices)
    )
    if use_group_stop:
      # Absolute group SL is structural (one price beyond zone/entries/swing).
      # Envelope distance uses declared leg ratios as relative weights only —
      # never a fake planning total lots. Live equity sizes broker volume later
      # in C#; it must not reshape the published absolute stop.
      stop_plan = plan_group_protective_stop(
        direction=direction,
        entry_zone_low=low,
        entry_zone_high=high,
        planned_leg_prices=leg_prices,
        resolved_leg_volumes=leg_ratios,
        structure_swing=getattr(match, "structure_swing", None),
        atr=atr,
        structure_buffer_atr=structure_buffer_atr,
        sweep_extreme=sweep_extreme,
        wick_buffer_atr=wick_buffer_atr,
        minimum_stop_pips=minimum_stop_pips,
        maximum_stop_pips=maximum_stop_pips,
        pip_size=pip,
        digits=digits,
        opposing_zone=opposing_zone,
      )
    else:
      stop_plan = plan_protective_stop(
        direction=direction,
        entry_price=planned_entry,
        structure_swing=getattr(match, "structure_swing", None),
        atr=atr,
        structure_buffer_atr=structure_buffer_atr,
        sweep_extreme=sweep_extreme,
        wick_buffer_atr=wick_buffer_atr,
        minimum_stop_pips=minimum_stop_pips,
        maximum_stop_pips=maximum_stop_pips,
        pip_size=pip,
        digits=digits,
        opposing_zone=opposing_zone,
      )
  except ProtectiveStopError as exc:
    stop_plan_error = str(exc)
    stop_plan_error_measured = dict(getattr(exc, "measured", None) or {})
  reward_risk = (
    max(0.0, remaining_pips) / float(stop_plan.final_stop_pips)
    if stop_plan is not None and stop_plan.final_stop_pips > 0
    else 0.0
  )
  raw_risk_multiplier = getattr(match, "risk_multiplier", 1.0)
  match_risk_multiplier = float(
    1.0 if raw_risk_multiplier is None else raw_risk_multiplier
  )
  effective_risk_multiplier = (
    match_risk_multiplier * policy.risk_multiplier
  )
  normalized_regime = (
    "range" if regime == "range"
    else str(regime or "unknown").strip().lower()
  )
  planned_leg_entry_prices: list[float]
  if route_plan.planned_leg_entry_prices:
    planned_leg_entry_prices = [
      round(float(price), 6) for price in route_plan.planned_leg_entry_prices
    ]
  elif planned_route == "single_limit":
    planned_leg_entry_prices = [round(planned_entry, 6)]
  elif planned_route == "zone_split":
    proximal = high if direction == "BUY" else low
    midpoint = round((low + high) / 2.0, 6)
    planned_leg_entry_prices = [round(proximal, 6), midpoint]
  else:
    planned_leg_entry_prices = []
  measured = {
    "policy_family": policy.family,
    "confluence": confluence,
    "min_confluence": policy.min_confluence,
    "zone_width_atr": round(zone_width_atr, 4),
    "max_zone_width_atr": policy.max_zone_width_atr,
    "remaining_target_room_pips": round(remaining_pips, 3),
    "remaining_target_room_atr": round(remaining_room_atr, 4),
    "min_target_room_atr": policy.min_target_room_atr,
    "reward_risk": round(reward_risk, 4),
    "min_reward_risk": policy.min_reward_risk,
    "policy_risk_multiplier": policy.risk_multiplier,
    "match_risk_multiplier": match_risk_multiplier,
    "effective_risk_multiplier": effective_risk_multiplier,
    "order_type_preference": policy.order_type_preference,
    "entry_distribution": entry_distribution,
    "planned_execution_route": planned_route,
    "planned_leg_entry_prices": planned_leg_entry_prices,
    "planned_leg_volume_ratios": [
      round(float(ratio), 6) for ratio in route_plan.planned_leg_volume_ratios
    ],
    "routing_reason": route_plan.routing_reason,
    "target_model": target_model,
    "target_reference_price": str(
      getattr(match, "target_reference_price", "broker_fill")
    ),
    "absolute_target_price": absolute_target,
    "planned_entry_price": round(planned_entry, 6),
    "planned_stop_error": stop_plan_error,
    "entry_plan_version": ENTRY_PLAN_VERSION,
    "regime": normalized_regime or "unknown",
    "permitted_regimes": list(policy.permitted_regimes),
    "structure_swing": getattr(match, "structure_swing", None),
    **opposing_zone_context_measured(opposing_zone),
    **stop_plan_error_measured,
  }
  if stop_plan is not None:
    measured.update(stop_plan.candidate_fields(entry_price=stop_plan.entry_price))
  if (
    direction not in {"BUY", "SELL"}
    or not all(math.isfinite(value) for value in (spot_price, low, high))
    or low > high
    or not math.isfinite(atr)
    or atr <= 0
  ):
    return ExecutionPolicyEvaluation(
      False,
      "invalid_execution_geometry",
      "entry zone, direction, spot, or ATR is invalid",
      True,
      measured,
      policy,
    )
  if stop_plan is None:
    known_stop_errors = {
      "stop_exceeds_envelope_after_wick",
      "stop_exceeds_max_envelope",
      "stop_inside_opposing_zone",
      "stop_inside_entry_zone",
      "stop_not_beyond_planned_entries",
    }
    return ExecutionPolicyEvaluation(
      False,
      (
        stop_plan_error
        if stop_plan_error in known_stop_errors
        else "protective_stop_unavailable"
      ),
      stop_plan_error or "protective stop could not be planned",
      True,
      measured,
      policy,
    )
  if (
    not math.isfinite(effective_risk_multiplier)
    or effective_risk_multiplier <= 0
    or effective_risk_multiplier > 1.0
  ):
    return ExecutionPolicyEvaluation(
      False,
      "invalid_risk_multiplier",
      "effective autonomous risk multiplier must be within (0, 1]",
      True,
      measured,
      policy,
    )
  if target_model not in {"absolute", "fill_relative", "hybrid"}:
    return ExecutionPolicyEvaluation(
      False,
      "invalid_target_model",
      f"unsupported target model {target_model}",
      True,
      measured,
      policy,
    )
  if target_model in {"absolute", "hybrid"} and (
    absolute_target is None or absolute_room_price <= 0
  ):
    return ExecutionPolicyEvaluation(
      False,
      "invalid_absolute_target_geometry",
      "absolute structural target is not ahead of the planned entry",
      True,
      measured,
      policy,
    )
  if target_model in {"fill_relative", "hybrid"} and not targets:
    return ExecutionPolicyEvaluation(
      False,
      "invalid_fill_relative_targets",
      "fill-relative target model requires a positive pip ladder",
      True,
      measured,
      policy,
    )
  preference_notes: list[dict[str, str]] = []
  if confluence < policy.min_confluence:
    preference_notes.append({
      "reason_code": "policy_confluence_below_minimum",
      "message": f"confluence {confluence} below {policy.min_confluence}",
    })
  if normalized_regime not in policy.permitted_regimes:
    preference_notes.append({
      "reason_code": "policy_regime_not_permitted",
      "message": f"{match.strategy} does not permit regime {normalized_regime}",
    })
  if zone_width_atr > policy.max_zone_width_atr:
    preference_notes.append({
      "reason_code": "policy_zone_too_wide",
      "message": (
        f"entry zone {zone_width_atr:.2f} ATR exceeds "
        f"{policy.max_zone_width_atr:.2f} ATR"
      ),
    })
  if remaining_room_atr < policy.min_target_room_atr:
    preference_notes.append({
      "reason_code": "policy_target_room_insufficient",
      "message": (
        f"remaining target room {remaining_room_atr:.2f} ATR below "
        f"{policy.min_target_room_atr:.2f} ATR"
      ),
    })
  if reward_risk < policy.min_reward_risk:
    preference_notes.append({
      "reason_code": "policy_reward_risk_insufficient",
      "message": (
        f"remaining reward/risk {reward_risk:.2f} below "
        f"{policy.min_reward_risk:.2f}"
      ),
    })
  if preference_notes:
    primary = preference_notes[0]
    return preference_allow(
      primary["reason_code"],
      primary["message"],
      {
        **measured,
        "preference_notes": preference_notes,
      },
      policy,
    )
  return ExecutionPolicyEvaluation(
    True,
    "policy_allowed",
    "strategy execution policy passed",
    False,
    measured,
    policy,
  )


def classify_tier(
  *,
  confluence: int,
  strategy: str,
  range_state: str | None = None,
  fallback_edge: bool = False,
  post_impulse: bool = False,
  one_sided: bool = False,
) -> str:
  """Tier A = full risk, Tier B = reduced risk, Tier C = preference telemetry."""
  family = strategy_family(strategy)
  if confluence < 1:
    return TIER_C
  if range_state == "provisional_range" or fallback_edge or one_sided:
    return TIER_B if confluence >= 2 else TIER_C
  if post_impulse or range_state == "post_impulse_range":
    return TIER_B
  if family == FAMILY_MOMENTUM_CONTINUATION and confluence >= 2:
    return TIER_A if confluence >= 3 else TIER_B
  if confluence >= 3:
    return TIER_A
  if confluence >= 2:
    return TIER_B
  return TIER_C


def risk_multiplier_for_tier(tier: str, cfg: Any | None = None, *, post_impulse: bool = False, one_sided: bool = False) -> float:
  tier = (tier or TIER_C).upper()
  a = float(getattr(cfg, "auto_trade_tier_a_risk_multiplier", 1.0) if cfg else 1.0)
  b = float(getattr(cfg, "auto_trade_tier_b_risk_multiplier", 0.5) if cfg else 0.5)
  c = float(getattr(cfg, "auto_trade_tier_c_risk_multiplier", b) if cfg else b)
  post = float(getattr(cfg, "auto_trade_post_impulse_risk_multiplier", 0.5) if cfg else 0.5)
  onesided = float(getattr(cfg, "auto_trade_one_sided_range_risk_multiplier", 0.5) if cfg else 0.5)
  if tier == TIER_A:
    mult = a
  elif tier == TIER_B:
    mult = b
  else:
    # Tier C remains executable with reduced size; quality is telemetry.
    mult = max(0.25, min(b, c))
  if post_impulse:
    mult = min(mult, post)
  if one_sided:
    mult = min(mult, onesided)
  return max(0.0, mult)


_FAMILY_MIN_DRIFT_SETTING = {
  FAMILY_RANGE_REVERSION: "auto_trade_range_min_entry_drift_pips",
  FAMILY_TREND_PULLBACK: "auto_trade_trend_min_entry_drift_pips",
  FAMILY_BREAKOUT_RETEST: "auto_trade_trend_min_entry_drift_pips",
  FAMILY_MOMENTUM_CONTINUATION: "auto_trade_trend_min_entry_drift_pips",
  FAMILY_MAPPED_ZONE_REACTION: "auto_trade_map_min_entry_drift_pips",
  FAMILY_KEY_LEVEL: "auto_trade_map_min_entry_drift_pips",
  FAMILY_SUPPLY_DEMAND: "auto_trade_map_min_entry_drift_pips",
  FAMILY_SESSION_LEVEL: "auto_trade_map_min_entry_drift_pips",
  FAMILY_TRENDLINE: "auto_trade_trend_min_entry_drift_pips",
}
_FAMILY_HARD_DRIFT_SETTING = {
  FAMILY_RANGE_REVERSION: "auto_trade_range_hard_entry_drift_pips",
  FAMILY_TREND_PULLBACK: "auto_trade_trend_hard_entry_drift_pips",
  FAMILY_BREAKOUT_RETEST: "auto_trade_trend_hard_entry_drift_pips",
  FAMILY_MOMENTUM_CONTINUATION: "auto_trade_trend_hard_entry_drift_pips",
  FAMILY_MAPPED_ZONE_REACTION: "auto_trade_map_hard_entry_drift_pips",
  FAMILY_KEY_LEVEL: "auto_trade_map_hard_entry_drift_pips",
  FAMILY_SUPPLY_DEMAND: "auto_trade_map_hard_entry_drift_pips",
  FAMILY_SESSION_LEVEL: "auto_trade_map_hard_entry_drift_pips",
  FAMILY_TRENDLINE: "auto_trade_trend_hard_entry_drift_pips",
}
_FAMILY_HARD_DRIFT_DEFAULT = {
  FAMILY_RANGE_REVERSION: 20.0,
  FAMILY_TREND_PULLBACK: 30.0,
  FAMILY_BREAKOUT_RETEST: 30.0,
  FAMILY_MOMENTUM_CONTINUATION: 30.0,
  FAMILY_MAPPED_ZONE_REACTION: 20.0,
  FAMILY_KEY_LEVEL: 20.0,
  FAMILY_SUPPLY_DEMAND: 20.0,
  FAMILY_SESSION_LEVEL: 20.0,
  FAMILY_TRENDLINE: 30.0,
}


def max_entry_drift_pips(
  *,
  strategy: str,
  atr: float,
  pip_size: float,
  remaining_target_room_pips: float | None,
  cfg: Any | None = None,
) -> tuple[float, dict[str, float]]:
  """Strategy-aware drift tolerance for the gap between when a setup formed
  and when the worker gets to evaluate it.

  Root cause of the 23-25 Jul frequency collapse: the previous formula was
  a bare min(configured, ATR x mult, room x 0.45) with no floor - on tight
  XAU M1 ATR this could collapse the effective tolerance to 3-5 pips, less
  than normal tick/poll latency, so a perfectly good reaction was routinely
  discarded as "moved too far" before the worker ever saw it.

  adaptive_floor = max(configured minimum, ATR-based drift) restores a
  latency-realistic floor without removing protection: `room_cap` and the
  strategy's own absolute hard cap still apply on top, so a setup whose
  target room is genuinely consumed, or that has moved further than any
  reasonable latency explains, is still capped/rejected.
  """
  policy = policy_for(strategy, cfg)
  family = strategy_family(strategy)
  pip = pip_size if pip_size > 0 else 0.1
  atr_pips = (atr / pip) * policy.max_entry_drift_atr if atr > 0 else 0.0
  configured = policy.max_entry_drift_pips
  # Do NOT fold AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS into adaptive drift.
  # Adaptive drift is observation tolerance; executor distance is a separate
  # hard publication gate (see entry_distance.measure_entry_distance).
  min_setting = _FAMILY_MIN_DRIFT_SETTING.get(family)
  configured_minimum = (
    float(getattr(cfg, min_setting, 0.0)) if cfg is not None and min_setting else 0.0
  )
  adaptive_floor = max(configured, configured_minimum, atr_pips)
  room_cap = float("inf")
  if remaining_target_room_pips is not None:
    room_cap = max(0.0, remaining_target_room_pips)
  hard_setting = _FAMILY_HARD_DRIFT_SETTING.get(family)
  default_hard_cap = _FAMILY_HARD_DRIFT_DEFAULT.get(family, configured)
  hard_cap = (
    float(getattr(cfg, hard_setting, default_hard_cap))
    if cfg is not None and hard_setting else configured
  )
  limit = min(adaptive_floor, room_cap, hard_cap)
  measured = {
    "configured_pips": round(configured, 3),
    "atr_pips": round(atr_pips, 3),
    "adaptive_floor_pips": round(adaptive_floor, 3),
    "room_cap_pips": (
      round(room_cap, 3) if math.isfinite(room_cap) else -1.0
    ),
    "hard_cap_pips": round(hard_cap, 3),
    "effective_pips": round(max(0.0, limit), 3),
  }
  return max(0.0, limit), measured
