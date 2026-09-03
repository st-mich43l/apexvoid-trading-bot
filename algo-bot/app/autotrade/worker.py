"""Redis worker for ApexVoid Algo strategies and execution delivery.

The private OHLC strategies consume cTrader bars directly.  Scanner detectors
may also publish a typed completed strategy match; the worker transports that
decision to the executor without confirming it again or routing it by regime.
It never parses rendered Telegram text or imports scanner detector functions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
import asyncio
import hashlib
import json
import logging
import math
from typing import Any, Awaitable, Callable

from app.persistence import redis_state
from app.autotrade import units
from app.core import instrument_geometry
from app.autotrade.range_targets import configured_range_targets
from app.autotrade.candidate_publish import (
  acquire_owned_lock,
  autonomous_cycle_owner_key,
  candidate_key,
  explicit_test_fallback_enabled,
  publish_candidate_atomic,
  publish_ranked_cycle,
  release_owned_lock,
)
from app.autotrade.arbitration import (
  ArbitrationResult,
  CandidatePublicationResult,
  ExecutionIntent,
  arbitrate_execution_intents,
)
from app.autotrade.entry_distance import measure_entry_distance
from app.autotrade.execution_policy import (
  GUARD_MODE_OBSERVE,
  GUARD_MODE_STRICT,
  OUTCOME_ALLOW,
  OUTCOME_ALLOW_WITH_WARNING,
  OUTCOME_WAIT,
  ExecutionGuardDecision,
  GuardOutcome,
  StructuralBarrier,
  StructuralSourceIdentity,
  classify_barrier_relationship,
  classify_guard_severity,
  evaluate_execution_policy,
  is_preference_telemetry,
  max_entry_drift_pips,
  resolve_guard_mode,
  risk_multiplier_for_tier,
)
from app.autotrade.active_exposure import (
  apply_same_direction_stack_sizing,
  evaluate_entry_against_exposure,
  load_active_exposures,
)
from app.autotrade.protective_stop import opposing_zone_fingerprint
from app.autotrade.gate import (
  AutoScalpBox,
  AutoScalpDecision,
  AutoScalpRail,
  evaluate_auto_scalp_gate,
)
from app.autotrade.strategy_match import (
  StrategyMatch,
  strategy_match_key,
)
from app.autotrade.strategy_taxonomy import (
  bypasses_opposing_structure_gates,
  is_breakout_retest_scalp_strategy,
  is_m1_scalp_strategy,
  is_reaction_strategy,
  is_scalp_strategy,
  is_technique_or_confluence,
  match_bypasses_opposing_structure,
)
from app.autotrade.structural_target_room import (
  evaluate_structural_target_room,
  filter_displaced_opposing_entries,
  filter_shared_boundary_opposing_entries,
  zone_proximal_room_reference,
)
from app.autotrade.execution_confirmation import (
  EXPIRED as CONFIRMATION_EXPIRED,
  IMMEDIATE_CONFIRMATION,
  IN_ZONE_WAITING_M1,
  INVALIDATED as CONFIRMATION_INVALIDATED,
  M1_RETEST,
  M5_AUTHORITATIVE,
  PUBLISHED as CONFIRMATION_PUBLISHED,
  TRIGGER_PRICE_LEFT_ZONE,
  TRIGGER_READY,
  WAITING_RETEST,
  ExecutionConfirmation,
  ExecutionConfirmationState,
  confirmation_policy_for,
  deterministic_episode_id,
  executable_quote_in_zone,
  load_execution_confirmation,
  new_state,
  parse_bar_timestamp,
  save_execution_confirmation,
  scalp_maximum_chase_pips,
  scalp_effective_chase_pips,
  scalp_zone_access,
  ZONE_ACCESS_MOMENTUM_CHASE,
  ZONE_ACCESS_RETEST_ONLY,
)
from app.autotrade.multi_match import (
  dedupe_matches,
  deserialize_matches,
  select_primary,
  serialize_matches,
  strategy_matches_key,
)
from app.autotrade.lifecycle import emit_lifecycle, increment_metric
from app.autotrade.setup_lifecycle import (
  ARMED,
  CANCELLED,
  CONFIRMED,
  CONSUMED,
  EXPIRED,
  INVALIDATED,
  PLAN_BUILT,
  PLAN_PUBLISHED,
  TERMINAL_STATES,
  SetupLifecycleError,
  active_thesis_key,
  claim_active_thesis,
  is_publishable_setup_state,
  load_setup,
  normalize_setup_state,
  release_active_thesis,
  transition_setup,
)
from app.autotrade.strategy_match_ready import (
  READY_GROUP,
  READY_STREAM,
  StrategyMatchReadyEvent,
  enqueue_strategy_match_ready,
  ensure_ready_group,
  load_canonical_match,
  ready_consumer_name,
  save_ready_consumer_health,
  save_ready_snapshot,
)
from app.analysis.m1_trigger import (
  evaluate_m1_trigger_window,
  latest_eligible_m1_bar_ts,
)
from app.analysis.confluence_zone import (
  ConfluenceMember,
  claim_confluence_zone,
  release_confluence_zone,
  resolve_confluence_zone_id,
)
from app.autotrade.trade_plan import TradePlanError
from app.autotrade.trade_plan_builder import (
  TradePlanBuildRejected,
  build_trade_plan_from_strategy_match,
)
from app.autotrade.trade_plan_stream import (
  plan_key,
  publish_trade_plan,
  read_plan_state,
)
from app.autotrade.route_outcome import record_route_outcome, route_outcome_key
from app.autotrade.setup_card import save_forming_card_status, edit_forming_card_stop
from app.autotrade.reaction_identity import (
  THESIS_CLAIM_ACQUIRE_LUA,
  ACTIVE_THESIS_STATES,
  advance_thesis_rearm_on_bar,
  dump_claim,
  evaluate_thesis_rearm_for_publish,
  mapped_group_id,
  parse_reaction_claim,
  parse_thesis_claim,
  reaction_claim_key,
  reaction_claim_payload,
  thesis_claim_key,
  thesis_claim_payload,
  thesis_state_blocks_new_initial,
)
from app.autotrade.range_context import (
  ACTIVE_RANGE_STATES,
  PRIVATE_SOURCE_MAX_AGE_SECONDS,
  SCANNER_SOURCE_MAX_AGE_SECONDS,
  WORKER_SNAPSHOT_TTL_SECONDS,
  RangeContext,
  RangeExecutionEligibility,
  continue_range_episode,
  evaluate_range_box_eligibility,
  is_range_context_current,
  persist_private_range_observation,
  persist_resolved_range,
  private_range_context,
  range_context_key,
  range_context_source_key,
  range_geometry_matches_match,
  resolve_range_context,
)
from app.autotrade.range_lifecycle import (
  box_break_direction,
  disarmed_side_payload,
  load_breakout_retest_watch,
  mark_range_retired,
  persist_breakout_retest_watch,
  range_is_retired,
  retire_range_context,
  status_label_for_retired,
)
from app.autotrade.map_strategy import (
  MarketMap,
  MarketMapStrategyDecision,
  decode_market_map,
  evaluate_market_map_strategy,
  market_map_actionable_key,
  market_map_display_key,
  market_map_key,
)
from app.autotrade.scale_context import AutoScaleContext, build_auto_scale_context
from app.autotrade.trend import (
  RegimeInfo,
  TrendDecision,
  classify_regime,
  evaluate_trend_gate,
)
from app.core.config import runtime_config
from app.runtime.instrument_config import instrument_runtime_view
from app.runtime.price_identity import price_token
from app.persistence.store import event_in_window, nearest_currency_event
from app.analysis.ohlc_source import RedisOHLCSource, window_for_timeframe
from app.analysis.math_utils import atr_series
from app.analysis.types import Level, Zone
from app.analysis.zones import displacement, mark_mitigation, supply_demand
from app.analysis.levels import key_levels
from app.analysis.swings import find_swings


log = logging.getLogger(__name__)

# Phase 2I-A.1: worker HTF/overlap helpers now default `cfg` to the canonical
# `runtime_config` singleton. Every other worker call site that previously
# passed ``None`` still passes ``cfg=None`` so the callee builds its own
# narrow projection.


def _default_runtime_cfg() -> Any:
  from app.core.config import runtime_config

  return runtime_config


EXECUTION_TIMEFRAME = "M1"
CONTEXT_TIMEFRAMES = ("M5", "M15", "H1")
# Matches trend.py's own HTF-bias definition (classify_regime uses M15 too).
_HTF_TIMEFRAME = "M15"

# Injected at composition root (main/delivery). Worker must never import
# app.bot.client — architecture-guard regression enforces this.
FormingCardEditFn = Callable[[int, int, str], Awaitable[Any]]
_forming_card_edit_fn: FormingCardEditFn | None = None


def configure_forming_card_edit_fn(edit_fn: FormingCardEditFn | None) -> None:
  """Wire Telegram forming-card edits without importing bot.client here."""
  global _forming_card_edit_fn
  _forming_card_edit_fn = edit_fn


@dataclass(frozen=True)
class AutoTradeSpot:
  price: float
  ts: int
  fresh: bool
  bid: float | None = None
  ask: float | None = None

  def executable_price(self, direction: str) -> float:
    if direction.upper() == "BUY" and self.ask is not None:
      return self.ask
    if direction.upper() == "SELL" and self.bid is not None:
      return self.bid
    return self.price


_ACTIVE_V8_PLAN_STATES = frozenset({
  "published",
  "received",
  "armed",
  "submitted",
  "filled",
  "managing",
  "completed",
})
_TERMINAL_V8_PLAN_STATES = frozenset({
  "rejected",
  "cancelled",
  "expired",
})
_POST_PUBLICATION_SETUP_STATES = frozenset({
  PLAN_PUBLISHED,
  ARMED,
  CONSUMED,
})


@dataclass(frozen=True)
class ExistingV8State:
  plan_id: str
  setup_state: str | None
  plan_state: str | None
  plan_exists: bool
  already_published: bool
  already_terminal: bool
  owner_matches: bool


async def resolve_existing_v8_state(
  client: Any,
  match: StrategyMatch,
  *,
  cycle_id: str | None = None,
) -> ExistingV8State:
  """Resolve one setup's durable TradePlan truth before any dynamic preflight."""
  plan_id = _v8_plan_id(match)
  setup = await load_setup(client, match.match_id)
  plan_state = await read_plan_state(client, plan_id)
  plan_exists = bool(await client.exists(plan_key(plan_id)))
  owner_matches = False
  if cycle_id:
    raw_owner = await client.get(
      autonomous_cycle_owner_key(match.symbol, cycle_id)
    )
    if raw_owner is not None:
      text = (
        raw_owner.decode()
        if isinstance(raw_owner, bytes)
        else str(raw_owner)
      )
      try:
        owner = json.loads(text)
      except (TypeError, ValueError, json.JSONDecodeError):
        owner = {"intent_id": text}
      owner_matches = bool(
        isinstance(owner, dict)
        and (
          owner.get("plan_id") == plan_id
          or owner.get("setup_id") == match.match_id
          or owner.get("intent_id") == f"strategy:{match.match_id}"
        )
      )
  setup_state = None if setup is None else setup.state
  already_published = bool(
    setup_state in _POST_PUBLICATION_SETUP_STATES
    or plan_state in _ACTIVE_V8_PLAN_STATES
    or (
      setup_state == PLAN_BUILT
      and (plan_exists or plan_state is not None)
    )
  )
  return ExistingV8State(
    plan_id=plan_id,
    setup_state=setup_state,
    plan_state=plan_state,
    plan_exists=plan_exists,
    already_published=already_published,
    already_terminal=bool(
      setup_state in TERMINAL_STATES
      or plan_state in _TERMINAL_V8_PLAN_STATES
      or plan_state == "completed"
    ),
    owner_matches=owner_matches,
  )


def terminal_state_for_preflight_failure(
  current_state: str,
) -> str | None:
  """Map a preflight failure without allowing post-plan invalidation."""
  if current_state == PLAN_BUILT:
    return CANCELLED
  if current_state in _POST_PUBLICATION_SETUP_STATES:
    return None
  if current_state in TERMINAL_STATES:
    return None
  return INVALIDATED


def parse_cycle_owner_intent_id(raw: Any) -> str | None:
  """P1-5: extract intent_id from a publish_ranked_cycle owner record.

  publish_ranked_cycle stores the cycle owner as a JSON object
  ({symbol, cycle_id, intent_id, setup_id, plan_id, published_at}) - the
  raw JSON blob itself is never a valid winner_intent_id. A legacy or
  malformed value (predating the JSON payload, or a decode failure) falls
  back to the raw text unchanged, so any old data already written stays
  readable rather than becoming None.
  """
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
  except (TypeError, ValueError, json.JSONDecodeError):
    return text
  if isinstance(payload, dict) and payload.get("intent_id"):
    return str(payload["intent_id"])
  return text


def _executable_spot_price(spot: Any, direction: str) -> float:
  """Return the side-aware quote while tolerating legacy test snapshots."""
  resolver = getattr(spot, "executable_price", None)
  if callable(resolver):
    return float(resolver(direction))
  quote = (
    getattr(spot, "ask", None)
    if direction.upper() == "BUY"
    else getattr(spot, "bid", None)
  )
  return float(spot.price if quote is None else quote)


def _measure_executor_entry_distance(
  *,
  direction: str,
  spot: AutoTradeSpot,
  zone_low: float,
  zone_high: float,
  symbol: str,
):
  return measure_entry_distance(
    direction=direction,
    bid=getattr(spot, "bid", None),
    ask=getattr(spot, "ask", None),
    zone_low=zone_low,
    zone_high=zone_high,
    pip_size=units.pip_size(symbol),
    cap_pips=runtime_config.execution.entry.maximum_chase_distance_pips,
    mid_fallback=getattr(spot, "price", None),
  )


@dataclass(frozen=True)
class PrivateRouteIdentity:
  symbol: str
  match_id: str
  strategy: str
  family: str
  direction: str
  structural_source: str
  structural_zone_id: str
  issued_at: int
  expires_at: int
  current_price: float | None
  entry_low: float | None
  entry_high: float | None


@dataclass(frozen=True)
class PrivatePolicySubject:
  symbol: str
  strategy: str
  direction: str
  entry_low: float
  entry_high: float
  current_price: float
  confluence: int
  atr: float
  structure_swing: float
  targets_pips: tuple[int, ...]
  risk_multiplier: float
  target_model: str = "fill_relative"
  target_reference_price: str = "broker_fill"
  target_price: float | None = None
  absolute_target_price: float | None = None
  sweep_low: float | None = None
  sweep_high: float | None = None
  # Fitted scalp room unlocks opposing bypass in evaluate_execution_policy.
  full_take_profit_pips: int | None = None
  family: str | None = None
  strategy_mode: str | None = None



def _collect_fixed_rr_metric_sink(
  bucket: list[tuple[str, str, dict[str, str]]],
):
  """Sync sink that records fixed_rr room-rejection counters for later await."""
  def _sink(name: str, symbol: str, dimensions: dict[str, str]) -> None:
    bucket.append((name, symbol, dict(dimensions)))
  return _sink


def _fixed_rr_policy_targets(
  evaluation: Any,
) -> tuple[int, ...]:
  """Integer-pip projection for the legacy candidate stream.

  TradePlan V8 uses the exact absolute target price. The legacy stream only
  accepts integer pip offsets, so round the same approved policy result once.
  """
  if evaluation.measured.get("target_policy_mode") != "fixed_rr":
    return ()
  values: list[int] = []
  for raw in evaluation.measured.get("planned_target_pips") or ():
    try:
      value = int(round(float(raw)))
    except (TypeError, ValueError):
      continue
    if value > 0:
      values.append(value)
  return tuple(values)


@dataclass(frozen=True)
class ExecutionZoneClassification:
  side: str
  source: str
  timeframe: str
  width_pips: float
  width_atr: float
  execution_grade: bool
  context_only: bool
  invalid_geometry: bool


_STOP_CONTRACT_FIELDS = (
  "planned_stop_entry_price",
  "planned_stop_price",
  "planned_stop_distance",
  "planned_stop_pips",
  "planned_stop_raw_price",
  "planned_stop_clamped",
  "stop_source",
  "stop_plan_version",
  "planned_base_stop_price",
  "planned_base_stop_pips",
  "planned_final_stop_price",
  "planned_final_stop_distance",
  "planned_final_stop_pips",
  "stop_adjustment",
  "stop_adjustment_zone_id",
  "stop_adjustment_zone_low",
  "stop_adjustment_zone_high",
  # Entry plan: the route and entry the stop above was priced against. The
  # executor rejects route drift and material entry drift before submitting.
  "planned_execution_route",
  "planned_market_immediate",
  "planned_entry_price",
  "planned_leg_entry_prices",
  "entry_plan_version",
)


def _stop_contract_fields(measured: dict[str, Any]) -> dict[str, Any]:
  return {
    name: measured[name]
    for name in _STOP_CONTRACT_FIELDS
    if name in measured
  }


def classify_execution_zone(
  zone: Zone,
  *,
  atr: float,
  pip_size: float,
  cfg: Any,
  timeframe: str = _HTF_TIMEFRAME,
) -> ExecutionZoneClassification:
  width = float(zone.high - zone.low)
  invalid = (
    not math.isfinite(width)
    or width <= 0
    or pip_size <= 0
    or atr <= 0
  )
  width_pips = width / pip_size if pip_size > 0 else math.inf
  width_atr = width / atr if atr > 0 else math.inf
  exceeds = (
    width_atr > float(cfg.execution.policy.execution_zone_max_width_atr)
    or width_pips > float(cfg.execution.policy.execution_zone_max_width_pips)
  )
  return ExecutionZoneClassification(
    side=zone.side,
    source=zone.kind or "supply_demand",
    timeframe=timeframe,
    width_pips=round(width_pips, 3),
    width_atr=round(width_atr, 3),
    execution_grade=not invalid and not exceeds,
    context_only=not invalid and exceeds,
    invalid_geometry=invalid,
  )


def _symbols() -> set[str]:
  # rollout=live is the go-live switch; do not require a second CSV edit.
  live = {item.upper() for item in runtime_config.live_instruments()}
  if live:
    return live
  return {
    item.strip().upper()
    for item in runtime_config.contract.instrument.symbols.split(",")
    if item.strip()
  }


def _parse_bar_event(data: object) -> tuple[str, str, str] | None:
  text = data.decode() if isinstance(data, bytes) else str(data)
  parts = text.strip().split(":")
  if len(parts) < 3:
    return None
  return parts[0].upper(), parts[1].upper(), ":".join(parts[2:])


async def _load_frames(
  source: RedisOHLCSource,
  symbol: str,
  *,
  window: int | None = None,
  timeframes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
  frames: dict[str, Any] = {}
  for timeframe in timeframes or (EXECUTION_TIMEFRAME, *CONTEXT_TIMEFRAMES):
    count = (
      max(50, int(window)) if window is not None
      else window_for_timeframe(timeframe)
    )
    frame = await source.window(symbol, timeframe, count)
    if not frame.empty:
      frames[timeframe] = frame
  return frames


async def _load_spot(client: Any, symbol: str) -> AutoTradeSpot | None:
  raw = await client.get(f"price:{symbol.upper()}:spot")
  if raw is None:
    return None
  text = raw.decode() if isinstance(raw, bytes) else str(raw)
  try:
    payload = json.loads(text)
    bid = float(payload["bid"])
    ask = float(payload["ask"])
    ts = int(payload["ts"])
  except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None
  price = (bid + ask) / 2
  if not math.isfinite(price) or price <= 0:
    return None
  now = int(datetime.now(timezone.utc).timestamp())
  return AutoTradeSpot(
    price=price,
    ts=ts,
    fresh=0 <= now - ts <= max(
      1, runtime_config.market_data.spot.maximum_age_seconds,
    ),
    bid=bid,
    ask=ask,
  )


async def _load_strategy_match(
  client: Any,
  symbol: str,
) -> StrategyMatch | None:
  if not runtime_config.runtime.auto_trade.strategy_match_enabled:
    return None
  key = strategy_match_key(symbol)
  raw = await client.get(key)
  if raw is None:
    return None
  match = StrategyMatch.from_json(raw)
  now = int(datetime.now(timezone.utc).timestamp())
  if (
    match is None
    or match.symbol != symbol.upper()
    or now > match.expires_at
  ):
    if match is not None:
      await record_route_outcome(
        client,
        match,
        stage="scanner" if now > match.expires_at else "mode_check",
        status="expired" if now > match.expires_at else "blocked",
        reason_code=(
          "match_expired" if now > match.expires_at else "symbol_mismatch"
        ),
        message=(
          "StrategyMatch expired before execution"
          if now > match.expires_at
          else f"match symbol {match.symbol} does not match {symbol.upper()}"
        ),
        retained=False,
        publish_status=False,
      )
    await client.delete(key)
    return None
  return match


async def _load_strategy_matches(
  client: Any,
  symbol: str,
) -> list[StrategyMatch]:
  if not runtime_config.runtime.auto_trade.strategy_match_enabled:
    return []
  if not runtime_config.strategies.matching.multiple_matches_enabled:
    match = await _load_strategy_match(client, symbol)
    return [] if match is None else [match]
  raw = await client.get(strategy_matches_key(symbol))
  matches = deserialize_matches(raw)
  now = int(datetime.now(timezone.utc).timestamp())
  for match in matches:
    if match.symbol != symbol.upper():
      await record_route_outcome(
        client,
        match,
        stage="mode_check",
        status="blocked",
        reason_code="symbol_mismatch",
        message=f"match symbol {match.symbol} does not match {symbol.upper()}",
        retained=False,
        publish_status=False,
      )
    elif now > match.expires_at:
      await record_route_outcome(
        client,
        match,
        stage="scanner",
        status="expired",
        reason_code="match_expired",
        message="StrategyMatch expired before execution",
        retained=False,
        publish_status=False,
      )
  active = [
    match for match in matches
    if match.symbol == symbol.upper() and now <= match.expires_at
  ]
  if len(active) != len(matches):
    if active:
      from app.autotrade.multi_match import serialize_matches
      await client.set(
        strategy_matches_key(symbol),
        serialize_matches(active),
        ex=max(60, max(item.expires_at for item in active) - now),
      )
    else:
      await client.delete(strategy_matches_key(symbol))
  if active:
    return active
  legacy = await _load_strategy_match(client, symbol)
  return [] if legacy is None else [legacy]


async def _consume_strategy_match(
  client: Any,
  symbol: str,
  match: StrategyMatch,
) -> None:
  """Remove exactly one terminal/published match without touching siblings."""
  multi_key = strategy_matches_key(symbol)
  matches = deserialize_matches(await client.get(multi_key))
  kept = [item for item in matches if item.match_id != match.match_id]
  if len(kept) != len(matches):
    if kept:
      now = int(datetime.now(timezone.utc).timestamp())
      await client.set(
        multi_key,
        serialize_matches(kept),
        ex=max(60, max(item.expires_at for item in kept) - now),
      )
    else:
      await client.delete(multi_key)
  legacy_key = strategy_match_key(symbol)
  legacy = StrategyMatch.from_json(await client.get(legacy_key) or "")
  if legacy is not None and legacy.match_id == match.match_id:
    await client.delete(legacy_key)


async def _resolve_worker_range(
  client: Any,
  *,
  symbol: str,
  frames: dict[str, Any],
  private_decision: AutoScalpDecision,
  spot: AutoTradeSpot | None,
) -> tuple[AutoScalpDecision, RangeContext | None, dict[str, Any]]:
  now = int(datetime.now(timezone.utc).timestamp())
  m1 = frames.get(EXECUTION_TIMEFRAME)
  atr = 0.0
  if m1 is not None and not m1.empty:
    series = atr_series(m1, max(2, runtime_config.analysis.atr.length))
    if not series.empty:
      atr = float(series.iloc[-1])
  scanner_raw = await client.get(range_context_source_key(symbol, "scanner"))
  scanner_context = RangeContext.from_json(scanner_raw)
  if (
    scanner_context is not None
    and not is_range_context_current(
      scanner_context,
      now=now,
      max_age_seconds=SCANNER_SOURCE_MAX_AGE_SECONDS,
    )
  ):
    await increment_metric(client, "scanner_range_stale", symbol=symbol)
  previous_private_raw = await client.get(
    range_context_source_key(symbol, "private")
  )
  previous_private = RangeContext.from_json(previous_private_raw)
  private_context = private_range_context(
    symbol=symbol,
    decision=private_decision,
    atr=atr,
    pip_size=units.pip_size(symbol),
    generated_at=now,
    ttl=PRIVATE_SOURCE_MAX_AGE_SECONDS,
  )
  private_context = continue_range_episode(previous_private, private_context)
  if (
    private_context is not None
    and private_decision.box is not None
    and private_decision.box.box_id != private_context.range_id
  ):
    private_decision = replace(
      private_decision,
      box=replace(
        private_decision.box,
        box_id=private_context.range_id,
      ),
    )
  await persist_private_range_observation(
    client,
    symbol=symbol,
    context=private_context,
  )
  if private_context is not None:
    await increment_metric(client, "private_range_observed", symbol=symbol)
  elif previous_private_raw is not None:
    await increment_metric(client, "private_range_withdrawn", symbol=symbol)
  resolved, comparison = resolve_range_context(
    scanner_context,
    private_context,
    now=now,
  )
  previous_resolved_raw = await client.get(range_context_key(symbol))
  previous_resolved = RangeContext.from_json(previous_resolved_raw)
  resolved = continue_range_episode(
    previous_resolved,
    resolved,
    require_same_source=False,
  )
  if comparison.get("stale_source_excluded"):
    await increment_metric(
      client,
      "resolved_range_stale_source_excluded",
      symbol=symbol,
    )
  price = spot.price if spot is not None and spot.fresh else None
  if (
    private_decision.state == "box_broken"
    and private_decision.box is not None
    and price is not None
  ):
    direction = box_break_direction(private_decision, float(price))
    if direction is not None:
      base = private_context or resolved
      if base is not None:
        resolved = retire_range_context(
          base,
          direction=direction,
          now=now,
        )
        comparison = {
          **comparison,
          "state": "retired",
          "resolution": "accepted_structural_breakout",
          "reason": resolved.invalidation_reason,
        }
        await mark_range_retired(
          client,
          symbol=symbol,
          range_id=resolved.range_id,
          ttl=runtime_config.lifecycle.range_box.retirement_seconds,
        )
        await persist_breakout_retest_watch(
          client,
          symbol=symbol,
          range_id=resolved.range_id,
          direction=direction,
          lower=resolved.lower,
          upper=resolved.upper,
          ttl=runtime_config.lifecycle.range_box.retirement_seconds,
        )
        await _expire_range_matches(client, symbol, resolved.range_id)
  elif resolved is not None and await range_is_retired(
    client, symbol=symbol, range_id=resolved.range_id,
  ):
    watch = await load_breakout_retest_watch(client, symbol)
    if watch and watch.get("direction") in {"BUY", "SELL"}:
      direction = str(watch["direction"])
    elif price is not None and math.isfinite(float(price)):
      direction = (
        "BUY" if float(price) >= resolved.upper else "SELL"
      )
    else:
      direction = "BUY"
    resolved = retire_range_context(
      resolved,
      direction=direction,
      now=now,
    )
    comparison = {
      **comparison,
      "state": "retired",
      "resolution": "accepted_structural_breakout",
      "reason": resolved.invalidation_reason,
    }

  # Persist only after the native private decision, source resolution and
  # breakout retirement agree for this cycle.
  await persist_resolved_range(
    client,
    symbol=symbol,
    resolved=resolved,
    comparison=comparison,
  )
  if resolved is not None and previous_resolved_raw is None:
    await increment_metric(client, "resolved_range_created", symbol=symbol)
  elif resolved is None and previous_resolved_raw is not None:
    await increment_metric(client, "resolved_range_deleted", symbol=symbol)
  if comparison.get("disagreement"):
    await increment_metric(client, "range_context_disagreement", symbol=symbol)
    disagreement_hard = bool(
      runtime_config.actionability.gates.range_context_disagreement_gate_enabled
    )
    await increment_metric(
      client,
      (
        "range_context_disagreement_gated"
        if disagreement_hard else "range_context_disagreement_observed"
      ),
      symbol=symbol,
    )
    if disagreement_hard and private_decision.state == "candidate":
      private_decision = replace(
        private_decision,
        state="range_context_disagreement",
        direction=None,
        trigger=None,
        reasons=(
          *private_decision.reasons,
          "scanner/private range context disagreement gated by policy",
        ),
      )
  elif comparison.get("resolution") == "merged":
    await increment_metric(client, "range_context_merged", symbol=symbol)
  if resolved is not None:
    # Resolution may contribute a canonical episode id, never geometry.
    # Entry rails remain the ones produced by the native M1 detector.
    if (
      private_decision.box is not None
      and private_context is not None
      and resolved.state not in {"broken", "retired"}
    ):
      private_decision = replace(
        private_decision,
        box=replace(private_decision.box, box_id=resolved.range_id),
      )
    await _persist_range_side_states(
      client,
      symbol=symbol,
      context=resolved,
      decision=private_decision,
    )
  return private_decision, resolved, comparison


async def _persist_range_side_states(
  client: Any,
  *,
  symbol: str,
  context: RangeContext,
  decision: AutoScalpDecision,
) -> None:
  active = context.state in {
    "provisional",
    "confirmed",
    "post_impulse",
    "breakout_pending",
  }
  if not active and context.state not in {"broken", "retired"}:
    return
  # Broken/retired ranges must never keep armed rails.
  if context.state in {"broken", "retired"}:
    now = int(datetime.now(timezone.utc).timestamp())
    for direction in ("BUY", "SELL"):
      side_key = (
        f"auto_trade:range_side:{symbol.upper()}:{context.range_id}:"
        f"{direction}"
      )
      existing = {}
      existing_raw = await client.get(side_key)
      if existing_raw:
        try:
          existing = json.loads(existing_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
          existing = {}
      payload = disarmed_side_payload(
        context=context,
        direction=direction,
        existing=existing,
        now=now,
      )
      await client.set(
        side_key,
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        ex=max(300, runtime_config.lifecycle.range_box.retirement_seconds),
      )
      await client.delete(_box_edge_key(symbol, context.range_id, direction))
    return
  now = int(datetime.now(timezone.utc).timestamp())
  for direction, barrier in (
    ("BUY", context.lower_barrier),
    ("SELL", context.upper_barrier),
  ):
    side_key = (
      f"auto_trade:range_side:{symbol.upper()}:{context.range_id}:"
      f"{direction}"
    )
    existing = {}
    existing_raw = await client.get(side_key)
    if existing_raw:
      try:
        existing = json.loads(existing_raw)
      except (TypeError, ValueError, json.JSONDecodeError):
        existing = {}
    pending_order_ids = list(existing.get("pending_order_ids") or [])
    position_ids = list(existing.get("position_ids") or [])
    candidate_id = existing.get("candidate_id")
    existing_state = str(existing.get("state") or "").upper()
    state = "ARMED"
    if position_ids:
      state = "MANAGING"
    elif pending_order_ids:
      state = "ORDER_SUBMITTED"
    elif candidate_id and existing_state not in {
      "", "CLOSED", "REARMED", "REJECTED", "EXPIRED", "CANCELLED",
    }:
      state = existing_state
    if decision.direction == direction:
      if state == "ARMED":
        state = (
          "CONFIRMED"
          if decision.state == "candidate"
          else "EDGE_TOUCHED"
          if decision.state == "waiting_rejection"
            else state
        )
    if await client.exists(
      _box_edge_key(symbol, context.range_id, direction)
    ):
      state = str(existing.get("state") or "CANDIDATE_PUBLISHED")
    payload = {
      "range_id": context.range_id,
      "symbol": symbol.upper(),
      "direction": direction,
      "state": state,
      "candidate_id": candidate_id,
      "pending_order_ids": pending_order_ids,
      "position_ids": position_ids,
      "target_state": existing.get("target_state", "pending"),
      "invalidation_state": None,
      "last_trigger_bar": now,
      "last_confirmed_touch": now if state == "CONFIRMED" else None,
      "execution_count": int(existing.get("execution_count") or 0),
      "barrier": {
        "low": barrier.low,
        "high": barrier.high,
        "level": barrier.level,
      },
      "updated_at": now,
    }
    await client.set(
      side_key,
      json.dumps(payload, separators=(",", ":"), sort_keys=True),
      ex=max(300, runtime_config.lifecycle.range_box.retirement_seconds),
    )


async def _range_side_has_active_ownership(
  client: Any,
  *,
  symbol: str,
  range_id: str,
  direction: str,
) -> bool:
  raw = await client.get(
    (
      f"auto_trade:range_side:{symbol.upper()}:{range_id}:"
      f"{direction.upper()}"
    )
  )
  if not raw:
    return False
  try:
    payload = json.loads(
      raw.decode() if isinstance(raw, bytes) else str(raw)
    )
  except (TypeError, ValueError, json.JSONDecodeError):
    return False
  state = str(payload.get("state") or "").upper()
  return bool(
    payload.get("position_ids")
    or payload.get("pending_order_ids")
    or (
      payload.get("candidate_id")
      and state not in {
        "", "CLOSED", "REARMED", "REJECTED", "EXPIRED", "CANCELLED",
      }
    )
  )


async def _load_range_side_status(
  client: Any,
  *,
  symbol: str,
  range_id: str,
  direction: str,
) -> dict[str, Any]:
  raw = await client.get(
    f"auto_trade:range_side:{symbol.upper()}:{range_id}:{direction.upper()}"
  )
  if not raw:
    return {
      "state": "ARMED",
      "candidate_id": None,
      "pending_order_ids": [],
      "position_ids": [],
      "group_id": None,
    }
  try:
    payload = json.loads(
      raw.decode() if isinstance(raw, bytes) else str(raw)
    )
  except (TypeError, ValueError, json.JSONDecodeError):
    return {
      "state": "REJECTED",
      "candidate_id": None,
      "pending_order_ids": [],
      "position_ids": [],
      "group_id": None,
    }
  return {
    "state": str(payload.get("state") or "ARMED").upper(),
    "candidate_id": payload.get("candidate_id"),
    "pending_order_ids": list(payload.get("pending_order_ids") or []),
    "position_ids": list(payload.get("position_ids") or []),
    "group_id": payload.get("group_id"),
  }


async def _expire_range_matches(
  client: Any,
  symbol: str,
  range_id: str,
) -> None:
  matches = await _load_strategy_matches(client, symbol)
  kept = [item for item in matches if item.range_id != range_id]
  if len(kept) == len(matches):
    return
  if kept:
    from app.autotrade.multi_match import serialize_matches
    await client.set(
      strategy_matches_key(symbol),
      serialize_matches(kept),
      ex=max(60, max(item.expires_at for item in kept) - int(
        datetime.now(timezone.utc).timestamp()
      )),
    )
  else:
    await client.delete(strategy_matches_key(symbol))
  legacy = await client.get(strategy_match_key(symbol))
  if legacy:
    try:
      match = StrategyMatch.from_json(legacy)
    except (TypeError, ValueError, json.JSONDecodeError, KeyError):
      match = None
    if match is not None and match.range_id == range_id:
      await client.delete(strategy_match_key(symbol))


async def _mark_range_side_candidate(
  client: Any,
  *,
  symbol: str,
  range_id: str,
  direction: str,
  candidate_id: str,
  group_id: str | None = None,
) -> None:
  key = (
    f"auto_trade:range_side:{symbol.upper()}:{range_id}:"
    f"{direction.upper()}"
  )
  raw = await client.get(key)
  try:
    payload = json.loads(
      raw.decode() if isinstance(raw, bytes) else str(raw)
    ) if raw is not None else {}
  except (TypeError, ValueError, json.JSONDecodeError):
    payload = {}
  payload.update({
    "range_id": range_id,
    "symbol": symbol.upper(),
    "direction": direction.upper(),
    "state": "CANDIDATE_PUBLISHED",
    "candidate_id": candidate_id,
    "group_id": group_id or payload.get("group_id"),
    "updated_at": int(datetime.now(timezone.utc).timestamp()),
  })
  await client.set(
    key,
    json.dumps(payload, separators=(",", ":"), sort_keys=True),
    ex=max(300, runtime_config.lifecycle.range_box.retirement_seconds),
  )


def _eq_exclusion_reason(
  box: AutoScalpBox,
  entry_reference: float,
  fraction: float,
) -> str | None:
  """Reject an entry parked at the box's equilibrium (defect 2, 22 Jul).

  EQ is the lowest-information location in a range - neither an edge to fade
  nor a breakout to follow.
  """
  eq = (box.lower.level + box.upper.level) / 2
  width = box.upper.level - box.lower.level
  if width <= 0:
    return None
  if abs(entry_reference - eq) < max(0.0, fraction) * width:
    return f"EQ exclusion: entry {entry_reference:.5f} within {fraction:.0%} of box EQ {eq:.5f}"
  return None


def _edge_proximity_reason(
  rail: AutoScalpRail,
  entry_reference: float,
  atr: float,
  limit_atr: float,
) -> str | None:
  """A range-edge candidate must actually be near the edge it claims to trade."""
  if atr <= 0:
    return None
  distance_atr = abs(entry_reference - rail.level) / atr
  if distance_atr > max(0.0, limit_atr):
    return (
      f"Range Edge Scalp not near an edge: entry {entry_reference:.5f} is "
      f"{distance_atr:.2f} ATR from rail {rail.level:.5f} "
      f"(limit {limit_atr:.2f} ATR)"
    )
  return None


def _htf_zones(
  frames: dict[str, Any],
  cfg: Any | None = None,
  *,
  symbol: str = "XAU",
) -> list[Zone]:
  """Fresh/tested HTF (M15) supply/demand zones, for the A3 veto and the A2
  opposing-zone attachment. Independent of gate.py/trend.py's own M1 legs -
  this is the one place the shared analysis stack enters the autotrade path,
  and it enters only as a veto input, never as a signal.
  """
  if cfg is None:
    cfg = _default_runtime_cfg()
  htf = frames.get(_HTF_TIMEFRAME)
  if htf is None or htf.empty:
    return []
  atr_length = max(2, int(cfg.analysis.atr.length))
  atr_values = atr_series(htf, atr_length)
  current_atr = (
    float(atr_values.iloc[-1])
    if not atr_values.empty and math.isfinite(float(atr_values.iloc[-1]))
    else 0.0
  )
  legs = displacement(
    htf,
    atr_values,
    max(0.1, float(cfg.analysis.displacement.atr_mult)),
    max(0.0, float(cfg.analysis.momentum.body_frac)),
  )
  if not legs:
    return []
  zones = supply_demand(htf, legs)
  marked = mark_mitigation(zones, htf)
  pip_size = units.pip_size(symbol)
  return [
    zone
    for zone in marked
    if classify_execution_zone(
      zone,
      atr=current_atr,
      pip_size=pip_size,
      cfg=cfg,
    ).execution_grade
  ]


def _htf_levels(
  frames: dict[str, Any],
  cfg: Any | None = None,
  *,
  symbol: str = "XAU",
) -> list[Level]:
  """HTF (M15) round-number and reaction key levels, for the opposing-barrier
  veto below. Round-number levels aren't sided the way supply/demand zones
  are (a round number caps a rally the same way it floors a selloff), so
  they're kept as a separate ``Level`` list rather than folded into ``Zone``.
  """
  if cfg is None:
    cfg = _default_runtime_cfg()
  htf = frames.get(_HTF_TIMEFRAME)
  if htf is None or htf.empty:
    return []
  atr_length = max(2, int(cfg.analysis.atr.length))
  atr = atr_series(htf, atr_length)
  swings = find_swings(
    htf,
    max(1, int(cfg.analysis.swings.fractal_size)),
    max(0.0, float(cfg.analysis.swings.zigzag.pct)),
    max(0.0, float(cfg.analysis.swings.zigzag.atr_mult)),
    atr,
  )
  if not swings:
    return []
  return key_levels(
    swings,
    atr,
    max(0.0, float(cfg.analysis.levels.level_cluster_atr)),
    max(0.0, float(instrument_geometry.round_step(symbol))),
    max(1, int(cfg.analysis.levels.minimum_key_touches)),
  )


def _barrier_id(
  source_type: str,
  side: str,
  low: float,
  high: float,
  level_kind: str = "",
) -> str:
  return (
    f"{source_type}:{side}:{level_kind}:"
    f"{low:.5f}:{high:.5f}"
  )


def _structural_source_identity(
  *,
  strategy: str,
  family: str,
  structural_source: str,
  low: float,
  high: float,
  key_level: float | None,
  zone_id: str | None = None,
  level_id: str | None = None,
) -> StructuralSourceIdentity:
  return StructuralSourceIdentity(
    strategy=strategy,
    strategy_family=family,
    structural_source=structural_source,
    zone_id=zone_id,
    level_id=level_id,
    key_level=key_level,
    low=min(low, high),
    high=max(low, high),
  )


def _structural_barriers(
  zones: list[Zone],
  levels: list[Level],
  source: StructuralSourceIdentity,
  direction: str,
) -> list[StructuralBarrier]:
  """Convert raw analysis structures into sided, source-aware barriers."""
  result: list[StructuralBarrier] = []
  supports = {"demand"} if direction == "BUY" else {"supply"}
  for zone in zones:
    barrier_id = _barrier_id(
      "zone", zone.side, zone.low, zone.high, zone.kind,
    )
    overlaps_source = (
      zone.low <= source.high and zone.high >= source.low
    )
    primary = bool(
      source.zone_id == barrier_id
      or (
        overlaps_source
        and zone.side in supports
        and (
          source.key_level is None
          or zone.low <= source.key_level <= zone.high
        )
      )
    )
    result.append(StructuralBarrier(
      barrier_id=barrier_id,
      source_type="zone",
      side=zone.side,
      low=zone.low,
      high=zone.high,
      level_kind=zone.kind,
      timeframe=_HTF_TIMEFRAME,
      touches=zone.touches,
      score=zone.score,
      is_primary_source=primary,
      is_supporting_source=overlaps_source and zone.side in supports,
    ))
  for level in levels:
    low = level.price - level.band
    high = level.price + level.band
    barrier_id = _barrier_id(
      "level", "neutral", low, high, level.kind,
    )
    primary = bool(
      source.level_id == barrier_id
      or (
        low <= source.high
        and high >= source.low
        and source.key_level is not None
        and low <= source.key_level <= high
      )
    )
    result.append(StructuralBarrier(
      barrier_id=barrier_id,
      source_type="level",
      side="neutral",
      low=low,
      high=high,
      level_kind=level.kind,
      timeframe=_HTF_TIMEFRAME,
      touches=level.touches,
      score=level.strength,
      is_primary_source=primary,
    ))
  return result


def _opposing_barrier_decision(
  direction: str,
  entry_reference: float,
  target_reference: float | None,
  atr: float | None,
  zones: list[Zone],
  levels: list[Level],
  buffer_atr: float,
  *,
  source: StructuralSourceIdentity,
  guard_mode: str,
) -> ExecutionGuardDecision:
  relationships: list[tuple[StructuralBarrier, str]] = []
  for barrier in _structural_barriers(zones, levels, source, direction):
    relationship = classify_barrier_relationship(
      strategy=source.strategy,
      direction=direction,
      entry_reference=entry_reference,
      target_reference=target_reference,
      source_identity=source,
      barrier=barrier,
    )
    relationships.append((barrier, relationship))

  primary = next(
    (
      barrier for barrier, relationship in relationships
      if relationship == "primary_source"
    ),
    None,
  )
  contained = next(
    (
      (barrier, relationship) for barrier, relationship in relationships
      if relationship in ("overlapping_ambiguous", "overlapping_neutral")
    ),
    None,
  )
  if contained is not None:
    ambiguous, relationship = contained
    message = (
      f"entry {entry_reference:.5f} inside opposing/ambiguous "
      f"{ambiguous.level_kind or ambiguous.side} "
      f"{ambiguous.low:.5f}-{ambiguous.high:.5f}"
    )
    # A directional supply/demand zone the entry sits inside is a real
    # structural wall (23 Jul incident: a BUY filled inside an 8-touch SELL
    # resistance band) and stays an unconditional hard block -- that's
    # relationship == "overlapping_ambiguous", which classify_barrier_
    # relationship only returns when the barrier's side cleanly matches the
    # opposing set. A neutral key level (source_type == "level") or a zone
    # whose side couldn't be cleanly classified as opposing at all
    # (relationship == "overlapping_neutral") are both much weaker signals;
    # route them through the same telemetry-not-block treatment every
    # other soft structural signal already gets.
    is_neutral_level = ambiguous.source_type == "level"
    is_side_unclear = relationship == "overlapping_neutral"
    if is_neutral_level:
      reason_code = "entry_inside_opposing_level"
    elif is_side_unclear:
      reason_code = "entry_inside_ambiguous_zone"
    else:
      reason_code = "entry_inside_opposing_zone"
    decision = classify_guard_severity(
      "opposing_barrier",
      reason_code,
      message,
      guard_mode=guard_mode,
      hard_geometry=not (is_neutral_level or is_side_unclear),
    )
    return replace(
      decision,
      barrier=ambiguous,
      measured={
        "entry_reference": entry_reference,
        "relationship": relationship,
      },
    )

  ahead: list[tuple[float, StructuralBarrier]] = []
  for barrier, relationship in relationships:
    if relationship != "opposing_ahead":
      continue
    distance = (
      barrier.low - entry_reference
      if direction == "BUY"
      else entry_reference - barrier.high
    )
    if distance >= 0:
      ahead.append((distance, barrier))
  if ahead and atr and atr > 0 and buffer_atr > 0:
    distance, barrier = min(ahead, key=lambda item: item[0])
    if distance <= buffer_atr * atr:
      message = (
        f"Opposing barrier ahead: {direction} into "
        f"{barrier.level_kind or barrier.side} "
        f"{barrier.low:.5f}-{barrier.high:.5f} "
        f"({distance:.5f} away)"
      )
      decision = classify_guard_severity(
        "opposing_barrier",
        "opposing_barrier",
        message,
        guard_mode=guard_mode,
      )
      return replace(
        decision,
        barrier=barrier,
        measured={
          "entry_reference": entry_reference,
          "distance": distance,
          "distance_atr": distance / atr,
          "relationship": "opposing_ahead",
        },
      )

  if primary is not None:
    return ExecutionGuardDecision(
      "opposing_barrier",
      OUTCOME_ALLOW,
      "primary_source_excluded_from_barrier",
      (
        f"primary source {primary.low:.5f}-{primary.high:.5f} "
        "excluded from opposing barriers"
      ),
      False,
      measured={"relationship": "primary_source"},
      barrier=primary,
    )
  return ExecutionGuardDecision(
    "opposing_barrier",
    OUTCOME_ALLOW,
    "no_opposing_barrier",
    "no opposing barrier",
    False,
  )


def _defended_level_guard(
  symbol: str,
  entry_reference: float,
  *,
  direction: str,
  guard_mode: str,
) -> ExecutionGuardDecision:
  """Block fresh BUY entries near a macro-significant defended price level.

  2026 USDJPY dig: Japan/the US ran a record ~Y11.73T (~$73B) joint
  intervention when USDJPY breached 160 — the dollar snapped from 163 to
  ~156-157 within days. Intervention sells USDJPY, so the asymmetric risk
  is being **long** into that ceiling — not short.

  Prod failure mode (2026-08-25): a symmetric 100-pip buffer around 160
  hard-blocked every USDJPY plan (including SELLs at ~159.4) while
  activation_allowed kept climbing — zero publishes lifetime. Guard is
  therefore:

  - **BUY** (or unknown side) within ``buffer`` of a configured level →
    hard block (``hard_geometry=True``, ignores observe mode).
  - **SELL** near the level → allow (aligned with intervention direction).
  - Outside buffer → allow.

  Off by default (defended_levels empty / buffer 0); currently only
  configured for USDJPY.
  """
  levels = instrument_geometry.defended_levels(symbol)
  buffer_price = instrument_geometry.defended_level_buffer_price(symbol)
  if not levels or buffer_price <= 0:
    return ExecutionGuardDecision(
      "defended_level",
      OUTCOME_ALLOW,
      "no_defended_level_configured",
      "no defended level configured",
      False,
    )
  nearest = min(levels, key=lambda level: abs(level - entry_reference))
  distance = abs(nearest - entry_reference)
  if distance > buffer_price:
    return ExecutionGuardDecision(
      "defended_level",
      OUTCOME_ALLOW,
      "no_defended_level_nearby",
      f"nearest defended level {nearest:.5f} is {distance:.5f} away",
      False,
    )
  side = str(direction or "").upper()
  if side == "SELL":
    return ExecutionGuardDecision(
      "defended_level",
      OUTCOME_ALLOW,
      "defended_level_sell_aligned",
      (
        f"SELL {entry_reference:.5f} near defended {nearest:.5f} "
        f"(buffer {buffer_price:.5f}) aligned with intervention risk"
      ),
      False,
    )
  message = (
    f"entry {entry_reference:.5f} is {distance:.5f} from defended level "
    f"{nearest:.5f} (buffer {buffer_price:.5f})"
  )
  return classify_guard_severity(
    "defended_level",
    "entry_near_defended_level",
    message,
    guard_mode=guard_mode,
    hard_geometry=True,
  )


def _opposing_barrier_reason(
  direction: str,
  entry_reference: float,
  atr: float | None,
  zones: list[Zone],
  levels: list[Level],
  buffer_atr: float,
  *,
  exclude_low: float | None = None,
  exclude_high: float | None = None,
) -> str | None:
  """Veto a direction about to run straight into an opposing HTF barrier it
  hasn't broken through yet (22 Jul incident: a Box Breakout BUY filled 20
  pips below a published round-number supply level nobody checked). This is
  the mirror image of ``_htf_veto_reason`` above: that one protects the zone
  a trade is retesting *from*; this one checks what could cap the move
  *ahead* of entry - the opposing side, not the supporting one.

  An entry already *inside* an opposing barrier (23 Jul incident: a BUY
  filled inside a SELL resistance band tested eight times) is vetoed
  unconditionally, with no ATR/buffer tolerance - that geometry has zero
  room by definition. Reason strings for this case start with "entry " so
  callers can attribute it to its own reject counter; see
  ``_opposing_barrier_condition`` below.

  ``exclude_low``/``exclude_high``, when given, drop any barrier bound
  that overlaps the candidate's own structural source before either check
  runs - see ``_excludes_own_source``. A structural source must never veto
  the strategy explicitly trading it.
  """
  low = (
    entry_reference if exclude_low is None
    else min(exclude_low, exclude_high or exclude_low)
  )
  high = (
    entry_reference if exclude_high is None
    else max(exclude_high, exclude_low or exclude_high)
  )
  source = _structural_source_identity(
    strategy="legacy",
    family="",
    structural_source="legacy",
    low=low,
    high=high,
    key_level=entry_reference if exclude_low is not None else None,
  )
  decision = _opposing_barrier_decision(
    direction,
    entry_reference,
    None,
    atr,
    zones,
    levels,
    buffer_atr,
    source=source,
    guard_mode=GUARD_MODE_STRICT,
  )
  return decision.message if decision.hard_block else None


def _opposing_barrier_condition(reason: str) -> str:
  """Gate-reject condition key for an ``_opposing_barrier_reason`` hit -
  containment (zero room by definition) and ahead-of-entry (buffer/ATR
  tolerance applied) are geometrically distinct failures and must stay
  separable in the reject counters.
  """
  return (
    "entry_inside_opposing_zone" if reason.startswith("entry ")
    else "opposing_barrier"
  )


def _counter_bias_barrier_between(
  direction: str,
  entry_reference: float,
  target: float,
  zones: list[Zone],
  levels: list[Level],
) -> tuple[float, str] | None:
  """Nearest structural barrier strictly between ``entry_reference`` and
  ``target``, as (near_edge_price, description). Shared by
  ``_counter_bias_target_barrier_reason`` (existence check) and
  ``_adapt_counter_bias_target`` (Fix 7 - anchor the target to the barrier
  instead of only rejecting).
  """
  if direction == "BUY":
    between = [
      zone for zone in zones
      if zone.side == "supply"
      and zone.high >= entry_reference
      and zone.low <= target
    ]
    barrier = _nearest_directional_zone("SELL", entry_reference, between)
    if barrier is not None:
      return barrier.low, f"{barrier.side} {barrier.low:.5f}-{barrier.high:.5f}"
  else:
    between = [
      zone for zone in zones
      if zone.side == "demand"
      and zone.low <= entry_reference
      and zone.high >= target
    ]
    barrier = _nearest_directional_zone("BUY", entry_reference, between)
    if barrier is not None:
      return barrier.high, f"{barrier.side} {barrier.low:.5f}-{barrier.high:.5f}"

  level_bounds = [
    (level.price - level.band, level.price + level.band, level.kind)
    for level in levels
  ]
  ahead = [
    (abs(entry_reference - low), low, high, kind)
    for low, high, kind in level_bounds
    if (
      direction == "BUY"
      and high >= entry_reference
      and low <= target
    ) or (
      direction == "SELL"
      and low <= entry_reference
      and high >= target
    )
  ]
  if not ahead:
    return None
  _, low, high, kind = min(ahead, key=lambda item: item[0])
  near_edge = low if direction == "BUY" else high
  return near_edge, f"{kind} {low:.5f}-{high:.5f}"


def _counter_bias_target_barrier_reason(
  match: StrategyMatch,
  entry_reference: float,
  zones: list[Zone],
  levels: list[Level],
) -> str | None:
  """Reject a counter-bias mean-reversion route obstructed before box EQ."""
  if "counter_bias" not in match.tags or match.target_price is None:
    return None
  target = float(match.target_price)
  if (
    match.direction == "BUY" and target <= entry_reference
    or match.direction == "SELL" and target >= entry_reference
  ):
    return (
      f"counter-bias target {target:.5f} is not ahead of "
      f"{match.direction} entry {entry_reference:.5f}"
    )
  barrier = _counter_bias_barrier_between(
    match.direction, entry_reference, target, zones, levels,
  )
  if barrier is None:
    return None
  _, description = barrier
  return f"counter-bias target blocked before EQ {target:.5f} by {description}"


_MIN_COUNTER_BIAS_TARGET_PIPS = 15


def _adapt_counter_bias_target(
  match: StrategyMatch,
  entry_reference: float,
  zones: list[Zone],
  levels: list[Level],
  pip_size: float,
) -> tuple[StrategyMatch, GuardOutcome]:
  """Fix 7: a barrier before a counter-bias target caps the target instead
  of rejecting the setup outright. Selects the largest configured target
  that still fits inside the room to the barrier (buffered a couple of
  pips short of it), and trims ``targets_pips`` to match; only blocks when
  even the smallest configured target does not fit.
  """
  target = float(match.target_price) if match.target_price is not None else None
  if target is None or "counter_bias" not in match.tags:
    return match, GuardOutcome(
      "counter_bias", OUTCOME_ALLOW, "not_counter_bias", "", False,
      measured={"target_outcome": "target_unchanged"},
    )
  if (
    match.direction == "BUY" and target <= entry_reference
    or match.direction == "SELL" and target >= entry_reference
  ):
    # Not adaptable - the target itself is on the wrong side of entry,
    # a genuine invalidation regardless of guard mode.
    return match, GuardOutcome(
      "counter_bias",
      "block",
      "target_not_ahead_of_entry",
      f"counter-bias target {target:.5f} is not ahead of "
      f"{match.direction} entry {entry_reference:.5f}",
      True,
    )
  source_levels = [
    level for level in levels
    if not (
      level.price - level.band <= match.key_level <= level.price + level.band
      and level.price - level.band <= match.entry_high
      and level.price + level.band >= match.entry_low
    )
  ]
  barrier = _counter_bias_barrier_between(
    match.direction, entry_reference, target, zones, source_levels,
  )
  if barrier is None:
    return match, GuardOutcome(
      "counter_bias", OUTCOME_ALLOW, "no_barrier", "no barrier before target", False,
      measured={"target_outcome": "target_unchanged"},
    )
  barrier_price, description = barrier
  buffer_pips = 2.0
  if match.direction == "BUY":
    room_pips = (barrier_price - entry_reference) / pip_size - buffer_pips
  else:
    room_pips = (entry_reference - barrier_price) / pip_size - buffer_pips
  fitted = max(
    (pips for pips in match.targets_pips if pips <= room_pips),
    default=None,
  )
  if fitted is None and room_pips >= _MIN_COUNTER_BIAS_TARGET_PIPS:
    fitted = max(
      _MIN_COUNTER_BIAS_TARGET_PIPS,
      int(math.floor(room_pips)),
    )
  if fitted is None:
    return match, GuardOutcome(
      "counter_bias",
      OUTCOME_ALLOW_WITH_WARNING,
      "target_room_insufficient",
      (
        f"counter-bias target preference before EQ {target:.5f} by {description}: "
        f"room {room_pips:.1f}p does not fit the smallest configured target "
        f"({min(match.targets_pips) if match.targets_pips else 0}p)"
      ),
      False,
      measured={
        "target_outcome": "target_room_insufficient",
        "preference_telemetry": True,
        "available_room_pips": round(room_pips, 1),
        "minimum_target_pips": (
          min(match.targets_pips)
          if match.targets_pips else _MIN_COUNTER_BIAS_TARGET_PIPS
        ),
        "barrier_price": barrier_price,
      },
    )
  adjusted_target = (
    entry_reference + fitted * pip_size
    if match.direction == "BUY"
    else entry_reference - fitted * pip_size
  )
  adapted = replace(
    match,
    target_price=adjusted_target,
    target_model="hybrid",
    target_reference_price="planned_entry",
    absolute_target_price=adjusted_target,
    targets_pips=tuple(sorted(set([
      *(p for p in match.targets_pips if p <= room_pips),
      fitted,
    ]))),
  )
  return adapted, GuardOutcome(
    "counter_bias",
    "adjust_target",
    "target_capped_by_structure",
    (
      f"counter-bias target adapted {target:.5f} -> {adjusted_target:.5f} "
      f"(room {room_pips:.1f}p, barrier {description})"
    ),
    False,
    measured={
      "target_outcome": "target_adapted",
      "original_target": target,
      "adjusted_target": adjusted_target,
      "barrier_price": barrier_price,
      "available_room_pips": round(room_pips, 1),
      "selected_target_pips": fitted,
    },
  )


def _zone_cooldown_key(symbol: str, direction: str) -> str:
  return f"auto_trade:zone:cooldown:{symbol.upper()}:{direction.upper()}"


async def _zone_cooldown_reason(
  client: Any,
  symbol: str,
  direction: str,
  entry_reference: float,
  atr: float | None,
  cooldown_atr: float,
) -> str | None:
  """Veto a same-direction re-entry near a price that just stopped a trade
  out (23 Jul 2026 incident: a stopped-out zone was re-traded 15 minutes
  later).

  The marker is written by AutoTradeEngine.cs whenever a tracked position
  vanishes from the broker without the engine itself having closed it - a
  clean take-profit exit never produces one (see AutoTradeEngine.cs's
  reconcile stale-position branch) - but the vanish itself is ambiguous
  between a genuine stop-loss and a manual/external close, and the current
  broker integration has no execution-history lookup to tell them apart.
  Root cause of the post-23-Jul frequency collapse: the marker was treated
  as a confirmed stop-out unconditionally, blocking every ambiguous close
  (including manual closes) for the full cooldown window. Only a marker
  explicitly tagged ``reason=stop_loss`` and ``confidence=confirmed``
  enforces the block now; legacy markers and anything the engine could not
  positively attribute pass straight through (fail open, matching the
  pattern the zone-reconcile circuit breaker already uses for "don't guess,
  don't destroy the opportunity").
  """
  if (
    not runtime_config.lifecycle.zone.cooldown_enabled
    or not atr or atr <= 0 or cooldown_atr <= 0
  ):
    return None
  raw = await client.get(_zone_cooldown_key(symbol, direction))
  if raw is None:
    return None
  try:
    state = json.loads(raw)
    recorded_entry = float(state["entry_price"])
  except (TypeError, ValueError, KeyError, json.JSONDecodeError):
    return None
  if (
    state.get("reason") != "stop_loss"
    or state.get("confidence") != "confirmed"
  ):
    return None
  distance_atr = abs(entry_reference - recorded_entry) / atr
  if distance_atr > cooldown_atr:
    return None
  return (
    f"zone cooldown: {direction} entry {entry_reference:.5f} is "
    f"{distance_atr:.2f} ATR from a stopped-out entry at "
    f"{recorded_entry:.5f} (limit {cooldown_atr:.2f} ATR)"
  )


def _has_overlapping_zones(market_map: MarketMap | None) -> bool:
  """True when the published Market Map itself contains a BUY and a SELL
  band whose ranges intersect at all - a self-contradiction in the map, not
  yet necessarily where any candidate is entering. Feeds the observability
  counter regardless of the veto flag or any specific candidate.
  """
  if market_map is None:
    return False
  return any(
    buy.lo <= sell.hi and sell.lo <= buy.hi
    for buy in market_map.buys
    for sell in market_map.sells
  )


def _overlapping_zone_conflict_reason(
  entry_reference: float,
  market_map: MarketMap | None,
) -> str | None:
  """Veto an entry that falls inside both a demand (BUY) and a supply
  (SELL) band on the same published Market Map (23 Jul 2026 incident: BUY
  4,112-4,122 and SELL 4,116-4,127 overlapped 4,116-4,122; the fill landed
  inside it). Direction-agnostic - a price the map calls both a floor and a
  ceiling is not a tradeable location in either direction.
  """
  if market_map is None:
    return None
  demand_hit = next(
    (entry for entry in market_map.buys if entry.lo <= entry_reference <= entry.hi),
    None,
  )
  supply_hit = next(
    (entry for entry in market_map.sells if entry.lo <= entry_reference <= entry.hi),
    None,
  )
  if demand_hit is None or supply_hit is None:
    return None
  return (
    f"entry {entry_reference:.5f} inside both demand "
    f"{demand_hit.lo:.5f}-{demand_hit.hi:.5f} and supply "
    f"{supply_hit.lo:.5f}-{supply_hit.hi:.5f}"
  )


def _resolve_overlap_thesis(
  direction: str,
  entry_reference: float,
  market_map: MarketMap | None,
  m1: Any,
  atr: float | None,
  cfg: Any | None = None,
  *,
  symbol: str = "XAU",
) -> GuardOutcome:
  """Resolve an entry inside both a demand and a supply band by the same
  M1 reaction-lookback memory ``map_strategy.py`` already computes for its
  own reaction selection (PR #100), instead of the previous unconditional
  "both directions are dead" veto. Never trims or deletes either band from
  the Market Map itself - this only decides whether THIS candidate's
  thesis has directional confirmation.
  """
  from app.autotrade.map_strategy import _reaction_in_lookback

  if cfg is None:
    cfg = instrument_runtime_view(symbol)
  guard_mode = resolve_guard_mode(cfg)
  if market_map is None:
    return GuardOutcome("overlap", OUTCOME_ALLOW, "no_map", "no market map", False)
  demand_hit = next(
    (entry for entry in market_map.buys if entry.lo <= entry_reference <= entry.hi),
    None,
  )
  supply_hit = next(
    (entry for entry in market_map.sells if entry.lo <= entry_reference <= entry.hi),
    None,
  )
  if demand_hit is None or supply_hit is None:
    return GuardOutcome("overlap", OUTCOME_ALLOW, "no_overlap", "no overlap", False)
  reason = (
    f"entry {entry_reference:.5f} inside both demand "
    f"{demand_hit.lo:.5f}-{demand_hit.hi:.5f} and supply "
    f"{supply_hit.lo:.5f}-{supply_hit.hi:.5f}"
  )
  if m1 is None or getattr(m1, "empty", True) or not atr or atr <= 0:
    return classify_guard_severity(
      "overlap",
      "ambiguous_waiting_confirmation",
      reason,
      guard_mode=guard_mode,
    )
  tolerance = max(0.5 * units.pip_size(symbol), 0.5 * atr)
  own_entry = demand_hit if direction == "BUY" else supply_hit
  own_reaction = _reaction_in_lookback(
    m1, own_entry, direction, atr, tolerance, cfg, entry_reference,
  )
  if own_reaction is not None and own_reaction.reaction_type in ("rejection", "reclaim"):
    return GuardOutcome(
      "overlap", OUTCOME_ALLOW, "reaction_direction_resolved",
      f"{reason} - {direction} {own_reaction.reaction_type} confirms thesis",
      False,
    )
  opposite_direction = "SELL" if direction == "BUY" else "BUY"
  opposite_entry = supply_hit if direction == "BUY" else demand_hit
  opposite_reaction = _reaction_in_lookback(
    m1, opposite_entry, opposite_direction, atr, tolerance, cfg, entry_reference,
  )
  if (
    opposite_reaction is not None
    and opposite_reaction.reaction_type in ("rejection", "reclaim")
  ):
    return classify_guard_severity(
      "overlap",
      "opposing_zone_ahead",
      (
        f"{reason} - {opposite_direction} reaction confirmed "
        f"instead of {direction}"
      ),
      guard_mode=guard_mode,
    )
  return classify_guard_severity(
    "overlap",
    "ambiguous_waiting_confirmation",
    reason,
    guard_mode=guard_mode,
  )


def _opposing_zone_identity(
  zone: Zone,
  *,
  symbol: str,
  timeframe: str,
) -> str:
  """Exact identity of the zone a stop may be pushed beyond.

  Zone detectors carry no stored id, and the detector name identifies the
  detector rather than the zone, so two different zones from one detector
  would share it. The fingerprint therefore includes the zone's own geometry
  and provenance, and the executor derives the same string.
  """
  stored = getattr(zone, "zone_id", None)
  if stored:
    return str(stored)
  created = getattr(zone, "created_ts", None)
  return opposing_zone_fingerprint(
    symbol=symbol,
    timeframe=timeframe,
    side=zone.side,
    low=zone.low,
    high=zone.high,
    created_bar_ts=(
      int(created.timestamp()) if created is not None else zone.origin_index
    ),
    source=zone.source or getattr(zone, "kind", "") or "zone",
  )


def _opposing_zone_policy_kwargs(
  zone: Zone | None,
  *,
  atr: float,
  pip_size: float,
  symbol: str,
  timeframe: str,
) -> dict[str, float | str | None]:
  if zone is None:
    return {}
  return {
    "opposing_zone_low": float(zone.low),
    "opposing_zone_high": float(zone.high),
    "opposing_zone_id": _opposing_zone_identity(
      zone,
      symbol=symbol,
      timeframe=timeframe,
    ),
  }


def _zone_overlaps_candidate_band(
  zone: Zone,
  *,
  candidate_low: float,
  candidate_high: float,
  atr: float,
  pip_size: float,
) -> bool:
  """True when an HTF zone is the candidate's own wall (stacked map noise)."""
  from app.autotrade.structural_target_room import overlap_exclusion_threshold

  low = min(float(candidate_low), float(candidate_high))
  high = max(float(candidate_low), float(candidate_high))
  overlap = min(high, float(zone.high)) - max(low, float(zone.low))
  if overlap <= 0:
    return False
  threshold = overlap_exclusion_threshold(pip_size=pip_size, atr=atr)
  return overlap >= threshold


def _nearest_directional_zone(
  direction: str,
  entry_reference: float,
  zones: list[Zone],
  *,
  candidate_entry_low: float | None = None,
  candidate_entry_high: float | None = None,
  atr: float | None = None,
  pip_size: float | None = None,
  exclude_entry_structure: bool = True,
) -> Zone | None:
  """Nearest HTF zone on the side that can trap the stop of ``direction``.

  Supply for SELL, demand for BUY. Used for opposing-zone attachment and
  HTF veto.

  Excludes the candidate's own entry structure: a SELL at supply used to
  pick that same supply (distance 0), then opposing-stop push blew the
  envelope and silenced valid trades. Same for BUY at demand.
  """
  side = "supply" if direction == "SELL" else "demand"
  candidates = [zone for zone in zones if zone.side == side]
  if not candidates:
    return None

  band_low = candidate_entry_low
  band_high = candidate_entry_high
  if (
    band_low is not None
    and band_high is not None
    and atr is not None
    and pip_size is not None
    and float(atr) > 0
    and float(pip_size) > 0
  ):
    candidates = [
      zone for zone in candidates
      if not _zone_overlaps_candidate_band(
        zone,
        candidate_low=float(band_low),
        candidate_high=float(band_high),
        atr=float(atr),
        pip_size=float(pip_size),
      )
    ]
  if exclude_entry_structure:
    candidates = [
      zone for zone in candidates
      if not (float(zone.low) <= float(entry_reference) <= float(zone.high))
    ]
  if not candidates:
    return None

  def _distance(zone: Zone) -> float:
    if zone.low <= entry_reference <= zone.high:
      return 0.0
    return min(abs(entry_reference - zone.low), abs(entry_reference - zone.high))

  return min(candidates, key=_distance)


def _htf_veto_reason(
  direction: str,
  entry_reference: float,
  zone: Zone | None,
) -> str | None:
  """Veto a direction that opposes a fresh HTF zone price hasn't reached yet
  (defect 4, 22 Jul: SELL taken 13 pips below untested supply). A short
  should be taken at supply, not beneath it.
  """
  if zone is None or zone.touches > 0:
    return None
  untested_and_ahead = (
    zone.low > entry_reference if direction == "SELL"
    else zone.high < entry_reference
  )
  if not untested_and_ahead:
    return None
  kind = "supply" if direction == "SELL" else "demand"
  side_word = "below" if direction == "SELL" else "above"
  return (
    f"HTF veto: {direction} {side_word} untested {kind} "
    f"{zone.low:.5f}-{zone.high:.5f}"
  )


async def _record_gate_reject(client: Any, symbol: str, condition: str) -> None:
  try:
    await client.hincrby(
      f"auto_trade:gate_reject:{symbol.upper()}:{condition}",
      "count",
      1,
    )
  except Exception:
    log.exception(
      "gate-reject counter failed symbol=%s condition=%s", symbol, condition,
    )


async def _increment_strategy_match_blocked(
  client: Any,
  symbol: str,
  reason: str,
) -> None:
  """Aggregate plus reason-specific strategy-match gate counters."""
  await increment_metric(client, "strategy_match_blocked", symbol=symbol)
  await increment_metric(
    client, f"strategy_match_blocked:{reason}", symbol=symbol,
  )


async def _record_guard_evaluation(
  client: Any,
  symbol: str,
  outcome: GuardOutcome,
  *,
  strategy: str = "",
  direction: str = "",
  source_structure: str = "",
) -> None:
  """Fix 10: full observability for every structural-guard evaluation, not
  just terminal blocks - `auto_trade:gate_reject:*` (legacy) only
  increments when ``outcome.hard_block`` is true; this counter always
  fires so allow/warning/wait/adjust outcomes stay visible too.
  """
  try:
    now = int(datetime.now(timezone.utc).timestamp())
    counter_key = (
      f"auto_trade:guard_evaluation:{symbol.upper()}:"
      f"{outcome.guard}:{outcome.outcome}"
    )
    await client.hincrby(
      counter_key,
      "count",
      1,
    )
    await client.hset(counter_key, mapping={"last_at": now})
    metric_key = None
    if outcome.reason_code == "primary_source_excluded_from_barrier":
      metric_key = "primary_source_excluded_from_barrier"
    elif outcome.reason_code == "ambiguous_waiting_confirmation":
      metric_key = "ambiguous_overlap_waiting"
    elif outcome.outcome == OUTCOME_WAIT:
      metric_key = "structural_guard_waiting"
    elif outcome.hard_block:
      metric_key = "structural_guard_would_block"
    elif outcome.outcome != OUTCOME_ALLOW:
      metric_key = "structural_guard_allowed_demo"
    if metric_key:
      await client.hincrby(
        f"auto_trade:metrics:{symbol.upper()}", metric_key, 1,
      )
    barrier = (
      asdict(outcome.barrier) if outcome.barrier is not None else None
    )
    await client.set(
      f"auto_trade:last_guard:{symbol.upper()}",
      json.dumps({
        "strategy": strategy,
        "direction": direction,
        "guard": outcome.guard,
        "outcome": outcome.outcome,
        "reason": outcome.reason_code,
        "message": outcome.message,
        "hard_block": outcome.hard_block,
        "source_structure": source_structure,
        "opposing_structure": barrier,
        "measured": outcome.measured,
        "updated_at": now,
      }, separators=(",", ":"), sort_keys=True),
      ex=86400,
    )
  except Exception:
    log.exception(
      "guard-evaluation counter failed symbol=%s guard=%s outcome=%s",
      symbol, outcome.guard, outcome.outcome,
    )


async def _record_market_map_strategy_telemetry(
  client: Any,
  symbol: str,
  decision: MarketMapStrategyDecision,
) -> None:
  """Expose the exact entry set the Market Map strategy evaluated."""
  try:
    payload = [
      entry.payload()
      for entry in decision.actionable_entries
    ]
    await client.set(
      market_map_actionable_key(symbol),
      json.dumps(payload, separators=(",", ":"), sort_keys=True),
      ex=3600,
    )
    counts = dict(decision.filter_counts)
    rejected = int(counts.get("degenerate_width", 0))
    if rejected:
      await client.incrby(
        f"auto_trade:map_zone_rejected:{symbol.upper()}:degenerate_width",
        rejected,
      )
  except Exception:
    log.exception("Market Map strategy telemetry failed symbol=%s", symbol)


def _candidate_id(
  symbol: str,
  trigger_ts: str,
  decision: AutoScalpDecision,
) -> str:
  rail = decision.rail
  box = decision.box
  if rail is None or box is None or decision.direction is None:
    raise ValueError("candidate decision requires a box, rail, and direction")
  raw = (
    f"v3|box-range|{box.box_id}|{symbol.upper()}|M1|{trigger_ts}|"
    f"{decision.direction.upper()}|{rail.low:.5f}|{rail.high:.5f}"
  )
  return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _group_id(*parts: object) -> str:
  raw = "|".join(str(part) for part in parts if part is not None)
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _intent_freshness(raw: object, fallback: int = 0) -> float:
  text = str(raw or "").strip()
  if text:
    try:
      value = float(text)
      return value / 1000 if value > 1e12 else value
    except ValueError:
      try:
        return datetime.fromisoformat(
          text.replace("Z", "+00:00")
        ).timestamp()
      except ValueError:
        pass
  return float(fallback)


def _band_distance_pips(
  price: float | None,
  low: float,
  high: float,
  symbol: str,
) -> float:
  if price is None or low <= price <= high:
    return 0.0
  return (
    min(abs(price - low), abs(price - high))
    / units.pip_size(symbol)
  )


async def _record_private_route(
  client: Any,
  *,
  symbol: str,
  event_ts: str,
  strategy: str,
  family: str,
  direction: str,
  source: str,
  structural_id: str,
  entry_low: float,
  entry_high: float,
  spot_price: float | None,
  status: str,
  reason_code: str,
  message: str,
  candidate_id: str | None = None,
  group_id: str | None = None,
  retained: bool,
  stage: str | None = None,
  measured: dict[str, Any] | None = None,
  preflight_reason_code: str | None = None,
  arbitration_reason_code: str | None = None,
  publication_reason_code: str | None = None,
  terminal_reason_code: str | None = None,
  winner_intent_id: str | None = None,
  executor_event_id: str | None = None,
) -> None:
  now = int(datetime.now(timezone.utc).timestamp())
  identity = PrivateRouteIdentity(
    symbol=symbol.upper(),
    match_id=_group_id(
      symbol,
      family,
      direction,
      structural_id,
    ),
    strategy=strategy,
    family=family,
    direction=direction.upper(),
    structural_source=source,
    structural_zone_id=structural_id,
    issued_at=int(_intent_freshness(event_ts, now)),
    expires_at=(
      now + max(300, runtime_config.lifecycle.candidate.storage_ttl_seconds)
    ),
    current_price=spot_price,
    entry_low=entry_low,
    entry_high=entry_high,
  )
  await record_route_outcome(
    client,
    identity,
    stage=(
      stage
      or ("stream_publish" if candidate_id else "candidate_claim")
    ),
    status=status,  # type: ignore[arg-type]
    reason_code=reason_code,
    message=message,
    measured=measured,
    candidate_id=candidate_id,
    group_id=group_id,
    executor_event_id=executor_event_id,
    retained=retained,
    preflight_reason_code=preflight_reason_code,
    arbitration_reason_code=arbitration_reason_code,
    publication_reason_code=publication_reason_code,
    terminal_reason_code=terminal_reason_code,
    winner_intent_id=winner_intent_id,
    signal_source=source,
    publish_status=False,
  )


def _strategy_group_id(match: StrategyMatch, *, thesis_cycle: int = 1) -> str:
  if match.thesis_id and (
    match.reaction_id or match.family == "mapped_zone"
    or match.strategy_mode == "mapped_zone_reaction"
  ):
    return mapped_group_id(
      symbol=match.symbol,
      strategy_family=match.family or "mapped_zone",
      direction=match.direction,
      thesis_id=match.thesis_id,
      thesis_cycle=thesis_cycle,
    )
  if match.reaction_id:
    return mapped_group_id(
      symbol=match.symbol,
      strategy_family=match.family or "mapped_zone",
      direction=match.direction,
      thesis_id="",
      reaction_id=match.reaction_id,
    )
  structural_key = (
    match.range_id
    or match.structural_zone_id
    or match.zone_id
    or (
      f"{price_token(match.key_level, pip_size=units.pip_size(match.symbol))}:"
      f"{price_token(match.entry_low, pip_size=units.pip_size(match.symbol))}:"
      f"{price_token(match.entry_high, pip_size=units.pip_size(match.symbol))}"
    )
  )
  return _group_id(
    match.symbol,
    match.family or match.strategy,
    match.direction,
    structural_key,
  )


def _thesis_lock_enabled() -> bool:
  return bool(runtime_config.execution.mapped_zone.thesis_lock_enabled)


async def _load_thesis_claim(client: Any, thesis_id: str | None) -> dict[str, Any] | None:
  if not thesis_id:
    return None
  return parse_thesis_claim(await client.get(thesis_claim_key(thesis_id)))


async def _save_thesis_claim(client: Any, thesis_id: str, payload: dict[str, Any]) -> None:
  await client.set(thesis_claim_key(thesis_id), dump_claim(payload))


async def _acquire_thesis_claim(client: Any, payload_json: str, thesis_id: str) -> bool:
  key = thesis_claim_key(thesis_id)
  try:
    result = await client.eval(
      THESIS_CLAIM_ACQUIRE_LUA,
      1,
      key,
      payload_json,
    )
    return int(result or 0) == 1
  except Exception:
    log.exception("thesis claim lua acquire failed; using conditional SET")
  existing = parse_thesis_claim(await client.get(key))
  if existing is None:
    return bool(await client.set(key, payload_json, nx=True))
  state = str(existing.get("state") or "").casefold()
  rearm = bool(existing.get("rearm_ready"))
  if state == "rearm_ready" or (
    state in {"closed", "cancelled", "rejected", "expired"} and rearm
  ):
    await client.set(key, payload_json)
    return True
  if state in {"cancelled", "rejected", "expired"}:
    await client.set(key, payload_json)
    return True
  return False


async def _mark_reaction_claim_terminal(
  client: Any,
  *,
  reaction_id: str | None,
  state: str,
  thesis_id: str | None = None,
) -> None:
  if reaction_id:
    key = reaction_claim_key(reaction_id)
    existing = parse_reaction_claim(await client.get(key))
    if existing is not None:
      existing["state"] = state
      await client.set(key, dump_claim(existing))
  if thesis_id and _thesis_lock_enabled():
    claim = await _load_thesis_claim(client, thesis_id)
    if claim is None:
      return
    claim["state"] = state
    if state in {"cancelled", "rejected", "expired"}:
      claim["terminal_at"] = int(datetime.now(timezone.utc).timestamp())
      # Rejected/cancelled before a live managed group may recycle.
      if state in {"cancelled", "rejected", "expired"}:
        claim["rearm_ready"] = True
    await _save_thesis_claim(client, thesis_id, claim)


async def _mark_thesis_terminal_waiting_exit(
  client: Any,
  *,
  thesis_id: str | None,
  reaction_id: str | None = None,
) -> None:
  if not thesis_id or not _thesis_lock_enabled():
    return
  claim = await _load_thesis_claim(client, thesis_id)
  if claim is None:
    return
  now = int(datetime.now(timezone.utc).timestamp())
  claim["state"] = "terminal_waiting_exit"
  claim["terminal_at"] = now
  claim["rearm_ready"] = False
  claim["outside_bar_count"] = 0
  claim["first_outside_bar_ts"] = None
  claim["latest_outside_bar_ts"] = None
  claim["reentry_bar_ts"] = None
  claim["exit_detected_at"] = None
  if reaction_id:
    claim["active_reaction_id"] = reaction_id
  await _save_thesis_claim(client, thesis_id, claim)
  await increment_metric(client, "mapped_thesis_terminal", symbol=claim.get("symbol"))


async def _advance_mapped_thesis_rearms(
  client: Any,
  *,
  symbol: str,
  m1: Any,
  atr: float,
) -> None:
  """Advance exit/outside-bar tracking for terminal mapped theses."""
  if not _thesis_lock_enabled() or m1 is None or getattr(m1, "empty", True):
    return
  try:
    bar = m1.iloc[-1]
    bar_ts = str(m1.index[-1])
    bar_low = float(bar["low"])
    bar_high = float(bar["high"])
    close = float(bar["close"])
  except Exception:
    return
  now = int(datetime.now(timezone.utc).timestamp())
  rearm_atr = float(runtime_config.lifecycle.mapped_zone.reaction_rearm_atr)
  rearm_bars = int(runtime_config.lifecycle.mapped_zone.reaction_rearm_bars)
  pattern = "auto_trade:thesis_claim:*"
  async for raw_key in client.scan_iter(match=pattern, count=50):
    key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
    claim = parse_thesis_claim(await client.get(key))
    if claim is None:
      continue
    if str(claim.get("symbol") or "").upper() != symbol.upper():
      continue
    state = str(claim.get("state") or "").casefold()
    if state not in {"terminal_waiting_exit", "outside_zone", "closed"}:
      continue
    updated, metric = advance_thesis_rearm_on_bar(
      claim,
      bar_ts=bar_ts,
      bar_low=bar_low,
      bar_high=bar_high,
      close=close,
      atr=float(atr) if atr and atr > 0 else float(claim.get("atr") or 0) or 1.0,
      rearm_atr=rearm_atr,
      rearm_bars=rearm_bars,
      now_ts=now,
    )
    if updated != claim:
      await client.set(key, dump_claim(updated))
    if metric:
      await increment_metric(client, metric, symbol=symbol)


async def _reconcile_legacy_mapped_thesis_claims(client: Any) -> None:
  """Create thesis claims for open mapped groups that predate the lock."""
  if not _thesis_lock_enabled():
    return
  pattern = "auto_trade:group_plan:*"
  async for raw_key in client.scan_iter(match=pattern, count=50):
    key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
    raw = await client.get(key)
    if raw is None:
      continue
    try:
      text = raw.decode() if isinstance(raw, bytes) else str(raw)
      plan = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
      continue
    if not isinstance(plan, dict):
      continue
    thesis_id = plan.get("ThesisId") or plan.get("thesis_id")
    reaction_id = plan.get("ReactionId") or plan.get("reaction_id")
    zone_id = plan.get("ZoneId") or plan.get("zone_id") or plan.get("StructuralZoneId")
    symbol = str(plan.get("Symbol") or plan.get("symbol") or runtime_config.contract.instrument.canonical_symbol).upper()
    direction = str(plan.get("Direction") or plan.get("direction") or "").upper()
    strategy = str(plan.get("Setup") or plan.get("setup") or "Mapped Zone Reaction")
    family = str(
      plan.get("StrategyFamily") or plan.get("strategy_family") or "mapped_zone"
    )
    if family not in {"mapped_zone", "mapped_zone_reaction"} and "mapped" not in strategy.casefold():
      continue
    if not thesis_id and reaction_id and zone_id and direction:
      from app.autotrade.reaction_identity import mapped_thesis_id
      thesis_id = mapped_thesis_id(
        symbol=symbol,
        strategy=strategy if strategy else "Mapped Zone Reaction",
        direction=direction,
        structural_zone_id=str(zone_id),
      )
    if not thesis_id:
      await increment_metric(client, "legacy_group_thesis_unattributed", symbol=symbol)
      continue
    existing = await _load_thesis_claim(client, str(thesis_id))
    if existing is not None and thesis_state_blocks_new_initial(existing.get("state")):
      continue
    if existing is not None and str(existing.get("state") or "") in ACTIVE_THESIS_STATES:
      continue
    now = int(datetime.now(timezone.utc).timestamp())
    body = thesis_claim_payload(
      thesis_id=str(thesis_id),
      strategy=strategy,
      strategy_family="mapped_zone",
      symbol=symbol,
      direction=direction or "BUY",
      structural_zone_id=str(zone_id or ""),
      structural_zone_low=None,
      structural_zone_high=None,
      active_reaction_id=str(reaction_id or ""),
      candidate_id=str(plan.get("CandidateId") or plan.get("candidate_id") or ""),
      group_id=str(plan.get("GroupId") or plan.get("group_id") or ""),
      state="managing",
      claimed_at=now,
      touch_bar_ts="",
      confirmation_bar_ts="",
      thesis_cycle=1,
    )
    claimed = await client.set(thesis_claim_key(str(thesis_id)), body, nx=True)
    if claimed:
      await increment_metric(client, "legacy_group_thesis_recovered", symbol=symbol)


def _trend_group_id(
  symbol: str,
  decision: TrendDecision,
) -> str:
  return _group_id(
    symbol.upper(),
    "trend",
    decision.direction.upper(),
    decision.mode,
    f"{decision.key_level:.5f}",
    f"{decision.entry_zone[0]:.5f}",
    f"{decision.entry_zone[1]:.5f}",
  )


def _strategy_mode_enabled(match: StrategyMatch) -> bool:
  from app.autotrade.strategy_registry import strategy_mode_enabled

  return strategy_mode_enabled(match.strategy, runtime_config)


def _trend_bias_metadata(
  regime: RegimeInfo,
  direction: str,
) -> tuple[str, str]:
  raw_bias = next(
    (
      reason.partition("=")[2].strip().casefold()
      for reason in regime.reasons
      if reason.startswith("htf_bias=")
    ),
    "range",
  )
  bias = {
    "up": "bullish",
    "down": "bearish",
  }.get(raw_bias, "neutral")
  if bias == "neutral":
    return bias, "neutral"
  local_bias = "bullish" if direction.upper() == "BUY" else "bearish"
  return bias, "with_bias" if bias == local_bias else "counter_bias"


def _instrument_currencies(symbol: str) -> tuple[str, str] | None:
  """Split a 6-letter FX pair like 'GBPJPY' into ('GBP', 'JPY').

  None for anything that isn't a two-fiat-currency pair (XAU and friends),
  so the event-cluster guard below safely no-ops for them.
  """
  upper = symbol.upper()
  if len(upper) != 6 or not upper.isalpha():
    return None
  first, second = upper[:3], upper[3:]
  return None if first == second else (first, second)


async def _event_cluster_guard(symbol: str, now: int) -> dict | None:
  """Widened news guard for a compounding event cluster.

  2026 GBP/JPY dig: a BoE data print and a BoJ policy statement landing in
  the same 48h window compounds volatility rather than adding it -- the
  single-event news_guard_minutes window (30m by default) is far too
  narrow to cover that. When both of this instrument's constituent
  currencies have a high-impact event within event_cluster_span_hours of
  each other, apply the wider event_cluster_guard_minutes window around
  whichever event is nearer to `now` instead. Off by default
  (event_cluster_guard_enabled); currently only turned on for GBPJPY.
  """
  gates = runtime_config.actionability.gates
  if not gates.event_cluster_guard_enabled:
    return None
  currencies = _instrument_currencies(symbol)
  if currencies is None:
    return None
  span = max(1, gates.event_cluster_span_hours) * 3600
  first_currency, second_currency = currencies
  first_event = await nearest_currency_event(
    first_currency, now - span, now + span, now,
  )
  second_event = await nearest_currency_event(
    second_currency, now - span, now + span, now,
  )
  if first_event is None or second_event is None:
    return None
  nearer = min(
    (first_event, second_event),
    key=lambda event: abs(int(event["ts_utc"]) - now),
  )
  guard_window = max(0, gates.event_cluster_guard_minutes) * 60
  if abs(int(nearer["ts_utc"]) - now) > guard_window:
    return None
  return nearer


async def _news_guard_hit(symbol: str, now: int) -> dict | None:
  """The normal single-event news guard, widened by an event-cluster hit."""
  cluster_hit = await _event_cluster_guard(symbol, now)
  if cluster_hit is not None:
    return cluster_hit
  return await event_in_window(
    now, max(0, runtime_config.actionability.gates.news_guard_minutes) * 60,
  )


async def _publish_candidate(
  client: Any,
  symbol: str,
  event_ts: str,
  spot: AutoTradeSpot | None,
  decision: AutoScalpDecision,
  scale_context: AutoScaleContext | None = None,
  *,
  regime: RegimeInfo | None = None,
  htf_zones: list[Zone] | None = None,
  htf_levels: list[Level] | None = None,
  gate_source: str = "private_ohlc",
  market_map: MarketMap | None = None,
  frames: dict[str, Any] | None = None,
) -> str | None:
  if (
    not runtime_config.runtime.auto_trade.enabled
    or not runtime_config.strategies.range_reversion.enabled
    or spot is None
    or not spot.fresh
    or decision.state != "candidate"
    or decision.rail is None
    or decision.box is None
    or decision.direction is None
    or scale_context is None
    or decision.confluence < max(
      1, runtime_config.actionability.gates.min_confluence,
    )
  ):
    return None
  if decision.full_tp_pips not in configured_range_targets():
    # gate.py already selected this via the shared range_targets ladder; a
    # mismatch here means config drifted between the gate call and now, or
    # a caller passed a stale decision - either way it must be traceable,
    # not folded silently into the compound guard above.
    await _record_gate_reject(client, symbol, "insufficient_target_room")
    return None
  entry_reference = spot.price
  guard_mode = resolve_guard_mode()
  if regime is not None and regime.state != "chop":
    regime_outcome = ExecutionGuardDecision(
      "regime",
      "block",
      "range_edge_not_chop",
      (
        f"range-box strategy requires chop; regime={regime.state}"
      ),
      True,
    )
    await _record_guard_evaluation(
      client, symbol, regime_outcome,
      strategy="Range Box Scalp",
      direction=decision.direction,
      source_structure=(
        f"range_box_edge {decision.rail.low:.5f}-{decision.rail.high:.5f}"
      ),
    )
    await _record_gate_reject(client, symbol, "range_edge_not_chop")
    return None
  eq_reason = _eq_exclusion_reason(
    decision.box,
    entry_reference,
    runtime_config.actionability.gates.eq_exclusion_fraction,
  )
  if eq_reason is not None:
    eq_outcome = classify_guard_severity(
      "eq_exclusion",
      "eq_exclusion",
      eq_reason,
      guard_mode=guard_mode,
    )
    await _record_guard_evaluation(
      client, symbol, eq_outcome,
      strategy="Range Box Scalp",
      direction=decision.direction,
      source_structure="range_box_edge",
    )
    log.info(
      "auto-scalp candidate %s symbol=%s reason=%s",
      "blocked" if eq_outcome.hard_block else eq_outcome.outcome,
      symbol,
      eq_reason,
    )
    if eq_outcome.hard_block:
      await _record_gate_reject(client, symbol, "eq_exclusion")
      return None
  edge_reason = _edge_proximity_reason(
    decision.rail,
    entry_reference,
    scale_context.atr,
    runtime_config.actionability.gates.edge_proximity_atr,
  )
  if edge_reason is not None:
    edge_outcome = classify_guard_severity(
      "edge_proximity",
      "edge_proximity",
      edge_reason,
      guard_mode=guard_mode,
    )
    await _record_guard_evaluation(
      client, symbol, edge_outcome,
      strategy="Range Box Scalp",
      direction=decision.direction,
      source_structure="range_box_edge",
    )
    log.info(
      "auto-scalp candidate %s symbol=%s reason=%s",
      "blocked" if edge_outcome.hard_block else edge_outcome.outcome,
      symbol,
      edge_reason,
    )
    if edge_outcome.hard_block:
      await _record_gate_reject(client, symbol, "edge_proximity")
      return None
  opposing_zone = _nearest_directional_zone(
    decision.direction,
    entry_reference,
    htf_zones or [],
    candidate_entry_low=float(decision.entry_zone[0]),
    candidate_entry_high=float(decision.entry_zone[1]),
    atr=float(scale_context.atr),
    pip_size=float(units.pip_size(symbol)),
  )
  # Range/scalp with fitted native room is not HTF-opposing gated.
  scalp_ignores_opposing = bypasses_opposing_structure_gates(
    "Range Box Scalp",
    full_take_profit_pips=decision.full_tp_pips,
    family="range",
    strategy_mode="auto_box_scalp",
  )
  if (
    runtime_config.actionability.gates.htf_veto_enabled
    and not scalp_ignores_opposing
  ):
    veto_reason = _htf_veto_reason(decision.direction, entry_reference, opposing_zone)
    if veto_reason is not None:
      veto_outcome = classify_guard_severity(
        "htf_veto",
        "htf_veto",
        veto_reason,
        guard_mode=guard_mode,
      )
      await _record_guard_evaluation(
        client, symbol, veto_outcome,
        strategy="Range Box Scalp",
        direction=decision.direction,
        source_structure="range_box_edge",
      )
      log.info(
        "auto-scalp candidate %s symbol=%s reason=%s",
        "blocked" if veto_outcome.hard_block else veto_outcome.outcome,
        symbol,
        veto_reason,
      )
      if veto_outcome.hard_block:
        await _record_gate_reject(client, symbol, "htf_veto")
        return None
  m1 = (frames or {}).get("M1") if frames is not None else None
  # Range/scalp may enter inside HTF opposing structure; native range room
  # (select_range_target / EQ room) remains the room gate.
  cooldown_reason = await _zone_cooldown_reason(
    client, symbol, decision.direction, entry_reference,
    scale_context.atr, runtime_config.lifecycle.zone.cooldown_atr,
  )
  if cooldown_reason is not None:
    cooldown_outcome = classify_guard_severity(
      "zone_cooldown", "zone_cooldown", cooldown_reason,
      guard_mode=guard_mode, hard_geometry=False,
    )
    await _record_guard_evaluation(
      client, symbol, cooldown_outcome,
      strategy="Range Box Scalp",
      direction=decision.direction,
      source_structure="range_box_edge",
    )
    log.info(
      "auto-scalp candidate %s symbol=%s reason=%s",
      "blocked" if cooldown_outcome.hard_block else cooldown_outcome.outcome,
      symbol, cooldown_reason,
    )
    if cooldown_outcome.hard_block:
      await _record_gate_reject(client, symbol, "zone_cooldown")
      return None
  if (
    runtime_config.actionability.overlapping_zones.veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
  ):
    overlap_outcome = _resolve_overlap_thesis(
      decision.direction, entry_reference, market_map, m1,
      scale_context.atr, None, symbol=symbol,
    )
    if overlap_outcome.reason_code not in ("no_map", "no_overlap"):
      await _record_guard_evaluation(
        client, symbol, overlap_outcome,
        strategy="Range Box Scalp",
        direction=decision.direction,
        source_structure="range_box_edge",
      )
      log.info(
        "auto-scalp candidate %s symbol=%s reason=%s",
        "blocked" if overlap_outcome.hard_block else overlap_outcome.outcome,
        symbol, overlap_outcome.message,
      )
    if overlap_outcome.hard_block:
      await _record_gate_reject(client, symbol, "overlapping_zone_conflict")
      return None
    if overlap_outcome.outcome == OUTCOME_WAIT:
      return None

  now = int(datetime.now(timezone.utc).timestamp())
  try:
    guarded = await _news_guard_hit(
      symbol, now,
    )
  except Exception:
    log.exception("auto-scalp candidate blocked: news guard unavailable")
    return None
  if guarded is not None:
    log.info(
      "auto-scalp candidate blocked by news guard symbol=%s event=%s",
      symbol,
      guarded.get("title", "high-impact event"),
    )
    return None

  trigger_ts = str(event_ts or "")
  candidate_id = _candidate_id(symbol, trigger_ts, decision)
  range_tier = "A" if decision.confluence >= 3 else "B"
  range_policy = evaluate_execution_policy(
    PrivatePolicySubject(
      symbol=symbol,
      strategy="Range Box Scalp",
      direction=decision.direction.upper(),
      entry_low=decision.rail.low,
      entry_high=decision.rail.high,
      current_price=spot.price,
      confluence=decision.confluence,
      atr=scale_context.atr,
      structure_swing=scale_context.structure_swing,
      targets_pips=(
        (int(decision.full_tp_pips),)
        if decision.full_tp_pips is not None else ()
      ),
      risk_multiplier=risk_multiplier_for_tier(
        range_tier, None, range_scalp=True,
      ),
      sweep_low=decision.sweep_low,
      sweep_high=decision.sweep_high,
      full_take_profit_pips=(
        int(decision.full_tp_pips)
        if decision.full_tp_pips is not None else None
      ),
      family="range",
      strategy_mode="auto_box_scalp",
    ),
    spot_price=_executable_spot_price(spot, decision.direction),
    regime=regime.state if regime is not None else "chop",
    pip_size=units.pip_size(symbol),
    cfg=None,
    **(
      {}
      if scalp_ignores_opposing
      else _opposing_zone_policy_kwargs(
        opposing_zone,
        atr=scale_context.atr,
        pip_size=units.pip_size(symbol),
        symbol=symbol,
        timeframe=EXECUTION_TIMEFRAME,
      )
    ),
  )
  if not range_policy.allowed:
    await _record_gate_reject(
      client, symbol, range_policy.reason_code,
    )
    return None
  fixed_rr_targets = _fixed_rr_policy_targets(range_policy)
  range_targets = (
    list(fixed_rr_targets)
    if fixed_rr_targets
    else [
      int(runtime_config.execution.range.box_scale_out_trigger_pips),
      int(decision.full_tp_pips),
    ]
    if (
      runtime_config.strategies.range_reversion.box_scale_out_enabled
      and not runtime_config.strategies.range_reversion.flip_enabled
      and decision.full_tp_pips is not None
      and int(decision.full_tp_pips)
        > int(runtime_config.execution.range.box_scale_out_threshold_pips)
    )
    else [int(decision.full_tp_pips)]
    if decision.full_tp_pips is not None
    else []
  )
  payload = {
    "version": 5,
    "candidate_id": candidate_id,
    "group_id": _group_id(
      symbol,
      "range",
      decision.box.box_id,
      decision.direction,
    ),
    "strategy_family": "range",
    "zone_id": f"{decision.box.box_id}:{decision.direction.upper()}",
    "trigger_id": trigger_ts,
    "parent_group_id": None,
    "structural_source": "range_box_edge",
    "symbol": symbol.upper(),
    "timeframe": EXECUTION_TIMEFRAME,
    "setup": "Range Box Scalp",
    "mode": "auto_box_scalp",
    "signal_source": gate_source,
    "direction": decision.direction.upper(),
    "trigger_ts": trigger_ts,
    "created_at": now,
    "spot_ts": spot.ts,
    "current_price": spot.price,
    "key_level": decision.rail.level,
    "entry_zone": {
      "low": decision.rail.low,
      "high": decision.rail.high,
    },
    "confluence": decision.confluence,
    "tier": range_tier,
    "risk_multiplier": risk_multiplier_for_tier(
      range_tier,
      None,
      range_scalp=True,
    ),
    "reasons": list(decision.reasons),
    "range_id": decision.box.box_id,
    "range_low": decision.box.lower.level,
    "range_high": decision.box.upper.level,
    "full_take_profit_pips": (
      range_targets[-1] if range_targets else decision.full_tp_pips
    ),
    "targets_pips": range_targets,
    "target_model": "fill_relative",
    "target_reference_price": "broker_fill",
    "absolute_target_price": None,
    "order_type_preference": "either",
    "entry_distribution": "single",
    "scale_out_fraction": (
      float(runtime_config.execution.range.box_scale_out_fraction)
      if (
        not fixed_rr_targets
        and runtime_config.strategies.range_reversion.box_scale_out_enabled
        and not runtime_config.strategies.range_reversion.flip_enabled
        and decision.full_tp_pips is not None
        and int(decision.full_tp_pips)
          > int(runtime_config.execution.range.box_scale_out_threshold_pips)
      )
      else None
    ),
    "sweep_low": decision.sweep_low,
    "sweep_high": decision.sweep_high,
    "regime": regime.state if regime is not None else "chop",
    "bias": "neutral",
    "relationship_to_bias": "neutral",
    "opposing_zone_low": None if opposing_zone is None else opposing_zone.low,
    "opposing_zone_high": None if opposing_zone is None else opposing_zone.high,
    "opposing_zone_id": (
      None if opposing_zone is None else _opposing_zone_identity(
        opposing_zone,
        symbol=symbol,
        timeframe=EXECUTION_TIMEFRAME,
      )
    ),
    "add_zone_side": None if opposing_zone is None else opposing_zone.side,
    **_stop_contract_fields(range_policy.measured),
  }
  if scale_context is not None:
    payload.update({
      "bar_ts": scale_context.bar_ts,
      "atr": scale_context.atr,
      "structure_swing": scale_context.structure_swing,
      "displacement_direction": scale_context.displacement_direction,
      "displacement_age_bars": scale_context.displacement_age_bars,
      "bos_direction": scale_context.bos_direction,
      "bos_ts": scale_context.bos_ts,
      "opposing_level_distance_atr": (
        scale_context.opposing_level_distance_atr
      ),
      "counter_bos_ts": scale_context.counter_bos_ts,
      "extreme_price": scale_context.extreme_price,
      "extreme_ts": scale_context.extreme_ts,
      "rejection_confirmed": scale_context.rejection_confirmed,
    })
  publish_result = await publish_candidate_atomic(
    client,
    stream=runtime_config.contract.streams.candidates,
    candidate_id=candidate_id,
    payload=json.dumps(payload, separators=(",", ":")),
    ttl=runtime_config.lifecycle.candidate.storage_ttl_seconds,
    maxlen=runtime_config.contract.streams.candidate_maximum_length,
    ownership_key=autonomous_cycle_owner_key(symbol, trigger_ts),
    ownership_payload=candidate_id,
    ownership_ttl=runtime_config.lifecycle.candidate.storage_ttl_seconds,
    allow_non_atomic_test_fallback=explicit_test_fallback_enabled(client),
  )
  published, executor_event_id = publish_result
  if not published:
    await _record_private_route(
      client,
      symbol=symbol,
      event_ts=event_ts,
      strategy="Range Box Scalp",
      family="range",
      direction=decision.direction,
      source=gate_source,
      structural_id=decision.box.box_id,
      entry_low=decision.rail.low,
      entry_high=decision.rail.high,
      spot_price=spot.price,
      status=(
        "blocked"
        if publish_result.status == "atomic_publish_unavailable"
        else "duplicate_suppressed"
      ),
      reason_code=publish_result.status,
      message=f"private range publication failed: {publish_result.status}",
      group_id=payload["group_id"],
      retained=True,
      stage=(
        "stream_publish"
        if publish_result.status == "atomic_publish_unavailable"
        else "candidate_claim"
      ),
      publication_reason_code=publish_result.status,
    )
    return None
  await _record_private_route(
    client,
    symbol=symbol,
    event_ts=event_ts,
    strategy="Range Box Scalp",
    family="range",
    direction=decision.direction,
    source=gate_source,
    structural_id=decision.box.box_id,
    entry_low=decision.rail.low,
    entry_high=decision.rail.high,
    spot_price=spot.price,
    status="candidate_published",
    reason_code="candidate_published",
    message="private range candidate published atomically",
    candidate_id=candidate_id,
    group_id=payload["group_id"],
    retained=False,
    stage="stream_publish",
    publication_reason_code="candidate_published",
    executor_event_id=executor_event_id,
  )
  await increment_metric(client, "candidate_published", symbol=symbol)
  await emit_lifecycle(
    client,
    "candidate_published",
    symbol=symbol,
    candidate_id=candidate_id,
    range_id=decision.box.box_id,
    group_id=payload["group_id"],
    strategy="Range Box Scalp",
    strategy_family="range",
    direction=decision.direction,
    timeframe=EXECUTION_TIMEFRAME,
    entry_zone=payload["entry_zone"],
    current_price=spot.price,
    target_plan=[decision.full_tp_pips],
    message="private range candidate published to executor",
    publish_status=True,
  )
  await _mark_range_side_candidate(
    client,
    symbol=symbol,
    range_id=decision.box.box_id,
    direction=decision.direction,
    candidate_id=candidate_id,
    group_id=payload["group_id"],
  )
  await client.set(
    _box_edge_key(symbol, decision.box.box_id, decision.direction),
    "1",
    ex=max(300, runtime_config.lifecycle.range_box.retirement_seconds),
  )
  log.info(
    "auto-scalp candidate published id=%s symbol=%s direction=%s",
    candidate_id[:12],
    symbol,
    decision.direction,
  )
  return candidate_id


async def _publish_strategy_match(
  client: Any,
  symbol: str,
  spot: AutoTradeSpot | None,
  match: StrategyMatch,
  *,
  consume_redis_match: bool = True,
  match_source: str = "scanner_strategy_match",
  htf_zones: list[Zone] | None = None,
  htf_levels: list[Level] | None = None,
  regime: RegimeInfo | None = None,
  market_map: MarketMap | None = None,
  frames: dict[str, Any] | None = None,
  cycle_id: str | None = None,
) -> str | None:
  """Publish a completed scanner strategy match without PA re-confirmation."""
  async def route(
    stage: str,
    status: str,
    reason_code: str,
    message: str,
    *,
    measured: dict[str, Any] | None = None,
    retained: bool,
    candidate_id: str | None = None,
    group_id: str | None = None,
    executor_event_id: str | None = None,
    publish_status: bool = True,
  ) -> None:
    await record_route_outcome(
      client,
      match,
      stage=stage,  # type: ignore[arg-type]
      status=status,  # type: ignore[arg-type]
      reason_code=reason_code,
      message=message,
      measured={
        "guard_mode": resolve_guard_mode(),
        "spot_price": None if spot is None else spot.price,
        "entry_low": match.entry_low,
        "entry_high": match.entry_high,
        **(measured or {}),
      },
      retained=retained,
      candidate_id=candidate_id,
      group_id=group_id,
      executor_event_id=executor_event_id,
      publish_status=publish_status,
    )

  await increment_metric(client, "strategy_match_evaluated", symbol=symbol)
  if spot is None or not spot.fresh:
    await emit_lifecycle(
      client,
      "waiting_for_price",
      symbol=symbol,
      candidate_id=match.match_id,
      match_id=match.match_id,
      range_id=match.range_id,
      group_id=_strategy_group_id(match),
      strategy=match.strategy,
      strategy_family=match.family,
      direction=match.direction,
      timeframe=match.source_tf,
      entry_zone={"low": match.entry_low, "high": match.entry_high},
      reason_code="stale_or_missing_spot",
      message="strategy match waits for a fresh cTrader quote",
    )
    await route(
      "spot_check",
      "waiting",
      "stale_or_missing_spot",
      "strategy match waits for a fresh cTrader quote",
      retained=True,
    )
    await increment_metric(client, "strategy_match_waiting", symbol=symbol)
    return None
  if not runtime_config.runtime.auto_trade.enabled:
    await _consume_strategy_match(client, symbol, match)
    await route(
      "mode_check", "blocked", "auto_trade_disabled",
      "autonomous execution is disabled", retained=False,
    )
    await _increment_strategy_match_blocked(
      client, symbol, "auto_trade_disabled",
    )
    return None
  if not runtime_config.runtime.auto_trade.strategy_match_enabled:
    await _consume_strategy_match(client, symbol, match)
    await route(
      "mode_check", "blocked", "strategy_match_disabled",
      "new StrategyMatch routing is disabled; existing positions remain managed",
      retained=False,
    )
    await _increment_strategy_match_blocked(
      client, symbol, "strategy_match_disabled",
    )
    return None
  if not _strategy_mode_enabled(match):
    await _consume_strategy_match(client, symbol, match)
    await route(
      "mode_check", "blocked", "strategy_disabled",
      f"{match.strategy} execution is disabled", retained=False,
    )
    await _increment_strategy_match_blocked(
      client, symbol, "strategy_disabled",
    )
    return None
  if match.symbol != symbol.upper():
    await route(
      "mode_check", "blocked", "symbol_mismatch",
      f"match symbol {match.symbol} does not match worker {symbol.upper()}",
      retained=False,
    )
    await _consume_strategy_match(client, symbol, match)
    await _increment_strategy_match_blocked(
      client, symbol, "symbol_mismatch",
    )
    return None
  if match.confluence < max(1, runtime_config.actionability.gates.min_confluence):
    await route(
      "mode_check", "blocked", "confluence_below_minimum",
      (
        f"confluence {match.confluence} below minimum "
        f"{max(1, runtime_config.actionability.gates.min_confluence)}"
      ),
      measured={
        "confluence": match.confluence,
        "minimum_confluence": max(
          1, runtime_config.actionability.gates.min_confluence,
        ),
      },
      retained=False,
    )
    await _consume_strategy_match(client, symbol, match)
    await _increment_strategy_match_blocked(
      client, symbol, "confluence_below_minimum",
    )
    return None
  guard_mode = resolve_guard_mode()
  source_summary = (
    f"{match.structural_source or match.strategy} "
    f"{match.entry_low:.5f}-{match.entry_high:.5f}"
  )
  if match.is_range_edge:
    # Range Edge Scalp ("Range Box Scalp" label) is a mean-reversion play on
    # an actual consolidation, same as the private box gate above - it must
    # not fire once regime has moved past chop (22 Jul incident: this exact
    # path filled a BUY straight into a sharp post-rally pullback, stopped
    # in under a minute). Other strategy_match types (Box Breakout, Liquidity
    # Sweep, Mapped Zone Reaction, ...) are trend/breakout-appropriate by
    # design and stay ungated here.
    if regime is not None and regime.state != "chop":
      regime_outcome = ExecutionGuardDecision(
        "regime",
        "block",
        "range_edge_not_chop",
        (
          f"range-edge strategy requires chop; regime={regime.state}"
        ),
        True,
      )
      await _record_guard_evaluation(
        client, symbol, regime_outcome,
        strategy=match.strategy,
        direction=match.direction,
        source_structure=source_summary,
      )
      await _consume_strategy_match(client, symbol, match)
      await _record_gate_reject(client, symbol, "range_edge_not_chop")
      await _increment_strategy_match_blocked(
        client, symbol, "range_edge_not_chop",
      )
      await route(
        "mode_check", "blocked", "range_edge_not_chop",
        regime_outcome.message, measured=regime_outcome.measured,
        retained=False,
      )
      return None
    assert match.range_id is not None
    assert match.range_low is not None
    assert match.range_high is not None
    scanner_context = RangeContext.from_json(
      await client.get(range_context_source_key(symbol, "scanner"))
    )
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if scanner_context is None:
      await _consume_strategy_match(client, symbol, match)
      await route(
        "range_context",
        "expired",
        "range_context_withdrawn",
        "scanner range source withdrawn after match build",
        retained=False,
      )
      await increment_metric(
        client, "range_edge_context_expired", symbol=symbol,
      )
      return None
    if not is_range_context_current(
      scanner_context,
      now=now_ts,
      max_age_seconds=SCANNER_SOURCE_MAX_AGE_SECONDS,
    ):
      if match.expires_at > now_ts:
        await route(
          "range_context",
          "waiting",
          "scanner_range_stale",
          "scanner range source is stale; match retained",
          measured={
            "age_seconds": max(0, now_ts - scanner_context.generated_at),
          },
          retained=True,
        )
        await increment_metric(client, "scanner_range_stale", symbol=symbol)
        return None
      await _consume_strategy_match(client, symbol, match)
      await route(
        "range_context",
        "expired",
        "range_context_withdrawn",
        "scanner range source stale beyond match TTL",
        retained=False,
      )
      await increment_metric(
        client, "range_edge_context_expired", symbol=symbol,
      )
      return None
    if (
      scanner_context.state not in ACTIVE_RANGE_STATES
      or not range_geometry_matches_match(
        scanner_context,
        range_id=match.range_id,
        range_low=match.range_low,
        range_high=match.range_high,
      )
    ):
      await _consume_strategy_match(client, symbol, match)
      await route(
        "range_context",
        "expired",
        "range_context_withdrawn",
        "scanner range no longer matches this Range Edge thesis",
        retained=False,
      )
      await increment_metric(
        client, "range_edge_context_expired", symbol=symbol,
      )
      return None
    if await client.exists(_box_retired_key(symbol, match.range_id)):
      await _consume_strategy_match(client, symbol, match)
      await route(
        "entry_invalidation", "expired", "range_retired",
        "range thesis has retired", retained=False,
      )
      return None
    edge_key = _box_edge_key(symbol, match.range_id, match.direction)
    if await client.exists(edge_key):
      midpoint = (match.range_low + match.range_high) / 2
      crossed_midpoint = (
        spot.price >= midpoint
        if match.direction == "BUY"
        else spot.price <= midpoint
      )
      if not crossed_midpoint:
        await route(
          "entry_invalidation", "waiting", "range_rearm_pending",
          "range entry waits for midpoint rearm", retained=True,
        )
        return None
      await client.delete(edge_key)
  m1 = (frames or {}).get("M1")

  match, cb_outcome = _adapt_counter_bias_target(
    match, spot.price, htf_zones or [], htf_levels or [], units.pip_size(symbol),
  )
  if cb_outcome.reason_code not in ("not_counter_bias", "no_barrier"):
    await _record_guard_evaluation(
      client, symbol, cb_outcome,
      strategy=match.strategy,
      direction=match.direction,
      source_structure=source_summary,
    )
    log.info(
      "strategy match %s symbol=%s strategy=%s reason=%s",
      "blocked" if cb_outcome.hard_block else cb_outcome.outcome,
      symbol, match.strategy, cb_outcome.message,
    )
  if cb_outcome.hard_block:
    await _consume_strategy_match(client, symbol, match)
    await _record_gate_reject(client, symbol, "counter_bias_target_barrier")
    await route(
      "counter_bias", "blocked", "target_room_insufficient",
      cb_outcome.message, measured=cb_outcome.measured, retained=False,
    )
    await increment_metric(
      client,
      f"{match.strategy.lower().replace(' ', '_')}_target_room_insufficient",
      symbol=symbol,
    )
    return None

  strategy_opposing_zone = _nearest_directional_zone(
    match.direction,
    spot.price,
    htf_zones or [],
    candidate_entry_low=match.entry_low,
    candidate_entry_high=match.entry_high,
    atr=float(match.atr),
    pip_size=float(units.pip_size(symbol)),
  )
  executable_quote = _executable_spot_price(spot, match.direction)
  # Geometry measurement only — never a worker publication gate.
  policy_evaluation = evaluate_execution_policy(
    match,
    spot_price=spot.price,
    executable_quote=executable_quote,
    regime=None if regime is None else regime.state,
    pip_size=units.pip_size(symbol),
    cfg=None,
    **_opposing_zone_policy_kwargs(
      strategy_opposing_zone,
      atr=match.atr,
      pip_size=units.pip_size(symbol),
      symbol=symbol,
      timeframe=match.source_tf,
    ),
  )
  if (
    not match_bypasses_opposing_structure(match)
    and (
      runtime_config.actionability.gates.opposing_barrier_veto_enabled
      or guard_mode == GUARD_MODE_OBSERVE
    )
  ):
    source = _structural_source_identity(
      strategy=match.strategy,
      family=match.family,
      structural_source=match.structural_source or match.strategy,
      low=match.entry_low,
      high=match.entry_high,
      key_level=match.key_level,
      zone_id=match.zone_id,
      level_id=match.level_id,
    )
    barrier_outcome = _opposing_barrier_decision(
      match.direction, spot.price, match.target_price, match.atr,
      htf_zones or [], htf_levels or [],
      runtime_config.actionability.target_room.barrier_buffer_atr,
      source=source,
      guard_mode=guard_mode,
    )
    if barrier_outcome.reason_code != "no_opposing_barrier":
      await _record_guard_evaluation(
        client, symbol, barrier_outcome,
        strategy=match.strategy,
        direction=match.direction,
        source_structure=source_summary,
      )
      log.info(
        "strategy match %s symbol=%s strategy=%s reason=%s",
        "blocked" if barrier_outcome.hard_block else barrier_outcome.outcome,
        symbol, match.strategy, barrier_outcome.message,
      )
    if barrier_outcome.hard_block:
      await _consume_strategy_match(client, symbol, match)
      await _record_gate_reject(
        client, symbol, barrier_outcome.reason_code,
      )
      await route(
        "opposing_barrier", "blocked", barrier_outcome.reason_code,
        barrier_outcome.message, measured=barrier_outcome.measured,
        retained=False,
      )
      return None
    if barrier_outcome.outcome == OUTCOME_WAIT:
      await route(
        "opposing_barrier", "waiting", barrier_outcome.reason_code,
        barrier_outcome.message, measured=barrier_outcome.measured,
        retained=True,
      )
      return None

  cooldown_reason = await _zone_cooldown_reason(
    client, symbol, match.direction, spot.price,
    match.atr, runtime_config.lifecycle.zone.cooldown_atr,
  )
  if cooldown_reason is not None:
    cooldown_outcome = classify_guard_severity(
      "zone_cooldown", "zone_cooldown", cooldown_reason,
      guard_mode=guard_mode, hard_geometry=False,
    )
    await _record_guard_evaluation(
      client, symbol, cooldown_outcome,
      strategy=match.strategy,
      direction=match.direction,
      source_structure=source_summary,
    )
    log.info(
      "strategy match %s symbol=%s strategy=%s reason=%s",
      "blocked" if cooldown_outcome.hard_block else cooldown_outcome.outcome,
      symbol, match.strategy, cooldown_reason,
    )
    if cooldown_outcome.hard_block:
      await _consume_strategy_match(client, symbol, match)
      await _record_gate_reject(client, symbol, "zone_cooldown")
      await route(
        "cooldown", "blocked", "zone_cooldown",
        cooldown_reason, measured=cooldown_outcome.measured, retained=False,
      )
      return None

  if (
    runtime_config.actionability.overlapping_zones.veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
  ):
    overlap_outcome = _resolve_overlap_thesis(
      match.direction,
      spot.price,
      market_map,
      m1,
      match.atr,
      None,
      symbol=symbol,
    )
    if overlap_outcome.reason_code not in ("no_map", "no_overlap"):
      await _record_guard_evaluation(
        client, symbol, overlap_outcome,
        strategy=match.strategy,
        direction=match.direction,
        source_structure=source_summary,
      )
      log.info(
        "strategy match %s symbol=%s strategy=%s reason=%s",
        "blocked" if overlap_outcome.hard_block else overlap_outcome.outcome,
        symbol, match.strategy, overlap_outcome.message,
      )
    if overlap_outcome.hard_block:
      await _consume_strategy_match(client, symbol, match)
      await _record_gate_reject(client, symbol, "overlapping_zone_conflict")
      await route(
        "overlap", "blocked", "overlapping_zone_conflict",
        overlap_outcome.message, measured=overlap_outcome.measured,
        retained=False,
      )
      return None
    if overlap_outcome.outcome == OUTCOME_WAIT:
      await route(
        "overlap", "waiting", overlap_outcome.reason_code,
        overlap_outcome.message, measured=overlap_outcome.measured,
        retained=True,
      )
      return None

  invalidated = (
    match.direction == "BUY" and spot.price < match.structure_swing
    or match.direction == "SELL" and spot.price > match.structure_swing
  )
  if invalidated:
    invalidation_outcome = ExecutionGuardDecision(
      "entry_drift",
      "block",
      "reaction_crossed_invalidation",
      (
        f"{match.direction} reaction crossed invalidation "
        f"{match.structure_swing:.5f} at {spot.price:.5f}"
      ),
      True,
      measured={
        "spot_price": spot.price,
        "invalidation_price": match.structure_swing,
      },
    )
    await _record_guard_evaluation(
      client, symbol, invalidation_outcome,
      strategy=match.strategy,
      direction=match.direction,
      source_structure=source_summary,
    )
    await _consume_strategy_match(client, symbol, match)
    await _record_gate_reject(
      client, symbol, "reaction_crossed_invalidation",
    )
    await route(
      "entry_invalidation", "blocked", "reaction_crossed_invalidation",
      invalidation_outcome.message, measured=invalidation_outcome.measured,
      retained=False,
    )
    return None

  distance = (
    match.entry_low - spot.price
    if spot.price < match.entry_low
    else spot.price - match.entry_high
    if spot.price > match.entry_high
    else 0.0
  )
  distance_pips = distance / units.pip_size(symbol)
  remaining_room = float(
    policy_evaluation.measured.get("remaining_target_room_pips", 0.0)
  )
  distance_limit, drift_measured = max_entry_drift_pips(
    strategy=match.strategy,
    atr=float(match.atr),
    pip_size=units.pip_size(symbol),
    remaining_target_room_pips=remaining_room,
    cfg=None,
  )
  if distance_pips > distance_limit:
    # Genuinely stale only when even the strategy's absolute hard cap is
    # exceeded - a single tick beyond the (now latency-realistic) adaptive
    # limit is a non-terminal "wait", not an invalidation (Fix 9).
    hard_cap = drift_measured.get("hard_cap_pips", distance_limit)
    drift_outcome = (
      ExecutionGuardDecision(
        "entry_drift",
        "block",
        "strategy_entry_moved_beyond_hard_cap",
        (
          f"entry moved {distance_pips:.1f}p beyond hard cap "
          f"{hard_cap:.1f}p"
        ),
        True,
        measured={
          **drift_measured,
          "distance_pips": round(distance_pips, 3),
        },
      )
      if distance_pips > hard_cap else ExecutionGuardDecision(
        "entry_drift",
        OUTCOME_WAIT,
        "strategy_entry_moved",
        f"entry moved {distance_pips:.1f}p (limit {distance_limit:.1f}p)",
        False,
        measured={
          **drift_measured,
          "distance_pips": round(distance_pips, 3),
        },
      )
    )
    await _record_guard_evaluation(
      client, symbol, drift_outcome,
      strategy=match.strategy,
      direction=match.direction,
      source_structure=source_summary,
    )
    log.info(
      "strategy match %s id=%s strategy=%s: entry moved %.1f pips "
      "(limit %.1f measured=%s)",
      "blocked" if drift_outcome.hard_block else drift_outcome.outcome,
      match.match_id[:12],
      match.strategy,
      distance_pips,
      distance_limit,
      drift_measured,
    )
    if drift_outcome.hard_block:
      await _consume_strategy_match(client, symbol, match)
      await _record_gate_reject(client, symbol, "strategy_entry_moved")
      await route(
        "entry_drift", "blocked",
        "strategy_entry_moved_beyond_hard_cap",
        drift_outcome.message,
        measured={
          **drift_outcome.measured,
          "distance_price": round(distance, 6),
          "distance_pips": round(distance_pips, 3),
          "adaptive_limit_pips": round(distance_limit, 3),
          "hard_cap_pips": round(float(hard_cap), 3),
          "spot_price": spot.price,
          "entry_low": match.entry_low,
          "entry_high": match.entry_high,
        },
        retained=False,
      )
      return None
    await route(
      "entry_drift", "waiting", "strategy_entry_moved",
      drift_outcome.message,
      measured={
        **drift_outcome.measured,
        "distance_price": round(distance, 6),
        "distance_pips": round(distance_pips, 3),
        "adaptive_limit_pips": round(distance_limit, 3),
        "hard_cap_pips": round(float(hard_cap), 3),
        "spot_price": spot.price,
        "entry_low": match.entry_low,
        "entry_high": match.entry_high,
      },
      retained=True,
    )
    return None
  executor_measurement = _measure_executor_entry_distance(
    direction=match.direction,
    spot=spot,
    zone_low=match.entry_low,
    zone_high=match.entry_high,
    symbol=symbol,
  )
  if not executor_measurement.within_cap:
    await route(
      "entry_distance",
      "waiting",
      "executor_entry_envelope_exceeded",
      (
        f"executor quote {float(executor_measurement.executable_quote):.5f} "
        f"is {float(executor_measurement.distance_pips):.1f}p outside entry "
        f"zone (executor limit "
        f"{float(executor_measurement.cap_pips):.1f}p)"
      ),
      measured={
        **executor_measurement.as_measured(),
        "entry_low": match.entry_low,
        "entry_high": match.entry_high,
        "spot_price": spot.price,
        "bid": spot.bid,
        "ask": spot.ask,
      },
      retained=True,
    )
    await increment_metric(
      client, "publication_entry_distance_wait", symbol=symbol,
    )
    return None
  await increment_metric(
    client, "publication_entry_distance_passed", symbol=symbol,
  )
  now = int(datetime.now(timezone.utc).timestamp())
  try:
    guarded = await _news_guard_hit(
      symbol, now,
    )
  except Exception:
    log.exception("strategy match blocked: news guard unavailable")
    await route(
      "news", "waiting", "news_guard_unavailable",
      "news guard unavailable; match retained", retained=True,
    )
    return None
  if guarded is not None:
    log.info(
      "strategy match news-window preference observed symbol=%s "
      "strategy=%s event=%s",
      symbol,
      match.strategy,
      guarded.get("title", "high-impact event"),
    )
    await route(
      "news", "observed", "news_window_active",
      f"high-impact event preference: {guarded.get('title', 'unknown')}",
      measured={
        "event": guarded.get("title"),
        "preference_telemetry": True,
      },
      retained=True,
    )
    # News window is preference telemetry — do not refuse publication.

  thesis_cycle = 1
  thesis_claim_existing: dict[str, Any] | None = None
  if match.thesis_id and _thesis_lock_enabled():
    await increment_metric(client, "mapped_thesis_evaluated", symbol=symbol)
    thesis_claim_existing = await _load_thesis_claim(client, match.thesis_id)
    if thesis_claim_existing is not None:
      decision = evaluate_thesis_rearm_for_publish(
        thesis_claim_existing,
        new_touch_ts=str(match.touch_bar_ts or ""),
        new_confirmation_ts=str(match.confirmation_bar_ts or ""),
        price=float(spot.price),
        atr=float(match.atr),
        rearm_atr=float(
          runtime_config.lifecycle.mapped_zone.reaction_rearm_atr
        ),
        rearm_bars=int(
          runtime_config.lifecycle.mapped_zone.reaction_rearm_bars
        ),
      )
      if not decision.allowed:
        await increment_metric(
          client, "duplicate_thesis_suppressed", symbol=symbol,
        )
        await increment_metric(
          client, "same_thesis_group_active", symbol=symbol,
        )
        log.info(
          "duplicate mapped thesis suppressed thesis=%s reaction=%s "
          "reason=%s state=%s",
          match.thesis_id[:12],
          (match.reaction_id or "")[:12],
          decision.reason_code,
          decision.state,
        )
        await _consume_strategy_match(client, symbol, match)
        await route(
          "candidate_claim", "duplicate_suppressed",
          decision.reason_code,
          "an active group already owns this mapped thesis",
          measured={"thesis_state": decision.state},
          retained=False,
        )
        return None
      thesis_cycle = int(thesis_claim_existing.get("thesis_cycle") or 1) + 1
      await increment_metric(client, "mapped_thesis_rearmed", symbol=symbol)

  group_id = _strategy_group_id(match, thesis_cycle=thesis_cycle)
  if match.reaction_id:
    await increment_metric(client, "mapped_reaction_evaluated", symbol=symbol)
    claim_key = reaction_claim_key(match.reaction_id)
    existing_claim = parse_reaction_claim(await client.get(claim_key))
    if existing_claim is not None:
      # Same reaction_id replay: keep reaction-level protection.
      state = str(existing_claim.get("state") or "").casefold()
      if state not in {
        "closed", "cancelled", "rejected", "expired", "terminal", "rearm_ready",
      }:
        await increment_metric(
          client, "duplicate_reaction_suppressed", symbol=symbol,
        )
        if existing_claim.get("group_id"):
          await increment_metric(
            client, "same_thesis_group_active", symbol=symbol,
          )
        log.info(
          "duplicate mapped reaction suppressed id=%s symbol=%s claim=%s",
          match.reaction_id[:12],
          symbol,
          existing_claim.get("state"),
        )
        await _consume_strategy_match(client, symbol, match)
        await route(
          "candidate_claim", "duplicate_suppressed",
          "duplicate_reaction",
          "an active candidate already owns this reaction",
          measured={"reaction_state": state},
          retained=False,
        )
        return None
      # A terminal claim is replaced by compare-and-set inside the atomic
      # publication Lua script. Deleting it here would create a crash window.
      await increment_metric(client, "mapped_reaction_rearmed", symbol=symbol)

  candidate_id = match.match_id
  if await client.exists(candidate_key(candidate_id)):
    if match.reaction_id:
      await increment_metric(
        client, "duplicate_reaction_suppressed", symbol=symbol,
      )
    await route(
      "candidate_claim", "duplicate_suppressed",
      "duplicate_candidate",
      "candidate ID is already claimed; match retained pending ownership proof",
      retained=True,
    )
    return None

  reaction_claim_previous_raw: Any = None
  reaction_body: str | None = None
  if match.reaction_id:
    claim_key = reaction_claim_key(match.reaction_id)
    reaction_claim_previous_raw = await client.get(claim_key)
    reaction_body = reaction_claim_payload(
      reaction_id=match.reaction_id,
      thesis_id=match.thesis_id or "",
      candidate_id=candidate_id,
      group_id=group_id,
      touch_bar_ts=str(match.touch_bar_ts or ""),
      confirmation_bar_ts=str(match.confirmation_bar_ts or ""),
      state="claimed",
      claimed_at=now,
      structural_zone_id=str(
        match.structural_zone_id or match.zone_id or ""
      ),
      symbol=symbol,
      direction=match.direction,
      structural_zone_low=match.structural_zone_low,
      structural_zone_high=match.structural_zone_high,
    )

  thesis_claim_previous_raw: Any = None
  thesis_body: str | None = None
  if match.thesis_id and _thesis_lock_enabled():
    thesis_claim_previous_raw = await client.get(
      thesis_claim_key(match.thesis_id)
    )
    thesis_body = thesis_claim_payload(
      thesis_id=match.thesis_id,
      strategy=match.strategy,
      strategy_family=match.family or "mapped_zone",
      symbol=symbol,
      direction=match.direction,
      structural_zone_id=str(match.structural_zone_id or match.zone_id or ""),
      structural_zone_low=match.structural_zone_low,
      structural_zone_high=match.structural_zone_high,
      active_reaction_id=str(match.reaction_id or ""),
      candidate_id=candidate_id,
      group_id=group_id,
      state="candidate_published",
      claimed_at=now,
      touch_bar_ts=str(match.touch_bar_ts or ""),
      confirmation_bar_ts=str(match.confirmation_bar_ts or ""),
      thesis_cycle=thesis_cycle,
      rearm_ready=False,
    )

  setup = (
    f"{match.strategy} · counter_bias"
    if "counter_bias" in match.tags
    else match.strategy
  )
  payload = {
    "version": 5,
    "candidate_id": candidate_id,
    "match_id": match.match_id,
    "group_id": group_id,
    "strategy_family": match.family or "scanner",
    "zone_id": (
      match.structural_zone_id
      or match.zone_id
      or match.range_id
      or f"{match.key_level:.5f}:{match.entry_low:.5f}:{match.entry_high:.5f}"
    ),
    "level_id": match.level_id,
    "trigger_id": match.event_ts,
    "parent_group_id": None,
    "structural_source": match.structural_source or match.strategy,
    "reaction_id": match.reaction_id,
    "thesis_id": match.thesis_id,
    "structural_zone_id": match.structural_zone_id or match.zone_id,
    "structural_zone_low": match.structural_zone_low,
    "structural_zone_high": match.structural_zone_high,
    "thesis_cycle": thesis_cycle,
    "touch_bar_ts": match.touch_bar_ts,
    "confirmation_bar_ts": match.confirmation_bar_ts,
    "reaction_type": match.reaction_type,
    "symbol": symbol.upper(),
    "timeframe": match.source_tf,
    "setup": setup,
    "mode": "auto_strategy_match",
    "signal_source": match_source,
    "source_strategy": match.strategy,
    "source_event_ts": match.event_ts,
    "direction": match.direction,
    "trigger_ts": match.event_ts,
    "created_at": now,
    "spot_ts": spot.ts,
    "current_price": spot.price,
    "key_level": match.key_level,
    "entry_zone": {"low": match.entry_low, "high": match.entry_high},
    "confluence": match.confluence,
    "confluence_v1": match.confluence_v1,
    "confluence_v2": match.confluence_v2,
    "confluence_v2_raw": match.confluence_v2_raw,
    "confluence_scoring_version": match.confluence_scoring_version,
    "reasons": list(match.reasons),
    "bar_ts": int(match.event_ts) if match.event_ts.isdigit() else None,
    "atr": match.atr,
    "structure_swing": match.structure_swing,
    "targets_pips": list(match.targets_pips),
    "strategy_tags": list(match.tags),
    "target_price": match.target_price,
    "target_model": match.target_model,
    "target_reference_price": match.target_reference_price,
    "absolute_target_price": (
      match.absolute_target_price
      if match.absolute_target_price is not None
      else match.target_price
    ),
    "tier": match.tier,
    "risk_multiplier": policy_evaluation.measured[
      "effective_risk_multiplier"
    ],
    "family": match.family,
    "range_state": match.range_state,
    "range_id": match.range_id,
    "range_low": match.range_low,
    "range_high": match.range_high,
    "full_take_profit_pips": match.full_take_profit_pips,
    "regime": "strategy_match",
    "bias": (
      "bullish" if market_map is not None and market_map.bias == "up"
      else "bearish" if market_map is not None and market_map.bias == "down"
      else "neutral"
    ),
    "relationship_to_bias": (
      "counter_bias" if "counter_bias" in match.tags
      else "neutral" if market_map is None or market_map.bias == "range"
      else "with_bias"
    ),
    "target_adjustment": (
      cb_outcome.measured
      if cb_outcome.outcome == "adjust_target" else None
    ),
    "order_type_preference": (
      policy_evaluation.policy.order_type_preference
      if policy_evaluation.policy is not None else "either"
    ),
    "entry_distribution": policy_evaluation.measured.get(
      "entry_distribution", "single",
    ),
    "opposing_zone_low": (
      None if strategy_opposing_zone is None else strategy_opposing_zone.low
    ),
    "opposing_zone_high": (
      None if strategy_opposing_zone is None else strategy_opposing_zone.high
    ),
    "opposing_zone_id": (
      None if strategy_opposing_zone is None else _opposing_zone_identity(
        strategy_opposing_zone,
        symbol=symbol,
        timeframe=match.source_tf,
      )
    ),
    "add_zone_side": (
      None if strategy_opposing_zone is None else strategy_opposing_zone.side
    ),
    **_stop_contract_fields(policy_evaluation.measured),
  }

  try:
    publish_result = await publish_candidate_atomic(
      client,
      stream=runtime_config.contract.streams.candidates,
      candidate_id=candidate_id,
      payload=json.dumps(payload, separators=(",", ":")),
      ttl=runtime_config.lifecycle.candidate.storage_ttl_seconds,
      maxlen=runtime_config.contract.streams.candidate_maximum_length,
      reaction_key=(
        reaction_claim_key(match.reaction_id)
        if match.reaction_id else None
      ),
      reaction_payload=reaction_body,
      expected_reaction_payload=(
        reaction_claim_previous_raw.decode()
        if isinstance(reaction_claim_previous_raw, bytes)
        else reaction_claim_previous_raw
      ),
      thesis_key=(
        thesis_claim_key(match.thesis_id)
        if match.thesis_id and _thesis_lock_enabled() else None
      ),
      thesis_payload=thesis_body,
      expected_thesis_payload=(
        thesis_claim_previous_raw.decode()
        if isinstance(thesis_claim_previous_raw, bytes)
        else thesis_claim_previous_raw
      ),
      ownership_key=(
        autonomous_cycle_owner_key(symbol, cycle_id)
        if cycle_id else None
      ),
      ownership_payload=candidate_id,
      ownership_ttl=runtime_config.lifecycle.candidate.storage_ttl_seconds,
      allow_non_atomic_test_fallback=explicit_test_fallback_enabled(client),
    )
  except Exception as exc:
    await route(
      "stream_publish", "waiting", "stream_publish_failed",
      f"candidate stream publish failed: {type(exc).__name__}",
      retained=True,
    )
    return None
  published, executor_event_id = publish_result
  if not published:
    reason_code = publish_result.status
    if reason_code == "atomic_publish_unavailable":
      await route(
        "stream_publish", "blocked", reason_code,
        "atomic Redis publication is unavailable; failed closed",
        retained=True,
      )
      return None
    await route(
      "candidate_claim", "duplicate_suppressed",
      reason_code,
      f"atomic ownership rejected candidate: {reason_code}",
      retained=True,
    )
    return None
  if match.reaction_id:
    await increment_metric(client, "mapped_reaction_claimed", symbol=symbol)
  if match.thesis_id and _thesis_lock_enabled():
    await increment_metric(client, "mapped_thesis_claimed", symbol=symbol)
  await increment_metric(client, "candidate_published", symbol=symbol)
  await increment_metric(
    client, "strategy_match_candidate_published", symbol=symbol,
  )
  publish_executor_measurement = _measure_executor_entry_distance(
    direction=match.direction,
    spot=spot,
    zone_low=match.entry_low,
    zone_high=match.entry_high,
    symbol=symbol,
  )
  await route(
    "stream_publish", "candidate_published", "candidate_published",
    "candidate published to executor stream",
    measured={
      **{
        key: policy_evaluation.measured[key]
        for key in (
          "planned_execution_route",
          "planned_entry_price",
          "order_type_preference",
        )
        if key in policy_evaluation.measured
      },
      **publish_executor_measurement.as_measured(),
      "distance_price": round(distance, 6),
      "distance_pips": round(distance_pips, 3),
      "adaptive_limit_pips": round(distance_limit, 3),
      "hard_cap_pips": drift_measured.get("hard_cap_pips"),
      "spot_price": spot.price,
      "entry_low": match.entry_low,
      "entry_high": match.entry_high,
    },
    retained=False,
    candidate_id=candidate_id,
    group_id=group_id,
    executor_event_id=executor_event_id,
  )
  if is_reaction_strategy(match.strategy) or match.family in {
    "key_level", "session_level", "trendline",
  }:
    await increment_metric(
      client, "structural_reaction_candidate_published", symbol=symbol,
    )
  await emit_lifecycle(
    client,
    "candidate_published",
    symbol=symbol,
    candidate_id=candidate_id,
    match_id=match.match_id,
    range_id=match.range_id,
    group_id=payload["group_id"],
    strategy=match.strategy,
    strategy_family=payload["strategy_family"],
    direction=match.direction,
    timeframe=match.source_tf,
    entry_zone=payload["entry_zone"],
    current_price=spot.price,
    target_plan=list(match.targets_pips),
    message="strategy match candidate published to executor",
    measured={
      "reaction_id": match.reaction_id,
      "thesis_id": match.thesis_id,
      "structural_zone_id": match.structural_zone_id or match.zone_id,
      "structural_zone_low": match.structural_zone_low,
      "structural_zone_high": match.structural_zone_high,
      "touch_bar_ts": match.touch_bar_ts,
      "confirmation_bar_ts": match.confirmation_bar_ts,
    },
    publish_status=True,
  )
  if match.is_range_edge and match.range_id is not None:
    await _mark_range_side_candidate(
      client,
      symbol=symbol,
      range_id=match.range_id,
      direction=match.direction,
      candidate_id=candidate_id,
      group_id=group_id,
    )
  await _consume_strategy_match(client, symbol, match)
  if match.is_range_edge:
    await client.set(
      _box_edge_key(symbol, match.range_id, match.direction),
      json.dumps({
        "source": "scanner_strategy_match",
        "direction": match.direction,
        "midpoint": (match.range_low + match.range_high) / 2,
      }, separators=(",", ":")),
      ex=max(300, runtime_config.lifecycle.range_box.retirement_seconds),
    )
  log.info(
    "strategy candidate published id=%s symbol=%s strategy=%s direction=%s",
    candidate_id[:12],
    symbol,
    match.strategy,
    match.direction,
  )
  return candidate_id


def _v8_plan_id(match: StrategyMatch) -> str:
  return f"v8:{match.match_id}"


async def _record_v8_build_rejected(
  client: Any,
  symbol: str,
  match: StrategyMatch,
  reason_code: str,
  message: str,
  measured: dict[str, Any],
) -> None:
  """Hard TradePlan reject: metric + terminalize setup so it does not keep watching."""
  await _record_gate_reject(client, symbol, f"v8_{reason_code}")
  terminal_state = (
    EXPIRED
    if reason_code == "policy_reward_risk_insufficient"
    else INVALIDATED
  )
  lifecycle_reason = (
    "confirmation_expired"
    if terminal_state == EXPIRED
    else f"v8_{reason_code}"
  )
  setup_id = match.match_id
  try:
    await transition_setup(
      client,
      setup_id,
      terminal_state,
      reason_code=lifecycle_reason,
    )
  except SetupLifecycleError:
    log.exception(
      "v8 setup could not terminalize after build rejection "
      "symbol=%s setup_id=%s reason=%s",
      symbol,
      setup_id,
      reason_code,
    )
  await emit_lifecycle(
    client,
    terminal_state,
    symbol=symbol,
    match_id=setup_id,
    correlation_id=setup_id,
    timeframe=match.source_tf,
    reason_code=lifecycle_reason,
    message=message,
    measured=measured,
    publish_status=True,
  )
  await emit_lifecycle(
    client,
    "rejected",
    symbol=symbol,
    correlation_id=match.match_id,
    timeframe=match.source_tf,
    reason_code=reason_code,
    message=message,
    measured=measured,
  )
  await _consume_strategy_match(client, symbol, match)
  await record_route_outcome(
    client,
    match,
    stage="publication",
    status="blocked",
    reason_code=reason_code,
    message=message,
    measured={
      "setup_id": setup_id,
      "match_id": setup_id,
      **dict(measured or {}),
    },
    retained=False,
    publish_status=False,
  )
  stop_detail = measured.get("stop_reject_detail")
  stop_zone = None
  zone_low = measured.get("stop_side_opposing_zone_low")
  zone_high = measured.get("stop_side_opposing_zone_high")
  if zone_low is not None and zone_high is not None:
    stop_zone = f"{zone_low}-{zone_high}"
  log.info(
    "v8 plan build rejected symbol=%s match_id=%s reason=%s message=%s "
    "stop_detail=%s base_stop=%s pushed_stop=%s stop_zone=%s "
    "max_pips=%s over_envelope_pips=%s terminal=%s",
    symbol,
    match.match_id[:12],
    reason_code,
    message,
    stop_detail,
    measured.get("planned_base_stop_price"),
    measured.get("planned_pushed_stop_price"),
    stop_zone,
    measured.get("stop_max_envelope_pips"),
    measured.get("pushed_over_envelope_pips"),
    terminal_state,
  )


async def _emit_setup_card_status(
  client: Any,
  match: StrategyMatch,
  *,
  status_line: str,
  reason_code: str,
  message: str,
  measured: dict[str, Any] | None = None,
) -> None:
  await save_forming_card_status(
    client,
    match.match_id,
    status_line,
  )
  await emit_lifecycle(
    client,
    "setup_status",
    symbol=match.symbol,
    correlation_id=match.match_id,
    match_id=match.match_id,
    strategy=match.strategy,
    strategy_family=match.family,
    direction=match.direction,
    timeframe=match.source_tf,
    reason_code=reason_code,
    message=message,
    measured={
      "status_line": status_line,
      "setup_id": match.match_id,
      "match_id": match.match_id,
      "scanner_event_ts": match.event_ts,
      "entry_low": match.entry_low,
      "entry_high": match.entry_high,
      "market_map_id": (
        ""
        if match.execution_eligibility is None
        else match.execution_eligibility.market_map_id
      ),
      **(measured or {}),
    },
    publish_status=True,
  )


async def _persist_v8_confirmation_phase(
  client: Any,
  symbol: str,
  match: StrategyMatch,
  state: ExecutionConfirmationState,
  *,
  reason_code: str,
  message: str,
  evidence: Any | None,
  metric: str | None = None,
  status: str = "waiting",
) -> None:
  await save_execution_confirmation(
    client,
    state,
    expires_at=match.expires_at,
  )
  if metric is not None:
    await increment_metric(client, metric, symbol=symbol)
  measured = {
    "setup_id": match.match_id,
    "match_id": match.match_id,
    "phase": state.phase,
    "episode_id": state.episode_id,
    "confirmation_source": state.trigger_source,
    "trigger_bar_ts": state.trigger_bar_ts,
    "last_evaluated_m1_ts": state.last_evaluated_m1_ts,
    "zone_entered_at": state.zone_entered_at,
    "zone_exited_at": state.zone_exited_at,
    "zone_low": match.entry_low,
    "zone_high": match.entry_high,
  }
  if evidence is not None:
    measured.update({
      "executable_quote": evidence.executable_quote,
      "quote_side": evidence.quote_side,
      "quote_inside_zone": evidence.inside,
      "distance_to_zone": evidence.distance_to_zone,
      "distance_pips": evidence.distance_pips,
      "tolerance_price": evidence.tolerance_price,
    })
  await record_route_outcome(
    client,
    match,
    stage="preflight",
    status=status,
    reason_code=reason_code,
    message=message,
    measured=measured,
    retained=status != "candidate_published",
    publication_reason_code=(
      reason_code if status == "candidate_published" else None
    ),
    publish_status=False,
  )
  if state.phase in {CONFIRMATION_EXPIRED, CONFIRMATION_INVALIDATED}:
    pass
  elif state.phase in {WAITING_RETEST, TRIGGER_PRICE_LEFT_ZONE}:
    await _emit_setup_card_status(
      client,
      match,
      status_line=(
        "🟠 <b>WAITING RETEST</b> · executable quote is outside "
        "the confirmed entry zone"
      ),
      reason_code=reason_code,
      message=message,
      measured=measured,
    )
  elif state.phase == CONFIRMATION_PUBLISHED:
    # Standalone PLAN PUBLISHED status removed — root card already owns the
    # published state via lifecycle/edit path.
    pass
  else:
    # PREFLIGHT lifecycle card status removed from the architecture.
    pass
  log.info(
    "v8 execution confirmation symbol=%s setup_id=%s match_id=%s "
    "direction=%s phase=%s executable_quote=%s quote_side=%s "
    "zone_low=%.5f zone_high=%.5f episode_id=%s "
    "confirmation_source=%s trigger_bar_ts=%s last_evaluated_m1_ts=%s "
    "reason_code=%s",
    symbol,
    match.match_id,
    match.match_id,
    match.direction,
    state.phase,
    None if evidence is None else evidence.executable_quote,
    None if evidence is None else evidence.quote_side,
    match.entry_low,
    match.entry_high,
    state.episode_id,
    state.trigger_source,
    state.trigger_bar_ts,
    state.last_evaluated_m1_ts,
    reason_code,
  )


def _resolve_match_confluence_claim_id(
  symbol: str,
  match: StrategyMatch,
  market_map: Any | None,
) -> str | None:
  """Use scanner's merged zone identity; resolve only for legacy matches."""
  if match.confluence_zone_id:
    return match.confluence_zone_id
  if market_map is None:
    return None
  other_members = [
    ConfluenceMember(
      member_id=f"{getattr(entry, 'tier', 'entry')}:{entry.lo:.5f}:{entry.hi:.5f}",
      side=entry.side,
      low=float(entry.lo),
      high=float(entry.hi),
      kind=next(
        (tag for tag in entry.tags if tag.casefold() in {
          "demand", "supply", "ob", "fvg", "breaker",
        }),
        entry.tier,
      ),
      score=float(entry.score),
    )
    for entry in getattr(market_map, "actionable_entries", None) or []
  ]
  return resolve_confluence_zone_id(
    match.entry_low,
    match.entry_high,
    "buy" if match.direction == "BUY" else "sell",
    match.tags or (match.structural_kind or "candidate",),
    other_members=other_members,
    symbol=symbol,
    atr=match.atr,
    pip_size=units.pip_size(symbol),
    source_tf=match.source_tf,
    max_width=float(instrument_geometry.merge_max_width(symbol)),
    gap=float(instrument_geometry.merge_gap_price(symbol)),
    candidate_id=match.match_id,
  )


async def _publish_trade_plan_v8(
  client: Any,
  symbol: str,
  spot: AutoTradeSpot | None,
  match: StrategyMatch,
  *,
  htf_zones: list[Zone] | None = None,
  htf_levels: list[Level] | None = None,
  regime: RegimeInfo | None = None,
  frames: dict[str, Any] | None = None,
  market_map: Any | None = None,
) -> str | None:
  """Build and publish a TradePlan V8 from an already-CONFIRMED match.

  Deliberately separate from _publish_strategy_match (the V6 path) rather
  than sharing its body: V6's function is full of V6-only concerns
  (candidate_id/group_id shaping, ZoneFillPlanner routing, ...) that must
  not leak into the V8 contract. What IS shared are the same quality/safety
  guard functions the V6 path already calls (opposing barrier, zone
  cooldown, overlapping-zone veto) - these are Python-side risk checks with
  no C#-side duplicate, not the dual-planning anti-pattern the ADR is
  about. _adapt_counter_bias_target is deliberately NOT called here. The
  scanner-owned target ladder is only revalidated with the shared pure
  structural-target-room helper against the latest Market Map; this is a
  final stale-context safety check, not a second strategy planner.

  A formed setup may continue through final preflight only while the
  side-aware executable quote is inside the scanner's raw entry zone plus
  the configured spread tolerance. Outside setups persist WAITING_RETEST
  until price returns. A fresh M1 pattern can refine timing and stop
  anchoring, but distance outside the entry contract never authorizes entry.

  Returns the published plan_id, or None if not published (still outside the
  entry contract, thesis/zone already claimed by another setup, or a
  guard/policy rejection - always recorded via _record_v8_build_rejected,
  never a bare silent return, except the ordinary retained retest wait).
  """
  existing = await resolve_existing_v8_state(client, match)
  if existing.already_terminal:
    return existing.plan_id if existing.plan_exists else None
  if existing.already_published:
    if existing.setup_state == PLAN_BUILT:
      await transition_setup(
        client,
        match.match_id,
        PLAN_PUBLISHED,
        reason_code="v8_publish_reconciled",
      )
    # No standalone PLAN PUBLISHED card status — root card owns updates.
    return existing.plan_id
  if existing.setup_state == PLAN_BUILT:
    await transition_setup(
      client,
      match.match_id,
      CANCELLED,
      reason_code="v8_plan_build_incomplete",
    )
    # No other caller reaches this branch, so nothing else will ever clear
    # the forming card for it - publish_status=True routes this through
    # delivery.py's _CARD_TERMINAL_TYPES handling (kill_setup_card), which
    # keeps worker.py itself free of any direct Telegram dependency.
    await emit_lifecycle(
      client,
      CANCELLED,
      symbol=match.symbol,
      match_id=match.match_id,
      correlation_id=match.match_id,
      timeframe=match.source_tf,
      reason_code="v8_plan_build_incomplete",
      message="TradePlan V8 build left incomplete across a restart/crash",
      publish_status=True,
    )
    return None
  if spot is None or not spot.fresh:
    await record_route_outcome(
      client,
      match,
      stage="spot_check",
      status="waiting",
      reason_code="stale_spot",
      message="fresh bid/ask snapshot is required for execution confirmation",
      retained=True,
      publish_status=False,
    )
    return None
  if not match.thesis_id:
    await _record_v8_build_rejected(
      client, symbol, match, "missing_stable_thesis_id",
      "match has no thesis_id - setup_lifecycle wiring did not attach one",
      {},
    )
    return None

  # Technique pack: pair reaction windows for non-scalp; HFS killzone for scalps.
  from app.autotrade.killzone import (
    confirmation_is_sweep_body,
    evaluate_killzone_gate,
    evaluate_reaction_publish_window,
    reaction_require_killzone,
    reaction_require_publish_window,
    technique_enforce,
    technique_require_sweep_body,
  )

  inst = instrument_geometry.instrument_runtime(symbol)
  tech = getattr(inst.execution, "technique", None)
  enforce_pack = technique_enforce(inst)
  candidate_is_scalp = is_scalp_strategy(
    str(getattr(match, "strategy", "") or ""),
    family=str(getattr(match, "strategy_family", "") or getattr(match, "family", "") or "")
    or None,
    strategy_mode=str(getattr(match, "strategy_mode", "") or "") or None,
  )
  spot_ts = int(getattr(spot, "ts", 0) or int(datetime.now(timezone.utc).timestamp()))
  if candidate_is_scalp:
    # Optional clock sterilizer (prod off). Discovery permits are structure/
    # technique driven; weak volume/momentum is rejected by analysis.
    require_kz = False if tech is None else bool(
      getattr(tech, "scalp_require_killzone", False),
    )
    from app.scalping.context import classify_session

    scalp_session = classify_session(spot_ts, inst)
    kz = evaluate_killzone_gate(
      ts=spot_ts,
      cfg=inst,
      require=require_kz and enforce_pack,
    )
    if not kz.allowed:
      log.info(
        "v8 publish blocked outside killzone symbol=%s match_id=%s "
        "utc_hour=%s killzone=%s session=%s",
        symbol,
        match.match_id,
        kz.utc_hour,
        kz.killzone_name,
        scalp_session,
      )
      await _record_v8_build_rejected(
        client,
        symbol,
        match,
        "outside_killzone",
        "technique pack: executable publish blocked outside killzone",
        {
          "killzone_name": kz.killzone_name,
          "utc_hour": kz.utc_hour,
          "session": scalp_session,
          **kz.measured,
        },
      )
      return None
  else:
    # Optional clock sterilizer (prod off). Structure/technique decide.
    win = evaluate_reaction_publish_window(
      ts=spot_ts,
      cfg=inst,
      require=enforce_pack and reaction_require_publish_window(inst),
    )
    if not win.allowed:
      log.info(
        "v8 publish waiting outside_reaction_publish_window symbol=%s "
        "match_id=%s utc_hour=%s",
        symbol,
        match.match_id,
        win.utc_hour,
      )
      await record_route_outcome(
        client,
        match,
        stage="technique",
        status="waiting",
        reason_code="outside_reaction_publish_window",
        message="technique pack: non-scalp publish waits for pair session window",
        measured=dict(win.measured),
        retained=True,
        publish_status=False,
      )
      return None
    require_kz = reaction_require_killzone(
      inst,
      strategy=str(getattr(match, "strategy", "") or ""),
    )
    kz = evaluate_killzone_gate(
      ts=spot_ts,
      cfg=inst,
      require=require_kz and enforce_pack,
    )
    if not kz.allowed:
      log.info(
        "v8 publish blocked outside killzone symbol=%s match_id=%s "
        "utc_hour=%s killzone=%s",
        symbol,
        match.match_id,
        kz.utc_hour,
        kz.killzone_name,
      )
      await _record_v8_build_rejected(
        client,
        symbol,
        match,
        "outside_killzone",
        "technique pack: executable publish blocked outside killzone",
        {
          "killzone_name": kz.killzone_name,
          "utc_hour": kz.utc_hour,
          **kz.measured,
        },
      )
      return None

  if (
    technique_require_sweep_body(inst)
    and (
      is_reaction_strategy(match.strategy)
      or match.family in {
        "mapped_zone_reaction",
        "liquidity_reversal",
        "range_reversion",
      }
    )
  ):
    trigger_name = (
      str(match.entry_activation_trigger or "")
      or str(match.reaction_type or "")
    )
    if not confirmation_is_sweep_body(trigger_name):
      await _record_v8_build_rejected(
        client,
        symbol,
        match,
        "confirmation_requires_sweep_body",
        "technique pack: reaction publish requires sweep_reclaim/body_close",
        {
          "trigger": trigger_name or None,
          "killzone_name": kz.killzone_name,
          "utc_hour": kz.utc_hour,
        },
      )
      return None

  setup_id = match.match_id
  setup_record = await load_setup(client, setup_id)
  if setup_record is None or not is_publishable_setup_state(setup_record.state):
    await _record_v8_build_rejected(
      client, symbol, match, "setup_not_confirmed",
      f"setup {setup_id!r} is not in a publishable state "
      f"({setup_record.state if setup_record else 'missing'})",
      {},
    )
    return None
  # Legacy ACK/ARMED Redis nodes are publishable as CONFIRMED; never write
  # those states from new code.
  if normalize_setup_state(setup_record.state) == CONFIRMED:
    setup_record = replace(setup_record, state=CONFIRMED)

  now_ts = int(datetime.now(timezone.utc).timestamp())
  quote_ts = int(getattr(spot, "ts", 0) or now_ts)
  policy = confirmation_policy_for(match)
  pip_size = units.pip_size(symbol)
  evidence = executable_quote_in_zone(
    match.direction,
    getattr(spot, "bid", None),
    getattr(spot, "ask", None),
    match.entry_low,
    match.entry_high,
    max(
      0.0,
      float(inst.execution.entry.contract_tolerance_pips) * pip_size,
    ),
    pip_size=pip_size,
  )
  # HFS / range-scalp activation already allows trade-direction chase within
  # maximum_chase_pips. V8 used to require quote-inside only
  # (execution_eligible = evidence.inside), so chase activations were parked
  # as waiting_retest_entry_zone until price returned — by then envelope /
  # stack / thesis often killed the plan (Aug 20 HFS gold dig). Treat chase
  # as immediately executable for those families, matching activation.
  candidate_allows_chase = is_m1_scalp_strategy(str(match.strategy)) or is_scalp_strategy(
    str(match.strategy or ""),
    family=str(getattr(match, "family", "") or "") or None,
    strategy_mode=str(getattr(match, "strategy_mode", "") or "") or None,
  )
  if candidate_allows_chase:
    zone_access_mode = (
      ZONE_ACCESS_RETEST_ONLY
      if is_breakout_retest_scalp_strategy(str(match.strategy))
      else ZONE_ACCESS_MOMENTUM_CHASE
    )
    try:
      side = str(match.direction).upper()
      worst = float(match.entry_high if side == "BUY" else match.entry_low)
      stop_pips = abs(worst - float(match.structure_swing)) / pip_size
    except (TypeError, ValueError, AttributeError):
      stop_pips = None
    chase_cap = scalp_effective_chase_pips(inst, stop_pips=stop_pips)
    scalp_access = scalp_zone_access(
      match.direction,
      getattr(spot, "bid", None),
      getattr(spot, "ask", None),
      match.entry_low,
      match.entry_high,
      max(
        0.0,
        float(inst.execution.entry.contract_tolerance_pips) * pip_size,
      ),
      pip_size=pip_size,
      maximum_chase_pips=chase_cap,
      zone_access_mode=zone_access_mode,
    )
    evidence = scalp_access.evidence
    execution_eligible = scalp_access.executable
  else:
    execution_eligible = evidence.inside

  if match.expires_at and now_ts >= int(match.expires_at):
    try:
      await transition_setup(
        client,
        setup_id,
        EXPIRED,
        reason_code=(
          "confirmation_expired"
          if policy.m5_authoritative_contract else "m1_trigger_expired"
        ),
      )
    except SetupLifecycleError:
      log.exception(
        "v8 setup could not expire symbol=%s setup_id=%s",
        symbol, setup_id,
      )
    if policy.m5_authoritative_contract:
      await _persist_v8_confirmation_phase(
        client,
        symbol,
        match,
        new_state(
          setup_id,
          CONFIRMATION_EXPIRED,
          now=now_ts,
        ),
        reason_code="confirmation_expired",
        message="setup confirmation expired before execution",
        evidence=evidence,
        status="expired",
      )
    await emit_lifecycle(
      client,
      EXPIRED,
      symbol=symbol,
      match_id=setup_id,
      correlation_id=setup_id,
      timeframe=match.source_tf,
      reason_code=(
        "confirmation_expired"
        if policy.m5_authoritative_contract else "m1_trigger_expired"
      ),
      message="setup confirmation expired before execution",
      publish_status=True,
    )
    return None

  invalidation_quote = (
    evidence.executable_quote
    if evidence.executable_quote is not None else float(spot.price)
  )
  structure_invalidated = (
    match.direction == "BUY" and invalidation_quote < match.structure_swing
    or match.direction == "SELL" and invalidation_quote > match.structure_swing
  )
  if structure_invalidated:
    try:
      await transition_setup(
        client,
        setup_id,
        INVALIDATED,
        reason_code="structure_invalidated_before_entry",
      )
    except SetupLifecycleError:
      log.exception(
        "v8 setup could not invalidate symbol=%s setup_id=%s",
        symbol,
        setup_id,
      )
    if policy.m5_authoritative_contract:
      await _persist_v8_confirmation_phase(
        client,
        symbol,
        match,
        new_state(
          setup_id,
          CONFIRMATION_INVALIDATED,
          now=now_ts,
        ),
        reason_code="structure_invalidated_before_entry",
        message="structure invalidated before an executable entry",
        evidence=evidence,
        status="blocked",
      )
    await emit_lifecycle(
      client,
      INVALIDATED,
      symbol=symbol,
      match_id=setup_id,
      correlation_id=setup_id,
      timeframe=match.source_tf,
      reason_code="structure_invalidated_before_entry",
      message="structure invalidated before an executable entry",
      publish_status=True,
    )
    return None

  if policy.m5_authoritative_contract and not policy.metadata_valid:
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      "confirmation_metadata_missing",
      "scanner reaction is missing authoritative confirmation metadata",
      {
        "touch_bar_ts": match.touch_bar_ts,
        "confirmation_bar_ts": match.confirmation_bar_ts,
        "reaction_type": match.reaction_type,
        "structural_zone_id": match.structural_zone_id,
      },
    )
    try:
      await transition_setup(
        client,
        setup_id,
        INVALIDATED,
        reason_code="confirmation_metadata_missing",
      )
    except SetupLifecycleError:
      log.exception(
        "v8 setup could not invalidate missing confirmation metadata "
        "symbol=%s setup_id=%s",
        symbol,
        setup_id,
      )
    await _persist_v8_confirmation_phase(
      client,
      symbol,
      match,
      new_state(
        setup_id,
        CONFIRMATION_INVALIDATED,
        now=now_ts,
      ),
      reason_code="confirmation_metadata_missing",
      message="scanner reaction is missing authoritative confirmation metadata",
      evidence=evidence,
      status="blocked",
    )
    await emit_lifecycle(
      client,
      INVALIDATED,
      symbol=symbol,
      match_id=setup_id,
      correlation_id=setup_id,
      timeframe=match.source_tf,
      reason_code="confirmation_metadata_missing",
      message="scanner reaction is missing authoritative confirmation metadata",
      publish_status=True,
    )
    return None

  confirmation: ExecutionConfirmation | None = None
  trigger = None
  execution_state = await load_execution_confirmation(client, setup_id)
  confirmation_boundary = (
    parse_bar_timestamp(match.confirmation_bar_ts)
    or int(match.issued_at)
  )

  # WORKER_ACKNOWLEDGED / ARMED_WAITING_TRIGGER removed. CONFIRMED setups
  # either wait for retest (outside zone) or continue with zone-presence
  # confirmation (inside zone). M1 is optional preference telemetry.
  if (
    normalize_setup_state(setup_record.state) == CONFIRMED
    and confirmation is None
  ):
    if not execution_eligible:
      execution_state = new_state(
        setup_id,
        WAITING_RETEST,
        now=now_ts,
        zone_exited_at=quote_ts,
      )
      await _persist_v8_confirmation_phase(
        client,
        symbol,
        match,
        execution_state,
        reason_code="waiting_retest_entry_zone",
        message="confirmed setup is outside its executable entry zone",
        evidence=evidence,
        metric="waiting_retest",
      )
      return None
    await increment_metric(
      client,
      "zone_presence_immediate_eligible",
      symbol=symbol,
    )
    episode_id = deterministic_episode_id(
      setup_id,
      match.direction,
      match.entry_low,
      match.entry_high,
      confirmation_boundary,
    )
    execution_state = new_state(
      setup_id,
      IN_ZONE_WAITING_M1,
      now=now_ts,
      episode_id=episode_id,
      zone_entered_at=confirmation_boundary,
      last_inside_at=quote_ts,
    )
    await _persist_v8_confirmation_phase(
      client,
      symbol,
      match,
      execution_state,
      reason_code="entry_contract_satisfied",
      message="formed setup is inside its entry zone; M1 is optional",
      evidence=evidence,
      metric="zone_presence_immediate_eligible",
      status="checking",
    )

  m1 = None if frames is None else frames.get("M1")
  if (
    normalize_setup_state(setup_record.state) in {CONFIRMED, PLAN_BUILT}
    and confirmation is None
  ):
    if execution_state is None:
      # Upgrade-safe: an already-confirmed setup from the pre-episode runtime
      # starts a new quote-in-zone retest observation.
      execution_state = new_state(
        setup_id,
        WAITING_RETEST,
        now=now_ts,
        zone_exited_at=quote_ts if not execution_eligible else None,
      )

    if execution_state.phase == CONFIRMATION_PUBLISHED:
      return None

    if execution_state.phase == IMMEDIATE_CONFIRMATION:
      if execution_eligible:
        confirmation = ExecutionConfirmation(
          source=M5_AUTHORITATIVE,
          pattern=match.reaction_type,
          bar_ts=confirmation_boundary,
          wick_extreme=None,
          zone_episode_id=str(execution_state.episode_id),
          message="scanner M5 reaction confirmation is authoritative",
        )
      else:
        execution_state = new_state(
          setup_id,
          WAITING_RETEST,
          now=now_ts,
          episode_id=execution_state.episode_id,
          zone_entered_at=execution_state.zone_entered_at,
          zone_exited_at=quote_ts,
          last_inside_at=execution_state.last_inside_at,
          trigger_bar_ts=execution_state.trigger_bar_ts,
          trigger_pattern=execution_state.trigger_pattern,
          trigger_source=execution_state.trigger_source,
          trigger_consumed=True,
        )
        await _persist_v8_confirmation_phase(
          client,
          symbol,
          match,
          execution_state,
          reason_code="waiting_retest",
          message="executable quote left before immediate publication",
          evidence=evidence,
          metric="waiting_retest",
        )
        return None

    if (
      confirmation is None
      and execution_state.phase in {
        WAITING_RETEST,
        TRIGGER_PRICE_LEFT_ZONE,
      }
    ):
      if not execution_eligible:
        if execution_state.zone_exited_at != quote_ts:
          execution_state = new_state(
            setup_id,
            execution_state.phase,
            now=now_ts,
            episode_id=execution_state.episode_id,
            zone_entered_at=execution_state.zone_entered_at,
            zone_exited_at=quote_ts,
            last_inside_at=execution_state.last_inside_at,
            last_evaluated_m1_ts=execution_state.last_evaluated_m1_ts,
            trigger_bar_ts=execution_state.trigger_bar_ts,
            trigger_pattern=execution_state.trigger_pattern,
            trigger_source=execution_state.trigger_source,
            trigger_consumed=execution_state.trigger_consumed,
          )
        await _persist_v8_confirmation_phase(
          client,
          symbol,
          match,
          execution_state,
          reason_code="waiting_retest_entry_zone",
          message="confirmed setup is outside its executable entry zone",
          evidence=evidence,
        )
        return None
      episode_id = deterministic_episode_id(
        setup_id,
        match.direction,
        match.entry_low,
        match.entry_high,
        quote_ts,
      )
      execution_state = new_state(
        setup_id,
        IN_ZONE_WAITING_M1,
        now=now_ts,
        episode_id=episode_id,
        zone_entered_at=quote_ts,
        last_inside_at=quote_ts,
      )
      await _persist_v8_confirmation_phase(
        client,
        symbol,
        match,
        execution_state,
        reason_code="waiting_m1_retest",
        message="retest entered execution distance; checking optional M1",
        evidence=evidence,
        metric="zone_episode_started",
      )
      await increment_metric(
        client,
        "zone_reentered",
        symbol=symbol,
      )

    if confirmation is None and execution_state.phase in {
      IN_ZONE_WAITING_M1,
      TRIGGER_READY,
    }:
      episode_start = max(
        int(execution_state.zone_entered_at or quote_ts),
        confirmation_boundary + 1,
      )
      trigger = (
        None
        if m1 is None or getattr(m1, "empty", False)
        else evaluate_m1_trigger_window(
          m1,
          zone_low=match.entry_low,
          zone_high=match.entry_high,
          key_level=match.key_level,
          direction=match.direction,
          earliest_bar_ts=episode_start,
          after_bar_ts=execution_state.last_evaluated_m1_ts,
          cfg=instrument_runtime_view(symbol),
        )
      )
      latest_evaluated = latest_eligible_m1_bar_ts(
        m1,
        earliest_bar_ts=episode_start,
        after_bar_ts=execution_state.last_evaluated_m1_ts,
      )
      if trigger is None:
        confirmation_source = M5_AUTHORITATIVE
        trigger = ExecutionConfirmation(
          source=confirmation_source,
          pattern=match.reaction_type,
          bar_ts=quote_ts,
          wick_extreme=None,
          zone_episode_id=str(execution_state.episode_id or ""),
          message="entry-zone presence authorized execution without M1",
        )
        execution_state = new_state(
          setup_id,
          TRIGGER_READY,
          now=now_ts,
          episode_id=execution_state.episode_id,
          zone_entered_at=execution_state.zone_entered_at,
          last_inside_at=quote_ts,
          last_evaluated_m1_ts=(
            latest_evaluated
            if latest_evaluated is not None
            else execution_state.last_evaluated_m1_ts
          ),
          trigger_bar_ts=quote_ts,
          trigger_pattern=match.reaction_type,
          trigger_source=confirmation_source,
          trigger_consumed=True,
        )
        await _persist_v8_confirmation_phase(
          client,
          symbol,
          match,
          execution_state,
          reason_code="entry_contract_satisfied",
          message="quote entered the executable entry zone; M1 optional",
          evidence=evidence,
          metric="reaction_entry_contract_satisfied",
          status="checking",
        )
      else:
        confirmation_source = M1_RETEST

      trigger_bar_ts = int(trigger.bar_ts)
      validity_bars = max(
        1,
        int(runtime_config.lifecycle.retest.trigger_validity_bars),
      )
      trigger_deadline = trigger_bar_ts + 60 + validity_bars * 60
      if quote_ts > trigger_deadline:
        execution_state = new_state(
          setup_id,
          IN_ZONE_WAITING_M1 if execution_eligible else WAITING_RETEST,
          now=now_ts,
          episode_id=execution_state.episode_id,
          zone_entered_at=execution_state.zone_entered_at,
          zone_exited_at=None if execution_eligible else quote_ts,
          last_inside_at=(
            quote_ts
            if execution_eligible else execution_state.last_inside_at
          ),
          last_evaluated_m1_ts=trigger_bar_ts,
          trigger_bar_ts=trigger_bar_ts,
          trigger_pattern=trigger.pattern,
          trigger_source=confirmation_source,
          trigger_consumed=True,
        )
        await _persist_v8_confirmation_phase(
          client,
          symbol,
          match,
          execution_state,
          reason_code="stale_m1_trigger_ignored",
          message="M1 retest trigger exceeded its execution validity window",
          evidence=evidence,
          metric="reaction_stale_m1_ignored",
        )
        confirmation_source = M5_AUTHORITATIVE
        trigger = ExecutionConfirmation(
          source=confirmation_source,
          pattern=match.reaction_type,
          bar_ts=quote_ts,
          wick_extreme=None,
          zone_episode_id=str(execution_state.episode_id or ""),
          message="stale M1 ignored; entry-zone presence authorized execution",
        )
        trigger_bar_ts = quote_ts
      if not execution_eligible:
        execution_state = new_state(
          setup_id,
          TRIGGER_PRICE_LEFT_ZONE,
          now=now_ts,
          episode_id=execution_state.episode_id,
          zone_entered_at=execution_state.zone_entered_at,
          zone_exited_at=quote_ts,
          last_inside_at=execution_state.last_inside_at,
          last_evaluated_m1_ts=trigger_bar_ts,
          trigger_bar_ts=trigger_bar_ts,
          trigger_pattern=trigger.pattern,
          trigger_source=confirmation_source,
          trigger_consumed=True,
        )
        await _persist_v8_confirmation_phase(
          client,
          symbol,
          match,
          execution_state,
          reason_code="trigger_price_left_zone",
          message="M1 trigger closed but executable quote already left the zone",
          evidence=evidence,
          metric="reaction_trigger_price_left_zone",
        )
        return None
      execution_state = new_state(
        setup_id,
        TRIGGER_READY,
        now=now_ts,
        episode_id=execution_state.episode_id,
        zone_entered_at=execution_state.zone_entered_at,
        last_inside_at=quote_ts,
        last_evaluated_m1_ts=trigger_bar_ts,
        trigger_bar_ts=trigger_bar_ts,
        trigger_pattern=trigger.pattern,
        trigger_source=confirmation_source,
        trigger_consumed=True,
      )
      await _persist_v8_confirmation_phase(
        client,
        symbol,
        match,
        execution_state,
        reason_code=(
          "m1_retest_triggered"
          if confirmation_source == M1_RETEST
          else "entry_contract_satisfied"
        ),
        message=(
          "fresh in-zone M1 trigger accepted for current retest episode"
          if confirmation_source == M1_RETEST
          else "entry-zone presence authorized execution without M1"
        ),
        evidence=evidence,
        metric=(
          "reaction_m1_trigger_found"
          if confirmation_source == M1_RETEST
          else "reaction_entry_contract_ready"
        ),
        status="checking",
      )
      confirmation = ExecutionConfirmation(
        source=confirmation_source,
        pattern=trigger.pattern,
        bar_ts=trigger_bar_ts,
        wick_extreme=trigger.wick_extreme,
        zone_episode_id=str(execution_state.episode_id),
        message=trigger.message,
      )

  if confirmation is None:
    # M1 pattern is preference telemetry. Zone presence alone authorizes
    # publication for every family once the entry contract is satisfied.
    if not execution_eligible:
      return None
    episode_id = deterministic_episode_id(
      setup_id,
      match.direction,
      match.entry_low,
      match.entry_high,
      confirmation_boundary,
    )
    trigger = (
      None
      if m1 is None or getattr(m1, "empty", False)
      else evaluate_m1_trigger_window(
        m1,
        zone_low=match.entry_low,
        zone_high=match.entry_high,
        key_level=match.key_level,
        direction=match.direction,
        earliest_bar_ts=confirmation_boundary,
        after_bar_ts=None,
        cfg=instrument_runtime_view(symbol),
      )
    )
    if trigger is not None:
      confirmation = ExecutionConfirmation(
        source=M1_RETEST,
        pattern=trigger.pattern,
        bar_ts=int(trigger.bar_ts),
        wick_extreme=trigger.wick_extreme,
        zone_episode_id=episode_id,
        message=trigger.message,
      )
    else:
      confirmation = ExecutionConfirmation(
        source=M5_AUTHORITATIVE,
        pattern=match.reaction_type,
        bar_ts=confirmation_boundary,
        wick_extreme=None,
        zone_episode_id=episode_id,
        message="entry-zone presence authorized execution without M1",
      )
    execution_state = new_state(
      setup_id,
      TRIGGER_READY,
      now=now_ts,
      episode_id=episode_id,
      zone_entered_at=quote_ts,
      last_inside_at=quote_ts,
      trigger_bar_ts=confirmation.bar_ts,
      trigger_pattern=confirmation.pattern,
      trigger_source=confirmation.source,
      trigger_consumed=True,
    )
    await _persist_v8_confirmation_phase(
      client,
      symbol,
      match,
      execution_state,
      reason_code="entry_contract_satisfied",
      message=confirmation.message,
      evidence=evidence,
      metric="setup_zone_presence_ready",
      status="checking",
    )

  entry_reference = _executable_spot_price(spot, match.direction)
  execution_match = match
  if policy.m5_authoritative_contract and confirmation.source == M1_RETEST:
    validity_bars = max(
      1,
      int(runtime_config.lifecycle.retest.trigger_validity_bars),
    )
    trigger_expiry = confirmation.bar_ts + 60 + validity_bars * 60
    execution_match = replace(
      match,
      expires_at=min(int(match.expires_at), trigger_expiry),
    )
  room_entries = (
    ()
    if (
      market_map is None
      or match_bypasses_opposing_structure(execution_match)
    )
    else tuple(getattr(market_map, "actionable_entries", ()) or ())
  )
  displacement_lookback = max(
    0, int(runtime_config.execution.policy.displacement_override_lookback_bars),
  )
  displacement_state: dict[str, object] = {
    "applied": False,
    "lookback_bars": displacement_lookback,
  }
  if displacement_lookback > 0 and frames is not None:
    room_frame = frames.get(execution_match.source_tf)
    if room_frame is not None and not room_frame.empty and "close" in room_frame.columns:
      recent_closes = tuple(
        float(value) for value in room_frame["close"].tail(displacement_lookback)
      )
      before = len(room_entries)
      room_entries = filter_displaced_opposing_entries(
        room_entries,
        direction=execution_match.direction,
        recent_closes=recent_closes,
      )
      displacement_state = {
        "applied": True,
        "lookback_bars": displacement_lookback,
        "recent_closes": list(recent_closes),
        "entries_before": before,
        "entries_after": len(room_entries),
        "dropped": before - len(room_entries),
      }
    else:
      displacement_state = {
        "applied": False,
        "lookback_bars": displacement_lookback,
        "reason": "no_closed_bars",
      }
  pip_size = units.pip_size(symbol)
  room_planned, room_reference_source = zone_proximal_room_reference(
    direction=execution_match.direction,
    spot_price=entry_reference,
    candidate_entry_low=execution_match.entry_low,
    candidate_entry_high=execution_match.entry_high,
    pip_size=pip_size,
    atr=execution_match.atr,
  )
  shared_boundary_state: dict[str, object] = {"applied": False}
  if room_entries:
    before_shared = len(room_entries)
    room_entries, shared_boundary_state = filter_shared_boundary_opposing_entries(
      room_entries,
      direction=execution_match.direction,
      candidate_entry_low=execution_match.entry_low,
      candidate_entry_high=execution_match.entry_high,
      pip_size=pip_size,
      atr=execution_match.atr,
      planned_entry=room_planned,
    )
    shared_boundary_state = {
      **shared_boundary_state,
      "entries_before_filter": before_shared,
    }
  target_room = evaluate_structural_target_room(
    direction=execution_match.direction,
    planned_entry_price=room_planned,
    candidate_entry_low=execution_match.entry_low,
    candidate_entry_high=execution_match.entry_high,
    configured_target_pips=execution_match.targets_pips,
    actionable_entries=room_entries,
    atr=execution_match.atr,
    pip_size=pip_size,
    barrier_buffer_atr=float(
      runtime_config.actionability.target_room.barrier_buffer_atr
    ),
    min_capped_target_pips=float(
      runtime_config.actionability.target_room.minimum_capped_target_pips
    ),
    execution_cost_pips=float(runtime_config.execution.policy.execution_cost_pips),
    displacement_state=displacement_state,
    room_reference_source=room_reference_source,
    executable_entry_price=entry_reference,
    shared_boundary_state=shared_boundary_state,
    allow_same_wall_overlap=is_technique_or_confluence(
      execution_match.strategy,
    ),
  )
  if not target_room.allowed:
    # Counter-bias vs HTF intentionally presses into opposing structure.
    # Keep the setup when native usable room still clears the floor —
    # prod was dying on v8_opposing_entry_overlap while Bias:counter_bias
    # cards never published (live 2026-08-06). Zero/negative room still fails.
    bias = str(
      getattr(execution_match, "bias_relationship", None)
      or execution_match.strategy_mode
      or ""
    ).casefold()
    tags = {
      str(tag).casefold() for tag in (execution_match.tags or ())
    }
    is_counter_bias = "counter_bias" in tags or bias == "counter_bias"
    room_measured = dict(target_room.measured or {})
    try:
      room_pips = float(
        room_measured.get("usable_room_pips")
        or room_measured.get("room_pips")
        or 0.0
      )
    except (TypeError, ValueError):
      room_pips = 0.0
    min_room = float(
      runtime_config.actionability.target_room.minimum_capped_target_pips or 15
    )
    soft_codes = {
      "opposing_entry_overlap",
      "opposing_entry_contained",
      "opposing_major_no_room",
    }
    if (
      is_counter_bias
      and str(target_room.reason_code or "") in soft_codes
      and room_pips + 1e-9 >= min_room
    ):
      log.info(
        "v8 counter_bias keeping setup past %s room_pips=%.1f match=%s",
        target_room.reason_code,
        room_pips,
        match.match_id[:12],
      )
    else:
      # Hard reject structural conflicts (e.g. SELL entry inside demand /
      # opposing_entry_contained). Preference-only demotion previously let
      # those plans publish and hedge the correct side.
      await _record_v8_build_rejected(
        client,
        symbol,
        match,
        str(target_room.reason_code or "opposing_structure_blocked"),
        target_room.message
        or "planned entry conflicts with opposing actionable structure",
        room_measured,
      )
      await increment_metric(
        client,
        "target_room_rejected",
        symbol=symbol,
        dimensions={"reason": str(target_room.reason_code or "unknown")},
      )
      return None
  match_for_plan = execution_match
  if (
    target_room.opposing_entry is not None
    and target_room.fitted_targets_pips
    and tuple(target_room.fitted_targets_pips) != tuple(execution_match.targets_pips)
  ):
    # Fitted targets must only ever equal the full configured ladder now.
    # Refuse silent shrink-to-one-tiny-TP (live 2026-08-06 +9 pip full exit).
    log.warning(
      "v8 ignoring non-matching fitted_targets_pips match=%s fitted=%s configured=%s",
      execution_match.match_id,
      target_room.fitted_targets_pips,
      execution_match.targets_pips,
    )

  zone_claim_id = _resolve_match_confluence_claim_id(
    symbol,
    match_for_plan,
    market_map,
  )
  if zone_claim_id is not None:
    zone_claimed = await claim_confluence_zone(
      client, zone_id=zone_claim_id, owner_id=setup_id,
    )
    if not zone_claimed:
      await _record_v8_build_rejected(
        client, symbol, match, "zone_already_claimed",
        f"merged confluence zone {zone_claim_id!r} is already owned by a "
        "different setup - one merged zone may own at most one order",
        {"zone_id": zone_claim_id},
      )
      return None

  claimed = await claim_active_thesis(
    client, symbol=symbol, thesis_id=match.thesis_id, setup_id=setup_id,
  )
  if not claimed:
    if zone_claim_id is not None:
      await release_confluence_zone(
        client, zone_id=zone_claim_id, owner_id=setup_id,
      )
    await _record_v8_build_rejected(
      client, symbol, match, "thesis_already_owned",
      f"thesis {match.thesis_id!r} is already owned by a different setup - "
      "one active thesis may own at most one autonomous initial plan",
      {},
    )
    return None

  strategy_opposing_zone = _nearest_directional_zone(
    match_for_plan.direction,
    spot.price,
    htf_zones or [],
    candidate_entry_low=match_for_plan.entry_low,
    candidate_entry_high=match_for_plan.entry_high,
    atr=float(match_for_plan.atr),
    pip_size=float(units.pip_size(symbol)),
  )
  guard_mode = resolve_guard_mode()
  source = _structural_source_identity(
    strategy=match_for_plan.strategy,
    family=match_for_plan.family,
    structural_source=(
      match_for_plan.structural_source or match_for_plan.strategy
    ),
    low=match_for_plan.entry_low,
    high=match_for_plan.entry_high,
    key_level=match_for_plan.key_level,
    zone_id=match_for_plan.zone_id,
    level_id=match_for_plan.level_id,
  )
  barrier_outcome = _opposing_barrier_decision(
    match_for_plan.direction,
    spot.price,
    match_for_plan.target_price,
    match_for_plan.atr,
    htf_zones or [], htf_levels or [],
    runtime_config.actionability.target_room.barrier_buffer_atr,
    source=source,
    guard_mode=guard_mode,
  )
  async def _release_claims() -> None:
    await release_active_thesis(
      client, symbol=symbol, thesis_id=match.thesis_id, setup_id=setup_id,
    )
    if zone_claim_id is not None:
      await release_confluence_zone(
        client, zone_id=zone_claim_id, owner_id=setup_id,
      )

  if (
    barrier_outcome.hard_block
    and not match_bypasses_opposing_structure(match_for_plan)
  ):
    await _release_claims()
    await _record_v8_build_rejected(
      client, symbol, match, barrier_outcome.reason_code,
      barrier_outcome.message, barrier_outcome.measured,
    )
    return None

  # Defended-level guard: intervention risk is long into the macro ceiling
  # (USDJPY 160). SELLs near the level stay eligible; BUYs hard-block.
  defended_outcome = _defended_level_guard(
    symbol,
    spot.price,
    direction=str(getattr(match_for_plan, "direction", "") or ""),
    guard_mode=guard_mode,
  )
  if defended_outcome.hard_block:
    await _release_claims()
    await _record_v8_build_rejected(
      client, symbol, match, defended_outcome.reason_code,
      defended_outcome.message, defended_outcome.measured,
    )
    return None

  # HTF veto: reject when the nearest opposing HTF zone is still untested and
  # ahead of the executable quote (defect 4: a short taken below untested
  # supply). Preflight used to enforce this; TradePlan owns it now. Scalps with
  # fitted native room skip HTF opposing — range/HFS room is the gate.
  if (
    runtime_config.actionability.gates.htf_veto_enabled
    and not match_bypasses_opposing_structure(match_for_plan)
  ):
    htf_opposing = _nearest_directional_zone(
      match_for_plan.direction,
      spot.price,
      htf_zones or [],
      candidate_entry_low=match_for_plan.entry_low,
      candidate_entry_high=match_for_plan.entry_high,
      atr=float(match_for_plan.atr),
      pip_size=float(units.pip_size(symbol)),
    )
    htf_reason = _htf_veto_reason(match_for_plan.direction, spot.price, htf_opposing)
    if htf_reason is not None:
      htf_severity = classify_guard_severity(
        "htf_veto",
        "htf_veto",
        htf_reason,
        guard_mode=guard_mode,
      )
      if htf_severity.hard_block:
        await _release_claims()
        await _record_v8_build_rejected(
          client, symbol, match, "htf_veto", htf_reason, dict(htf_severity.measured),
        )
        return None

  # Hard overlap veto: an entry inside both demand and supply must fail before
  # publishing, resolved via the reaction-lookback thesis check that the
  # preflight used to run.
  overlap_outcome = _resolve_overlap_thesis(
    match_for_plan.direction,
    entry_reference,
    market_map,
    None if frames is None else frames.get("M1"),
    float(match_for_plan.atr),
    None,
    symbol=symbol,
  )
  if overlap_outcome.hard_block:
    await _release_claims()
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      overlap_outcome.reason_code,
      overlap_outcome.message,
      dict(overlap_outcome.measured),
    )
    return None
  if overlap_outcome.reason_code not in {"no_map", "no_overlap"}:
    log.info(
      "v8 overlap preference observed symbol=%s reason=%s",
      symbol, overlap_outcome.reason_code,
    )

  # News window: a lookup failure retains the intent (non-terminal wait);
  # an active window is preference telemetry only (matches old preflight
  # executable=True behavior).
  news_now = int(datetime.now(timezone.utc).timestamp())
  try:
    news_event = await _news_guard_hit(
      symbol, news_now,
    )
  except Exception:
    await _release_claims()
    await record_route_outcome(
      client,
      match,
      stage="news",
      status="waiting",
      reason_code="news_guard_unavailable",
      message="news guard unavailable; intent retained",
      retained=True,
      publish_status=False,
    )
    return None
  if news_event is not None:
    log.info(
      "v8 news preference observed symbol=%s title=%s",
      symbol, news_event.get("title", "unknown"),
    )

  cooldown_reason = await _zone_cooldown_reason(
    client, symbol, match.direction, spot.price,
    match.atr, runtime_config.lifecycle.zone.cooldown_atr,
  )
  if cooldown_reason is not None:
    log.info(
      "v8 zone_cooldown preference observed symbol=%s reason=%s",
      symbol, cooldown_reason,
    )
    # Zone cooldown is preference telemetry — continue to publish.

  exposures = await load_active_exposures(client)
  scalp_ignores_opposing_active = match_bypasses_opposing_structure(
    match_for_plan,
  )
  candidate_is_scalp = is_scalp_strategy(
    str(getattr(match_for_plan, "strategy", "") or ""),
    family=str(getattr(match_for_plan, "strategy_family", "") or "") or None,
    strategy_mode=str(
      getattr(match_for_plan, "strategy_mode", "") or ""
    ) or None,
  )
  exposure = evaluate_entry_against_exposure(
    direction=match_for_plan.direction,
    entry_price=float(entry_reference),
    exposures=exposures,
    candidate_symbol=symbol,
    min_price_separation=float(
      instrument_geometry.opposing_minimum_separation_price(symbol)
    ),
    same_direction_size_fraction=float(
      runtime_config.risk.position_limits.same_direction_stack_size_fraction
    ),
    # Active opposite position must not block HFS / Range Edge when native
    # min room already fitted (owner 2026-08-06).
    ignore_opposing_active=scalp_ignores_opposing_active,
    # Non-scalp may same-dir stack at 60% only after every open plan has
    # booked TP2 and the candidate is Tier A. Scalps may stack freely.
    allow_same_direction_stack=candidate_is_scalp,
    candidate_tier=str(getattr(match_for_plan, "tier", "") or ""),
  )
  if exposure.block:
    await _release_claims()
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      str(exposure.reason_code or "opposing_active_too_close"),
      exposure.message,
      dict(exposure.measured or {}),
    )
    return None
  if exposure.reason_code == "opposing_active_too_close_ignored_scalp":
    log.info(
      "v8 scalp ignores opposing-active separation symbol=%s match_id=%s %s",
      symbol,
      match.match_id[:12],
      exposure.message,
    )
  same_direction_stack = bool(exposure.same_direction_stack)
  if same_direction_stack:
    log.info(
      "v8 same-direction stack symbol=%s match_id=%s %s",
      symbol,
      match.match_id[:12],
      exposure.message,
    )

  opposing_zone_low = (
    strategy_opposing_zone.low if strategy_opposing_zone is not None else None
  )
  opposing_zone_high = (
    strategy_opposing_zone.high if strategy_opposing_zone is not None else None
  )
  if match_bypasses_opposing_structure(match_for_plan):
    opposing_kwargs: dict[str, Any] = {}
    opposing_zone_low = None
    opposing_zone_high = None
  else:
    opposing_kwargs = _opposing_zone_policy_kwargs(
      strategy_opposing_zone,
      atr=float(match_for_plan.atr),
      pip_size=float(units.pip_size(symbol)),
      symbol=symbol,
      timeframe=str(match_for_plan.source_tf or "M5"),
    )
  # Zone-split capability + required-limit-side checks: mirror old preflight
  # policy gates against the fresh policy evaluation for the plan-time match.
  side_aware_quote = _executable_spot_price(spot, match_for_plan.direction)
  fixed_rr_target = instrument_geometry.fixed_reward_risk(symbol) is not None
  fixed_rr_room: float | None = None
  target_room_measured = dict(target_room.measured or {})
  if (
    fixed_rr_target
    and target_room.opposing_entry is not None
    and not target_room_measured.get("weak_opposing_level_ignored")
  ):
    raw_room = target_room_measured.get("usable_room_pips")
    if raw_room is not None:
      try:
        fixed_rr_room = max(0.0, float(raw_room))
      except (TypeError, ValueError):
        fixed_rr_room = None
  fixed_rr_metrics: list[tuple[str, str, dict[str, str]]] = []
  gate_policy = evaluate_execution_policy(
    match_for_plan,
    spot_price=spot.price,
    executable_quote=side_aware_quote,
    regime=None if regime is None else regime.state,
    pip_size=units.pip_size(symbol),
    cfg=None,
    available_target_room_pips=fixed_rr_room,
    metric_sink=_collect_fixed_rr_metric_sink(fixed_rr_metrics),
    **opposing_kwargs,
  )
  for metric_name, metric_symbol, metric_dims in fixed_rr_metrics:
    await increment_metric(
      client,
      metric_name,
      symbol=metric_symbol,
      dimensions=metric_dims,
    )
  gate_measured = dict(gate_policy.measured)
  if fixed_rr_target and not gate_policy.allowed:
    await _release_claims()
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      gate_policy.reason_code,
      gate_policy.message,
      gate_measured,
    )
    return None
  gate_entry_distribution = str(gate_measured.get("entry_distribution", "single"))
  if (
    gate_entry_distribution == "zone_split"
    and not runtime_config.execution.zone_scaling.fill_enabled
  ):
    await _release_claims()
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      "zone_split_capability_unavailable",
      "execution policy requires disabled zone-fill capability",
      gate_measured,
    )
    return None
  gate_order_type = (
    gate_policy.policy.order_type_preference
    if gate_policy.policy is not None else "either"
  )
  gate_direction = str(match_for_plan.direction).upper()
  gate_entry_low = float(match_for_plan.entry_low)
  gate_entry_high = float(match_for_plan.entry_high)
  gate_zone_width = gate_entry_high - gate_entry_low
  gate_limit_side_valid = (
    gate_direction == "BUY"
    and (
      gate_entry_high <= spot.price
      or (
        gate_entry_low <= spot.price < gate_entry_high
        and spot.price - gate_entry_low >= gate_zone_width * 0.35
      )
    )
    or gate_direction == "SELL"
    and (
      gate_entry_low >= spot.price
      or (
        gate_entry_low < spot.price <= gate_entry_high
        and gate_entry_high - spot.price >= gate_zone_width * 0.35
      )
    )
  )
  if gate_order_type == "limit" and not gate_limit_side_valid:
    await _release_claims()
    await record_route_outcome(
      client,
      match,
      stage="policy",
      status="waiting",
      reason_code="required_limit_side_unavailable",
      message="required limit entry is not currently on a valid broker side",
      measured=gate_measured,
      retained=True,
      publish_status=False,
    )
    return None
  try:
    # Native XAU HFS 1:2: after TP1 books (50%), move SL to BE for the
    # runner — same contract as other multi-target plans. C# runtime only
    # applies BE when HighestBookedTargetIndex advances (actual broker
    # close), so deferred/touch-only TP1 cannot arm BE. 1:1 single-exit
    # plans still leave be_after unset via closes_at_first_target.
    plan = build_trade_plan_from_strategy_match(
      match_for_plan,
      plan_id=_v8_plan_id(match_for_plan),
      setup_id=setup_id,
      thesis_id=match.thesis_id,
      pip_size=Decimal(str(units.pip_size(symbol))),
      spot_price=spot.price,
      regime=None if regime is None else regime.state,
      cfg=inst,
      opposing_zone_low=opposing_kwargs.get(
        "opposing_zone_low", opposing_zone_low,
      ),
      opposing_zone_high=opposing_kwargs.get(
        "opposing_zone_high", opposing_zone_high,
      ),
      opposing_zone_id=opposing_kwargs.get("opposing_zone_id"),
      executable_quote=entry_reference,
      confirmation_source=confirmation.source,
      execution_confirmation_bar_ts=confirmation.bar_ts,
      zone_episode_id=confirmation.zone_episode_id,
      trigger_wick_extreme=confirmation.wick_extreme,
      max_volume=int(instrument_geometry.plan_max_volume(symbol)),
      now_ts=now_ts,
      same_direction_stack=same_direction_stack,
      same_direction_size_fraction=float(
        runtime_config.risk.position_limits.same_direction_stack_size_fraction
      ),
      be_after_target_index=0,
      approved_measured=gate_measured if fixed_rr_target else None,
    )
  except TradePlanBuildRejected as exc:
    await _release_claims()
    rejection_measured = {
      **(
        target_room.measured
        if target_room.opposing_entry is not None
        else {}
      ),
      **exc.measured,
    }
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      exc.reason_code,
      exc.message,
      rejection_measured,
    )
    # Terminalize already ran inside _record_v8_build_rejected. Persist
    # confirmation phase for reaction-family setups when confirmation exists.
    terminal_state = (
      EXPIRED
      if exc.reason_code == "policy_reward_risk_insufficient"
      else INVALIDATED
    )
    lifecycle_reason = (
      (
        "m1_trigger_expired"
        if confirmation.source == M1_RETEST
        else "confirmation_expired"
      )
      if terminal_state == EXPIRED
      else f"v8_{exc.reason_code}"
    )
    if policy.m5_authoritative_contract:
      await _persist_v8_confirmation_phase(
        client,
        symbol,
        match,
        new_state(
          setup_id,
          (
            CONFIRMATION_EXPIRED
            if terminal_state == EXPIRED
            else CONFIRMATION_INVALIDATED
          ),
          now=now_ts,
          episode_id=confirmation.zone_episode_id,
          trigger_bar_ts=confirmation.bar_ts,
          trigger_pattern=confirmation.pattern,
          trigger_source=confirmation.source,
          trigger_consumed=True,
        ),
        reason_code=lifecycle_reason,
        message=exc.message,
        evidence=evidence,
        status="expired" if terminal_state == EXPIRED else "blocked",
      )
    return None
  except TradePlanError as exc:
    # Defense in depth: builder should already wrap validate() failures as
    # TradePlanBuildRejected. Still release claims if a TradePlanError escapes.
    await _release_claims()
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      "trade_plan_invalid",
      str(exc),
      {},
    )
    return None
  except Exception as exc:
    # Live 2026-08-20: uncaught exception after claim_active_thesis left
    # analysis:active_thesis:XAU:1681edb5 orphaned for ~24h and blocked
    # later HFS with thesis_already_owned. Always release on unexpected fail.
    await _release_claims()
    log.exception(
      "v8 plan publish failed after thesis claim symbol=%s setup_id=%s "
      "match_id=%s",
      symbol,
      setup_id,
      match.match_id,
    )
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      "v8_publish_exception",
      f"{type(exc).__name__}: {exc}",
      {},
    )
    return None

  try:
    setup_live = await load_setup(client, setup_id)
    if setup_live is None or setup_live.state in TERMINAL_STATES:
      await _release_claims()
      log.info(
        "v8 publish aborted: setup is terminal symbol=%s setup_id=%s "
        "state=%s plan_id=%s",
        symbol,
        setup_id,
        None if setup_live is None else setup_live.state,
        plan.plan_id,
      )
      return None
    if setup_live.state in {PLAN_PUBLISHED, ARMED}:
      log.info(
        "v8 publish skipped: setup already published symbol=%s "
        "setup_id=%s state=%s plan_id=%s",
        symbol, setup_id, setup_live.state, plan.plan_id,
      )
      return plan.plan_id
    if setup_live.state != PLAN_BUILT:
      await transition_setup(
        client,
        setup_id,
        PLAN_BUILT,
        reason_code="v8_builder",
      )
    await publish_trade_plan(client, plan)
    await transition_setup(
      client, setup_id, PLAN_PUBLISHED, reason_code="v8_stream_publish",
    )
  except SetupLifecycleError:
    log.exception(
      "v8 plan publish blocked by setup lifecycle symbol=%s "
      "setup_id=%s plan_id=%s",
      symbol, setup_id, plan.plan_id,
    )
    await _release_claims()
    return None
  except Exception as exc:
    await _release_claims()
    log.exception(
      "v8 plan stream publish failed after thesis claim symbol=%s "
      "setup_id=%s plan_id=%s",
      symbol, setup_id, plan.plan_id,
    )
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      "v8_publish_exception",
      f"{type(exc).__name__}: {exc}",
      {"plan_id": plan.plan_id},
    )
    return None
  published_state = new_state(
    setup_id,
    CONFIRMATION_PUBLISHED,
    now=now_ts,
    episode_id=confirmation.zone_episode_id,
    zone_entered_at=(
      None if execution_state is None else execution_state.zone_entered_at
    ),
    last_inside_at=quote_ts,
    last_evaluated_m1_ts=(
      None if execution_state is None
      else execution_state.last_evaluated_m1_ts
    ),
    trigger_bar_ts=confirmation.bar_ts,
    trigger_pattern=confirmation.pattern,
    trigger_source=confirmation.source,
    trigger_consumed=True,
  )
  await _persist_v8_confirmation_phase(
    client,
    symbol,
    match,
    published_state,
    reason_code=(
      "m1_soft_confirmation"
      if confirmation.source == M1_RETEST
      else "entry_contract_satisfied"
    ),
    message="TradePlan V8 published inside the executable entry contract",
    evidence=evidence,
    metric="entry_contract_plan_published",
    status="candidate_published",
  )
  await increment_metric(client, "v8_plan_published", symbol=symbol)
  if policy.reaction_family:
    await increment_metric(
      client,
      (
        "reaction_m1_stop_refinement_used"
        if confirmation.source == M1_RETEST
        else "reaction_non_m1_stop_used"
      ),
      symbol=symbol,
    )
  await emit_lifecycle(
    client,
    "candidate_published",
    symbol=symbol,
    correlation_id=setup_id,
    timeframe=match.source_tf,
    reason_code="",
    message=f"TradePlan V8 published: {match.strategy} {match.direction}",
    measured={
      "plan_id": plan.plan_id,
      "thesis_id": plan.thesis_id,
      "entry_type": plan.entry.type,
      "stop_price": str(plan.stop.price),
      "targets": [str(target.price) for target in plan.targets],
      "confirmation_source": confirmation.source,
      "confirmation_bar_ts": confirmation.bar_ts,
      "zone_episode_id": confirmation.zone_episode_id,
    },
  )
  try:
    from app.autotrade.setup_card import (
      ensure_plan_published_root_card,
      schedule_deferred_root_card_ensure,
    )
    from aiogram.exceptions import TelegramRetryAfter

    # ensure (not just edit): a plan can reach this point without ever
    # having a root card -- HFS's own synchronous publish attempt is only
    # one of the ways a plan gets published here. The same match, once
    # persisted to strategy_matches, is also independently discovered and
    # published by this cycle's own arbitration on a later pass in the
    # same tick, bypassing publish_hfs_live() (and its card-ensure)
    # entirely. Live 2026-08-06: an HFS fill with zero Telegram card,
    # confirmed to have published via exactly this second path (own
    # publish_hfs_live call logged status=remained_watching; this
    # function then logged the actual publish moments later in the same
    # cycle). ensure_plan_published_root_card() creates the card if
    # missing or just refreshes Stop on an existing one either way.
    await ensure_plan_published_root_card(
      client, match, edit_fn=_forming_card_edit_fn,
    )
  except TelegramRetryAfter as exc:
    delay = float(getattr(exc, "retry_after", 5) or 5) + 1.0
    log.error(
      "v8 forming card flood-limited setup_id=%s plan_id=%s "
      "retry_after=%ss; scheduling deferred ensure",
      setup_id,
      plan.plan_id,
      getattr(exc, "retry_after", None),
    )
    await schedule_deferred_root_card_ensure(
      client, match, delay_seconds=delay,
    )
  except Exception:
    log.exception(
      "v8 forming card stop refresh failed setup_id=%s plan_id=%s",
      setup_id,
      plan.plan_id,
    )
    await schedule_deferred_root_card_ensure(
      client, match, delay_seconds=5.0,
    )
  log.info(
    "v8 plan published id=%s symbol=%s strategy=%s direction=%s entry_type=%s",
    plan.plan_id, symbol, match.strategy, match.direction, plan.entry.type,
  )
  return plan.plan_id


_TREND_SETUP_LABELS = {
  "pullback": "Trend Pullback",
  "breakout_continuation": "Breakout Continuation",
  "box_breakout": "Box Breakout",
}
_TREND_MODE_LABELS = {
  "pullback": "auto_trend_pullback",
  "breakout_continuation": "auto_trend_breakout",
  "box_breakout": "auto_box_breakout",
}


def _trend_candidate_id(
  symbol: str,
  trigger_ts: str,
  trend_decision: TrendDecision,
) -> str:
  if trend_decision.direction is None or trend_decision.mode is None:
    raise ValueError("trend candidate requires a direction and mode")
  key_level = (
    trend_decision.key_level if trend_decision.key_level is not None else 0.0
  )
  raw = (
    f"v3|trend|{symbol.upper()}|{trend_decision.mode}|{trigger_ts}|"
    f"{trend_decision.direction.upper()}|{key_level:.5f}"
  )
  return hashlib.sha256(raw.encode("ascii")).hexdigest()


async def _publish_trend_candidate(
  client: Any,
  symbol: str,
  event_ts: str,
  spot: AutoTradeSpot | None,
  regime: RegimeInfo,
  trend_decision: TrendDecision,
  htf_zones: list[Zone] | None = None,
  htf_levels: list[Level] | None = None,
  market_map: MarketMap | None = None,
  frames: dict[str, Any] | None = None,
) -> str | None:
  if (
    not runtime_config.runtime.auto_trade.enabled
    or not runtime_config.strategies.trend.enabled
    or spot is None
    or not spot.fresh
    or regime.state not in ("trend", "breakout")
    or trend_decision.state != "candidate"
    or trend_decision.direction is None
    or trend_decision.mode not in _TREND_SETUP_LABELS
    or trend_decision.entry_zone is None
    or trend_decision.key_level is None
    or trend_decision.atr is None
    or trend_decision.structure_swing is None
    or not trend_decision.targets_pips
    or trend_decision.confluence < max(
      1, runtime_config.actionability.gates.min_confluence,
    )
  ):
    return None

  trend_setup = _TREND_SETUP_LABELS[trend_decision.mode]
  trend_tier = "A" if trend_decision.confluence >= 3 else "B"
  entry_reference = spot.price
  opposing_zone = _nearest_directional_zone(
    trend_decision.direction,
    entry_reference,
    htf_zones or [],
    candidate_entry_low=trend_decision.entry_zone[0],
    candidate_entry_high=trend_decision.entry_zone[1],
    atr=float(trend_decision.atr),
    pip_size=float(units.pip_size(symbol)),
  )
  absolute_target = (
    max(trend_decision.target_prices)
    if trend_decision.direction == "BUY" and trend_decision.target_prices
    else min(trend_decision.target_prices)
    if trend_decision.target_prices
    else None
  )
  trend_policy_subject = PrivatePolicySubject(
    symbol=symbol,
    strategy=trend_setup,
    direction=trend_decision.direction,
    entry_low=trend_decision.entry_zone[0],
    entry_high=trend_decision.entry_zone[1],
    current_price=spot.price,
    confluence=trend_decision.confluence,
    atr=trend_decision.atr,
    structure_swing=trend_decision.structure_swing,
    targets_pips=trend_decision.targets_pips,
    risk_multiplier=risk_multiplier_for_tier(trend_tier),
    target_model="hybrid" if absolute_target is not None else "fill_relative",
    target_reference_price=(
      "planned_entry" if absolute_target is not None else "broker_fill"
    ),
    target_price=absolute_target,
    absolute_target_price=absolute_target,
  )
  trend_policy = evaluate_execution_policy(
    trend_policy_subject,
    spot_price=_executable_spot_price(spot, trend_decision.direction),
    regime=regime.state,
    pip_size=units.pip_size(symbol),
    cfg=None,
    **_opposing_zone_policy_kwargs(
      opposing_zone,
      atr=trend_decision.atr,
      pip_size=units.pip_size(symbol),
      symbol=symbol,
      timeframe=EXECUTION_TIMEFRAME,
    ),
  )
  if not trend_policy.allowed:
    await _record_private_route(
      client,
      symbol=symbol,
      event_ts=event_ts,
      strategy=trend_setup,
      family="trend",
      direction=trend_decision.direction,
      source="private_trend",
      structural_id=_trend_group_id(symbol, trend_decision),
      entry_low=trend_decision.entry_zone[0],
      entry_high=trend_decision.entry_zone[1],
      spot_price=spot.price,
      status="blocked" if trend_policy.terminal else "waiting",
      reason_code=trend_policy.reason_code,
      message=trend_policy.message,
      retained=not trend_policy.terminal,
    )
    return None
  fixed_rr_targets = _fixed_rr_policy_targets(trend_policy)
  fixed_rr_prices = tuple(
    float(value)
    for value in (trend_policy.measured.get("planned_target_prices") or ())
  )
  published_targets = (
    fixed_rr_targets if fixed_rr_targets else trend_decision.targets_pips
  )
  published_absolute_target = (
    fixed_rr_prices[-1] if fixed_rr_prices else absolute_target
  )
  published_target_model = (
    "hybrid" if fixed_rr_prices else trend_policy_subject.target_model
  )
  published_target_reference = (
    "planned_entry" if fixed_rr_prices
    else trend_policy_subject.target_reference_price
  )

  guard_mode = resolve_guard_mode()
  if runtime_config.actionability.gates.htf_veto_enabled:
    veto_reason = _htf_veto_reason(
      trend_decision.direction, entry_reference, opposing_zone,
    )
    if veto_reason is not None:
      veto_outcome = classify_guard_severity(
        "htf_veto",
        "htf_veto",
        veto_reason,
        guard_mode=guard_mode,
      )
      await _record_guard_evaluation(
        client, symbol, veto_outcome,
        strategy=_TREND_SETUP_LABELS[trend_decision.mode],
        direction=trend_decision.direction,
        source_structure=trend_decision.mode,
      )
      log.info(
        "auto-trend candidate %s symbol=%s reason=%s",
        "blocked" if veto_outcome.hard_block else veto_outcome.outcome,
        symbol,
        veto_reason,
      )
      if veto_outcome.hard_block:
        await _record_gate_reject(client, symbol, "htf_veto")
        return None
  trend_m1 = (frames or {}).get("M1") if frames is not None else None
  if (
    runtime_config.actionability.gates.opposing_barrier_veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
  ):
    source = _structural_source_identity(
      strategy=_TREND_SETUP_LABELS[trend_decision.mode],
      family="trend",
      structural_source=trend_decision.mode,
      low=trend_decision.entry_zone[0],
      high=trend_decision.entry_zone[1],
      key_level=trend_decision.key_level,
    )
    barrier_outcome = _opposing_barrier_decision(
      trend_decision.direction, entry_reference, None, trend_decision.atr,
      htf_zones or [], htf_levels or [],
      runtime_config.actionability.target_room.barrier_buffer_atr,
      source=source,
      guard_mode=guard_mode,
    )
    if barrier_outcome.reason_code != "no_opposing_barrier":
      await _record_guard_evaluation(
        client, symbol, barrier_outcome,
        strategy=_TREND_SETUP_LABELS[trend_decision.mode],
        direction=trend_decision.direction,
        source_structure=trend_decision.mode,
      )
      log.info(
        "auto-trend candidate %s symbol=%s reason=%s",
        "blocked" if barrier_outcome.hard_block else barrier_outcome.outcome,
        symbol, barrier_outcome.message,
      )
    if barrier_outcome.hard_block:
      await _record_gate_reject(
        client, symbol, barrier_outcome.reason_code,
      )
      return None
    if barrier_outcome.outcome == OUTCOME_WAIT:
      return None
  cooldown_reason = await _zone_cooldown_reason(
    client, symbol, trend_decision.direction, entry_reference,
    trend_decision.atr, runtime_config.lifecycle.zone.cooldown_atr,
  )
  if cooldown_reason is not None:
    cooldown_outcome = classify_guard_severity(
      "zone_cooldown", "zone_cooldown", cooldown_reason,
      guard_mode=guard_mode, hard_geometry=False,
    )
    await _record_guard_evaluation(
      client, symbol, cooldown_outcome,
      strategy=_TREND_SETUP_LABELS[trend_decision.mode],
      direction=trend_decision.direction,
      source_structure=trend_decision.mode,
    )
    log.info(
      "auto-trend candidate %s symbol=%s reason=%s",
      "blocked" if cooldown_outcome.hard_block else cooldown_outcome.outcome,
      symbol, cooldown_reason,
    )
    if cooldown_outcome.hard_block:
      await _record_gate_reject(client, symbol, "zone_cooldown")
      return None
  if (
    runtime_config.actionability.overlapping_zones.veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
  ):
    overlap_outcome = _resolve_overlap_thesis(
      trend_decision.direction, entry_reference, market_map, trend_m1,
      trend_decision.atr, None, symbol=symbol,
    )
    if overlap_outcome.reason_code not in ("no_map", "no_overlap"):
      await _record_guard_evaluation(
        client, symbol, overlap_outcome,
        strategy=_TREND_SETUP_LABELS[trend_decision.mode],
        direction=trend_decision.direction,
        source_structure=trend_decision.mode,
      )
      log.info(
        "auto-trend candidate %s symbol=%s reason=%s",
        "blocked" if overlap_outcome.hard_block else overlap_outcome.outcome,
        symbol, overlap_outcome.message,
      )
    if overlap_outcome.hard_block:
      await _record_gate_reject(client, symbol, "overlapping_zone_conflict")
      return None
    if overlap_outcome.outcome == OUTCOME_WAIT:
      return None

  now = int(datetime.now(timezone.utc).timestamp())
  try:
    guarded = await _news_guard_hit(
      symbol, now,
    )
  except Exception:
    log.exception("auto-trend candidate blocked: news guard unavailable")
    return None
  if guarded is not None:
    log.info(
      "auto-trend candidate blocked by news guard symbol=%s event=%s",
      symbol,
      guarded.get("title", "high-impact event"),
    )
    return None

  trigger_ts = str(event_ts or "")
  candidate_id = _trend_candidate_id(symbol, trigger_ts, trend_decision)
  # Scale-in add evaluation (ScaleInTriggerPlanner, ctrader-engine) needs
  # displacement/BOS/opposing-level context on trend candidates the same
  # way box-scalp candidates already carry it - this is the wiring gap that
  # left the momentum-add path unreachable in production (no regime="trend"
  # candidate ever carried these fields before). There's no analogous
  # single "target rail" for a trend candidate's own ladder, so
  # opposing_level_distance_atr stays unset here (momentum's buffer check
  # is a no-op when absent, same as any other candidate type lacking it).
  scale_context = (
    build_auto_scale_context(
      frames or {},
      trend_decision.direction,
      spot_price=entry_reference,
      cfg=None,
    )
    if frames is not None else None
  )
  group_id = _trend_group_id(symbol, trend_decision)
  parent_group_id = None
  raw_snapshot = await client.get(
    f"auto_trade:executor_snapshot:{symbol.upper()}"
  )
  if raw_snapshot:
    try:
      snapshot = json.loads(
        raw_snapshot.decode()
        if isinstance(raw_snapshot, bytes)
        else str(raw_snapshot)
      )
      if group_id[:10] in (snapshot.get("group_ids") or []):
        parent_group_id = group_id
    except (TypeError, ValueError, json.JSONDecodeError):
      log.warning("Invalid executor snapshot while routing trend candidate")
  bias, relationship_to_bias = _trend_bias_metadata(
    regime,
    trend_decision.direction,
  )
  payload = {
    "version": 5,
    "candidate_id": candidate_id,
    "group_id": group_id,
    "strategy_family": "trend",
    "zone_id": (
      f"{trend_decision.key_level:.5f}:"
      f"{trend_decision.entry_zone[0]:.5f}:"
      f"{trend_decision.entry_zone[1]:.5f}"
    ),
    "trigger_id": trigger_ts,
    "parent_group_id": parent_group_id,
    "structural_source": trend_decision.mode,
    "symbol": symbol.upper(),
    "timeframe": EXECUTION_TIMEFRAME,
    "setup": _TREND_SETUP_LABELS[trend_decision.mode],
    "mode": _TREND_MODE_LABELS[trend_decision.mode],
    "direction": trend_decision.direction.upper(),
    "trigger_ts": trigger_ts,
    "created_at": now,
    "spot_ts": spot.ts,
    "current_price": spot.price,
    "key_level": trend_decision.key_level,
    "entry_zone": {
      "low": trend_decision.entry_zone[0],
      "high": trend_decision.entry_zone[1],
    },
    "confluence": trend_decision.confluence,
    "reasons": list(trend_decision.reasons),
    "atr": trend_decision.atr,
    "structure_swing": trend_decision.structure_swing,
    "targets_pips": list(published_targets),
    "target_model": published_target_model,
    "target_reference_price": published_target_reference,
    "absolute_target_price": published_absolute_target,
    "target_price": published_absolute_target,
    "tier": trend_tier,
    "risk_multiplier": trend_policy.measured[
      "effective_risk_multiplier"
    ],
    "order_type_preference": (
      trend_policy.policy.order_type_preference
      if trend_policy.policy is not None else "either"
    ),
    "entry_distribution": trend_policy.measured.get(
      "entry_distribution", "single",
    ),
    **_stop_contract_fields(trend_policy.measured),
    "regime": regime.state,
    "bias": bias,
    "relationship_to_bias": relationship_to_bias,
    "opposing_zone_low": None if opposing_zone is None else opposing_zone.low,
    "opposing_zone_high": None if opposing_zone is None else opposing_zone.high,
    "opposing_zone_id": (
      None if opposing_zone is None else _opposing_zone_identity(
        opposing_zone,
        symbol=symbol,
        timeframe=EXECUTION_TIMEFRAME,
      )
    ),
    "add_zone_side": None if opposing_zone is None else opposing_zone.side,
  }
  if scale_context is not None:
    payload.update({
      "displacement_direction": scale_context.displacement_direction,
      "displacement_age_bars": scale_context.displacement_age_bars,
      "bos_direction": scale_context.bos_direction,
      "bos_ts": scale_context.bos_ts,
      "counter_bos_ts": scale_context.counter_bos_ts,
      "extreme_price": scale_context.extreme_price,
      "extreme_ts": scale_context.extreme_ts,
      "rejection_confirmed": scale_context.rejection_confirmed,
    })
  publish_result = await publish_candidate_atomic(
    client,
    stream=runtime_config.contract.streams.candidates,
    candidate_id=candidate_id,
    payload=json.dumps(payload, separators=(",", ":")),
    ttl=runtime_config.lifecycle.candidate.storage_ttl_seconds,
    maxlen=runtime_config.contract.streams.candidate_maximum_length,
    ownership_key=(
      autonomous_cycle_owner_key(symbol, trigger_ts)
      if parent_group_id is None else None
    ),
    ownership_payload=candidate_id,
    ownership_ttl=runtime_config.lifecycle.candidate.storage_ttl_seconds,
    allow_non_atomic_test_fallback=explicit_test_fallback_enabled(client),
  )
  published, executor_event_id = publish_result
  if not published:
    await _record_private_route(
      client,
      symbol=symbol,
      event_ts=event_ts,
      strategy=trend_setup,
      family="trend",
      direction=trend_decision.direction,
      source="private_trend",
      structural_id=group_id,
      entry_low=trend_decision.entry_zone[0],
      entry_high=trend_decision.entry_zone[1],
      spot_price=spot.price,
      status=(
        "blocked"
        if publish_result.status == "atomic_publish_unavailable"
        else "duplicate_suppressed"
      ),
      reason_code=publish_result.status,
      message=f"private trend publication failed: {publish_result.status}",
      group_id=group_id,
      retained=True,
      stage=(
        "stream_publish"
        if publish_result.status == "atomic_publish_unavailable"
        else "candidate_claim"
      ),
      publication_reason_code=publish_result.status,
    )
    return None
  await _record_private_route(
    client,
    symbol=symbol,
    event_ts=event_ts,
    strategy=trend_setup,
    family="trend",
    direction=trend_decision.direction,
    source="private_trend",
    structural_id=group_id,
    entry_low=trend_decision.entry_zone[0],
    entry_high=trend_decision.entry_zone[1],
    spot_price=spot.price,
    status="candidate_published",
    reason_code="candidate_published",
    message="private trend candidate published atomically",
    candidate_id=candidate_id,
    group_id=group_id,
    retained=False,
    stage="stream_publish",
    publication_reason_code="candidate_published",
    executor_event_id=executor_event_id,
  )
  await increment_metric(client, "candidate_published", symbol=symbol)
  await emit_lifecycle(
    client,
    "candidate_published",
    symbol=symbol,
    candidate_id=candidate_id,
    group_id=payload["group_id"],
    strategy=payload["setup"],
    strategy_family="trend",
    direction=trend_decision.direction,
    timeframe=EXECUTION_TIMEFRAME,
    entry_zone=payload["entry_zone"],
    current_price=spot.price,
    target_plan=list(published_targets),
    message="private trend candidate published to executor",
    publish_status=True,
  )
  log.info(
    "auto-trend candidate published id=%s symbol=%s mode=%s direction=%s",
    candidate_id[:12],
    symbol,
    trend_decision.mode,
    trend_decision.direction,
  )
  return candidate_id


def _status_payload(
  decision: AutoScalpDecision,
  *,
  symbol: str,
  event_ts: str,
  frames: dict[str, Any],
  spot: AutoTradeSpot | None,
  candidate_id: str | None,
  regime: RegimeInfo | None = None,
  trend_decision: TrendDecision | None = None,
  gate_source: str = "private_ohlc",
  strategy_match: StrategyMatch | None = None,
  market_map_decision: MarketMapStrategyDecision | None = None,
  breakout_retest: dict[str, Any] | None = None,
  resolved_range: RangeContext | None = None,
  box_eligibility: RangeExecutionEligibility | None = None,
  box_candidate_id: str | None = None,
) -> dict[str, Any]:
  rail = decision.rail
  target = decision.target
  box = decision.box
  trend_routed = (
    gate_source in {"private_ohlc", "private_trend"}
    and trend_decision is not None
    and trend_decision.state == "candidate"
    and (
      decision.state != "candidate"
      or (regime is not None and regime.state != "chop")
      or (
        box_eligibility is not None
        and not box_eligibility.eligible
      )
      or trend_decision.confluence > decision.confluence
    )
  )
  state = decision.state
  direction = decision.direction
  reasons = decision.reasons
  if (
    breakout_retest
    and str(breakout_retest.get("state") or "") == "waiting"
    and strategy_match is None
    and candidate_id is None
  ):
    state = "breakout_retest_waiting"
    direction = str(breakout_retest.get("direction") or direction or "")
    zone_low = breakout_retest.get("zone_low")
    zone_high = breakout_retest.get("zone_high")
    reasons = (
      (
        f"breakout retest waiting at {float(zone_low):.5f}-{float(zone_high):.5f}",
      )
      if zone_low is not None and zone_high is not None
      else ("breakout retest waiting",)
    )
  elif strategy_match is not None:
    state = "candidate" if candidate_id is not None else "strategy_match_waiting"
    direction = strategy_match.direction
    reasons = strategy_match.reasons
  elif trend_routed and trend_decision is not None:
    state = (
      trend_decision.state
      if runtime_config.strategies.trend.enabled
      else "trend_disabled"
    )
    direction = trend_decision.direction
  elif (
    market_map_decision is not None
    and market_map_decision.state != "candidate"
    and decision.state != "candidate"
    and decision.state != "box_broken"
  ):
    state = market_map_decision.state
    reasons = market_map_decision.reasons
  selected_strategy = None
  selected_timeframe = None
  if strategy_match is not None and candidate_id is not None:
    selected_strategy = strategy_match.strategy
    selected_timeframe = strategy_match.source_tf
  elif trend_routed and trend_decision is not None and candidate_id is not None:
    selected_strategy = _TREND_SETUP_LABELS.get(
      trend_decision.mode or "",
      "Trend Strategy",
    )
    selected_timeframe = EXECUTION_TIMEFRAME
  elif box_candidate_id is not None:
    selected_strategy = "Range Box Scalp"
    selected_timeframe = EXECUTION_TIMEFRAME
  elif (
    box_eligibility is not None
    and box_eligibility.reason_code == "candidate_ready"
    and box_candidate_id is None
  ):
    selected_strategy = "Range Box publish failed"
    selected_timeframe = EXECUTION_TIMEFRAME
  elif box_eligibility is not None and not box_eligibility.eligible:
    if box_eligibility.reason_code in {
      "waiting_for_touch",
      "waiting_for_rejection",
    }:
      selected_strategy = (
        f"Range Box waiting · {box_eligibility.reason_code}"
      )
      selected_timeframe = EXECUTION_TIMEFRAME
    elif box_eligibility.has_current_private_box:
      selected_strategy = (
        f"Range Box ineligible · {box_eligibility.reason_code}"
      )
      selected_timeframe = EXECUTION_TIMEFRAME
  range_status = None
  if resolved_range is not None:
    range_status = (
      status_label_for_retired(resolved_range)
      if resolved_range.state == "retired"
      else f"{resolved_range.state}"
    )
  return {
    "state": state,
    "box_state": decision.state,
    "range_status": range_status,
    "symbol": symbol,
    "tf": EXECUTION_TIMEFRAME,
    "event_ts": event_ts,
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "trigger": decision.trigger,
    "direction": direction,
    "rail": None if rail is None else {
      "low": rail.low,
      "high": rail.high,
      "level": rail.level,
      "role": rail.role,
      "timeframes": list(rail.timeframes),
      "sources": list(rail.sources),
    },
    "target": None if target is None else {
      "low": target.low,
      "high": target.high,
      "level": target.level,
      "role": target.role,
    },
    "target_room_pips": decision.target_room_pips,
    "full_tp_pips": decision.full_tp_pips,
    "box": None if box is None else {
      "id": box.box_id,
      "low": box.lower.level,
      "high": box.upper.level,
      "width_pips": box.width_pips,
    },
    "rail_count": decision.rail_count,
    "spot_fresh": None if spot is None else spot.fresh,
    "candidate_id": candidate_id,
    "published": candidate_id is not None,
    "gate_source": gate_source,
    "market_map_state": (
      None if market_map_decision is None else market_map_decision.state
    ),
    "market_map_reasons": (
      [] if market_map_decision is None else list(market_map_decision.reasons)
    ),
    "market_map_entries_seen": (
      0 if market_map_decision is None else market_map_decision.entries_seen
    ),
    "market_map_entries_actionable": (
      0
      if market_map_decision is None
      else len(market_map_decision.actionable_entries)
    ),
    "market_map_top": (
      []
      if market_map_decision is None
      else [
        {
          **entry.payload(),
          "distance": entry.distance,
        }
        for entry in market_map_decision.actionable_entries[:3]
      ]
    ),
    "market_map_filter_counts": (
      {}
      if market_map_decision is None
      else dict(market_map_decision.filter_counts)
    ),
    "market_map_track_limit": (
      None
      if market_map_decision is None
      else market_map_decision.track_limit
    ),
    "market_map_execute_limit": (
      None
      if market_map_decision is None
      else market_map_decision.execute_limit
    ),
    "market_map_id": (
      None if market_map_decision is None else market_map_decision.map_id
    ),
    "market_map_reaction": (
      None
      if market_map_decision is None
      or market_map_decision.reaction_type is None
      else {
        "touch_bar_ts": market_map_decision.touch_bar_ts,
        "confirmation_bar_ts": market_map_decision.confirmation_bar_ts,
        "reaction_age_bars": market_map_decision.reaction_age_bars,
        "reaction_type": market_map_decision.reaction_type,
      }
    ),
    "breakout_retest": breakout_retest,
    "selected_strategy": selected_strategy,
    "selected_timeframe": selected_timeframe,
    "selection_state": (
      "published"
      if candidate_id is not None
      else "matched_waiting_execution"
      if selected_strategy is not None
      else "no_match"
    ),
    "strategy_match": None if strategy_match is None else {
      "id": strategy_match.match_id,
      "strategy": strategy_match.strategy,
      "strategy_mode": strategy_match.strategy_mode,
      "direction": strategy_match.direction,
      "source_tf": strategy_match.source_tf,
      "event_ts": strategy_match.event_ts,
      "expires_at": strategy_match.expires_at,
    },
    "reasons": list(reasons),
    "frames": {
      timeframe: len(frame)
      for timeframe, frame in sorted(frames.items())
    },
    "regime": None if regime is None else regime.state,
    "regime_reasons": [] if regime is None else list(regime.reasons),
    "trend_state": None if trend_decision is None else trend_decision.state,
    "trend_mode": None if trend_decision is None else trend_decision.mode,
    "trend_reasons": (
      [] if trend_decision is None else list(trend_decision.reasons)
    ),
  }


def _box_retired_key(symbol: str, box_id: str) -> str:
  return f"auto_trade:box:retired:{symbol.upper()}:{box_id}"


def _box_edge_key(symbol: str, box_id: str, direction: str) -> str:
  return (
    f"auto_trade:box:edge:{symbol.upper()}:{box_id}:"
    f"{direction.upper()}"
  )


async def _rearm_scanner_range_edges(
  client: Any,
  symbol: str,
  spot: AutoTradeSpot | None,
) -> None:
  """Re-arm a scanner range side after price crosses the stored box EQ."""
  if spot is None or not spot.fresh:
    return
  pattern = f"auto_trade:box:edge:{symbol.upper()}:*"
  async for raw_key in client.scan_iter(match=pattern):
    key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
    raw_value = await client.get(key)
    try:
      payload = json.loads(
        raw_value.decode() if isinstance(raw_value, bytes) else str(raw_value)
      )
      if payload.get("source") != "scanner_strategy_match":
        continue
      direction = str(payload["direction"]).upper()
      midpoint = float(payload["midpoint"])
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
      continue
    crossed = (
      spot.price >= midpoint if direction == "BUY" else spot.price <= midpoint
    )
    range_id = key.rsplit(":", 2)[-2]
    owned = (
      direction in {"BUY", "SELL"}
      and await _range_side_has_active_ownership(
        client,
        symbol=symbol,
        range_id=range_id,
        direction=direction,
      )
    )
    if direction in {"BUY", "SELL"} and crossed and not owned:
      await client.delete(key)


async def _apply_box_retirement(
  client: Any,
  symbol: str,
  decision: AutoScalpDecision,
  price: float | None = None,
) -> AutoScalpDecision:
  box = decision.box
  if box is None:
    return decision
  key = _box_retired_key(symbol, box.box_id)
  if price is not None and math.isfinite(price):
    midpoint = (box.lower.level + box.upper.level) / 2
    buy_owned = await _range_side_has_active_ownership(
      client,
      symbol=symbol,
      range_id=box.box_id,
      direction="BUY",
    )
    sell_owned = await _range_side_has_active_ownership(
      client,
      symbol=symbol,
      range_id=box.box_id,
      direction="SELL",
    )
    if price >= midpoint and not buy_owned:
      await client.delete(_box_edge_key(symbol, box.box_id, "BUY"))
    if price <= midpoint and not sell_owned:
      await client.delete(_box_edge_key(symbol, box.box_id, "SELL"))
  if decision.state == "box_broken":
    await client.set(
      key,
      "1",
      ex=max(300, runtime_config.lifecycle.range_box.retirement_seconds),
    )
    return decision
  if decision.state == "candidate" and await client.exists(key):
    return replace(
      decision,
      state="box_retired",
      reasons=(*decision.reasons, "box already retired after breakout"),
    )
  if (
    decision.state == "candidate"
    and decision.direction is not None
    and await _range_side_has_active_ownership(
      client,
      symbol=symbol,
      range_id=box.box_id,
      direction=decision.direction,
    )
  ):
    return replace(
      decision,
      state="edge_owned",
      reasons=(
        *decision.reasons,
        "range side already owned by candidate/order/position",
      ),
    )
  if (
    decision.state == "candidate"
    and decision.direction is not None
    and await client.exists(_box_edge_key(
      symbol,
      box.box_id,
      decision.direction,
    ))
  ):
    return replace(
      decision,
      state="edge_disarmed",
      reasons=(*decision.reasons, "edge waits for a midpoint reset"),
    )
  return decision


@dataclass(frozen=True)
class _AdmissionFailure:
  """Terse admission-time verdict for a scanner or private strategy intent."""

  reason_code: str
  terminal: bool
  message: str
  stage: str = "mode_check"
  measured: dict[str, Any] = field(default_factory=dict)


async def _admit_strategy_intent_for_cycle(
  client: Any,
  intent: ExecutionIntent,
  match: StrategyMatch,
  *,
  spot: AutoTradeSpot | None,
  regime: RegimeInfo,
  htf_zones: list[Zone],
  htf_levels: list[Level],
) -> _AdmissionFailure | None:
  """Admit or reject a StrategyMatch intent before cross-engine arbitration.

  Returns ``None`` when the intent should enter arbitration (including the
  case where TradePlan already published a plan for it — TradePlan reconciles that itself).
  A returned _AdmissionFailure records why the intent must be filtered out;
  TradePlan's own hard gates (HTF veto, overlap, news, zone-split, limit-side,
  exposure) still run afterward if the intent is admitted and wins.
  """
  existing = await resolve_existing_v8_state(
    client,
    match,
    cycle_id=intent.cycle_id,
  )
  if existing.already_terminal:
    return _AdmissionFailure(
      reason_code=(
        existing.plan_state
        or existing.setup_state
        or "existing_v8_terminal"
      ),
      terminal=True,
      message="durable TradePlan lifecycle is already terminal",
      stage="publication_reconciliation",
      measured={"plan_id": existing.plan_id},
    )
  if existing.already_published:
    # the TradePlan runtime will reconcile the existing plan when it runs; admit as-is.
    return None
  if not runtime_config.runtime.auto_trade.enabled:
    return _AdmissionFailure(
      reason_code="auto_trade_disabled",
      terminal=True,
      message="autonomous execution is disabled",
    )
  if not runtime_config.runtime.auto_trade.strategy_match_enabled:
    return _AdmissionFailure(
      reason_code="strategy_match_disabled",
      terminal=True,
      message="StrategyMatch routing is disabled",
    )
  if not _strategy_mode_enabled(match):
    return _AdmissionFailure(
      reason_code="strategy_disabled",
      terminal=True,
      message=f"{match.strategy} execution is disabled",
    )
  if match.symbol != intent.symbol.upper():
    return _AdmissionFailure(
      reason_code="symbol_mismatch",
      terminal=True,
      message="intent symbol does not match worker symbol",
    )
  if intent.source == "scanner_strategy_match":
    eligibility = match.execution_eligibility
    if eligibility is None:
      return _AdmissionFailure(
        reason_code="static_eligibility_missing",
        terminal=True,
        message="scanner match has no authoritative static eligibility",
        stage="static_eligibility",
      )
    if not eligibility.allowed:
      return _AdmissionFailure(
        reason_code="static_eligibility_contract_violation",
        terminal=True,
        message="analysis-only scanner result reached the executable store",
        stage="static_eligibility",
        measured={
          "scanner_reason_code": eligibility.reason_code,
          "market_map_id": eligibility.market_map_id,
        },
      )
  if match.confluence < max(1, runtime_config.actionability.gates.min_confluence):
    return _AdmissionFailure(
      reason_code="confluence_below_minimum",
      terminal=True,
      message="strategy confluence is below the global minimum",
      measured={"confluence": match.confluence},
    )
  if match.is_range_edge:
    if regime.state != "chop":
      return _AdmissionFailure(
        reason_code="range_edge_not_chop",
        terminal=True,
        message=f"Range Edge requires chop; regime={regime.state}",
        stage="range_context",
      )
    scanner_context = RangeContext.from_json(
      await client.get(range_context_source_key(intent.symbol, "scanner"))
    )
    now = int(datetime.now(timezone.utc).timestamp())
    if (
      scanner_context is None
      or not is_range_context_current(
        scanner_context,
        now=now,
        max_age_seconds=SCANNER_SOURCE_MAX_AGE_SECONDS,
      )
      or scanner_context.state not in ACTIVE_RANGE_STATES
      or not range_geometry_matches_match(
        scanner_context,
        range_id=match.range_id,
        range_low=match.range_low,
        range_high=match.range_high,
      )
    ):
      return _AdmissionFailure(
        reason_code=(
          "range_context_withdrawn"
          if scanner_context is None else "scanner_range_stale"
        ),
        terminal=scanner_context is None,
        message="Range Edge requires its current scanner range episode",
        stage="range_context",
      )
  if spot is not None and spot.fresh:
    _, counter_bias = _adapt_counter_bias_target(
      match,
      spot.price,
      htf_zones,
      htf_levels,
      units.pip_size(intent.symbol),
    )
    if counter_bias.hard_block:
      return _AdmissionFailure(
        reason_code=counter_bias.reason_code,
        terminal=True,
        message=counter_bias.message,
        stage="counter_bias",
        measured=dict(counter_bias.measured or {}),
      )
  return None


def _arbitration_followup(
  intent: ExecutionIntent,
  *,
  arbitration: ArbitrationResult,
  published_intent: ExecutionIntent | None,
  ordered_ids: set[str],
  attempted_intent_ids: set[str],
) -> tuple[str, str, str] | None:
  """Return only the arbitration evidence not already owned by a publisher.

  An attempted publisher records its exact final claim/stream result itself.
  Returning a generic follow-up for it would overwrite the material failure
  that the operator needs to diagnose.
  """
  if intent.intent_id in attempted_intent_ids:
    return None
  suppressed = intent.intent_id not in ordered_ids
  reason_code = (
    arbitration.reason_code
    if not arbitration.ordered
    else "another_intent_won"
    if published_intent is not None
    else "selected_direction_exhausted"
    if suppressed
    else "publication_attempt_failed"
  )
  status = "arbitration_suppressed" if suppressed else "waiting"
  message = (
    "intent excluded by cross-engine direction arbitration"
    if suppressed
    else "intent did not obtain final atomic publication ownership"
  )
  return status, reason_code, message


_WAITING_RETEST_PUBLICATION_REASONS = frozenset({
  "waiting_retest_entry_zone",
  "waiting_retest",
  "reaction_confirmation_handoff",
  "waiting_m1_retest",
  "trigger_price_left_zone",
  "stale_m1_trigger_ignored",
  "entry_contract_satisfied",
})


async def _strategy_publication_result(
  client: Any,
  match: StrategyMatch,
  candidate_id: str | None,
) -> CandidatePublicationResult:
  """Translate the legacy publisher return into a fallback-safe result."""
  if candidate_id is not None:
    return CandidatePublicationResult.published(candidate_id)
  existing = await resolve_existing_v8_state(client, match)
  if existing.already_terminal:
    return CandidatePublicationResult.terminal_reject(
      existing.plan_state
      or existing.setup_state
      or "existing_v8_terminal"
    )
  if existing.already_published:
    return CandidatePublicationResult.published(existing.plan_id)
  raw = await client.get(route_outcome_key(match.symbol, match.match_id))
  try:
    snapshot = json.loads(
      raw.decode() if isinstance(raw, bytes) else str(raw)
    ) if raw else {}
  except (TypeError, ValueError, json.JSONDecodeError):
    snapshot = {}
  status = str(snapshot.get("status") or "")
  reason = str(snapshot.get("reason_code") or "publication_unavailable")
  retained = snapshot.get("retained")
  if status in {"blocked", "expired"} and retained is False:
    return CandidatePublicationResult.terminal_reject(reason)
  if reason == "duplicate_candidate":
    return CandidatePublicationResult.blocked("duplicate_candidate", reason)
  if reason == "duplicate_reaction":
    return CandidatePublicationResult.blocked("duplicate_reaction", reason)
  if reason.startswith("duplicate_thesis") or reason == "same_thesis_group_active":
    return CandidatePublicationResult.blocked("duplicate_thesis", reason)
  if reason in {"cycle_conflict", "conflict"}:
    return CandidatePublicationResult.blocked("cycle_conflict", reason)
  # publish returned None because the reaction is still waiting for its retest.
  # The old preflight kept this outside arbitration precisely so it wouldn't
  # suppress executable lower-ranked intents; TradePlan-cutover surfaces the same
  # semantics as a terminal reject so ranked fallback can still publish.
  if (
    status == "waiting"
    and retained is not False
    and reason in _WAITING_RETEST_PUBLICATION_REASONS
  ):
    return CandidatePublicationResult.terminal_reject(reason)
  return CandidatePublicationResult.blocked(
    "publication_unavailable", reason,
  )


async def _group_is_active(
  client: Any,
  symbol: str,
  group_id: str | None,
) -> bool:
  if not group_id:
    return False
  raw = await client.get(f"auto_trade:executor_snapshot:{symbol.upper()}")
  if not raw:
    return False
  try:
    snapshot = json.loads(
      raw.decode() if isinstance(raw, bytes) else str(raw)
    )
  except (TypeError, ValueError, json.JSONDecodeError):
    return False
  tokens = {str(item) for item in snapshot.get("group_ids") or []}
  return group_id in tokens or group_id[:10] in tokens


def _normalize_trade_direction(value: object) -> str | None:
  if value is None:
    return None
  if isinstance(value, int):
    if value == 0:
      return "BUY"
    if value == 1:
      return "SELL"
    return None
  text = str(value).strip().upper()
  if text in {"BUY", "B", "0"}:
    return "BUY"
  if text in {"SELL", "S", "1"}:
    return "SELL"
  return None


async def _load_tracked_position_states(client: Any) -> list[dict[str, Any]]:
  raw_ids = await client.smembers("auto_trade:positions")
  if not raw_ids:
    return []
  positions: list[dict[str, Any]] = []
  for raw_id in raw_ids:
    token = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
    try:
      position_id = int(token)
    except (TypeError, ValueError):
      continue
    raw = await client.get(f"auto_trade:position:{position_id}")
    if not raw:
      continue
    try:
      payload = json.loads(
        raw.decode() if isinstance(raw, bytes) else str(raw)
      )
    except (TypeError, ValueError, json.JSONDecodeError):
      continue
    if isinstance(payload, dict):
      positions.append(payload)
  return positions


async def _active_opposite_initial_group(
  client: Any,
  *,
  direction: str,
  symbol: str | None = None,
) -> dict[str, Any] | None:
  """Mirror the C# executor guard for opposite autonomous initial groups."""
  from app.autotrade.active_exposure import normalize_symbol

  wanted = str(direction or "").upper()
  if wanted not in {"BUY", "SELL"}:
    return None
  opposite = "SELL" if wanted == "BUY" else "BUY"
  wanted_symbol = normalize_symbol(symbol)
  for payload in await _load_tracked_position_states(client):
    if str(payload.get("parent_group_id") or "").strip():
      continue
    if wanted_symbol is not None:
      payload_symbol = normalize_symbol(
        payload.get("symbol") or payload.get("Symbol")
      )
      if payload_symbol is None or payload_symbol != wanted_symbol:
        continue
    remaining = payload.get("remaining_volume")
    if remaining is not None:
      try:
        if int(remaining) <= 0:
          continue
      except (TypeError, ValueError):
        pass
    if _normalize_trade_direction(payload.get("direction")) == opposite:
      return payload
  return None


def _ohlc_frame(frames: dict[str, Any], *keys: str):
  """Pick an OHLC frame without bool-coercing pandas objects.

  ``df or fallback`` raises ValueError on a live DataFrame (prod 2026-08-17
  mapped thesis rearm: ``frames.get("M1") or frames.get("M1")``).
  """
  for key in keys:
    frame = frames.get(key)
    if frame is not None:
      return frame
  return None


async def _advance_mapped_thesis_rearms_from_frames(
  client: Any,
  *,
  symbol: str,
  frames: dict[str, Any],
) -> None:
  try:
    from app.analysis.indicators import atr as atr_indicator
    m1 = _ohlc_frame(frames, EXECUTION_TIMEFRAME, "M1")
    if m1 is None or len(m1) < 15:
      return
    atr_series = atr_indicator(m1, int(runtime_config.analysis.atr.length))
    atr_for_rearm = float(atr_series.iloc[-1])
    if math.isfinite(atr_for_rearm) and atr_for_rearm > 0:
      await _advance_mapped_thesis_rearms(
        client,
        symbol=symbol,
        m1=m1,
        atr=atr_for_rearm,
      )
  except Exception:
    log.exception("mapped thesis rearm advance failed symbol=%s", symbol)


async def _persist_idle_last_gate(
  client: Any,
  *,
  symbol: str,
  event_ts: str,
  spot: AutoTradeSpot | None,
) -> None:
  payload = {
    "state": "idle_no_match",
    "box_state": "idle_no_match",
    "symbol": symbol.upper(),
    "tf": EXECUTION_TIMEFRAME,
    "event_ts": event_ts,
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "gate_source": "idle_no_match",
    "published": False,
    "candidate_id": None,
    "tracked_strategy_matches": [],
    "published_candidate_ids": [],
    "published_candidate": None,
    "selected_strategy": None,
    "selected_timeframe": None,
    "selection_state": "no_match",
    "reasons": ["no leftover StrategyMatch"],
    "spot_fresh": None if spot is None else spot.fresh,
    "arbitration": {
      "reason_code": "no_intent",
      "intent_count": 0,
      "arbitrable_intent_ids": [],
      "ordered_intent_ids": [],
      "suppressed_intent_ids": [],
      "winner_intent_id": None,
    },
  }
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set(
    "auto_trade:last_gate",
    encoded,
    ex=WORKER_SNAPSHOT_TTL_SECONDS,
  )
  await client.set(
    f"auto_trade:last_gate:{symbol.upper()}",
    encoded,
    ex=WORKER_SNAPSHOT_TTL_SECONDS,
  )


async def _handle_event(
  data: object,
  *,
  source: RedisOHLCSource | None = None,
  client: Any | None = None,
  ready_match_id: str | None = None,
) -> AutoScalpDecision | None:
  parsed = _parse_bar_event(data)
  if parsed is None:
    return None
  symbol, timeframe, event_ts = parsed
  if timeframe != EXECUTION_TIMEFRAME or symbol not in _symbols():
    return None

  client = client or redis_state.get_client()
  source = source or RedisOHLCSource(client)
  spot = await _load_spot(client, symbol)
  scanner_strategy_matches = await _load_strategy_matches(client, symbol)
  if ready_match_id is not None:
    scanner_strategy_matches = [
      item for item in scanner_strategy_matches
      if item.match_id == ready_match_id
    ]
  if not scanner_strategy_matches:
    frames = await _load_frames(
      source, symbol, timeframes=(EXECUTION_TIMEFRAME,),
    )
    await _rearm_scanner_range_edges(client, symbol, spot)
    await _advance_mapped_thesis_rearms_from_frames(
      client, symbol=symbol, frames=frames,
    )
    await _persist_idle_last_gate(
      client, symbol=symbol, event_ts=event_ts, spot=spot,
    )
    return None

  frames = await _load_frames(source, symbol)
  await _rearm_scanner_range_edges(client, symbol, spot)
  await _advance_mapped_thesis_rearms_from_frames(
    client, symbol=symbol, frames=frames,
  )
  private_decision = evaluate_auto_scalp_gate(
    frames,
    symbol=symbol,
    spot_price=None if spot is None or not spot.fresh else spot.price,
  )
  private_decision, resolved_range, range_comparison = await _resolve_worker_range(
    client,
    symbol=symbol,
    frames=frames,
    private_decision=private_decision,
    spot=spot,
  )
  cached_market_map = decode_market_map(
    await client.get(market_map_key(symbol))
  )
  guard_market_map = (
    cached_market_map
    if runtime_config.actionability.gates.market_map_guard_enabled
    else None
  )
  displayed_market_map = decode_market_map(
    await client.get(market_map_display_key(symbol))
  )
  strategy_cfg = instrument_runtime_view(symbol)
  market_map_decision = evaluate_market_map_strategy(
    frames,
    symbol=symbol,
    event_ts=event_ts,
    spot_price=(
      spot.price if spot is not None and spot.fresh else None
    ),
    cfg=strategy_cfg,
    market_map=cached_market_map,
    rendered_map=displayed_market_map,
  )
  await _record_market_map_strategy_telemetry(
    client,
    symbol,
    market_map_decision,
  )
  strategy_matches = list(scanner_strategy_matches)
  if ready_match_id is None and market_map_decision.match is not None:
    strategy_matches.append(market_map_decision.match)
  if runtime_config.strategies.matching.multiple_matches_enabled and strategy_matches:
    strategy_matches, _ = dedupe_matches(
      strategy_matches,
      atr=strategy_matches[0].atr,
      cfg=None,
    )
  elif strategy_matches:
    strategy_matches = [strategy_matches[0]]
  strategy_match = select_primary(strategy_matches)
  decision = private_decision
  observed_gate_source = (
    "multi_strategy_match"
    if len(strategy_matches) > 1
    else "scanner_strategy_match"
    if scanner_strategy_matches
    else "market_map_strategy"
    if market_map_decision.match is not None
    else "private_ohlc"
  )
  regime = classify_regime(
    frames,
    decision,
    strategy_cfg,
    symbol=symbol,
  )
  trend_decision = evaluate_trend_gate(
    frames,
    regime,
    decision,
    symbol=symbol,
    spot_price=None if spot is None or not spot.fresh else spot.price,
    cfg=strategy_cfg,
  )
  closed_price = (
    float(frames[EXECUTION_TIMEFRAME]["close"].iloc[-1])
    if EXECUTION_TIMEFRAME in frames
    else None
  )
  decision = await _apply_box_retirement(
    client,
    symbol,
    decision,
    closed_price,
  )
  box_eligibility = evaluate_range_box_eligibility(
    symbol=symbol,
    decision=decision,
    private_context=RangeContext.from_json(
      await client.get(range_context_source_key(symbol, "private"))
    ),
    resolved=resolved_range,
    regime_state=regime.state,
    now=int(datetime.now(timezone.utc).timestamp()),
    range_enabled=bool(runtime_config.strategies.range_reversion.enabled),
  )
  box_selected = box_eligibility.eligible
  if box_eligibility.eligible:
    await increment_metric(client, "range_box_eligible", symbol=symbol)
  else:
    await increment_metric(
      client,
      f"range_box_ineligible:{box_eligibility.reason_code}",
      symbol=symbol,
    )
    await increment_metric(client, "range_box_ineligible", symbol=symbol)
  # Private box/trend have no V8 publish path. Do not build scale context
  # for them on the autonomous cycle.
  spot_price = spot.price if spot is not None and spot.fresh else None
  box_intent_id = None
  trend_intent_id = None
  strategy_candidate_ids: list[str] = []
  box_candidate_id = None
  trend_candidate_id = None
  published_match: StrategyMatch | None = None
  published_intent: ExecutionIntent | None = None
  attempted_intent_ids: set[str] = set()
  intents: list[ExecutionIntent] = []
  intent_matches: dict[str, StrategyMatch] = {}
  intent_subjects: dict[str, Any] = {}
  arbitrable: list[ExecutionIntent] = []
  arbitration = arbitrate_execution_intents([])
  if strategy_matches:
    htf_zones = _htf_zones(frames, None, symbol=symbol)
    htf_levels = _htf_levels(frames, None, symbol=symbol)
    for routed_match in strategy_matches:
      intent_id = f"strategy:{routed_match.match_id}"
      intent_matches[intent_id] = routed_match
      group_id = _strategy_group_id(routed_match)
      intent = ExecutionIntent(
        intent_id=intent_id,
        source=(
          "market_map_strategy"
          if routed_match.strategy_mode == "mapped_zone_reaction"
          else "scanner_strategy_match"
        ),
        strategy=routed_match.strategy,
        direction=routed_match.direction,
        confluence=routed_match.confluence,
        tier=routed_match.tier,
        freshness=_intent_freshness(
          routed_match.confirmation_bar_ts or routed_match.event_ts,
          routed_match.issued_at,
        ),
        distance_pips=_band_distance_pips(
          spot_price,
          routed_match.entry_low,
          routed_match.entry_high,
          symbol,
        ),
        symbol=symbol.upper(),
        timeframe=routed_match.source_tf,
        family=routed_match.family,
        entry_low=routed_match.entry_low,
        entry_high=routed_match.entry_high,
        structural_id=str(
          routed_match.structural_zone_id
          or routed_match.zone_id
          or routed_match.level_id
          or routed_match.match_id
        ),
        match_id=routed_match.match_id,
        reaction_id=routed_match.reaction_id,
        thesis_id=routed_match.thesis_id,
        current_price=spot_price,
        target_model=routed_match.target_model,
        targets_pips=routed_match.targets_pips,
        absolute_target_price=(
          routed_match.absolute_target_price
          if routed_match.absolute_target_price is not None
          else routed_match.target_price
        ),
        target_reference_price=routed_match.target_reference_price,
        proposed_group_id=group_id,
        cycle_id=str(event_ts or ""),
      )
      intents.append(intent)
      intent_subjects[intent_id] = routed_match
    # The private M1 range gate (gate.py) is retired as an autonomous setup
    # source (H1->M15->M5 single-analysis-source cutover, P2) - M1 no longer
    # originates trade candidates on its own. evaluate_auto_scalp_gate/
    # evaluate_range_box_eligibility above are still called because `decision`
    # also feeds classify_regime's breakout classifier (a distinct, still-valid
    # concern) and box_eligibility still feeds status/telemetry payloads below;
    # box_intent_id staying permanently None is what guarantees this gate can
    # never construct an ExecutionIntent, so it emits no setups.
    box_intent_id = None
    trend_intent_id = None
    # Private trend has no TradePlan V8 path. Do not build intents or
    # record publication_unavailable private routes on leftover matches.

    arbitrable: list[ExecutionIntent] = []
    for intent in intents:
      routed_match = intent_matches.get(intent.intent_id)
      if routed_match is not None:
        failure = await _admit_strategy_intent_for_cycle(
          client,
          intent,
          routed_match,
          spot=spot,
          regime=regime,
          htf_zones=htf_zones,
          htf_levels=htf_levels,
        )
        if failure is None:
          arbitrable.append(intent)
          continue
        status = "blocked" if failure.terminal else "waiting"
        await record_route_outcome(
          client,
          routed_match,
          stage=failure.stage,  # type: ignore[arg-type]
          status=status,  # type: ignore[arg-type]
          reason_code=failure.reason_code,
          message=failure.message,
          measured=failure.measured,
          retained=not failure.terminal,
          signal_source=intent.source,
          publish_status=failure.terminal,
        )
        if failure.terminal:
          setup = await load_setup(client, routed_match.match_id)
          next_state = (
            None
            if setup is None
            else terminal_state_for_preflight_failure(setup.state)
          )
          if setup is not None and next_state is not None:
            try:
              await transition_setup(
                client,
                routed_match.match_id,
                next_state,
                reason_code=failure.reason_code,
              )
              await emit_lifecycle(
                client,
                next_state,
                symbol=routed_match.symbol,
                match_id=routed_match.match_id,
                correlation_id=routed_match.match_id,
                timeframe=routed_match.source_tf,
                reason_code=failure.reason_code,
                message=failure.message,
                publish_status=True,
              )
            except SetupLifecycleError:
              log.exception(
                "terminal admission lifecycle transition failed "
                "symbol=%s setup_id=%s reason=%s",
                symbol,
                routed_match.match_id,
                failure.reason_code,
              )
          await _consume_strategy_match(client, symbol, routed_match)
      else:
        # Private intents (range / trend) are recorded as unavailable and
        # kept out of arbitration; the V6 candidate path is retired and no
        # TradePlan equivalent publishes them.
        await _record_private_route(
          client,
          symbol=symbol,
          event_ts=event_ts,
          strategy=intent.strategy,
          family=intent.family,
          direction=intent.direction,
          source=intent.source,
          structural_id=intent.structural_id,
          entry_low=intent.entry_low,
          entry_high=intent.entry_high,
          spot_price=spot_price,
          status="blocked",
          reason_code="publication_unavailable",
          message="private strategy has no active TradePlan publication path",
          group_id=intent.proposed_group_id,
          retained=False,
          stage="publication",
          terminal_reason_code="publication_unavailable",
        )
    arbitration = arbitrate_execution_intents(
      arbitrable,
      conflict_margin=runtime_config.actionability.scanner_gates.conflict_margin,
    )

    strategy_candidate_ids: list[str] = []
    box_candidate_id = None
    trend_candidate_id = None
    published_match: StrategyMatch | None = None
    published_intent: ExecutionIntent | None = None
    attempted_intent_ids: set[str] = set()
    cycle_id = str(event_ts or "")

    async def publish_ranked_intent(
      intent: ExecutionIntent,
    ) -> CandidatePublicationResult:
      nonlocal box_candidate_id
      nonlocal trend_candidate_id
      nonlocal published_match
      nonlocal published_intent

      attempted_intent_ids.add(intent.intent_id)
      published = None
      publication_result: CandidatePublicationResult | None = None
      routed_match = intent_matches.get(intent.intent_id)
      if routed_match is not None:
        route_lock = (
          f"auto_trade:route_lock:{symbol.upper()}:{routed_match.match_id}"
        )
        route_lock_token = await acquire_owned_lock(
          client, route_lock, ttl=30,
        )
        if route_lock_token is None:
          await record_route_outcome(
            client,
            routed_match,
            stage="candidate_claim",
            status="waiting",
            reason_code="route_evaluation_in_progress",
            message="another worker is evaluating this exact match",
            retained=True,
            publish_status=False,
          )
          publication_result = CandidatePublicationResult.blocked(
            "route_in_progress",
          )
          return publication_result
        try:
          # TradePlan V8 is the sole autonomous order path, per
          # docs/adr-trade-plan-v8-cutover.md - the V6 candidate path is
          # removed entirely for autonomous publication (not gated behind a
          # mode) so a confirmed setup can never arm both a TradePlan and a V6
          # candidate for the same thesis. Existing open V6 positions are
          # untouched; this only blocks new autonomous publication.
          published = await _publish_trade_plan_v8(
            client,
            symbol,
            spot,
            routed_match,
            htf_zones=htf_zones,
            htf_levels=htf_levels,
            regime=regime,
            frames=frames,
            # Final structural geometry is a correctness boundary, not an
            # optional soft guard. Use the canonical cached Market Map even
            # when the legacy guard toggle is disabled.
            market_map=cached_market_map,
          )
        finally:
          await release_owned_lock(client, route_lock, route_lock_token)
        if published is not None:
          strategy_candidate_ids.append(published)
          published_match = routed_match
          await record_route_outcome(
            client,
            routed_match,
            stage="stream_publish",
            status="candidate_published",
            reason_code="candidate_published",
            message="selected strategy candidate published atomically",
            candidate_id=published,
            group_id=intent.proposed_group_id,
            retained=False,
            arbitration_reason_code="selected_for_publication",
            publication_reason_code="candidate_published",
            winner_intent_id=intent.intent_id,
            signal_source=intent.source,
            publish_status=False,
          )
        publication_result = await _strategy_publication_result(
          client, routed_match, published,
        )
      elif intent.intent_id == box_intent_id:
        # Private M1 range gate is a V6-only autonomous detector (Section A/L
        # of the TradePlan cutover) - it never feeds scanner.py's setup lifecycle, so
        # it has no TradePlan equivalent and must not publish new autonomous
        # candidates now that TradePlan V8 is the sole autonomous path.
        published = None
        box_candidate_id = published
        publication_result = (
          CandidatePublicationResult.published(published)
          if published is not None
          else CandidatePublicationResult.blocked("publication_unavailable")
        )
      elif intent.intent_id == trend_intent_id:
        # Private trend detector (trend.py) is likewise V6-only autonomous
        # analysis, parallel to (not fed by) scanner.py - see Section A/L. It
        # must not publish new autonomous candidates now that TradePlan V8 is the sole
        # autonomous path.
        published = None
        trend_candidate_id = published
        publication_result = (
          CandidatePublicationResult.published(published)
          if published is not None
          else CandidatePublicationResult.blocked("publication_unavailable")
        )
      if publication_result is None:
        publication_result = CandidatePublicationResult.blocked(
          "publication_unavailable",
        )
      if publication_result.candidate_id is not None:
        published_intent = intent
      return publication_result

    cycle_publication_result: CandidatePublicationResult | None = None
    if arbitration.ordered:
      cycle_publication_result = await publish_ranked_cycle(
        client,
        symbol=symbol,
        cycle_id=cycle_id,
        ordered=arbitration.ordered,
        publisher=publish_ranked_intent,
      )
      if (
        not attempted_intent_ids
        and cycle_publication_result.status
          in {"route_in_progress", "cycle_conflict"}
      ):
        top = arbitration.ordered[0]
        attempted_intent_ids.add(top.intent_id)
        routed_top = intent_matches.get(top.intent_id)
        status = (
          "waiting"
          if cycle_publication_result.status == "route_in_progress"
          else "duplicate_suppressed"
        )
        reason_code = (
          "route_evaluation_in_progress"
          if cycle_publication_result.status == "route_in_progress"
          else "cycle_conflict"
        )
        message = (
          "another worker owns publication arbitration for this cycle"
          if cycle_publication_result.status == "route_in_progress"
          else "this closed-bar cycle already has a publication owner"
        )
        existing_cycle_owner = await client.get(
          autonomous_cycle_owner_key(symbol, cycle_id)
        )
        winner_intent_id = parse_cycle_owner_intent_id(existing_cycle_owner)
        if routed_top is not None:
          await record_route_outcome(
            client,
            routed_top,
            stage="candidate_claim",
            status=status,  # type: ignore[arg-type]
            reason_code=reason_code,
            message=message,
            retained=True,
            winner_intent_id=winner_intent_id,
            publish_status=False,
          )
        else:
          await _record_private_route(
            client,
            symbol=symbol,
            event_ts=event_ts,
            strategy=top.strategy,
            family=top.family,
            direction=top.direction,
            source=top.source,
            structural_id=top.structural_id,
            entry_low=top.entry_low,
            entry_high=top.entry_high,
            spot_price=spot_price,
            status=status,
            reason_code=reason_code,
            message=message,
            group_id=top.proposed_group_id,
            retained=True,
            stage="candidate_claim",
            arbitration_reason_code=reason_code,
            winner_intent_id=winner_intent_id,
          )
    arbitrable_ids = {item.intent_id for item in arbitrable}
    ordered_ids = {item.intent_id for item in arbitration.ordered}
    for intent in intents:
      if (
        intent.intent_id not in arbitrable_ids
        or (
          published_intent is not None
          and intent.intent_id == published_intent.intent_id
        )
        or intent.intent_id in attempted_intent_ids
      ):
        continue
      followup = _arbitration_followup(
        intent,
        arbitration=arbitration,
        published_intent=published_intent,
        ordered_ids=ordered_ids,
        attempted_intent_ids=attempted_intent_ids,
      )
      if followup is None:
        continue
      status, reason_code, message = followup
      routed_match = intent_matches.get(intent.intent_id)
      if routed_match is not None:
        await record_route_outcome(
          client,
          routed_match,
          stage="arbitration",
          status=status,  # type: ignore[arg-type]
          reason_code=reason_code,
          message=message,
          retained=True,
          arbitration_reason_code=reason_code,
          winner_intent_id=(
            None if published_intent is None else published_intent.intent_id
          ),
          signal_source=intent.source,
          publish_status=False,
        )
      else:
        await _record_private_route(
          client,
          symbol=symbol,
          event_ts=event_ts,
          strategy=intent.strategy,
          family=intent.family,
          direction=intent.direction,
          source=intent.source,
          structural_id=intent.structural_id,
          entry_low=intent.entry_low,
          entry_high=intent.entry_high,
          spot_price=spot_price,
          status=status,
          reason_code=reason_code,
          message=message,
          group_id=intent.proposed_group_id,
          retained=True,
          stage="arbitration",
          arbitration_reason_code=reason_code,
          winner_intent_id=(
            None if published_intent is None else published_intent.intent_id
          ),
        )
    if (
      box_candidate_id is not None
      and box_intent_id is not None
      and decision.box is not None
      and decision.rail is not None
      and decision.direction is not None
      and (decision.state == "candidate" or box_eligibility.eligible)
    ):
      await _record_private_route(
        client,
        symbol=symbol,
        event_ts=event_ts,
        strategy="Range Box Scalp",
        family="range",
        direction=decision.direction,
        source="private_range",
        structural_id=decision.box.box_id,
        entry_low=decision.rail.low,
        entry_high=decision.rail.high,
        spot_price=spot_price,
        status="candidate_published",
        reason_code="candidate_published",
        message="private range candidate published",
        candidate_id=box_candidate_id,
        group_id=(
          None
          if box_candidate_id is None
          else _group_id(
            symbol,
            "range",
            decision.box.box_id,
            decision.direction,
          )
        ),
        retained=False,
        stage="stream_publish",
        arbitration_reason_code="selected_for_publication",
        publication_reason_code="candidate_published",
        winner_intent_id=box_intent_id,
      )
    if (
      trend_candidate_id is not None
      and trend_intent_id is not None
      and trend_decision.state == "candidate"
      and trend_decision.direction is not None
      and trend_decision.entry_zone is not None
      and trend_decision.mode is not None
    ):
      await _record_private_route(
        client,
        symbol=symbol,
        event_ts=event_ts,
        strategy=_TREND_SETUP_LABELS[trend_decision.mode],
        family="trend",
        direction=trend_decision.direction,
        source="private_trend",
        structural_id=_trend_group_id(symbol, trend_decision),
        entry_low=trend_decision.entry_zone[0],
        entry_high=trend_decision.entry_zone[1],
        spot_price=spot_price,
        status="candidate_published",
        reason_code="candidate_published",
        message="private trend candidate published",
        candidate_id=trend_candidate_id,
        group_id=(
          None
          if trend_candidate_id is None
          else _trend_group_id(symbol, trend_decision)
        ),
        retained=False,
        stage="stream_publish",
        arbitration_reason_code="selected_for_publication",
        publication_reason_code="candidate_published",
        winner_intent_id=trend_intent_id,
      )
  if _has_overlapping_zones(cached_market_map):
    await client.incr(f"auto_trade:zone_overlap:{symbol.upper()}")
  candidate_ids = [
    *strategy_candidate_ids,
    *([box_candidate_id] if box_candidate_id is not None else []),
    *([trend_candidate_id] if trend_candidate_id is not None else []),
  ]
  candidate_id = candidate_ids[0] if candidate_ids else None
  gate_source = (
    published_intent.source
    if published_intent is not None
    else observed_gate_source
  )
  status_strategy_match = (
    published_match
    if candidate_id is not None
    else strategy_match
  )
  if candidate_id is None:
    if strategy_match is None and decision.state != "candidate":
      await _record_gate_reject(client, symbol, decision.state)
    if (
      strategy_match is None
      and trend_decision.state != "candidate"
    ):
      await _record_gate_reject(client, symbol, trend_decision.state)
  payload = _status_payload(
    decision,
    symbol=symbol,
    event_ts=event_ts,
    frames=frames,
    spot=spot,
    candidate_id=candidate_id,
    regime=regime,
    trend_decision=trend_decision,
    gate_source=gate_source,
    strategy_match=status_strategy_match,
    market_map_decision=market_map_decision,
    breakout_retest=await load_breakout_retest_watch(client, symbol),
    resolved_range=resolved_range,
    box_eligibility=box_eligibility,
    box_candidate_id=box_candidate_id,
  )
  payload["tracked_strategy_matches"] = [
    {
      "id": item.match_id,
      "strategy": item.strategy,
      "family": item.family,
      "direction": item.direction,
      "range_id": item.range_id,
    }
    for item in strategy_matches
  ]
  payload["published_candidate_ids"] = candidate_ids
  payload["published_candidate"] = (
    None
    if published_intent is None
    else {
      "winner_intent_id": published_intent.intent_id,
      "candidate_id": candidate_id,
      "source_strategy": published_intent.strategy,
      "signal_source": published_intent.source,
      "family": published_intent.family,
      "direction": published_intent.direction,
      "timeframe": published_intent.timeframe,
      "group_id": published_intent.proposed_group_id,
    }
  )
  payload["arbitration"] = {
    "reason_code": arbitration.reason_code,
    "intent_count": len(intents),
    "arbitrable_intent_ids": [item.intent_id for item in arbitrable],
    "ordered_intent_ids": [
      item.intent_id for item in arbitration.ordered
    ],
    "suppressed_intent_ids": [
      item.intent_id for item in arbitration.suppressed
    ],
    "winner_intent_id": (
      None if published_intent is None else published_intent.intent_id
    ),
  }
  payload["box_eligibility"] = asdict(box_eligibility)
  payload["box_candidate_id"] = box_candidate_id
  range_buy_side = (
    None
    if resolved_range is None
    else await _load_range_side_status(
      client,
      symbol=symbol,
      range_id=resolved_range.range_id,
      direction="BUY",
    )
  )
  range_sell_side = (
    None
    if resolved_range is None
    else await _load_range_side_status(
      client,
      symbol=symbol,
      range_id=resolved_range.range_id,
      direction="SELL",
    )
  )
  payload["resolved_range"] = (
    None
    if resolved_range is None
    else {
      "range_id": resolved_range.range_id,
      "state": resolved_range.state,
      "source": resolved_range.source,
      "lower": resolved_range.lower,
      "upper": resolved_range.upper,
      "equilibrium": resolved_range.equilibrium,
      "buy_rail": range_buy_side,
      "sell_rail": range_sell_side,
    }
  )
  payload["range_context_comparison"] = range_comparison
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set(
    "auto_trade:last_gate",
    encoded,
    ex=WORKER_SNAPSHOT_TTL_SECONDS,
  )
  await client.set(
    f"auto_trade:last_gate:{symbol}",
    encoded,
    ex=WORKER_SNAPSHOT_TTL_SECONDS,
  )
  log.info(
    "ApexVoid Algo cycle symbol=%s source=%s state=%s trigger=%s "
    "direction=%s candidate=%s observed_regime=%s",
    symbol,
    gate_source,
    payload["state"],
    decision.trigger or "-",
    payload["direction"] or "-",
    candidate_id[:12] if candidate_id else "-",
    regime.state,
  )
  return decision


PUBLISH_STATUS_EXECUTION_HANDOFF_CREATED = "execution_handoff_created"
# Compat alias — older call sites / tests still reference PUBLISHED.
PUBLISH_STATUS_PUBLISHED = PUBLISH_STATUS_EXECUTION_HANDOFF_CREATED
PUBLISH_STATUS_REMAINED_WATCHING = "remained_watching"
PUBLISH_STATUS_INVALIDATED = "invalidated"
PUBLISH_STATUS_REJECTED = "rejected"
PUBLISH_STATUS_DUPLICATE_RECONCILED = "duplicate_reconciled"


@dataclass(frozen=True)
class PublishResult:
  """Outcome of one deterministic try_publish_executable_signal() pass.

  ``status`` is one of the PUBLISH_STATUS_* constants above. ``measured``
  carries whatever telemetry the underlying evaluation produced (route
  outcome style); it is best-effort and may be empty when the setup never
  reached a stage that records measurements.
  """

  status: str
  plan_id: str
  reason_code: str
  zone_id: str
  setup_id: str
  measured: Mapping[str, Any] = field(default_factory=dict)
  executable_quote: float | None = None
  quote_side: str | None = None


async def try_publish_executable_signal(
  client: Any,
  match: StrategyMatch,
  *,
  symbol: str,
  event_ts: str | None = None,
  source: RedisOHLCSource | None = None,
) -> PublishResult:
  """The one authoritative CONFIRMED-zone -> TradePlan V8 pass (ADR P0).

  Runs the exact same evaluation `_handle_event` already performs for a
  durable ready-stream wake-up (reload canonical setup, validate state,
  validate a fresh side-aware quote, validate quote-in-zone, validate any
  required M1 trigger, build+publish TradePlan V8 atomically) but does it
  synchronously, in the caller's own processing cycle, instead of via a
  Redis stream round-trip to a separate consumer task. Callers that already
  know a match is CONFIRMED and structurally eligible (the scanner, right
  after confirming it) should call this directly; a match that is not yet
  executable simply comes back ``remained_watching`` and the caller falls
  back to the durable `auto_trade:strategy_match_ready` queue for later
  retries (still required for waiting-retest/M1-trigger semantics).

  Never raises for an ordinary rejection/wait outcome - only reraises on an
  unexpected internal failure, matching every other entry point in this
  module.
  """
  setup_id = match.match_id
  zone_id = str(match.confluence_zone_id or match.structural_zone_id or "")
  plan_id = _v8_plan_id(match)
  bar_event = f"{symbol}:{EXECUTION_TIMEFRAME}:{event_ts or match.event_ts}"

  await _handle_event(bar_event, source=source, client=client, ready_match_id=setup_id)

  plan_state = await read_plan_state(client, plan_id)
  setup_after = await load_setup(client, setup_id)
  measured: dict[str, Any] = {}
  raw_route = await client.get(route_outcome_key(symbol, setup_id))
  if raw_route:
    try:
      route_payload = json.loads(
        raw_route.decode() if isinstance(raw_route, bytes) else raw_route,
      )
    except (TypeError, ValueError, json.JSONDecodeError):
      route_payload = {}
    if isinstance(route_payload, dict):
      measured = route_payload.get("measured") or {}
      reason_code = str(route_payload.get("reason_code") or "")
    else:
      reason_code = ""
  else:
    reason_code = ""

  spot = await _load_spot(client, symbol)
  executable_quote: float | None = None
  quote_side: str | None = None
  if spot is not None and spot.fresh:
    quote_side = "ask" if match.direction == "BUY" else "bid"
    executable_quote = spot.ask if match.direction == "BUY" else spot.bid

  if plan_state == "published":
    zone_id_for_lock = zone_id
    if zone_id_for_lock:
      try:
        from app.autotrade.zone_watch import (
          LOCKED_ZONE_WATCH_STATES,
          TERMINAL_ZONE_WATCH_STATES,
          load_zone_watch,
          lock_zone_watch_published,
        )

        latest = await load_zone_watch(client, zone_id_for_lock)
        if (
          latest is not None
          and latest.state not in TERMINAL_ZONE_WATCH_STATES
          and latest.state not in LOCKED_ZONE_WATCH_STATES
        ):
          await lock_zone_watch_published(
            client,
            zone_id_for_lock,
            plan_id=plan_id,
            reason_code=reason_code or "execution_handoff_created",
          )
      except Exception:
        log.exception(
          "zone watch publish lock failed zone_id=%s plan_id=%s",
          zone_id_for_lock,
          plan_id,
        )
    return PublishResult(
      status=PUBLISH_STATUS_EXECUTION_HANDOFF_CREATED,
      plan_id=plan_id,
      reason_code=reason_code or "execution_handoff_created",
      zone_id=zone_id,
      setup_id=setup_id,
      measured=measured,
      executable_quote=executable_quote,
      quote_side=quote_side,
    )
  if setup_after is None:
    return PublishResult(
      status=PUBLISH_STATUS_REJECTED,
      plan_id=plan_id,
      reason_code="setup_missing",
      zone_id=zone_id,
      setup_id=setup_id,
      measured=measured,
    )
  if setup_after.state == INVALIDATED:
    return PublishResult(
      status=PUBLISH_STATUS_INVALIDATED,
      plan_id=plan_id,
      reason_code=reason_code or "structure_invalidated",
      zone_id=zone_id,
      setup_id=setup_id,
      measured=measured,
      executable_quote=executable_quote,
      quote_side=quote_side,
    )
  if setup_after.state in TERMINAL_STATES:
    return PublishResult(
      status=PUBLISH_STATUS_REJECTED,
      plan_id=plan_id,
      reason_code=reason_code or setup_after.state,
      zone_id=zone_id,
      setup_id=setup_id,
      measured=measured,
      executable_quote=executable_quote,
      quote_side=quote_side,
    )
  return PublishResult(
    status=PUBLISH_STATUS_REMAINED_WATCHING,
    plan_id=plan_id,
    reason_code=reason_code or "zone_watching_retest",
    zone_id=zone_id,
    setup_id=setup_id,
    measured=measured,
    executable_quote=executable_quote,
    quote_side=quote_side,
  )


async def _process_strategy_match_ready_entry(
  client: Any,
  stream_id: object,
  fields: dict[object, object],
  *,
  source: RedisOHLCSource | None,
) -> bool:
  event = StrategyMatchReadyEvent.from_fields(fields)
  if event is None:
    await client.xack(READY_STREAM, READY_GROUP, stream_id)
    await increment_metric(client, "strategy_match_ready_invalid")
    return True

  await increment_metric(
    client,
    "strategy_match_ready_received",
    symbol=event.symbol,
    dimensions={"recovery": str(event.recovery).lower()},
  )
  setup = await load_setup(client, event.setup_id)
  now = int(datetime.now(timezone.utc).timestamp())
  await save_ready_snapshot(
    client,
    event,
    worker_received_at=now,
  )
  canonical = await load_canonical_match(client, event)
  existing = (
    None
    if canonical is None
    else await resolve_existing_v8_state(
      client,
      canonical,
      cycle_id=str(event.scanner_event_ts or ""),
    )
  )
  if existing is not None and existing.already_published:
    await client.xack(READY_STREAM, READY_GROUP, stream_id)
    await increment_metric(
      client,
      "strategy_match_ready_acked_existing_plan",
      symbol=event.symbol,
    )
    return True
  if setup is not None and setup.state in TERMINAL_STATES:
    await client.xack(READY_STREAM, READY_GROUP, stream_id)
    await increment_metric(
      client,
      "strategy_match_ready_acked_terminal",
      symbol=event.symbol,
    )
    return True
  if now >= event.expires_at:
    if setup is not None and setup.state not in TERMINAL_STATES:
      try:
        await transition_setup(
          client,
          event.setup_id,
          EXPIRED,
          reason_code="strategy_match_ready_expired",
        )
        await emit_lifecycle(
          client,
          EXPIRED,
          symbol=event.symbol,
          match_id=event.setup_id,
          correlation_id=event.setup_id,
          reason_code="strategy_match_ready_expired",
          message="durable ready event expired before the worker consumed it",
          publish_status=True,
        )
      except SetupLifecycleError:
        log.exception(
          "ready event expiry transition failed setup_id=%s",
          event.setup_id,
        )
    await client.xack(READY_STREAM, READY_GROUP, stream_id)
    await increment_metric(
      client,
      "strategy_match_ready_acked_expired",
      symbol=event.symbol,
    )
    return True

  match = canonical
  if match is None:
    await increment_metric(
      client,
      "strategy_match_ready_canonical_missing",
      symbol=event.symbol,
    )
    return False
  if (
    match.symbol != event.symbol
    or match.match_id != event.match_id
    or match.direction != event.direction
    or match.entry_low != event.entry_low
    or match.entry_high != event.entry_high
  ):
    if setup is not None and setup.state not in TERMINAL_STATES:
      try:
        await transition_setup(
          client,
          event.setup_id,
          INVALIDATED,
          reason_code="strategy_match_ready_contract_mismatch",
        )
        await emit_lifecycle(
          client,
          INVALIDATED,
          symbol=event.symbol,
          match_id=event.setup_id,
          correlation_id=event.setup_id,
          reason_code="strategy_match_ready_contract_mismatch",
          message="ready event contract no longer matches the canonical match",
          publish_status=True,
        )
      except SetupLifecycleError:
        log.exception(
          "ready event mismatch transition failed setup_id=%s",
          event.setup_id,
        )
    await client.xack(READY_STREAM, READY_GROUP, stream_id)
    await increment_metric(
      client,
      "strategy_match_ready_contract_mismatch",
      symbol=event.symbol,
    )
    return True

  await _handle_event(
    f"{event.symbol}:{EXECUTION_TIMEFRAME}:{event.scanner_event_ts}",
    source=source,
    client=client,
    ready_match_id=event.match_id,
  )
  setup = await load_setup(client, event.setup_id)
  # P0 root-cause fix: ARMED_WAITING_TRIGGER is NOT a publication outcome -
  # per setup_lifecycle.py's own docstring, it is exactly the node where a
  # retest/M1 timing wait begins. Acking here (as this used to) permanently
  # removes the one durable wake-up for a setup that has not produced a
  # TradePlan yet; the only remaining re-drive was non-durable Redis
  # Pub/Sub ("bars:new"), which silently drops a setup that misses a tick
  # (deploy/restart/network blip) - the setup then ages out via the expiry
  # sweeper with no TradePlan ever built. Leaving the entry unacked keeps it
  # pending in the consumer group; the caller's periodic xautoclaim
  # (recover_pending=True, min_idle_time=30s) durably re-drives this exact
  # setup on its own schedule regardless of pub/sub delivery, until it
  # actually reaches PLAN_PUBLISHED or a terminal state.
  durable = bool(
    setup is not None
    and setup.state in {
      PLAN_PUBLISHED,
      *TERMINAL_STATES,
    }
  )
  if not durable:
    return False
  await client.xack(READY_STREAM, READY_GROUP, stream_id)
  await increment_metric(
    client,
    "strategy_match_ready_acked",
    symbol=event.symbol,
    dimensions={"state": setup.state},
  )
  return True


async def _consume_strategy_match_ready_once(
  *,
  client: Any | None = None,
  source: RedisOHLCSource | None = None,
  consumer: str | None = None,
  block_ms: int = 0,
  recover_pending: bool = False,
  pending_min_idle_ms: int = 30_000,
) -> bool:
  """Consume at most one durable Scanner -> Worker wake-up."""
  client = client or redis_state.get_client()
  source = source or RedisOHLCSource(client)
  consumer = consumer or ready_consumer_name()
  await ensure_ready_group(client)
  entries: list[tuple[object, dict[object, object]]] = []
  if recover_pending:
    claimed = await client.xautoclaim(
      READY_STREAM,
      READY_GROUP,
      consumer,
      min_idle_time=max(0, pending_min_idle_ms),
      start_id="0-0",
      count=1,
    )
    if len(claimed) >= 2:
      entries = list(claimed[1])
  if not entries:
    read_kwargs = {"count": 1}
    if block_ms > 0:
      read_kwargs["block"] = block_ms
    streams = await client.xreadgroup(
      READY_GROUP,
      consumer,
      {READY_STREAM: ">"},
      **read_kwargs,
    )
    if streams:
      entries = list(streams[0][1])
  if not entries:
    return False
  stream_id, fields = entries[0]
  return await _process_strategy_match_ready_entry(
    client,
    stream_id,
    fields,
    source=source,
  )


async def _recover_unfinished_strategy_matches(
  client: Any,
) -> None:
  now = int(datetime.now(timezone.utc).timestamp())
  for symbol in _symbols():
    for match in await _load_strategy_matches(client, symbol):
      setup = await load_setup(client, match.match_id)
      if (
        setup is None
        or not is_publishable_setup_state(setup.state)
        or match.expires_at <= now
      ):
        continue
      plan_state = await read_plan_state(client, _v8_plan_id(match))
      if plan_state == "published":
        if setup.state == PLAN_BUILT:
          try:
            await transition_setup(
              client,
              match.match_id,
              PLAN_PUBLISHED,
              reason_code="v8_publish_reconciled",
            )
          except SetupLifecycleError:
            log.exception(
              "startup plan reconciliation failed setup_id=%s",
              match.match_id,
            )
        continue
      if match.thesis_id:
        owner = await client.get(
          active_thesis_key(symbol, str(match.thesis_id)),
        )
        if owner is not None:
          owner_id = owner.decode() if isinstance(owner, bytes) else str(owner)
          if owner_id != match.match_id:
            await increment_metric(
              client,
              "strategy_match_ready_recovery_owner_blocked",
              symbol=symbol,
            )
            continue
      stream_id = await enqueue_strategy_match_ready(
        client,
        match,
        market_map_id=(
          ""
          if match.execution_eligibility is None
          else match.execution_eligibility.market_map_id
        ),
        recovery=True,
      )
      if stream_id is not None:
        await increment_metric(
          client,
          "strategy_match_ready_pending_recovered",
          symbol=symbol,
        )


_READY_CONSUMER_BASE_BACKOFF_SECONDS = 1.0
_READY_CONSUMER_MAX_BACKOFF_SECONDS = 30.0


async def strategy_match_ready_loop() -> None:
  """Durably wake the worker when Scanner confirms an executable match.

  P0-11: ensure_ready_group and the startup reconciliation used to run
  with no retry boundary around them - a transient Redis error at process
  start (a connection blip during a rolling deploy) permanently killed
  this fire-and-forget task for the rest of the process's life, since
  nothing supervises it. The whole body now lives inside a bounded-backoff
  supervisor loop, and health is persisted at every state change so
  /auto_status can tell "the consumer is down" apart from "genuinely
  nothing to do right now."
  """
  if not runtime_config.runtime.auto_trade.enabled:
    return
  client = redis_state.get_client()
  source = RedisOHLCSource(client)
  consumer = ready_consumer_name()
  retry_count = 0

  while True:
    await save_ready_consumer_health(
      client, state="starting", consumer=consumer, retry_count=retry_count,
    )
    try:
      await ensure_ready_group(client)
      try:
        await _recover_unfinished_strategy_matches(client)
      except Exception:
        log.exception("strategy-match ready startup reconciliation failed")
      await save_ready_consumer_health(
        client, state="ready", consumer=consumer, retry_count=0,
      )
      log.info(
        "ApexVoid Algo consuming durable strategy matches stream=%s group=%s",
        READY_STREAM,
        READY_GROUP,
      )
      retry_count = 0
      while True:
        try:
          consumed = await _consume_strategy_match_ready_once(
            client=client,
            source=source,
            consumer=consumer,
            block_ms=5_000,
            recover_pending=True,
          )
          if consumed:
            await save_ready_consumer_health(
              client, state="ready", consumer=consumer,
              last_success_at=int(datetime.now(timezone.utc).timestamp()),
              retry_count=0,
            )
          else:
            continue
        except Exception as exc:
          log.exception(
            "strategy-match ready event failed; left pending for retry",
          )
          await increment_metric(client, "strategy_match_ready_failed")
          # A single event's processing failure is retried in place (it
          # stays pending in the consumer group), not fatal to the
          # consumer itself - degraded, matching P0-11/P1-6's fatal-vs-
          # degraded distinction for transient per-event failures.
          await save_ready_consumer_health(
            client, state="degraded_retrying", consumer=consumer,
            retry_count=retry_count, last_error=str(exc)[:500],
          )
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      retry_count += 1
      backoff = min(
        _READY_CONSUMER_MAX_BACKOFF_SECONDS,
        _READY_CONSUMER_BASE_BACKOFF_SECONDS * (2 ** min(retry_count, 5)),
      )
      log.exception(
        "strategy-match ready consumer setup failed, retrying in %.1fs "
        "(attempt %d)",
        backoff, retry_count,
      )
      await save_ready_consumer_health(
        client, state="degraded_retrying", consumer=consumer,
        retry_count=retry_count, last_error=str(exc)[:500],
      )
      await asyncio.sleep(backoff)


async def auto_scalp_loop() -> None:
  """Deprecated: closed bars are owned by bar_event_dispatcher_loop."""
  if not runtime_config.runtime.auto_trade.enabled:
    log.info("ApexVoid Algo gate disabled: AUTO_TRADE_ENABLED=false")
    return
  log.info("auto_scalp_loop idle; bar_event_dispatcher_loop owns bars:new")
