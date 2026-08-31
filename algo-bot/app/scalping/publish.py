"""Live publication bridge: ScalpOpportunity → StrategyMatch → TradePlan V8."""

from __future__ import annotations

from dataclasses import replace
import logging
import math
import time
from typing import Any

from app.analysis import scanner
from app.analysis.execution_eligibility import (
  EXECUTION_ELIGIBILITY_VERSION,
  STATIC_ELIGIBLE,
  ExecutionEligibility,
)
from app.analysis.structural_reaction_support import structural_thesis_id
from app.autotrade import worker
from app.autotrade.multi_match import (
  dedupe_matches,
  deserialize_matches,
  serialize_matches,
  strategy_matches_key,
)
from app.autotrade.strategy_match import StrategyMatch, strategy_match_key
from app.core.config import runtime_config
from app.scalping.models import (
  STRATEGY_DISPLAY,
  ScalpContextSnapshot,
  ScalpOpportunity,
)


log = logging.getLogger(__name__)


def _strategy_name(archetype: str) -> str:
  return STRATEGY_DISPLAY.get(archetype, f"{archetype.replace('_', ' ').title()} Scalp")


def _structural_kind(opportunity: ScalpOpportunity) -> str:
  return "demand" if opportunity.direction.upper() == "BUY" else "supply"


def _scalp_target_ladder(
  opportunity: ScalpOpportunity,
  cfg: Any | None = None,
) -> tuple[int, tuple[int, ...]]:
  """Final TP pips + published ladder.

  XAU discovery picks exactly 1:2 or 1:1 room:

  - **1:2** → ladder ``(1R, 2R)`` with equal close ratios (50% / 50%);
    after TP1 books, management moves SL to BE for the runner.
  - **1:1** → single target at 1R; equal-ratio builder assigns
    ``close_ratio=1.0`` so the engine books **full volume** at that print.

  Technique ``fixed_rr`` on XAU must not collapse this ladder — scalp keeps
  its own 1R/2R book; technique R expansion applies only to non-scalp
  strategies in execution policy.
  """
  final_pips = max(1, int(round(float(opportunity.expected_target_pips))))
  stop = max(1, int(round(float(opportunity.expected_stop_pips))))
  try:
    rr = float(opportunity.expected_reward_risk)
  except (TypeError, ValueError):
    rr = (final_pips / stop) if stop else 1.0
  if rr <= 1.05 or final_pips <= stop:
    return final_pips, (final_pips,)
  first = stop
  last = max(first, min(final_pips, stop * 2))
  if last <= first:
    return last, (last,)
  return last, (first, last)


def build_scalp_strategy_match(
  opportunity: ScalpOpportunity,
  context: ScalpContextSnapshot,
  *,
  bar_ts: int,
  quote_bid: float,
  quote_ask: float,
  location_reason: str | None = None,
  cfg: Any | None = None,
) -> StrategyMatch:
  strategy = _strategy_name(opportunity.archetype)
  structural_id = opportunity.episode_id or opportunity.opportunity_id
  touch = str(opportunity.trigger_bar_ts)
  confirm = str(bar_ts)
  match_id = structural_thesis_id(
    symbol=opportunity.symbol,
    strategy=strategy,
    direction=opportunity.direction,
    structural_source="scalp",
    structural_id=structural_id,
    touch_bar_ts=touch,
    confirmation_bar_ts=confirm,
  )
  mid = (float(quote_bid) + float(quote_ask)) / 2.0
  now = int(bar_ts)
  expires = max(now + 60, int(opportunity.expires_at))
  target_pips, targets_pips = _scalp_target_ladder(opportunity, cfg)
  htf_bias = str(context.htf_bias or "range")
  if htf_bias in {"", "unknown"}:
    htf_bias = "range"
  try:
    from app.autotrade import units as _units

    pip = float(_units.pip_size(opportunity.symbol))
  except Exception:
    pip = 0.1 if str(opportunity.symbol).upper() in {"XAU", "XAUUSD"} else 0.0001
  if not math.isfinite(pip) or pip <= 0:
    pip = 0.1
  entry = float(opportunity.trigger_price)
  structure_swing = float(opportunity.invalidation_price)
  # Risk is anchored on the worst-case fill inside the zone, not the trigger close.
  worst_fill = (
    float(opportunity.zone_high)
    if str(opportunity.direction).upper() == "BUY"
    else float(opportunity.zone_low)
  )
  derived_stop = abs(worst_fill - structure_swing) / pip
  expected_stop = float(opportunity.expected_stop_pips)
  if abs(derived_stop - expected_stop) > 1e-6:
    log.warning(
      "scalp stop invariant broken opportunity_id=%s trigger=%s "
      "worst_fill=%s invalidation=%s derived_stop_pips=%s expected_stop_pips=%s",
      opportunity.opportunity_id,
      entry,
      worst_fill,
      structure_swing,
      derived_stop,
      expected_stop,
    )
  # M1 scalp matches never pass through scanner.py detection, so static
  # eligibility is stamped here — the ScalpOpportunity pipeline already
  # ran activation gates.
  eligibility = ExecutionEligibility(
    version=EXECUTION_ELIGIBILITY_VERSION,
    allowed=True,
    state=STATIC_ELIGIBLE,
    reason_code="scalp_m1_eligible",
    message="M1 scalp opportunity is executable by construction",
    hard_block=False,
    direction=opportunity.direction.upper(),
    entry_low=float(opportunity.zone_low),
    entry_high=float(opportunity.zone_high),
    planned_entry_price=mid,
    calculated_at=now,
  )
  return StrategyMatch(
    version=1,
    match_id=match_id,
    symbol=opportunity.symbol.upper(),
    source_tf="M1",
    event_ts=str(bar_ts),
    issued_at=now,
    expires_at=expires,
    strategy=strategy,
    strategy_mode="scalp_m1",
    direction=opportunity.direction.upper(),
    key_level=float(opportunity.key_level),
    entry_low=float(opportunity.zone_low),
    entry_high=float(opportunity.zone_high),
    current_price=mid,
    confluence=3,
    execution_eligibility=eligibility,
    reasons=tuple(opportunity.reasons) or ("scalp_m1",),
    atr=float(context.atr or 1.0),
    structure_swing=float(structure_swing),
    targets_pips=targets_pips,
    full_take_profit_pips=target_pips,
    absolute_target_price=float(opportunity.expected_target_price),
    tier="A",
    family="scalp",
    structural_source="scalp",
    structural_zone_id=structural_id,
    structural_zone_low=float(opportunity.zone_low),
    structural_zone_high=float(opportunity.zone_high),
    structural_kind=_structural_kind(opportunity),
    structural_timeframe="M1",
    htf_bias=htf_bias,
    regime_kind=str(context.regime or "range"),
    touch_bar_ts=touch,
    confirmation_bar_ts=confirm,
    reaction_type=str(opportunity.trigger_type),
    entry_location_source="M5" if context.dealing_range_low is not None else None,
    entry_location_position=opportunity.location_position,
    entry_location_reason=location_reason,
    entry_activation_trigger=str(opportunity.trigger_type),
    entry_activation_trigger_ts=touch,
    math_pd=(
      None if context.dealing_range_position is None
      else float(context.dealing_range_position)
    ),
    math_fib_ratio=_scalp_math_fib_ratio(opportunity),
  )


def _scalp_math_fib_ratio(opportunity: Any) -> float | None:
  measured = getattr(opportunity, "measured", None) or {}
  if isinstance(measured, dict) and measured.get("retracement") is not None:
    try:
      return float(measured["retracement"])
    except (TypeError, ValueError):
      return None
  return None


async def _persist_scalp_match(client: Any, match: StrategyMatch) -> StrategyMatch:
  """Write scalp StrategyMatch into the same Redis keys the worker reads."""
  now = int(time.time())
  current_raw = await client.get(strategy_matches_key(match.symbol))
  current = deserialize_matches(current_raw) if current_raw else []
  active = [item for item in current if item.expires_at >= now]
  combined, _events = dedupe_matches(
    [*active, match],
    atr=match.atr,
  )
  ttl = max(60, int(match.expires_at) - now)
  await client.set(strategy_match_key(match.symbol), match.to_json(), ex=ttl)
  await client.set(
    strategy_matches_key(match.symbol), serialize_matches(combined), ex=ttl,
  )
  return match


async def publish_scalp_live(
  client: Any,
  match: StrategyMatch,
  *,
  symbol: str,
  bar_ts: int,
) -> worker.PublishResult | None:
  """Advance setup lifecycle and publish via the authoritative worker path."""
  if not bool(runtime_config.runtime.auto_trade.enabled):
    log.warning("scalp live publish blocked: runtime.auto_trade.enabled=false")
    return worker.PublishResult(
      status=worker.PUBLISH_STATUS_REJECTED,
      plan_id="",
      reason_code="auto_trade_disabled",
      zone_id=str(match.structural_zone_id or ""),
      setup_id=match.match_id,
    )

  lifecycle = await scanner._advance_setup_to_confirmed(
    client, match, symbol, "M1",
  )
  if lifecycle is None:
    log.warning(
      "scalp live publish: setup lifecycle advance failed match_id=%s",
      match.match_id,
    )
    return None

  _setup_id, thesis_id = lifecycle
  stamped = replace(match, thesis_id=str(thesis_id))
  stamped = await _persist_scalp_match(client, stamped)
  result = await worker.try_publish_executable_signal(
    client,
    stamped,
    symbol=symbol,
    event_ts=str(bar_ts),
  )
  log.info(
    "scalp live publish match_id=%s status=%s reason=%s plan_id=%s",
    stamped.match_id,
    getattr(result, "status", None),
    getattr(result, "reason_code", None),
    getattr(result, "plan_id", None),
  )
  return result
