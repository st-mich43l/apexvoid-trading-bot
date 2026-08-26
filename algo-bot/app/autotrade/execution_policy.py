"""Setup-aware execution policy, quality tiers, and strategy families."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import math
from types import SimpleNamespace
from typing import Any

from app.autotrade.execution_route import SCALP_MICRO_CLIPS, resolve_execution_route_plan
from app.autotrade.protective_stop import (
  ProtectiveStopError,
  opposing_zone_context_from_values,
  opposing_zone_context_measured,
  plan_group_protective_stop,
  plan_protective_stop,
  primary_tp_pips_from_match,
  stop_bounds_for_reaction_room,
)
from app.autotrade.strategy_taxonomy import (
  is_reaction_strategy,
  is_scalp_strategy,
  is_technique_or_confluence,
  is_zone_strategy,
  match_bypasses_opposing_structure,
)

GUARD_MODE_OBSERVE = "observe"
GUARD_MODE_BALANCED = "balanced"
GUARD_MODE_STRICT = "strict"
_GUARD_MODES = (GUARD_MODE_OBSERVE, GUARD_MODE_BALANCED, GUARD_MODE_STRICT)

def _default_runtime_cfg() -> Any:
  from app.core.config import runtime_config
  return runtime_config


def _instrument_context(symbol: str, cfg: Any) -> Any:
  resolver = getattr(cfg, "for_instrument", None)
  if symbol and callable(resolver):
    # Configuration has already been validated at startup. Do not silently
    # fall back to root/XAU policy if a live instrument cannot be resolved.
    return resolver(symbol)
  return cfg


def _instrument_digits(symbol: str, cfg: Any) -> int:
  context = _instrument_context(symbol, cfg)
  units = getattr(context, "units", None)
  if units is not None:
    return int(units.price_digits)
  return int(context.contract.instrument.price_digits or 2)


def _instrument_fixed_targeting(symbol: str, cfg: Any) -> Any | None:
  targeting = getattr(_instrument_context(symbol, cfg), "targeting", None)
  if targeting is not None:
    mode = getattr(targeting.mode, "value", targeting.mode)
    if str(mode) == "fixed_rr":
      return targeting
  return None


def _instrument_volume_multiplier(instrument_cfg: Any) -> float:
  """Pack volume scale for autonomous fixed_rr (FX) instruments.

  Reads ``manual.risk_multiplier`` (FX packs stamp ``1.5`` from
  ``manual_algo.sizing.fx_volume_multiplier``). XAU and non-fixed_rr
  contexts stay at ``1.0``. Invalid values fail soft to ``1.0``.
  """
  raw = getattr(
    getattr(instrument_cfg, "manual", None), "risk_multiplier", 1.0,
  )
  try:
    value = float(1.0 if raw is None else raw)
  except (TypeError, ValueError):
    return 1.0
  if not math.isfinite(value) or value <= 0:
    return 1.0
  return value

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
# Structural zero-room / entry-inside conflicts are NOT listed here — those
# hard-block via evaluate_structural_target_room + worker.
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
  # Positive room but ladder prefers more — legacy cap codes kept for
  # historical telemetry / old events:
  "opposing_barrier_target_capped",
  "opposing_barrier_target_capped_below_ladder",
  "configured_ladder_does_not_fit",
  # Opposing barrier present but not binding — the full configured ladder
  # already fits within buffered room; same allow/effective-target outcome
  # as no_opposing_barrier, split out only for telemetry clarity.
  "opposing_barrier_full_ladder_fits",
  # Owner 2026-08-06: barrier room ignored; configured partial ladder kept
  # when usable room is still above the execution-cost floor.
  "opposing_barrier_room_ignored_full_ladder",
  "opposing_barrier_no_configured_targets",
  # Entry falls inside a neutral key-level band (round number / reaction
  # level) rather than a directional supply/demand zone. Unlike genuine
  # zone containment (entry_inside_opposing_zone, still a hard block below),
  # a level has no side and is a weak/ambiguous signal on its own.
  "entry_inside_opposing_level",
  # Entry falls inside a zone whose side couldn't be cleanly classified as
  # opposing this direction (classify_barrier_relationship's
  # "overlapping_neutral"). Same reasoning as entry_inside_opposing_level:
  # only a zone that's unambiguously on the opposing side is the real
  # structural wall entry_inside_opposing_zone hard-blocks for.
  "entry_inside_ambiguous_zone",
})

# True structural conflicts — must hard-block publication.
HARD_STRUCTURAL_TARGET_ROOM_REASONS = frozenset({
  "opposing_entry_contained",
  "opposing_entry_overlap",
  "opposing_major_no_room",
  "opposing_barrier_no_target",
  "opposing_barrier_room_below_cost",
  "entry_inside_opposing_zone",
  "invalid_target_room_geometry",
  "execution_cost_insufficient_room",
})


def is_preference_telemetry(reason_code: str | None) -> bool:
  return str(reason_code or "").strip() in PREFERENCE_TELEMETRY_REASONS


def is_hard_structural_target_room(reason_code: str | None) -> bool:
  return str(reason_code or "").strip() in HARD_STRUCTURAL_TARGET_ROOM_REASONS


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
    # Bug (since this function's introduction in 13414b7): both branches of
    # this condition returned the same literal value, so a zone whose side
    # couldn't be cleanly classified as opposing (barrier.side == "neutral",
    # or not matching the opposing-side set at all -- already excluded from
    # "supportive" above by the side_supports check) was hard-blocked
    # exactly like a confirmed, cleanly-classified opposing zone. Only a
    # genuinely opposing zone containing the entry is the 23 Jul incident
    # this guard exists for (BUY filled inside an 8-touch SELL resistance
    # band, unambiguously barrier.side == "supply"); a side-unclear zone is
    # a materially weaker signal, same reasoning PR #223 already applied to
    # neutral key levels.
    return "overlapping_ambiguous" if opposing else "overlapping_neutral"
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


def resolve_guard_mode(cfg: Any | None = None) -> str:
  """Return the configured structural-guard mode.

  Production reads the authority-neutral runtime config
  (``actionability.structural_guard.guard_mode``); tests may inject a
  canonical-shaped override.
  """
  if cfg is None:
    cfg = _default_runtime_cfg()
  mode = str(cfg.actionability.structural_guard.guard_mode)
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
  "Chop Zone Reaction": FAMILY_RANGE_REVERSION,
  # M1 scalp live publishes through the same V7 plan builder; map to range
  # reversion so policy/stop planning runs. Native scalp room unlocks
  # opposing-structure bypass (strategy_taxonomy).
  "Range Sweep Scalp": FAMILY_RANGE_REVERSION,
  "Impulse Pullback Scalp": FAMILY_RANGE_REVERSION,
  "Breakout Retest Scalp": FAMILY_RANGE_REVERSION,
  "Momentum Chase Scalp": FAMILY_RANGE_REVERSION,
  # Legacy HFS display names (open plans / historical events):
  "HFS Range Sweep": FAMILY_RANGE_REVERSION,
  "HFS Impulse Pullback": FAMILY_RANGE_REVERSION,
  "HFS Breakout Retest": FAMILY_RANGE_REVERSION,
  "HFS Momentum Chase": FAMILY_RANGE_REVERSION,
  "Trend Pullback": FAMILY_TREND_PULLBACK,
  "Break & Retest": FAMILY_BREAKOUT_RETEST,
  "Box Breakout": FAMILY_BREAKOUT_RETEST,
  "Breakout Continuation": FAMILY_MOMENTUM_CONTINUATION,
  "Momentum Ride": FAMILY_MOMENTUM_CONTINUATION,
  "Mapped Zone Reaction": FAMILY_MAPPED_ZONE_REACTION,
  "Liquidity Sweep": FAMILY_LIQUIDITY_REVERSAL,
  "Snap-Back": FAMILY_LIQUIDITY_REVERSAL,
  "Key Level Reaction": FAMILY_KEY_LEVEL,
  "Zone Reaction": FAMILY_SUPPLY_DEMAND,
  "Flip Zone": FAMILY_SUPPLY_DEMAND,
  "Supply Demand": FAMILY_SUPPLY_DEMAND,
  "Order Block": FAMILY_SUPPLY_DEMAND,
  "FVG": FAMILY_SUPPLY_DEMAND,
  "iFVG": FAMILY_SUPPLY_DEMAND,
  "CRT": FAMILY_SUPPLY_DEMAND,
  "Confluence Zone": FAMILY_SUPPLY_DEMAND,
  # Legacy display names (kept for open plans / historical events):
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
  key = str(strategy or "")
  mapped = _STRATEGY_FAMILY.get(key)
  if mapped is not None:
    return mapped
  # Forward-compatible HFS labels (publish falls back to "HFS {archetype}").
  if key.startswith("HFS "):
    return FAMILY_RANGE_REVERSION
  return FAMILY_UNKNOWN


def policy_for(strategy: str, cfg: Any | None = None) -> ExecutionPolicy:
  family = strategy_family(strategy)
  if family == FAMILY_UNKNOWN:
    raise ValueError(f"unknown execution strategy: {strategy}")
  base = _DEFAULT_POLICIES[family]
  if cfg is None:
    cfg = _default_runtime_cfg()
  range_drift = float(
    cfg.execution.range.max_entry_drift_atr
    if cfg.execution.range.max_entry_drift_atr is not None
    else base.max_entry_drift_atr
  )
  trend_drift = float(
    cfg.execution.trend.max_entry_drift_atr
    if cfg.execution.trend.max_entry_drift_atr is not None
    else base.max_entry_drift_atr
  )
  map_drift = float(
    cfg.execution.mapped_zone.max_entry_drift_atr
    if cfg.execution.mapped_zone.max_entry_drift_atr is not None
    else base.max_entry_drift_atr
  )
  drift_overrides = {
    FAMILY_RANGE_REVERSION: range_drift,
    FAMILY_TREND_PULLBACK: trend_drift,
    FAMILY_BREAKOUT_RETEST: trend_drift,
    FAMILY_MOMENTUM_CONTINUATION: trend_drift,
    FAMILY_MAPPED_ZONE_REACTION: map_drift,
    FAMILY_KEY_LEVEL: map_drift,
    FAMILY_SUPPLY_DEMAND: map_drift,
    FAMILY_SESSION_LEVEL: map_drift,
    FAMILY_TRENDLINE: trend_drift,
  }
  range_min_rr = float(
    cfg.execution.range.min_rr
    if cfg.execution.range.min_rr is not None
    else base.min_reward_risk
  )
  return ExecutionPolicy(
    family=base.family,
    min_confluence=base.min_confluence,
    max_entry_drift_atr=drift_overrides.get(family, base.max_entry_drift_atr),
    max_entry_drift_pips=base.max_entry_drift_pips,
    max_zone_width_atr=base.max_zone_width_atr,
    min_target_room_atr=base.min_target_room_atr,
    min_reward_risk=range_min_rr if family == FAMILY_RANGE_REVERSION else base.min_reward_risk,
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
  available_target_room_pips: float | None = None,
) -> ExecutionPolicyEvaluation:
  """Enforce every declared setup policy before candidate publication."""
  if cfg is None:
    cfg = _default_runtime_cfg()
  symbol = str(getattr(match, "symbol", "") or "")
  instrument_cfg = _instrument_context(symbol, cfg)
  execution = instrument_cfg.execution
  try:
    policy = policy_for(
      str(getattr(match, "strategy", "")),
      SimpleNamespace(execution=execution),
    )
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
  digits = _instrument_digits("", instrument_cfg)
  zone_scaling = execution.zone_scaling
  execution_entry = execution.entry
  reaction_execution = execution.reaction
  route_plan = resolve_execution_route_plan(
    direction=direction,
    order_type_preference=policy.order_type_preference,
    entry_distribution=entry_distribution,
    executable_quote=quote,
    zone_low=low,
    zone_high=high,
    atr=atr,
    zone_fill_enabled=bool(zone_scaling.fill_enabled),
    zone_fill_min_atr=float(zone_scaling.fill_min_atr or 0.5),
    inside_zone_market_entry_enabled=bool(
      execution_entry.inside_zone_market_entry_enabled
    ),
    zone_fill_fallback_enabled=bool(zone_scaling.fill_fallback_enabled),
    digits=digits,
    allow_either=False,
    scale_first_leg_fraction=float(
      zone_scaling.first_leg_fraction or 0.80
    ),
    scale_step_atr=float(zone_scaling.scale_step_atr or 0.5),
    reaction_scale_enabled=bool(
      instrument_cfg.strategies.reaction.scale_enabled
    ),
    reaction_market_fraction=float(
      reaction_execution.market_fraction or 0.80
    ),
    reaction_scale_fraction=float(
      reaction_execution.scale_fraction or 0.20
    ),
    reaction_scale_step_atr=float(
      reaction_execution.scale_step_atr or 0.5
    ),
    reaction_scale_invalid_policy=str(
      reaction_execution.scale_invalid_policy or "single_market"
    ),
    strategy=str(getattr(match, "strategy", "") or ""),
    strategy_family=str(
      getattr(match, "family", None)
      or getattr(match, "strategy_family", None)
      or strategy_family(str(getattr(match, "strategy", "") or ""))
    ),
    entry_clips=int(getattr(
      getattr(instrument_cfg, "targeting", None),
      "entry_clips",
      SCALP_MICRO_CLIPS,
    )),
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
  stop_bounds_measured: dict[str, Any] = {}
  opposing_zone = None
  # Scalp tiers book sizing_risk_multiplier x the equity-table lots at the
  # same equity band (owner 2026-08-06). Left alone, stop geometry computed
  # below stays at the 1x envelope while volume doubles -- dollar risk
  # (lots x stop_distance) doubles with it. Shrink the pip envelope by the
  # same multiplier here so a 2x-volume scalp risks the same dollars as a
  # 1x reaction trade, not double.
  range_scalp = is_scalp_strategy(
    str(getattr(match, "strategy", "") or ""),
    family=str(getattr(match, "family", "") or strategy_family(
      str(getattr(match, "strategy", "") or ""),
    )),
    strategy_mode=str(getattr(match, "strategy_mode", "") or ""),
  )
  match_risk_multiplier = risk_multiplier_for_tier(
    str(getattr(match, "tier", None) or TIER_B),
    instrument_cfg,
    post_impulse=bool(
      getattr(match, "range_state", None) == "post_impulse_range"
    ),
    one_sided=str(getattr(match, "strategy", "") or "")
    == "One-Sided Range Reaction",
    range_scalp=range_scalp,
  )
  sizing_risk_multiplier = match_risk_multiplier * policy.risk_multiplier
  if not math.isfinite(sizing_risk_multiplier) or sizing_risk_multiplier <= 0:
    sizing_risk_multiplier = 1.0
  try:
    strategy_name = str(getattr(match, "strategy", ""))
    # Prefer effective remaining room (fitted / hybrid-capped) over the raw
    # ladder max so reaction SL tracks what the trade can actually reach.
    primary_tp = (
      remaining_pips
      if remaining_pips > 0
      else primary_tp_pips_from_match(match)
    )
    # Known ahead of the stop-bounds call so a wide room never collapses
    # [min, max] to a single point for a multi-leg group stop -- see
    # stop_bounds_for_reaction_room's for_group_stop docstring note.
    leg_prices = list(route_plan.planned_leg_entry_prices or ())
    leg_ratios = list(route_plan.planned_leg_volume_ratios or ())
    use_group_stop = (
      len(leg_prices) >= 2
      and len(leg_ratios) == len(leg_prices)
      and (
        entry_distribution == "zone_scale"
        or is_scalp_strategy(
          strategy_name,
          family=str(getattr(match, "family", "") or ""),
          strategy_mode=str(getattr(match, "strategy_mode", "") or ""),
        )
      )
    )
    (
      minimum_stop_pips,
      maximum_stop_pips,
      stop_bounds_measured,
    ) = stop_bounds_for_reaction_room(
      strategy=strategy_name,
      primary_tp_pips=primary_tp,
      pip_size=pip,
      cfg=instrument_cfg,
      for_group_stop=use_group_stop,
      symbol=symbol,
    )
    if sizing_risk_multiplier > 1.0:
      stop_bounds_measured = {
        **stop_bounds_measured,
        "stop_bounds_pre_sizing_min_pips": minimum_stop_pips,
        "stop_bounds_pre_sizing_max_pips": maximum_stop_pips,
        "sizing_risk_multiplier": sizing_risk_multiplier,
      }
      minimum_stop_pips = max(1, int(minimum_stop_pips / sizing_risk_multiplier))
      maximum_stop_pips = max(
        minimum_stop_pips, int(maximum_stop_pips / sizing_risk_multiplier),
      )
    if (
      is_reaction_strategy(strategy_name)
      or is_zone_strategy(strategy_name)
      or is_technique_or_confluence(strategy_name)
    ):
      reaction_cap = int(
        getattr(getattr(execution, "reaction", None), "stop_max_pips", 60)
        or 60
      )
      if maximum_stop_pips > reaction_cap:
        stop_bounds_measured = {
          **stop_bounds_measured,
          "stop_bounds_hard_capped_pips": reaction_cap,
          "stop_bounds_pre_hard_cap_pips": maximum_stop_pips,
        }
        maximum_stop_pips = reaction_cap
      if minimum_stop_pips > maximum_stop_pips:
        minimum_stop_pips = maximum_stop_pips
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
    # Scalp (Range / HFS) with fitted target room ignores HTF opposing stop
    # push/reject; native room is the gate. Envelope still applies.
    if match_bypasses_opposing_structure(match):
      zone_low = zone_high = zone_id = None
    opposing_zone = opposing_zone_context_from_values(
      opposing_zone_low=zone_low,
      opposing_zone_high=zone_high,
      opposing_zone_id=(
        str(zone_id) if zone_id is not None else None
      ),
      direction=direction,
      atr=atr,
      pip_size=pip,
      cfg=SimpleNamespace(execution=execution),
    )
    digits = _instrument_digits("", instrument_cfg)
    structure_buffer_atr = float(execution.scaling.add.stop_buffer_atr)
    wick_buffer_atr = float(execution.stops.wick_stop_buffer_atr)
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
  if (
    stop_plan is not None
    and (
      is_reaction_strategy(strategy_name)
      or is_zone_strategy(strategy_name)
      or is_technique_or_confluence(strategy_name)
    )
  ):
    reaction_cap = int(
      getattr(getattr(execution, "reaction", None), "stop_max_pips", 60)
      or 60
    )
    if float(stop_plan.final_stop_pips) > reaction_cap + 1e-9:
      stop_plan_error = "stop_exceeds_reaction_hard_cap"
      stop_plan_error_measured = {
        "final_stop_pips": float(stop_plan.final_stop_pips),
        "reaction_stop_max_pips": reaction_cap,
      }
      stop_plan = None
  reward_risk = (
    max(0.0, remaining_pips) / float(stop_plan.final_stop_pips)
    if stop_plan is not None and stop_plan.final_stop_pips > 0
    else 0.0
  )
  raw_risk_multiplier = getattr(match, "risk_multiplier", 1.0)
  # Never trust a stale Redis/zone-watch stamp for volume. Live Trend Pullback
  # Tier B kept booking risk_multiplier=0.5 after the helper returned 1.0
  # because analysis:zone_watch_candidate still held the pre-fix field.
  stamped_risk_multiplier = float(
    1.0 if raw_risk_multiplier is None else raw_risk_multiplier
  )
  # range_scalp / match_risk_multiplier already resolved above (needed
  # early to shrink the stop envelope for the same 2x-volume scalp tiers).
  # FX pack volume (manual.risk_multiplier=1.5) applies to autonomous
  # fixed_rr *reaction* only — never fold it into sizing_risk_multiplier
  # or the stop envelope would shrink the way scalp 2x does. Scalp already
  # books range_max (2.0 → engine clamps to 1.5 below $2k); do not stack.
  instrument_volume_multiplier = 1.0
  if (
    not range_scalp
    and _instrument_fixed_targeting("", instrument_cfg) is not None
  ):
    instrument_volume_multiplier = _instrument_volume_multiplier(
      instrument_cfg,
    )
  effective_risk_multiplier = (
    sizing_risk_multiplier * instrument_volume_multiplier
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
    "stamped_risk_multiplier": stamped_risk_multiplier,
    "instrument_volume_multiplier": instrument_volume_multiplier,
    "effective_risk_multiplier": effective_risk_multiplier,
    "order_type_preference": policy.order_type_preference,
    "entry_distribution": entry_distribution,
    "planned_execution_route": planned_route,
    "planned_market_immediate": route_plan.immediate_market,
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
    **stop_bounds_measured,
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
      "stop_exceeds_envelope_furthest_leg",
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
  fixed_targeting = _instrument_fixed_targeting("", instrument_cfg)
  if fixed_targeting is not None:
    preferred_reward_risk = float(fixed_targeting.reward_risk)
    entry_value = Decimal(str(planned_entry))
    stop_value = stop_plan.final_stop_price
    risk_distance = abs(entry_value - stop_value)
    quantum = Decimal(1).scaleb(-_instrument_digits("", instrument_cfg))
    configured_target_r_multiples = tuple(
      float(value) for value in fixed_targeting.target_r_multiples
    )
    available_room = (
      float(available_target_room_pips)
      if available_target_room_pips is not None
      and math.isfinite(float(available_target_room_pips))
      else remaining_pips
      if target_model in {"absolute", "hybrid"}
      else None
    )

    def _fixed_target_geometry(
      multiples: tuple[float, ...],
    ) -> tuple[list[Decimal], list[Decimal]]:
      prices: list[Decimal] = []
      pips_values: list[Decimal] = []
      for raw_multiple in multiples:
        reward_distance = risk_distance * Decimal(str(raw_multiple))
        target_value = (
          entry_value + reward_distance
          if direction == "BUY"
          else entry_value - reward_distance
        ).quantize(quantum, rounding=ROUND_HALF_UP)
        prices.append(target_value)
        pips_values.append(
          abs(target_value - entry_value) / Decimal(str(pip))
        )
      return prices, pips_values

    target_r_multiples = configured_target_r_multiples
    target_close_ratios = tuple(
      float(value) for value in fixed_targeting.close_ratios
    )
    target_values, target_pips_values = _fixed_target_geometry(
      target_r_multiples,
    )
    preferred_target_pips = target_pips_values[-1]
    fallback_reward_risk = min(1.0, preferred_reward_risk)
    fallback_values, fallback_pips_values = _fixed_target_geometry(
      (fallback_reward_risk,),
    )
    fallback_target_pips = fallback_pips_values[-1]
    fallback_used = False
    if (
      available_room is not None
      and available_room + 1e-9 < float(preferred_target_pips)
    ):
      if available_room + 1e-9 >= float(fallback_target_pips):
        target_r_multiples = (fallback_reward_risk,)
        target_close_ratios = (1.0,)
        target_values = fallback_values
        target_pips_values = fallback_pips_values
        fallback_used = True
      else:
        measured.update({
          "target_policy_mode": "fixed_rr",
          "target_preferred_reward_risk": preferred_reward_risk,
          "target_fallback_reward_risk": fallback_reward_risk,
          "available_target_room_pips": round(available_room, 3),
          "preferred_target_pips": format(preferred_target_pips, "f"),
          "minimum_target_pips": format(fallback_target_pips, "f"),
        })
        return ExecutionPolicyEvaluation(
          False,
          "fixed_rr_room_insufficient",
          (
            f"adaptive fixed RR needs at least {fallback_reward_risk:.2f}R "
            f"({float(fallback_target_pips):.1f} pips), remaining "
            f"{available_room:.1f}"
          ),
          True,
          measured,
          policy,
        )
    final_target_pips = target_pips_values[-1]
    actual_reward_risk = (
      final_target_pips / (risk_distance / Decimal(str(pip)))
      if risk_distance > 0
      else Decimal("0")
    )
    reward_risk = float(actual_reward_risk)
    measured.update({
      "target_policy_mode": "fixed_rr",
      "target_reward_risk": round(reward_risk, 4),
      "target_preferred_reward_risk": preferred_reward_risk,
      "target_fallback_reward_risk": fallback_reward_risk,
      "target_room_fallback_used": fallback_used,
      "planned_target_r_multiples": [
        format(Decimal(str(value)), "f")
        for value in target_r_multiples
      ],
      "planned_target_prices": [
        format(value, "f") for value in target_values
      ],
      "planned_target_pips": [
        format(value, "f") for value in target_pips_values
      ],
      "planned_target_close_ratios": [
        format(Decimal(str(value)), "f")
        for value in target_close_ratios
      ],
      "reward_risk": round(reward_risk, 4),
      "min_reward_risk": fallback_reward_risk,
    })
    trail_after_r = getattr(fixed_targeting, "trail_after_r", None)
    trail_to_r = getattr(fixed_targeting, "trail_to_r", None)
    if (
      not fallback_used
      and trail_after_r is not None
      and trail_to_r is not None
    ):
      trail_after_index = next(
        index for index, value in enumerate(target_r_multiples)
        if math.isclose(
          value, float(trail_after_r), rel_tol=0.0, abs_tol=1e-9,
        )
      )
      trail_to_index = next(
        index for index, value in enumerate(target_r_multiples)
        if math.isclose(
          value, float(trail_to_r), rel_tol=0.0, abs_tol=1e-9,
        )
      )
      measured.update({
        "planned_trail_after_target_id": f"TP{trail_after_index + 1}",
        "planned_trail_to_target_id": f"TP{trail_to_index + 1}",
      })
    if available_room is not None:
      measured["available_target_room_pips"] = round(available_room, 3)
  if (
    not math.isfinite(effective_risk_multiplier)
    or effective_risk_multiplier <= 0
  ):
    return ExecutionPolicyEvaluation(
      False,
      "invalid_risk_multiplier",
      "effective autonomous risk multiplier must be within (0, max]",
      True,
      measured,
      policy,
    )
  max_risk_multiplier = 1.0
  if policy.family == FAMILY_RANGE_REVERSION:
    max_risk_multiplier = float(
      instrument_cfg.risk.sizing.range_max_risk_multiplier or 2.0
    )
    if not math.isfinite(max_risk_multiplier) or max_risk_multiplier <= 0:
      max_risk_multiplier = 2.0
  # Autonomous FX fixed_rr reaction may book pack volume (1.5× table).
  if fixed_targeting is not None:
    pack_ceiling = _instrument_volume_multiplier(instrument_cfg)
    if pack_ceiling > max_risk_multiplier:
      max_risk_multiplier = pack_ceiling
  if effective_risk_multiplier > max_risk_multiplier:
    return ExecutionPolicyEvaluation(
      False,
      "invalid_risk_multiplier",
      "effective autonomous risk multiplier must be within (0, max]",
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


def risk_multiplier_for_tier(tier: str, cfg: Any | None = None, *, post_impulse: bool = False, one_sided: bool = False, range_scalp: bool = False) -> float:
  """Resolve volume multiplier for equity-table sizing.

  Owner 2026-08-06:
  - Scalp (HFS / Range Edge / Box) books **2×** equity-table lots.
  - Reaction / swing books **full** equity-table lots on every quality
    tier (A/B/C). Tier stars remain card/telemetry only — live Trend
    Pullback ⭐⭐ was half-sizing to 0.05 on a $887 → 0.10 table because
    Tier B was 0.5.
  - ``post_impulse`` / ``one_sided`` may still soft-cap below full table
    when those flags apply.
  """
  if cfg is None:
    cfg = _default_runtime_cfg()
  sizing = cfg.risk.sizing
  if range_scalp:
    scalp_mult = float(sizing.range_max_risk_multiplier)
    if not math.isfinite(scalp_mult) or scalp_mult <= 0:
      scalp_mult = 2.0
    return scalp_mult
  # Equity-table reaction: ignore tier A/B/C shrink.
  _ = tier
  mult = 1.0
  post = float(sizing.post_impulse_risk_multiplier)
  onesided = float(sizing.one_sided_range_risk_multiplier)
  if post_impulse:
    mult = min(mult, post)
  if one_sided:
    mult = min(mult, onesided)
  return max(0.0, mult)


def _family_min_drift_pips(cfg: Any, family: str) -> float | None:
  """Canonical read of the per-family minimum entry-drift setting.

  Returns None when the family does not have a configured minimum (e.g.
  outside the mapped ``_FAMILY_MIN_DRIFT_SETTING`` keys); the caller
  substitutes ``0.0`` for that case, matching the retired
  ``getattr(cfg, name, 0.0)`` fallback.
  """
  execution = cfg.execution
  if family == FAMILY_RANGE_REVERSION:
    return float(execution.range.min_entry_drift_pips)
  if family in {
    FAMILY_TREND_PULLBACK,
    FAMILY_BREAKOUT_RETEST,
    FAMILY_MOMENTUM_CONTINUATION,
    FAMILY_TRENDLINE,
  }:
    return float(execution.trend.min_entry_drift_pips)
  if family in {
    FAMILY_MAPPED_ZONE_REACTION,
    FAMILY_KEY_LEVEL,
    FAMILY_SUPPLY_DEMAND,
    FAMILY_SESSION_LEVEL,
  }:
    return float(execution.mapped_zone.min_entry_drift_pips)
  return None


def _family_hard_drift_pips(cfg: Any, family: str) -> float | None:
  execution = cfg.execution
  if family == FAMILY_RANGE_REVERSION:
    return float(execution.range.hard_entry_drift_pips)
  if family in {
    FAMILY_TREND_PULLBACK,
    FAMILY_BREAKOUT_RETEST,
    FAMILY_MOMENTUM_CONTINUATION,
    FAMILY_TRENDLINE,
  }:
    return float(execution.trend.hard_entry_drift_pips)
  if family in {
    FAMILY_MAPPED_ZONE_REACTION,
    FAMILY_KEY_LEVEL,
    FAMILY_SUPPLY_DEMAND,
    FAMILY_SESSION_LEVEL,
  }:
    return float(execution.mapped_zone.hard_entry_drift_pips)
  return None
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
  if cfg is None:
    cfg = _default_runtime_cfg()
  policy = policy_for(strategy, cfg)
  family = strategy_family(strategy)
  pip = pip_size if pip_size > 0 else 0.1
  atr_pips = (atr / pip) * policy.max_entry_drift_atr if atr > 0 else 0.0
  configured = policy.max_entry_drift_pips
  # Do NOT fold AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS into adaptive drift.
  # Adaptive drift is observation tolerance; executor distance is a separate
  # hard publication gate (see entry_distance.measure_entry_distance).
  family_min = _family_min_drift_pips(cfg, family)
  configured_minimum = 0.0 if family_min is None else family_min
  adaptive_floor = max(configured, configured_minimum, atr_pips)
  room_cap = float("inf")
  if remaining_target_room_pips is not None:
    room_cap = max(0.0, remaining_target_room_pips)
  family_hard = _family_hard_drift_pips(cfg, family)
  default_hard_cap = _FAMILY_HARD_DRIFT_DEFAULT.get(family, configured)
  hard_cap = default_hard_cap if family_hard is None else family_hard
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
