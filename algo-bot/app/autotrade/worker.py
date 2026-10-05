"""Redis worker for ApexVoid Algo strategies and execution delivery.

The private OHLC strategies consume cTrader bars directly.  Scanner detectors
may also publish a typed completed strategy match; the worker transports that
decision to the executor without confirming it again or routing it by regime.
It never parses rendered Telegram text or imports scanner detector functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import asyncio
import hashlib
import json
import logging
import math
from typing import Any, Awaitable, Callable

from app.persistence import redis_state
from app.runtime.instruments import (
  EffectiveInstrumentError,
  enabled_instruments,
  for_instrument,
  live_instruments,
)
from app.analysis_client.provenance import GO_ORIGIN_TAG
from app.autotrade.go_plan_cancel import read_plan_cancel, register_go_plan
from app.autotrade.go_live_opportunities import go_live_opportunity_ids
from app.autotrade.go_opportunity_policy import (
  go_arbitration_key,
  opportunity_id_for_match_id,
)
from app.autotrade.go_zone_book import opposing_entries_for_go_match
from app.autotrade import units
from app.core import instrument_geometry
from app.autotrade.cycle_publish import (
  acquire_owned_lock,
  autonomous_cycle_owner_key,
  publish_ranked_cycle,
  release_owned_lock,
)
from app.autotrade.arbitration import (
  ArbitrationResult,
  CandidatePublicationResult,
  ExecutionIntent,
  arbitrate_execution_intents,
)
from app.autotrade.execution_policy import (
  evaluate_execution_policy,
)
from app.autotrade.active_exposure import (
  XAU_OPPOSITE_SEPARATION_SATISFIED,
  evaluate_entry_against_exposure,
  evaluate_opposite_exposure,
  load_active_exposures,
)
from app.autotrade.entry_overlap import release_entry_zone, reserve_entry_zone
from app.autotrade.strategy_match import (
  StrategyMatch,
  strategy_match_key,
)
from app.autotrade.strategy_taxonomy import (
  is_breakout_retest_scalp_strategy,
  is_m1_scalp_strategy,
  is_reaction_strategy,
  is_scalp_strategy,
  is_technique_or_confluence,
  match_bypasses_opposing_structure,
)
from app.autotrade.structural_target_room import (
  evaluate_structural_target_room,
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
  GO_AUTHORITATIVE,
  confirmation_policy_for,
  deterministic_episode_id,
  executable_quote_in_zone,
  load_execution_confirmation,
  new_state,
  parse_bar_timestamp,
  save_execution_confirmation,
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
  claim_active_thesis,
  is_publishable_setup_state,
  load_setup,
  normalize_setup_state,
  release_active_thesis,
  transition_setup,
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
from app.autotrade.setup_card import save_forming_card_status
from app.autotrade.reaction_identity import (
  ACTIVE_THESIS_STATES,
  dump_claim,
  mapped_group_id,
  parse_thesis_claim,
  thesis_claim_key,
)
from app.autotrade.range_context import WORKER_SNAPSHOT_TTL_SECONDS
from app.core.config import runtime_config
from app.runtime.price_identity import price_token
from app.persistence.store import event_in_window, nearest_currency_event
from app.marketdata.ohlc import RedisOHLCSource, window_for_timeframe


log = logging.getLogger(__name__)

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


def _collect_fixed_rr_metric_sink(
  bucket: list[tuple[str, str, dict[str, str]]],
):
  """Sync sink that records fixed_rr room-rejection counters for later await."""
  def _sink(name: str, symbol: str, dimensions: dict[str, str]) -> None:
    bucket.append((name, symbol, dict(dimensions)))
  return _sink


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


def classify_execution_zone(
  zone: Any,
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
  live = {item.upper() for item in live_instruments(runtime_config)}
  if live:
    return live
  return {item.upper() for item in enabled_instruments(runtime_config)}


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
      1, runtime_config.analysis.spot.maximum_age_seconds,
    ),
    bid=bid,
    ask=ask,
  )


async def _load_strategy_match(
  client: Any,
  symbol: str,
) -> StrategyMatch | None:
  if not runtime_config.auto_algo.strategy_match_enabled:
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
  if not runtime_config.auto_algo.strategy_match_enabled:
    return []
  if not runtime_config.auto_algo.strategies.matching.multiple_matches_enabled:
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


_MIN_COUNTER_BIAS_TARGET_PIPS = 15


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
      now + max(300, runtime_config.auto_algo.lifecycle.candidate.storage_ttl_seconds)
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


def _strategy_mode_enabled(match: StrategyMatch) -> bool:
  from app.autotrade.strategy_registry import strategy_mode_enabled

  return strategy_mode_enabled(match.strategy, runtime_config)


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
  gates = runtime_config.auto_algo.actionability.gates
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
    now, max(0, runtime_config.auto_algo.actionability.gates.news_guard_minutes) * 60,
  )


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
  log.info(
    "v8 build rejected symbol=%s setup_id=%s reason=%s message=%s",
    symbol, match.match_id, reason_code, message,
  )
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


def _execution_quote_access(
  match: StrategyMatch,
  spot: AutoTradeSpot | None,
  symbol: str,
  inst: Any,
) -> tuple[Any, bool]:
  """Whether the side-aware executable quote may act on ``match`` right now.

  Returns ``(evidence, execution_eligible)``: the quote-versus-entry-zone
  evidence and whether it authorizes entry - quote inside the raw entry zone
  plus the configured contract tolerance, or, for scalp families, within their
  own chase allowance. The one definition shared by the plan builder and by
  arbitration, so "can execute now" can never mean two different things.
  """
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
  # Scalping / range-scalp activation already allows trade-direction chase within
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
  return evidence, execution_eligible


async def _publish_trade_plan_v8(
  client: Any,
  symbol: str,
  spot: AutoTradeSpot | None,
  match: StrategyMatch,
  *,
  regime: str | None = None,
  frames: dict[str, Any] | None = None,
) -> str | None:
  """Build and publish a TradePlan V8 from an already-CONFIRMED match.

  Deliberately separate from _publish_strategy_match (the V6 path) rather
  than sharing its body: V6's function is full of V6-only concerns
  (candidate_id/group_id shaping, ZoneFillPlanner routing, ...) that must
  not leak into the V8 contract. Python execution checks are shared only for
  legacy matches. A Go-origin match carries its complete technical thesis;
  Python does not run opposing-barrier, overlap, HTF, cooldown, target-room,
  or stop-rewrite logic against it. _adapt_counter_bias_target is deliberately
  NOT called here.

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
  # Go is the live technical source; provenance and the Go-only match filter remain the protection
  # against stale Python/ZoneWatch state. Everything after this point is
  # execution-time quote, confirmation, risk and order validation.
  if GO_ORIGIN_TAG in match.tags:
    # A Go opportunity that was invalidated/expired leaves a cancel tombstone.
    # A match that raced the withdrawal
    # (already in this cycle's memory) must not become a fresh plan.
    withdrawn = await read_plan_cancel(client, _v8_plan_id(match))
    if withdrawn is not None:
      await record_route_outcome(
        client,
        match,
        stage="mode_check",
        status="blocked",
        reason_code="go_plan_withdrawn",
        message=f"Go-derived plan was withdrawn before publication ({withdrawn.get('source')}: {withdrawn.get('reason')})",
        measured={"withdrawn_source": withdrawn.get("source"), "withdrawn_reason": withdrawn.get("reason")},
        retained=False,
        publish_status=False,
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

  # Technique pack: pair reaction windows for non-scalp; scalping killzone for scalps.
  from app.autotrade.killzone import (
    evaluate_killzone_gate,
    evaluate_reaction_publish_window,
    reaction_require_killzone,
    reaction_require_publish_window,
    technique_enforce,
  )

  inst = instrument_geometry.instrument_runtime(symbol)
  tech = getattr(inst.execution, "technique", None)
  enforce_pack = technique_enforce(inst)
  spot_ts = int(getattr(spot, "ts", 0) or int(datetime.now(timezone.utc).timestamp()))
  candidate_is_scalp = is_scalp_strategy(
    str(getattr(match, "strategy", "") or ""),
    family=str(getattr(match, "strategy_family", "") or getattr(match, "family", "") or "")
    or None,
    strategy_mode=str(getattr(match, "strategy_mode", "") or "") or None,
  )
  if candidate_is_scalp:
    # Optional global scalping clock sterilizer (prod off). Pair session quality is
    # assessed above, but it is deliberately not a time-of-day hard gate.
    require_kz = False if tech is None else bool(
      getattr(tech, "scalp_require_killzone", False),
    )
    from app.autotrade.session_context import classify_session

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
  authoritative_source = (
    GO_AUTHORITATIVE if GO_ORIGIN_TAG in match.tags else M5_AUTHORITATIVE
  )
  pip_size = units.pip_size(symbol)
  evidence, execution_eligible = _execution_quote_access(match, spot, symbol, inst)

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

  # Go opportunities already carry the closed-bar confirmation that created
  # the opportunity.  The worker must not rerun the retired Python M1
  # detector as optional telemetry or as a hidden second authority.
  m1 = None
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
          source=authoritative_source,
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
        message=(
          "retest entered execution distance; waiting for fresh M1"
          if policy.m1_required_on_retest
          else "retest entered execution distance; checking optional M1"
        ),
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
      trigger = None
      latest_evaluated = None
      if trigger is None:
        if policy.m1_required_on_retest:
          execution_state = new_state(
            setup_id,
            IN_ZONE_WAITING_M1,
            now=now_ts,
            episode_id=execution_state.episode_id,
            zone_entered_at=execution_state.zone_entered_at,
            last_inside_at=quote_ts,
            last_evaluated_m1_ts=(
              latest_evaluated
              if latest_evaluated is not None
              else execution_state.last_evaluated_m1_ts
            ),
          )
          await _persist_v8_confirmation_phase(
            client,
            symbol,
            match,
            execution_state,
            reason_code="micro_confirmation_missing",
            message="Trendline V2 interaction is waiting for a fresh M1 reclaim",
            evidence=evidence,
            metric="trendline_v2_m1_missing",
            status="checking",
          )
          return None
        confirmation_source = authoritative_source
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
        int(runtime_config.auto_algo.lifecycle.retest.trigger_validity_bars),
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
        if policy.m1_required_on_retest:
          await _persist_v8_confirmation_phase(
            client,
            symbol,
            match,
            new_state(
              setup_id,
              IN_ZONE_WAITING_M1 if execution_eligible else WAITING_RETEST,
              now=now_ts,
              episode_id=execution_state.episode_id,
              zone_entered_at=execution_state.zone_entered_at,
              zone_exited_at=None if execution_eligible else quote_ts,
              last_inside_at=execution_state.last_inside_at,
              last_evaluated_m1_ts=trigger_bar_ts,
            ),
            reason_code="micro_confirmation_stale",
            message="stale Trendline V2 M1 trigger ignored; waiting for a new one",
            evidence=evidence,
            metric="trendline_v2_m1_stale",
          )
          return None
        confirmation_source = authoritative_source
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
    if not execution_eligible or policy.m1_required_on_retest:
      return None
    episode_id = deterministic_episode_id(
      setup_id,
      match.direction,
      match.entry_low,
      match.entry_high,
      confirmation_boundary,
    )
    trigger = None
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
        source=authoritative_source,
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
  if (
    isinstance(getattr(match, "trendline_v2", None), dict)
    and str(match.trendline_v2.get("version", "")).casefold() == "v2"
    and confirmation is not None
    and confirmation.source == M1_RETEST
  ):
    trendline_telemetry = dict(match.trendline_v2)
    trendline_telemetry.update({
      "micro_confirmation_type": confirmation.pattern,
      "confirmation_at": int(confirmation.bar_ts),
      "entry_reason": "causal_confirmed_m5_reclaim_fresh_m1",
    })
    execution_match = replace(match, trendline_v2=trendline_telemetry)
  if policy.m5_authoritative_contract and confirmation.source == M1_RETEST:
    validity_bars = max(
      1,
      int(runtime_config.auto_algo.lifecycle.retest.trigger_validity_bars),
    )
    trigger_expiry = confirmation.bar_ts + 60 + validity_bars * 60
    execution_match = replace(
      match,
      expires_at=min(int(match.expires_at), trigger_expiry),
    )
  if match_bypasses_opposing_structure(execution_match):
    # Native-room scalp/range policy is provenance-neutral.
    room_entries = ()
  else:
    # Go owns technical structure. Read only the barrier book the
    # analysis-engine publishes (already width-gated, merged and reconciled
    # there); never reconstruct a competing Python zone book.
    room_entries = await opposing_entries_for_go_match(client, symbol)
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
    barrier_buffer_atr=instrument_geometry.structural_barrier_buffer_atr(
      symbol,
    ),
    min_capped_target_pips=float(
      runtime_config.auto_algo.actionability.target_room.minimum_capped_target_pips
    ),
    execution_cost_pips=float(runtime_config.execution.policy.execution_cost_pips),
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
      runtime_config.auto_algo.actionability.target_room.minimum_capped_target_pips or 15
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

  claimed = await claim_active_thesis(
    client, symbol=symbol, thesis_id=match.thesis_id, setup_id=setup_id,
  )
  if not claimed:
    await _record_v8_build_rejected(
      client, symbol, match, "thesis_already_owned",
      f"thesis {match.thesis_id!r} is already owned by a different setup - "
      "one active thesis may own at most one autonomous initial plan",
      {},
    )
    return None

  async def _release_claims() -> None:
    await release_active_thesis(
      client, symbol=symbol, thesis_id=match.thesis_id, setup_id=setup_id,
    )
    await release_entry_zone(client, symbol=symbol, setup_id=setup_id)

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

  overlap_blocker = await reserve_entry_zone(
    client,
    symbol=symbol,
    setup_id=setup_id,
    strategy=str(match_for_plan.strategy),
    direction=str(match_for_plan.direction),
    low=float(match_for_plan.entry_low),
    high=float(match_for_plan.entry_high),
    atr=float(match_for_plan.atr or 0.0),
    now=now_ts,
  )
  if overlap_blocker is not None:
    await _release_claims()
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      "entry_zone_overlap_same_direction",
      (
        f"{match_for_plan.direction} zone "
        f"{match_for_plan.entry_low:.5f}-{match_for_plan.entry_high:.5f} "
        f"overlaps {overlap_blocker.strategy} zone "
        f"{overlap_blocker.low:.5f}-{overlap_blocker.high:.5f} "
        "admitted within the last 45 minutes"
      ),
      {
        "overlap_setup_id": overlap_blocker.setup_id,
        "overlap_strategy": overlap_blocker.strategy,
        "overlap_low": overlap_blocker.low,
        "overlap_high": overlap_blocker.high,
        "overlap_reserved_at": overlap_blocker.reserved_at,
      },
    )
    return None

  exposures = await load_active_exposures(client)
  # Opposite-direction exposure is owned by the instrument policy
  # (config/instruments.yml exposure.opposite_position), never by strategy,
  # family or scalp status. FX: always blocked. XAU: >= 150 pips from every
  # opposite group. A missing/invalid policy fails closed.
  try:
    opposite_policy = instrument_geometry.opposite_position_policy(symbol)
  except EffectiveInstrumentError as exc:
    await _release_claims()
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      "opposite_exposure_policy_unavailable",
      str(exc),
      {"symbol": symbol},
    )
    return None
  opposite = evaluate_opposite_exposure(
    symbol,
    match_for_plan.direction,
    float(entry_reference),
    exposures,
    opposite_policy,
  )
  if not opposite.allowed:
    await _release_claims()
    log.info(
      "v8 opposite exposure blocked symbol=%s match_id=%s reason=%s %s",
      symbol,
      match.match_id[:12],
      opposite.reason_code,
      opposite.message,
    )
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      str(opposite.reason_code),
      opposite.message,
      dict(opposite.measured or {}),
    )
    return None
  if opposite.reason_code == XAU_OPPOSITE_SEPARATION_SATISFIED:
    log.info(
      "v8 opposite exposure separated symbol=%s match_id=%s %s",
      symbol,
      match.match_id[:12],
      opposite.message,
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
    same_direction_size_fraction=float(
      runtime_config.auto_algo.risk.position_limits.same_direction_stack_size_fraction
    ),
    # Non-scalp may same-dir stack at 60% only after every open plan has
    # booked TP2 and the candidate is Tier A. Scalps may stack freely.
    # (Same-direction behavior is unchanged by the opposite-exposure policy.)
    allow_same_direction_stack=candidate_is_scalp,
    candidate_tier=str(getattr(match_for_plan, "tier", "") or ""),
  )
  if exposure.block:
    await _release_claims()
    await _record_v8_build_rejected(
      client,
      symbol,
      match,
      str(exposure.reason_code or "same_direction_exposure_blocked"),
      exposure.message,
      dict(exposure.measured or {}),
    )
    return None
  same_direction_stack = bool(exposure.same_direction_stack)
  if same_direction_stack:
    log.info(
      "v8 same-direction stack symbol=%s match_id=%s %s",
      symbol,
      match.match_id[:12],
      exposure.message,
    )

  # Zone-split capability + required-limit-side checks: mirror old preflight
  # policy gates against the fresh policy evaluation for the plan-time match.
  side_aware_quote = _executable_spot_price(spot, match_for_plan.direction)
  fixed_rr_target = instrument_geometry.fixed_reward_risk(symbol) is not None
  fixed_rr_metrics: list[tuple[str, str, dict[str, str]]] = []
  # 2026-09 (Key Level structural repair Phase 2): restores a real opposing-
  # wall room cap on the fixed_rr ladder - deleted outright by PR #499
  # ("we work on technique zone not calculate opposing zone blindly"),
  # after which this call always passed available_target_room_pips=None
  # (confirmed by grep: zero production callers passed a real value since).
  # target_room (computed above against this same match_for_plan -
  # match_for_plan = execution_match, never reassigned since) already
  # measures room against the identical StructuralBarrierBook-derived
  # opposing entries feeding the room/containment check just above; reusing
  # its "room_pips" here avoids a second, possibly-divergent room lookup.
  # None when no opposing barrier was found (evaluate_execution_policy's
  # own "available_room is not None" guard already treats that as
  # unconstrained, matching today's behavior) or when the flag is off.
  available_target_room_pips = None
  gate_policy = evaluate_execution_policy(
    match_for_plan,
    spot_price=spot.price,
    executable_quote=side_aware_quote,
    regime=regime,
    pip_size=units.pip_size(symbol),
    cfg=None,
    available_target_room_pips=available_target_room_pips,
    metric_sink=_collect_fixed_rr_metric_sink(fixed_rr_metrics),
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
  gate_entry_low = float(
    gate_measured.get("planned_entry_zone_low", match_for_plan.entry_low)
  )
  gate_entry_high = float(
    gate_measured.get("planned_entry_zone_high", match_for_plan.entry_high)
  )
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
    # Native XAU scalping 1:2: after TP1 books (50%), move SL to BE for the
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
      regime=regime,
      cfg=inst,
      executable_quote=entry_reference,
      confirmation_source=confirmation.source,
      execution_confirmation_bar_ts=confirmation.bar_ts,
      zone_episode_id=confirmation.zone_episode_id,
      trigger_wick_extreme=confirmation.wick_extreme,
      max_volume=int(instrument_geometry.plan_max_volume(symbol)),
      # Go opportunities carry an authority-owned technical expiry.  Do not
      # re-anchor that deadline at Python publication time.
      now_ts=None,
      same_direction_stack=same_direction_stack,
      same_direction_size_fraction=float(
        runtime_config.auto_algo.risk.position_limits.same_direction_stack_size_fraction
      ),
      be_after_target_index=0,
      approved_measured=gate_measured,
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
    # later scalping with thesis_already_owned. Always release on unexpected fail.
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
    if GO_ORIGIN_TAG in match.tags:
      # Index before publishing: a cancellation must always be able to find every
      # Go-derived plan. A failure here aborts the publish (fail closed).
      await register_go_plan(client, plan_id=plan.plan_id, match=match, expires_at=plan.expires_at)
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
    # having a root card -- scalping's own synchronous publish attempt is only
    # one of the ways a plan gets published here. The same match, once
    # persisted to strategy_matches, is also independently discovered and
    # published by this cycle's own arbitration on a later pass in the
    # same tick, bypassing publish_scalp_live() (and its card-ensure)
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


# The Go event is the complete technical decision; this string is what the
# status report shows where the Python regime classifier used to report.
_GO_OWNED_REGIME = "go_owned"


def _status_payload(
  *,
  symbol: str,
  event_ts: str,
  frames: dict[str, Any],
  spot: AutoTradeSpot | None,
  candidate_id: str | None,
  gate_source: str,
  strategy_match: StrategyMatch | None,
) -> dict[str, Any]:
  if strategy_match is not None:
    state = "candidate" if candidate_id is not None else "strategy_match_waiting"
    direction = strategy_match.direction
    reasons = strategy_match.reasons
  else:
    state = "go_owned"
    direction = None
    reasons = ("technical facts supplied by Go Analysis Engine",)
  selected_strategy = None
  selected_timeframe = None
  if strategy_match is not None and candidate_id is not None:
    selected_strategy = strategy_match.strategy
    selected_timeframe = strategy_match.source_tf
  return {
    "state": state,
    "symbol": symbol,
    "tf": EXECUTION_TIMEFRAME,
    "event_ts": event_ts,
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "direction": direction,
    "spot_fresh": None if spot is None else spot.fresh,
    "candidate_id": candidate_id,
    "published": candidate_id is not None,
    "gate_source": gate_source,
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
    "regime": _GO_OWNED_REGIME,
  }


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
  if not runtime_config.auto_algo.enabled:
    return _AdmissionFailure(
      reason_code="auto_trade_disabled",
      terminal=True,
      message="autonomous execution is disabled",
    )
  if not runtime_config.auto_algo.strategy_match_enabled:
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
  if match.confluence < max(1, runtime_config.auto_algo.actionability.gates.min_confluence):
    return _AdmissionFailure(
      reason_code="confluence_below_minimum",
      terminal=True,
      message="strategy confluence is below the global minimum",
      measured={"confluence": match.confluence},
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


async def _persist_idle_last_gate(
  client: Any,
  *,
  symbol: str,
  event_ts: str,
  spot: AutoTradeSpot | None,
) -> None:
  payload = {
    "state": "idle_no_match",
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


def _reconcile_go_match_projection(
  matches: list[StrategyMatch],
  live_ids: frozenset[str],
) -> tuple[list[StrategyMatch], list[StrategyMatch]]:
  """Project Go's current live book onto the retained match cache.

  Kafka arbitration can arrive after the opportunity has disappeared from Go's
  rebuilt book (most commonly across an engine restart).  Keeping that match
  marked ``winner`` leaves a misleading executable-looking projection in
  Redis, even though the execution fence correctly refuses it.  Retain the
  record for audit/history, but make the withdrawal explicit and return only
  currently live matches to the execution path.
  """
  projected: list[StrategyMatch] = []
  executable: list[StrategyMatch] = []
  for match in matches:
    opportunity_id = opportunity_id_for_match_id(match.match_id)
    if opportunity_id in live_ids:
      projected.append(match)
      executable.append(match)
      continue
    stale = replace(
      match,
      arbitration_status="suppressed",
      arbitration_reason_code="go_opportunity_not_live",
    )
    projected.append(stale)
  return projected, executable


async def _restore_reappeared_go_arbitration(
  client: Any,
  matches: list[StrategyMatch],
) -> list[StrategyMatch]:
  """Restore a cached Go decision when a previously withdrawn ID returns."""
  restored: list[StrategyMatch] = []
  valid_statuses = {"winner", "suppressed", "conflict_held", "uncontested"}
  for match in matches:
    if match.arbitration_reason_code != "go_opportunity_not_live":
      restored.append(match)
      continue
    raw = await client.get(
      go_arbitration_key(match.symbol, opportunity_id_for_match_id(match.match_id)),
    )
    try:
      payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
      payload = {}
    status = payload.get("status")
    if status not in valid_statuses:
      restored.append(match)
      continue
    restored.append(replace(
      match,
      arbitration_status=status,
      arbitration_reason_code=payload.get("reason_code"),
    ))
  return restored


async def _handle_event(
  data: object,
  *,
  source: RedisOHLCSource | None = None,
  client: Any | None = None,
  ready_match_id: str | None = None,
) -> None:
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
  # Go is the sole automatic technical-opportunity producer. This filter is
  # applied even for a ready-stream wake-up: a ZoneWatch or legacy Python
  # caller cannot smuggle a non-Go match through the explicit-match path.
  scanner_strategy_matches = [
    item for item in scanner_strategy_matches
    if GO_ORIGIN_TAG in item.tags
  ]
  # Execute only what Go still holds live. Go rebuilds its book under the
  # current rules on every restart, so an old event it would no longer
  # create (for example a sliver zone from before a rule change) is absent
  # from its published set. An unavailable set fails open, but a verified
  # live set also reconciles the retained projection so a stale winner cannot
  # remain looking executable in Redis.
  if scanner_strategy_matches:
    live_ids = await go_live_opportunity_ids(client, symbol)
    if live_ids is not None:
      original_projection = scanner_strategy_matches
      projected, scanner_strategy_matches = _reconcile_go_match_projection(
        scanner_strategy_matches, live_ids,
      )
      scanner_strategy_matches = await _restore_reappeared_go_arbitration(
        client, scanner_strategy_matches,
      )
      restored_by_id = {
        item.match_id: item for item in scanner_strategy_matches
      }
      projected = [
        restored_by_id.get(item.match_id, item) for item in projected
      ]
      if projected != original_projection:
        now = int(datetime.now(timezone.utc).timestamp())
        await client.set(
          strategy_matches_key(symbol),
          serialize_matches(projected),
          ex=max(60, max(item.expires_at for item in projected) - now),
        )
  if ready_match_id is not None:
    scanner_strategy_matches = [
      item for item in scanner_strategy_matches
      if item.match_id == ready_match_id
    ]
  if not scanner_strategy_matches:
    await _persist_idle_last_gate(
      client, symbol=symbol, event_ts=event_ts, spot=spot,
    )
    return None

  # The Go event is the complete technical decision: do not call Python
  # regime, range, trendline, or scalp detectors on this path, and do not
  # let scanner_strategy_matches (see above) ever carry a non-Go match
  # here. OHLC is still loaded, same as the Python path (production
  # finding 2026-09-28: skipping it silently turned the execution-time
  # opposing-barrier/target-room recheck below into a no-op for every
  # Go-origin match, for lack of anything to check against) - this is the
  # "retain execution-time... risk... checks" case, not a second
  # technical-production source: nothing here builds a candidate, it only
  # rechecks whether Go's own confirmed geometry is already contained in a
  # standing opposing zone before letting it publish.
  frames = await _load_frames(source, symbol)
  strategy_matches = list(scanner_strategy_matches)
  if runtime_config.auto_algo.strategies.matching.multiple_matches_enabled and strategy_matches:
    strategy_matches, _ = dedupe_matches(
      strategy_matches,
      atr=strategy_matches[0].atr,
      cfg=None,
    )
  elif strategy_matches:
    strategy_matches = [strategy_matches[0]]
  strategy_match = select_primary(strategy_matches)
  observed_gate_source = (
    "multi_strategy_match"
    if len(strategy_matches) > 1
    else "scanner_strategy_match"
    if scanner_strategy_matches
    else "private_ohlc"
  )
  spot_price = spot.price if spot is not None and spot.fresh else None
  strategy_candidate_ids: list[str] = []
  published_match: StrategyMatch | None = None
  published_intent: ExecutionIntent | None = None
  attempted_intent_ids: set[str] = set()
  intents: list[ExecutionIntent] = []
  intent_matches: dict[str, StrategyMatch] = {}
  intent_subjects: dict[str, Any] = {}
  arbitrable: list[ExecutionIntent] = []
  arbitration = arbitrate_execution_intents([])
  if strategy_matches:
    execution_inst = instrument_geometry.instrument_runtime(symbol)
    for routed_match in strategy_matches:
      intent_id = f"strategy:{routed_match.match_id}"
      intent_matches[intent_id] = routed_match
      group_id = _strategy_group_id(routed_match)
      intent = ExecutionIntent(
        intent_id=intent_id,
        source=(
          "market_map_strategy"
          if routed_match.strategy_mode == "mapped_zone_reaction"
          else "go_analysis_engine"
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
        quality_overall=routed_match.quality_overall,
        bias_relationship=routed_match.bias_relationship,
        arbitration_status=routed_match.arbitration_status,
        arbitration_reason_code=routed_match.arbitration_reason_code,
        # Only an intent whose executable quote is inside its entry contract can
        # publish this cycle; the rest merely wait for a retest and must not
        # create a BUY-vs-SELL conflict with one that can.
        executable_now=bool(
          spot is not None
          and spot.fresh
          and _execution_quote_access(
            routed_match, spot, symbol, execution_inst,
          )[1]
        ),
      )
      intents.append(intent)
      intent_subjects[intent_id] = routed_match
    arbitrable: list[ExecutionIntent] = []
    for intent in intents:
      routed_match = intent_matches.get(intent.intent_id)
      if routed_match is not None:
        failure = await _admit_strategy_intent_for_cycle(
          client,
          intent,
          routed_match,
          spot=spot,
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
    # Go owns every technical opportunity. Algo Bot owns execution policy, so
    # arbitration runs only after freshness, quote and route admission have
    # removed stale/non-executable opportunities from the decision set. Go's
    # full-book arbitration remains provenance telemetry; it cannot veto a
    # fresh executable intent with an old technical opportunity.
    gates = runtime_config.auto_algo.actionability.scanner_gates
    arbitration = arbitrate_execution_intents(
      arbitrable,
      conflict_margin=float(gates.conflict_margin),
      use_quality_ranking=bool(gates.use_quality_ranking),
      conflict_margin_quality=float(gates.conflict_margin_quality),
    )

    published_match: StrategyMatch | None = None
    published_intent: ExecutionIntent | None = None
    attempted_intent_ids: set[str] = set()
    cycle_id = str(event_ts or "")

    async def publish_ranked_intent(
      intent: ExecutionIntent,
    ) -> CandidatePublicationResult:
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
          # docs/autotrade-execution-integrity.md - the V6 candidate path is
          # removed entirely for autonomous publication (not gated behind a
          # mode) so a confirmed setup can never arm both a TradePlan and a V6
          # candidate for the same thesis. Existing open V6 positions are
          # untouched; this only blocks new autonomous publication.
          published = await _publish_trade_plan_v8(
            client,
            symbol,
            spot,
            routed_match,
            regime=_GO_OWNED_REGIME,
            frames=frames,
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
  candidate_ids = list(strategy_candidate_ids)
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
  payload = _status_payload(
    symbol=symbol,
    event_ts=event_ts,
    frames=frames,
    spot=spot,
    candidate_id=candidate_id,
    gate_source=gate_source,
    strategy_match=status_strategy_match,
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
    "ApexVoid Algo cycle symbol=%s source=%s state=%s direction=%s candidate=%s",
    symbol,
    gate_source,
    payload["state"],
    payload["direction"] or "-",
    candidate_id[:12] if candidate_id else "-",
  )


PUBLISH_STATUS_EXECUTION_HANDOFF_CREATED = "execution_handoff_created"
# Compat alias — older call sites / tests still reference PUBLISHED.
PUBLISH_STATUS_PUBLISHED = PUBLISH_STATUS_EXECUTION_HANDOFF_CREATED
PUBLISH_STATUS_REMAINED_WATCHING = "remained_watching"
PUBLISH_STATUS_INVALIDATED = "invalidated"
PUBLISH_STATUS_REJECTED = "rejected"


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
  if GO_ORIGIN_TAG not in match.tags:
    # This is the final data-plane fence. A stale Python/ZoneWatch match may
    # still be present in Redis after a restart, but it can never become a new
    # automatic plan while Go owns technical production.
    await record_route_outcome(
      client,
      match,
      stage="mode_check",
      status="blocked",
      reason_code="python_match_rejected_live_go",
      message="Go-only automatic analysis rejects non-Go matches",
      retained=False,
      publish_status=False,
    )
    return PublishResult(
      status=PUBLISH_STATUS_REJECTED,
      plan_id=plan_id,
      reason_code="python_match_rejected_live_go",
      zone_id=zone_id,
      setup_id=setup_id,
    )
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
