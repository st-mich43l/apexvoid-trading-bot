"""Setup-aware execution policy, quality tiers, and strategy families."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

GUARD_MODE_OBSERVE = "observe"
GUARD_MODE_BALANCED = "balanced"
GUARD_MODE_STRICT = "strict"
_GUARD_MODES = (GUARD_MODE_OBSERVE, GUARD_MODE_BALANCED, GUARD_MODE_STRICT)

OUTCOME_ALLOW = "allow"
OUTCOME_ALLOW_WITH_WARNING = "allow_with_warning"
OUTCOME_ADJUST_TARGET = "adjust_target"
OUTCOME_WAIT = "wait"
OUTCOME_BLOCK = "block"


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

  ``hard_geometry`` marks conditions with zero tradeable room by
  construction (entry contained inside a barrier, not merely approaching
  one) - these stay blocking outside ``observe`` even when the softer
  ahead-of-entry/overlap/cooldown conditions have been downgraded to
  telemetry, matching demo_eval's own "hard blocking remains only for
  technical correctness" carve-out for genuinely zero-room geometry.
  """
  if guard_mode == GUARD_MODE_STRICT:
    return ExecutionGuardDecision(
      guard, OUTCOME_BLOCK, condition, reason, True,
    )
  if guard_mode == GUARD_MODE_OBSERVE:
    outcome = OUTCOME_WAIT if hard_geometry else OUTCOME_ALLOW_WITH_WARNING
    return ExecutionGuardDecision(
      guard, outcome, condition, reason, False,
    )
  # balanced: only zero-room containment still blocks; buffer/ATR-based
  # "ahead of entry" and other soft conditions become warnings.
  if hard_geometry:
    return ExecutionGuardDecision(
      guard, OUTCOME_BLOCK, condition, reason, True,
    )
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

_STRATEGY_FAMILY = {
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


_DEFAULT_POLICIES: dict[str, ExecutionPolicy] = {
  FAMILY_RANGE_REVERSION: ExecutionPolicy(
    FAMILY_RANGE_REVERSION, 2, 0.35, 8.0, 1.0, 0.5, 1.10, 1.0,
    "either", ("chop", "range", "unknown"),
  ),
  FAMILY_TREND_PULLBACK: ExecutionPolicy(
    FAMILY_TREND_PULLBACK, 2, 0.75, 15.0, 2.0, 0.6, 1.15, 1.0,
    "limit", ("trend", "breakout", "unknown"),
  ),
  FAMILY_BREAKOUT_RETEST: ExecutionPolicy(
    FAMILY_BREAKOUT_RETEST, 2, 0.85, 18.0, 2.5, 0.7, 1.20, 1.0,
    "either", ("trend", "breakout", "unknown"),
  ),
  FAMILY_MOMENTUM_CONTINUATION: ExecutionPolicy(
    FAMILY_MOMENTUM_CONTINUATION, 2, 1.0, 20.0, 3.0, 0.8, 1.15, 1.0,
    "market", ("trend", "breakout", "unknown"),
  ),
  FAMILY_LIQUIDITY_REVERSAL: ExecutionPolicy(
    FAMILY_LIQUIDITY_REVERSAL, 2, 0.45, 10.0, 1.5, 0.55, 1.15, 0.75,
    "either", ("chop", "range", "trend", "unknown"),
  ),
  FAMILY_MAPPED_ZONE_REACTION: ExecutionPolicy(
    FAMILY_MAPPED_ZONE_REACTION, 2, 0.40, 10.0, 2.0, 0.6, 1.15, 1.0,
    "either", ("chop", "range", "trend", "breakout", "unknown"),
  ),
}


def strategy_family(strategy: str) -> str:
  return _STRATEGY_FAMILY.get(strategy, FAMILY_TREND_PULLBACK)


def policy_for(strategy: str, cfg: Any | None = None) -> ExecutionPolicy:
  family = strategy_family(strategy)
  base = _DEFAULT_POLICIES.get(
    family, _DEFAULT_POLICIES[FAMILY_TREND_PULLBACK],
  )
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
  """Tier A = full risk, Tier B = reduced risk, Tier C = analysis only."""
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
  if tier == TIER_C:
    return 0.0
  a = float(getattr(cfg, "auto_trade_tier_a_risk_multiplier", 1.0) if cfg else 1.0)
  b = float(getattr(cfg, "auto_trade_tier_b_risk_multiplier", 0.5) if cfg else 0.5)
  post = float(getattr(cfg, "auto_trade_post_impulse_risk_multiplier", 0.5) if cfg else 0.5)
  onesided = float(getattr(cfg, "auto_trade_one_sided_range_risk_multiplier", 0.5) if cfg else 0.5)
  mult = a if tier == TIER_A else b
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
}
_FAMILY_HARD_DRIFT_SETTING = {
  FAMILY_RANGE_REVERSION: "auto_trade_range_hard_entry_drift_pips",
  FAMILY_TREND_PULLBACK: "auto_trade_trend_hard_entry_drift_pips",
  FAMILY_BREAKOUT_RETEST: "auto_trade_trend_hard_entry_drift_pips",
  FAMILY_MOMENTUM_CONTINUATION: "auto_trade_trend_hard_entry_drift_pips",
  FAMILY_MAPPED_ZONE_REACTION: "auto_trade_map_hard_entry_drift_pips",
}
_FAMILY_HARD_DRIFT_DEFAULT = {
  FAMILY_RANGE_REVERSION: 20.0,
  FAMILY_TREND_PULLBACK: 30.0,
  FAMILY_BREAKOUT_RETEST: 30.0,
  FAMILY_MOMENTUM_CONTINUATION: 30.0,
  FAMILY_MAPPED_ZONE_REACTION: 20.0,
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
  if cfg is not None:
    configured = max(
      configured,
      float(getattr(cfg, "auto_trade_max_entry_distance_pips", configured)),
    )
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
