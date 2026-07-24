"""Setup-aware execution policy, quality tiers, and strategy families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


def max_entry_drift_pips(
  *,
  strategy: str,
  atr: float,
  pip_size: float,
  remaining_target_room_pips: float | None,
  cfg: Any | None = None,
) -> tuple[float, dict[str, float]]:
  """Strategy-aware drift: min(configured pips, ATR×mult, room×fraction)."""
  policy = policy_for(strategy, cfg)
  pip = pip_size if pip_size > 0 else 0.1
  atr_pips = (atr / pip) * policy.max_entry_drift_atr if atr > 0 else policy.max_entry_drift_pips
  configured = policy.max_entry_drift_pips
  if cfg is not None:
    configured = max(
      configured,
      float(getattr(cfg, "auto_trade_max_entry_distance_pips", configured)),
    )
  room_cap = configured
  if remaining_target_room_pips is not None and remaining_target_room_pips > 0:
    room_cap = remaining_target_room_pips * 0.45
  limit = min(configured, atr_pips, room_cap)
  measured = {
    "configured_pips": round(configured, 3),
    "atr_pips": round(atr_pips, 3),
    "room_cap_pips": round(room_cap, 3),
    "effective_pips": round(max(0.0, limit), 3),
  }
  return max(0.0, limit), measured
