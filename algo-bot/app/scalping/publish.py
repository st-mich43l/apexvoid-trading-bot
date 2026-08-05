"""Live publication bridge: ScalpOpportunity → StrategyMatch → TradePlan V7."""

from __future__ import annotations

from dataclasses import replace
import logging
import time
from typing import Any

from app.analysis import scanner
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
  target_pips = max(1, int(round(float(opportunity.expected_target_pips))))
  htf_bias = str(context.htf_bias or "range")
  if htf_bias in {"", "unknown"}:
    htf_bias = "range"
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
    reasons=tuple(opportunity.reasons) or ("hfs",),
    atr=float(context.atr or 1.0),
    structure_swing=float(opportunity.invalidation_price),
    targets_pips=(target_pips,),
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
