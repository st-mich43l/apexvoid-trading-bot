"""Live publication bridge: ScalpOpportunity → StrategyMatch → TradePlan V7."""

from __future__ import annotations

from dataclasses import replace
import logging
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
  return STRATEGY_DISPLAY.get(archetype, f"HFS {archetype}")


def _structural_kind(opportunity: ScalpOpportunity) -> str:
  return "demand" if opportunity.direction.upper() == "BUY" else "supply"


def _hfs_target_ladder(
  opportunity: ScalpOpportunity,
) -> tuple[int, tuple[int, ...]]:
  """Final TP pips + published ladder.

  Owner 2026-08-11: when the scalp selected 1:2, book half at 1R and half
  at 2R (no trail / no BE). 1:1 stays a single full-exit TP.
  """
  final_pips = max(1, int(round(float(opportunity.expected_target_pips))))
  stop_raw = float(opportunity.expected_stop_pips or 0.0)
  stop_pips = max(1, int(round(stop_raw))) if stop_raw > 0 else None
  rr = float(opportunity.expected_reward_risk or 0.0)
  is_one_to_two = (
    stop_pips is not None
    and (
      rr >= 1.9
      or final_pips >= int(round(stop_raw * 1.9))
    )
  )
  if is_one_to_two and stop_pips is not None:
    one_r = stop_pips
    two_r = max(one_r + 1, final_pips, int(round(stop_raw * 2.0)))
    return two_r, (one_r, two_r)
  return final_pips, (final_pips,)


def build_hfs_strategy_match(
  opportunity: ScalpOpportunity,
  context: ScalpContextSnapshot,
  *,
  bar_ts: int,
  quote_bid: float,
  quote_ask: float,
  location_reason: str | None = None,
) -> StrategyMatch:
  strategy = _strategy_name(opportunity.archetype)
  structural_id = opportunity.episode_id or opportunity.opportunity_id
  touch = str(opportunity.trigger_bar_ts)
  confirm = str(bar_ts)
  match_id = structural_thesis_id(
    symbol=opportunity.symbol,
    strategy=strategy,
    direction=opportunity.direction,
    structural_source="hfs",
    structural_id=structural_id,
    touch_bar_ts=touch,
    confirmation_bar_ts=confirm,
  )
  mid = (float(quote_bid) + float(quote_ask)) / 2.0
  now = int(bar_ts)
  expires = max(now + 60, int(opportunity.expires_at))
  target_pips, targets_pips = _hfs_target_ladder(opportunity)
  htf_bias = str(context.htf_bias or "range")
  if htf_bias in {"", "unknown"}:
    htf_bias = "range"
  # HFS matches never pass through the classic scanner.py detection path,
  # so _static_execution_eligibility() never runs for them. Without this,
  # _admit_strategy_intent_for_cycle's static-eligibility gate (which only
  # exempts strategy_mode == "mapped_zone_reaction") sees a bare
  # source="scanner_strategy_match" intent with execution_eligibility=None
  # and hard-rejects it as static_eligibility_missing -- every single HFS
  # opportunity, unconditionally. The ScalpOpportunity pipeline already is
  # this match's eligibility check, so mark it eligible by construction.
  eligibility = ExecutionEligibility(
    version=EXECUTION_ELIGIBILITY_VERSION,
    allowed=True,
    state=STATIC_ELIGIBLE,
    reason_code="hfs_scalp_eligible",
    message="HFS scalp opportunity is executable by construction",
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
    strategy_mode="hfs_scalp",
    direction=opportunity.direction.upper(),
    key_level=float(opportunity.key_level),
    entry_low=float(opportunity.zone_low),
    entry_high=float(opportunity.zone_high),
    current_price=mid,
    confluence=3,
    execution_eligibility=eligibility,
    reasons=tuple(opportunity.reasons) or ("hfs",),
    atr=float(context.atr or 1.0),
    structure_swing=float(opportunity.invalidation_price),
    targets_pips=targets_pips,
    # Fitted HFS room unlocks opposing-structure bypass (native scalp room).
    full_take_profit_pips=target_pips,
    absolute_target_price=float(opportunity.expected_target_price),
    tier="A",
    family="hfs",
    structural_source="hfs",
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
  )


async def _persist_hfs_match(client: Any, match: StrategyMatch) -> StrategyMatch:
  """Write HFS StrategyMatch into the same Redis keys the worker reads.

  Live 2026-08-05: publish_hfs_live advanced setup lifecycle to CONFIRMED
  but never persisted the match under strategy_match:/strategy_matches:.
  try_publish → _handle_event then loaded zero matches for ready_match_id
  and returned remained_watching / zone_watching_retest until expiry.
  Mirror zone_execution_cutover._persist_match (without double-advancing
  lifecycle — caller already did that).
  """
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


async def publish_hfs_live(
  client: Any,
  match: StrategyMatch,
  *,
  symbol: str,
  bar_ts: int,
) -> worker.PublishResult | None:
  """Advance setup lifecycle and publish via the authoritative worker path."""
  if not bool(runtime_config.runtime.auto_trade.enabled):
    log.warning("HFS live publish blocked: runtime.auto_trade.enabled=false")
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
      "HFS live publish: setup lifecycle advance failed match_id=%s",
      match.match_id,
    )
    return None

  _setup_id, thesis_id = lifecycle
  stamped = replace(match, thesis_id=str(thesis_id))
  stamped = await _persist_hfs_match(client, stamped)
  # Root-card creation lives centrally in worker._publish_trade_plan_v8
  # (called from _handle_event, which this and every other publish route
  # -- including this cycle's own independent arbitration re-discovering
  # the same persisted match -- funnels through). Ensuring it here too
  # would only be redundant with that single shared choke point.
  result = await worker.try_publish_executable_signal(
    client,
    stamped,
    symbol=symbol,
    event_ts=str(bar_ts),
  )
  log.info(
    "HFS live publish match_id=%s status=%s reason=%s plan_id=%s",
    stamped.match_id,
    getattr(result, "status", None),
    getattr(result, "reason_code", None),
    getattr(result, "plan_id", None),
  )
  return result
