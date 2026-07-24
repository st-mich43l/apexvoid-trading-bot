"""Redis worker for ApexVoid Algo strategies and execution delivery.

The private OHLC strategies consume cTrader bars directly.  Scanner detectors
may also publish a typed completed strategy match; the worker transports that
decision to the executor without confirming it again or routing it by regime.
It never parses rendered Telegram text or imports scanner detector functions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from typing import Any

from app.persistence import redis_state
from app.autotrade import units
from app.autotrade.range_targets import configured_range_targets
from app.autotrade.execution_policy import (
  GUARD_MODE_OBSERVE,
  GUARD_MODE_STRICT,
  OUTCOME_ALLOW,
  OUTCOME_WAIT,
  ExecutionGuardDecision,
  GuardOutcome,
  StructuralBarrier,
  StructuralSourceIdentity,
  classify_barrier_relationship,
  classify_guard_severity,
  max_entry_drift_pips,
  resolve_guard_mode,
)
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
from app.autotrade.multi_match import (
  dedupe_matches,
  deserialize_matches,
  select_primary,
  serialize_matches,
  strategy_matches_key,
)
from app.autotrade.lifecycle import emit_lifecycle, increment_metric
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
  RangeContext,
  private_range_context,
  persist_range_resolution,
  range_context_source_key,
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
from app.core.config import settings
from app.persistence.store import event_in_window
from app.analysis.ohlc_source import RedisOHLCSource
from app.analysis.math_utils import atr_series
from app.analysis.types import Level, Zone
from app.analysis.zones import displacement, mark_mitigation, supply_demand
from app.analysis.levels import key_levels
from app.analysis.swings import find_swings


log = logging.getLogger(__name__)
EXECUTION_TIMEFRAME = "M1"
CONTEXT_TIMEFRAMES = ("M5", "M15", "M30")
# Matches trend.py's own HTF-bias definition (classify_regime uses M15 too).
_HTF_TIMEFRAME = "M15"
# Regime instrumentation: rolling 24h chop/trend/breakout share per symbol,
# used by delivery.py's /auto_status line and the mis-tuning alert below.
_REGIME_HISTORY_WINDOW_SECONDS = 24 * 3600
_REGIME_HISTORY_TTL_SECONDS = 26 * 3600
_REGIME_ALERT_COOLDOWN_SECONDS = 24 * 3600


@dataclass(frozen=True)
class AutoTradeSpot:
  price: float
  ts: int
  fresh: bool


def _symbols() -> set[str]:
  return {
    item.strip().upper()
    for item in settings.auto_trade_symbols.split(",")
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
  window: int = 240,
) -> dict[str, Any]:
  frames: dict[str, Any] = {}
  for timeframe in (EXECUTION_TIMEFRAME, *CONTEXT_TIMEFRAMES):
    frame = await source.window(symbol, timeframe, window)
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
    fresh=0 <= now - ts <= max(1, settings.auto_trade_spot_max_age),
  )


async def _load_strategy_match(
  client: Any,
  symbol: str,
) -> StrategyMatch | None:
  if not settings.auto_trade_strategy_bridge_enabled:
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
    await client.delete(key)
    return None
  return match


async def _load_strategy_matches(
  client: Any,
  symbol: str,
) -> list[StrategyMatch]:
  if not settings.auto_trade_strategy_bridge_enabled:
    return []
  if not settings.auto_trade_multi_match_enabled:
    match = await _load_strategy_match(client, symbol)
    return [] if match is None else [match]
  raw = await client.get(strategy_matches_key(symbol))
  matches = deserialize_matches(raw)
  now = int(datetime.now(timezone.utc).timestamp())
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
    series = atr_series(m1, max(2, settings.atr_length))
    if not series.empty:
      atr = float(series.iloc[-1])
  scanner_context = RangeContext.from_json(
    await client.get(range_context_source_key(symbol, "scanner"))
  )
  private_context = private_range_context(
    symbol=symbol,
    decision=private_decision,
    atr=atr,
    pip_size=units.pip_size(symbol),
    generated_at=now,
    ttl=max(300, settings.auto_trade_strategy_match_max_age_seconds),
  )
  resolved, comparison = resolve_range_context(
    scanner_context,
    private_context,
    now=now,
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
          ttl=settings.auto_trade_box_retire_seconds,
        )
        await persist_breakout_retest_watch(
          client,
          symbol=symbol,
          range_id=resolved.range_id,
          direction=direction,
          lower=resolved.lower,
          upper=resolved.upper,
          ttl=settings.auto_trade_box_retire_seconds,
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

  await persist_range_resolution(
    client,
    symbol=symbol,
    scanner=scanner_context,
    private=private_context,
    resolved=resolved,
    comparison=comparison,
  )
  if comparison.get("disagreement"):
    await increment_metric(client, "range_context_disagreement", symbol=symbol)
  elif comparison.get("resolution") == "merged":
    await increment_metric(client, "range_context_merged", symbol=symbol)
  if resolved is not None:
    if resolved.state not in {"broken", "retired"}:
      private_decision = evaluate_auto_scalp_gate(
        frames,
        symbol=symbol,
        spot_price=spot.price if spot is not None and spot.fresh else None,
        range_context=resolved,
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
        ex=max(300, settings.auto_trade_box_retire_seconds),
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
    state = "ARMED"
    if decision.direction == direction:
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
      "candidate_id": existing.get("candidate_id"),
      "pending_order_ids": existing.get("pending_order_ids", []),
      "position_ids": existing.get("position_ids", []),
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
      ex=max(300, settings.auto_trade_box_retire_seconds),
    )


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
    "updated_at": int(datetime.now(timezone.utc).timestamp()),
  })
  await client.set(
    key,
    json.dumps(payload, separators=(",", ":"), sort_keys=True),
    ex=max(300, settings.auto_trade_box_retire_seconds),
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
    return f"EQ exclusion: entry {entry_reference:.2f} within {fraction:.0%} of box EQ {eq:.2f}"
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
      f"Range Edge Scalp not near an edge: entry {entry_reference:.2f} is "
      f"{distance_atr:.2f} ATR from rail {rail.level:.2f} "
      f"(limit {limit_atr:.2f} ATR)"
    )
  return None


def _htf_zones(frames: dict[str, Any], cfg: Any) -> list[Zone]:
  """Fresh/tested HTF (M15) supply/demand zones, for the A3 veto and the A2
  opposing-zone attachment. Independent of gate.py/trend.py's own M1 legs -
  this is the one place the shared analysis stack enters the autotrade path,
  and it enters only as a veto input, never as a signal.
  """
  htf = frames.get(_HTF_TIMEFRAME)
  if htf is None or htf.empty:
    return []
  atr_length = max(2, int(getattr(cfg, "atr_length", 14)))
  legs = displacement(
    htf,
    atr_series(htf, atr_length),
    max(0.1, float(getattr(cfg, "displacement_atr_mult", 1.5))),
    max(0.0, float(getattr(cfg, "momentum_body_frac", 0.6))),
  )
  if not legs:
    return []
  zones = supply_demand(htf, legs)
  return mark_mitigation(zones, htf)


def _htf_levels(frames: dict[str, Any], cfg: Any) -> list[Level]:
  """HTF (M15) round-number and reaction key levels, for the opposing-barrier
  veto below. Round-number levels aren't sided the way supply/demand zones
  are (a round number caps a rally the same way it floors a selloff), so
  they're kept as a separate ``Level`` list rather than folded into ``Zone``.
  """
  htf = frames.get(_HTF_TIMEFRAME)
  if htf is None or htf.empty:
    return []
  atr_length = max(2, int(getattr(cfg, "atr_length", 14)))
  atr = atr_series(htf, atr_length)
  swings = find_swings(
    htf,
    max(1, int(getattr(cfg, "swing_fractal_n", 2))),
    max(0.0, float(getattr(cfg, "zigzag_pct", 0.0))),
    max(0.0, float(getattr(cfg, "zigzag_atr_mult", 1.0))),
    atr,
  )
  if not swings:
    return []
  return key_levels(
    swings,
    atr,
    max(0.0, float(getattr(cfg, "level_cluster_atr", 0.5))),
    max(0.1, float(getattr(cfg, "round_step", 5.0))),
    max(1, int(getattr(cfg, "key_level_min_touches", 2))),
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
  ambiguous = next(
    (
      barrier for barrier, relationship in relationships
      if relationship == "overlapping_ambiguous"
    ),
    None,
  )
  if ambiguous is not None:
    message = (
      f"entry {entry_reference:.2f} inside opposing/ambiguous "
      f"{ambiguous.level_kind or ambiguous.side} "
      f"{ambiguous.low:.2f}-{ambiguous.high:.2f}"
    )
    decision = classify_guard_severity(
      "opposing_barrier",
      "entry_inside_opposing_zone",
      message,
      guard_mode=guard_mode,
      hard_geometry=True,
    )
    return replace(
      decision,
      barrier=ambiguous,
      measured={
        "entry_reference": entry_reference,
        "relationship": "overlapping_ambiguous",
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
        f"{barrier.low:.2f}-{barrier.high:.2f} "
        f"({distance:.2f} away)"
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
        f"primary source {primary.low:.2f}-{primary.high:.2f} "
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
      return barrier.low, f"{barrier.side} {barrier.low:.2f}-{barrier.high:.2f}"
  else:
    between = [
      zone for zone in zones
      if zone.side == "demand"
      and zone.low <= entry_reference
      and zone.high >= target
    ]
    barrier = _nearest_directional_zone("BUY", entry_reference, between)
    if barrier is not None:
      return barrier.high, f"{barrier.side} {barrier.low:.2f}-{barrier.high:.2f}"

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
  return near_edge, f"{kind} {low:.2f}-{high:.2f}"


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
      f"counter-bias target {target:.2f} is not ahead of "
      f"{match.direction} entry {entry_reference:.2f}"
    )
  barrier = _counter_bias_barrier_between(
    match.direction, entry_reference, target, zones, levels,
  )
  if barrier is None:
    return None
  _, description = barrier
  return f"counter-bias target blocked before EQ {target:.2f} by {description}"


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
      f"counter-bias target {target:.2f} is not ahead of "
      f"{match.direction} entry {entry_reference:.2f}",
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
      "block",
      "target_room_insufficient",
      (
        f"counter-bias target blocked before EQ {target:.2f} by {description}: "
        f"room {room_pips:.1f}p does not fit the smallest configured target "
        f"({min(match.targets_pips) if match.targets_pips else 0}p)"
      ),
      True,
      measured={"available_room_pips": round(room_pips, 1), "barrier_price": barrier_price},
    )
  adjusted_target = (
    entry_reference + fitted * pip_size
    if match.direction == "BUY"
    else entry_reference - fitted * pip_size
  )
  adapted = replace(
    match,
    target_price=adjusted_target,
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
      f"counter-bias target adapted {target:.2f} -> {adjusted_target:.2f} "
      f"(room {room_pips:.1f}p, barrier {description})"
    ),
    False,
    measured={
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
    not settings.auto_trade_zone_cooldown_enabled
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
    f"zone cooldown: {direction} entry {entry_reference:.2f} is "
    f"{distance_atr:.2f} ATR from a stopped-out entry at "
    f"{recorded_entry:.2f} (limit {cooldown_atr:.2f} ATR)"
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
    f"entry {entry_reference:.2f} inside both demand "
    f"{demand_hit.lo:.2f}-{demand_hit.hi:.2f} and supply "
    f"{supply_hit.lo:.2f}-{supply_hit.hi:.2f}"
  )


def _resolve_overlap_thesis(
  direction: str,
  entry_reference: float,
  market_map: MarketMap | None,
  m1: Any,
  atr: float | None,
  cfg: Any,
) -> GuardOutcome:
  """Resolve an entry inside both a demand and a supply band by the same
  M1 reaction-lookback memory ``map_strategy.py`` already computes for its
  own reaction selection (PR #100), instead of the previous unconditional
  "both directions are dead" veto. Never trims or deletes either band from
  the Market Map itself - this only decides whether THIS candidate's
  thesis has directional confirmation.
  """
  from app.autotrade.map_strategy import _reaction_in_lookback

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
    f"entry {entry_reference:.2f} inside both demand "
    f"{demand_hit.lo:.2f}-{demand_hit.hi:.2f} and supply "
    f"{supply_hit.lo:.2f}-{supply_hit.hi:.2f}"
  )
  strict_block = guard_mode == GUARD_MODE_STRICT
  if m1 is None or getattr(m1, "empty", True) or not atr or atr <= 0:
    # No reaction data to resolve with - can't distinguish resolved from
    # ambiguous, so treat it the same as a genuinely ambiguous overlap.
    return GuardOutcome(
      "overlap", OUTCOME_WAIT, "ambiguous_waiting_confirmation", reason, strict_block,
    )
  tolerance = max(0.05, 0.5 * atr)
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
    return GuardOutcome(
      "overlap", OUTCOME_WAIT, "opposing_zone_ahead",
      f"{reason} - {opposite_direction} reaction confirmed instead of {direction}",
      strict_block,
    )
  return GuardOutcome(
    "overlap", OUTCOME_WAIT, "ambiguous_waiting_confirmation", reason, strict_block,
  )


def _nearest_directional_zone(
  direction: str,
  entry_reference: float,
  zones: list[Zone],
) -> Zone | None:
  """Nearest HTF zone on the side that justifies (and can trap the stop of)
  ``direction`` - supply for a SELL, demand for a BUY. Used both for A2's
  opposing-zone attachment (any freshness) and the A3 veto (fresh only).
  """
  side = "supply" if direction == "SELL" else "demand"
  candidates = [zone for zone in zones if zone.side == side]
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
    f"{zone.low:.2f}-{zone.high:.2f}"
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
    or f"{match.key_level:.2f}:{match.entry_low:.2f}:{match.entry_high:.2f}"
  )
  return _group_id(
    match.symbol,
    match.family or match.strategy,
    match.direction,
    structural_key,
  )


def _thesis_lock_enabled() -> bool:
  return bool(getattr(settings, "auto_trade_map_thesis_lock_enabled", True))


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
  rearm_atr = float(getattr(settings, "auto_trade_map_reaction_rearm_atr", 0.5))
  rearm_bars = int(getattr(settings, "auto_trade_map_reaction_rearm_bars", 3))
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
    symbol = str(plan.get("Symbol") or plan.get("symbol") or settings.auto_trade_canonical_symbol).upper()
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
  value = match.strategy.casefold()
  if match.is_range_edge or "range" in value:
    return settings.auto_trade_range_enabled
  if "mapped" in value or match.family == "mapped_zone":
    return settings.auto_trade_market_map_strategy_enabled
  if "liquidity" in value or "sweep" in value:
    return settings.auto_trade_liquidity_reversal_enabled
  if "retest" in value:
    return settings.auto_trade_retest_enabled
  if "breakout" in value or "breakdown" in value:
    return settings.auto_trade_breakout_enabled
  if (
    "reaction" in value
    or "rejection" in value
    or "supply" in value
    or "demand" in value
  ):
    return settings.auto_trade_reaction_enabled
  return settings.auto_trade_strategy_bridge_enabled


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
    not settings.auto_trade_enabled
    or not settings.auto_trade_range_enabled
    or spot is None
    or not spot.fresh
    or decision.state != "candidate"
    or decision.rail is None
    or decision.box is None
    or decision.direction is None
    or scale_context is None
    or decision.confluence < max(1, settings.auto_trade_min_confluence)
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
  guard_mode = resolve_guard_mode(settings)
  if regime is not None and regime.state != "chop":
    regime_outcome = classify_guard_severity(
      "regime",
      "range_edge_not_chop",
      (
        f"range-box strategy evaluated while regime={regime.state}; "
        "strategy geometry remains authoritative"
      ),
      guard_mode=guard_mode,
    )
    await _record_guard_evaluation(
      client, symbol, regime_outcome,
      strategy="Range Box Scalp",
      direction=decision.direction,
      source_structure=(
        f"range_box_edge {decision.rail.low:.2f}-{decision.rail.high:.2f}"
      ),
    )
    if regime_outcome.hard_block:
      await _record_gate_reject(client, symbol, "range_edge_not_chop")
      return None
  eq_reason = _eq_exclusion_reason(
    decision.box,
    entry_reference,
    settings.auto_trade_eq_exclusion_fraction,
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
    settings.auto_trade_edge_proximity_atr,
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
    decision.direction, entry_reference, htf_zones or [],
  )
  if settings.auto_trade_htf_veto_enabled:
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
  if (
    settings.auto_trade_opposing_barrier_veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
  ):
    source = _structural_source_identity(
      strategy="Range Box Scalp",
      family="range",
      structural_source="range_box_edge",
      low=decision.rail.low,
      high=decision.rail.high,
      key_level=decision.rail.level,
      zone_id=f"{decision.box.box_id}:{decision.direction.upper()}",
    )
    barrier_outcome = _opposing_barrier_decision(
      decision.direction, entry_reference, None, scale_context.atr,
      htf_zones or [], htf_levels or [],
      settings.auto_trade_opposing_barrier_atr,
      source=source,
      guard_mode=guard_mode,
    )
    if barrier_outcome.reason_code != "no_opposing_barrier":
      await _record_guard_evaluation(
        client, symbol, barrier_outcome,
        strategy="Range Box Scalp",
        direction=decision.direction,
        source_structure="range_box_edge",
      )
      log.info(
        "auto-scalp candidate %s symbol=%s reason=%s",
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
    client, symbol, decision.direction, entry_reference,
    scale_context.atr, settings.auto_trade_zone_cooldown_atr,
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
    settings.auto_trade_overlap_veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
  ):
    overlap_outcome = _resolve_overlap_thesis(
      decision.direction, entry_reference, market_map, m1,
      scale_context.atr, settings,
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
    guarded = await event_in_window(
      now,
      max(0, settings.auto_trade_news_guard_minutes) * 60,
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
  claimed = await client.set(
    f"auto_trade:candidate:{candidate_id}",
    "published",
    ex=max(60, settings.auto_trade_candidate_ttl),
    nx=True,
  )
  if not claimed:
    return None
  payload = {
    "version": 3,
    "candidate_id": candidate_id,
    "group_id": _group_id(
      symbol,
      "range",
      decision.box.box_id,
      decision.direction,
      candidate_id,
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
    "reasons": list(decision.reasons),
    "range_id": decision.box.box_id,
    "range_low": decision.box.lower.level,
    "range_high": decision.box.upper.level,
    "full_take_profit_pips": decision.full_tp_pips,
    "sweep_low": decision.sweep_low,
    "sweep_high": decision.sweep_high,
    "regime": regime.state if regime is not None else "chop",
    "bias": "neutral",
    "relationship_to_bias": "neutral",
    "opposing_zone_low": None if opposing_zone is None else opposing_zone.low,
    "opposing_zone_high": None if opposing_zone is None else opposing_zone.high,
    "add_zone_side": None if opposing_zone is None else opposing_zone.side,
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
  try:
    await client.xadd(
      settings.auto_trade_stream,
      {"payload": json.dumps(payload, separators=(",", ":"))},
      maxlen=max(100, settings.auto_trade_stream_maxlen),
      approximate=True,
    )
  except Exception:
    await client.delete(f"auto_trade:candidate:{candidate_id}")
    raise
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
  )
  await client.set(
    _box_edge_key(symbol, decision.box.box_id, decision.direction),
    "1",
    ex=max(300, settings.auto_trade_box_retire_seconds),
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
) -> str | None:
  """Publish a completed scanner strategy match without PA re-confirmation."""
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
    return None
  if (
    not settings.auto_trade_enabled
    or not _strategy_mode_enabled(match)
    or match.symbol != symbol.upper()
    or match.confluence < max(1, settings.auto_trade_min_confluence)
  ):
    return None
  guard_mode = resolve_guard_mode(settings)
  source_summary = (
    f"{match.structural_source or match.strategy} "
    f"{match.entry_low:.2f}-{match.entry_high:.2f}"
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
      regime_outcome = classify_guard_severity(
        "regime",
        "range_edge_not_chop",
        (
          f"range-edge strategy evaluated while regime={regime.state}; "
          "strategy structure remains authoritative"
        ),
        guard_mode=guard_mode,
      )
      await _record_guard_evaluation(
        client, symbol, regime_outcome,
        strategy=match.strategy,
        direction=match.direction,
        source_structure=source_summary,
      )
      if regime_outcome.hard_block:
        await _consume_strategy_match(client, symbol, match)
        await _record_gate_reject(client, symbol, "range_edge_not_chop")
        return None
    assert match.range_id is not None
    assert match.range_low is not None
    assert match.range_high is not None
    if await client.exists(_box_retired_key(symbol, match.range_id)):
      await _consume_strategy_match(client, symbol, match)
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
    return None

  if (
    settings.auto_trade_opposing_barrier_veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
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
      settings.auto_trade_opposing_barrier_atr,
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
      return None
    if barrier_outcome.outcome == OUTCOME_WAIT:
      return None

  cooldown_reason = await _zone_cooldown_reason(
    client, symbol, match.direction, spot.price,
    match.atr, settings.auto_trade_zone_cooldown_atr,
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
      return None

  if (
    settings.auto_trade_overlap_veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
  ):
    overlap_outcome = _resolve_overlap_thesis(
      match.direction, spot.price, market_map, m1, match.atr, settings,
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
      return None
    if overlap_outcome.outcome == OUTCOME_WAIT:
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
        f"{match.structure_swing:.2f} at {spot.price:.2f}"
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
    return None

  distance = (
    match.entry_low - spot.price
    if spot.price < match.entry_low
    else spot.price - match.entry_high
    if spot.price > match.entry_high
    else 0.0
  )
  distance_pips = distance / units.pip_size(symbol)
  remaining_room = None
  if match.full_take_profit_pips:
    remaining_room = float(match.full_take_profit_pips)
  distance_limit, drift_measured = max_entry_drift_pips(
    strategy=match.strategy,
    atr=float(match.atr),
    pip_size=units.pip_size(symbol),
    remaining_target_room_pips=remaining_room,
    cfg=settings,
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
      return None
    return None
  now = int(datetime.now(timezone.utc).timestamp())
  try:
    guarded = await event_in_window(
      now,
      max(0, settings.auto_trade_news_guard_minutes) * 60,
    )
  except Exception:
    log.exception("strategy match blocked: news guard unavailable")
    return None
  if guarded is not None:
    log.info(
      "strategy match blocked by news symbol=%s strategy=%s event=%s",
      symbol,
      match.strategy,
      guarded.get("title", "high-impact event"),
    )
    return None

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
        rearm_atr=float(getattr(settings, "auto_trade_map_reaction_rearm_atr", 0.5)),
        rearm_bars=int(getattr(settings, "auto_trade_map_reaction_rearm_bars", 3)),
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
        return None
      # Terminal reaction claim may be deleted once thesis rearm already passed.
      await client.delete(claim_key)
      await increment_metric(client, "mapped_reaction_rearmed", symbol=symbol)

  candidate_id = match.match_id
  claimed = await client.set(
    f"auto_trade:candidate:{candidate_id}",
    "published",
    ex=max(60, settings.auto_trade_candidate_ttl),
    nx=True,
  )
  if not claimed:
    if match.reaction_id:
      await increment_metric(
        client, "duplicate_reaction_suppressed", symbol=symbol,
      )
    await _consume_strategy_match(client, symbol, match)
    return None

  if match.reaction_id:
    claim_key = reaction_claim_key(match.reaction_id)
    claim_body = reaction_claim_payload(
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
    # Persist for the whole group lifetime; do not expire with lookback.
    reaction_claimed = await client.set(claim_key, claim_body, nx=True)
    if not reaction_claimed:
      await client.delete(f"auto_trade:candidate:{candidate_id}")
      await increment_metric(
        client, "duplicate_reaction_suppressed", symbol=symbol,
      )
      await _consume_strategy_match(client, symbol, match)
      return None
    await increment_metric(client, "mapped_reaction_claimed", symbol=symbol)

  if match.thesis_id and _thesis_lock_enabled():
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
    thesis_ok = await _acquire_thesis_claim(client, thesis_body, match.thesis_id)
    if not thesis_ok:
      await client.delete(f"auto_trade:candidate:{candidate_id}")
      if match.reaction_id:
        await client.delete(reaction_claim_key(match.reaction_id))
      await increment_metric(
        client, "duplicate_thesis_suppressed", symbol=symbol,
      )
      await increment_metric(
        client, "same_thesis_group_active", symbol=symbol,
      )
      await _consume_strategy_match(client, symbol, match)
      return None
    await increment_metric(client, "mapped_thesis_claimed", symbol=symbol)

  setup = (
    f"{match.strategy} · counter_bias"
    if "counter_bias" in match.tags
    else "Range Box Scalp"
    if match.is_range_edge
    else match.strategy
  )
  payload = {
    "version": 3 if match.is_range_edge else 4,
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
    "mode": "auto_box_scalp" if match.is_range_edge else "auto_strategy_match",
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
    "reasons": list(match.reasons),
    "bar_ts": int(match.event_ts) if match.event_ts.isdigit() else None,
    "atr": match.atr,
    "structure_swing": match.structure_swing,
    "targets_pips": list(match.targets_pips),
    "strategy_tags": list(match.tags),
    "target_price": match.target_price,
    "tier": match.tier,
    "risk_multiplier": match.risk_multiplier,
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
  }
  try:
    await client.xadd(
      settings.auto_trade_stream,
      {"payload": json.dumps(payload, separators=(",", ":"))},
      maxlen=max(100, settings.auto_trade_stream_maxlen),
      approximate=True,
    )
  except Exception:
    await client.delete(f"auto_trade:candidate:{candidate_id}")
    if match.reaction_id:
      await client.delete(reaction_claim_key(match.reaction_id))
    if match.thesis_id and _thesis_lock_enabled():
      await client.delete(thesis_claim_key(match.thesis_id))
    raise
  await increment_metric(client, "candidate_published", symbol=symbol)
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
      ex=max(300, settings.auto_trade_box_retire_seconds),
    )
  log.info(
    "strategy candidate published id=%s symbol=%s strategy=%s direction=%s",
    candidate_id[:12],
    symbol,
    match.strategy,
    match.direction,
  )
  return candidate_id


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
    not settings.auto_trade_enabled
    or not settings.auto_trade_trend_enabled
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
    or trend_decision.confluence < max(1, settings.auto_trade_min_confluence)
  ):
    return None

  entry_reference = spot.price
  opposing_zone = _nearest_directional_zone(
    trend_decision.direction, entry_reference, htf_zones or [],
  )
  guard_mode = resolve_guard_mode(settings)
  if settings.auto_trade_htf_veto_enabled:
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
    settings.auto_trade_opposing_barrier_veto_enabled
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
      settings.auto_trade_opposing_barrier_atr,
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
    trend_decision.atr, settings.auto_trade_zone_cooldown_atr,
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
    settings.auto_trade_overlap_veto_enabled
    or guard_mode == GUARD_MODE_OBSERVE
  ):
    overlap_outcome = _resolve_overlap_thesis(
      trend_decision.direction, entry_reference, market_map, trend_m1,
      trend_decision.atr, settings,
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
    guarded = await event_in_window(
      now,
      max(0, settings.auto_trade_news_guard_minutes) * 60,
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
  claimed = await client.set(
    f"auto_trade:candidate:{candidate_id}",
    "published",
    ex=max(60, settings.auto_trade_candidate_ttl),
    nx=True,
  )
  if not claimed:
    return None
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
      cfg=settings,
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
    "version": 3,
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
    "targets_pips": list(trend_decision.targets_pips),
    "regime": regime.state,
    "bias": bias,
    "relationship_to_bias": relationship_to_bias,
    "opposing_zone_low": None if opposing_zone is None else opposing_zone.low,
    "opposing_zone_high": None if opposing_zone is None else opposing_zone.high,
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
  try:
    await client.xadd(
      settings.auto_trade_stream,
      {"payload": json.dumps(payload, separators=(",", ":"))},
      maxlen=max(100, settings.auto_trade_stream_maxlen),
      approximate=True,
    )
  except Exception:
    await client.delete(f"auto_trade:candidate:{candidate_id}")
    raise
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
    target_plan=list(trend_decision.targets_pips),
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


def _regime_history_key(symbol: str) -> str:
  return f"auto_trade:regime_history:{symbol.upper()}"


def _regime_alert_key(symbol: str) -> str:
  return f"auto_trade:regime_alert_pending:{symbol.upper()}"


async def _record_regime(client: Any, symbol: str, state: str, now: int) -> None:
  key = _regime_history_key(symbol)
  await client.zadd(key, {f"{now}:{state}": now})
  await client.zremrangebyscore(key, 0, now - _REGIME_HISTORY_WINDOW_SECONDS)
  await client.expire(key, _REGIME_HISTORY_TTL_SECONDS)


async def regime_share_24h(client: Any, symbol: str) -> dict[str, float] | None:
  """Rolling 24h chop/trend/breakout share for ``symbol``.

  Returns ``None`` when there isn't yet close to a full day of samples, so
  callers (delivery.py's /auto_status) can show "warming up" instead of a
  misleading split computed from a handful of bars.
  """
  key = _regime_history_key(symbol)
  now = int(datetime.now(timezone.utc).timestamp())
  await client.zremrangebyscore(key, 0, now - _REGIME_HISTORY_WINDOW_SECONDS)
  members = await client.zrangebyscore(
    key,
    now - _REGIME_HISTORY_WINDOW_SECONDS,
    now,
  )
  if not members:
    return None
  counts = {"chop": 0, "trend": 0, "breakout": 0}
  oldest_ts: int | None = None
  for member in members:
    text = member.decode() if isinstance(member, bytes) else str(member)
    ts_text, _, state = text.partition(":")
    try:
      ts = int(ts_text)
    except ValueError:
      continue
    if oldest_ts is None or ts < oldest_ts:
      oldest_ts = ts
    if state in counts:
      counts[state] += 1
  total = sum(counts.values())
  if total == 0 or oldest_ts is None:
    return None
  # Require the samples to span close to a full day before trusting the
  # split - a freshly-started bot shouldn't alarm on a 100%/0% sliver.
  if now - oldest_ts < _REGIME_HISTORY_WINDOW_SECONDS * 0.9:
    return None
  return {state: value / total for state, value in counts.items()}


async def _maybe_flag_regime_alert(
  client: Any,
  symbol: str,
  shares: dict[str, float] | None,
) -> None:
  """Flag (at most once per 24h per symbol) that the chop share is high
  enough to warrant an owner DM. worker.py cannot import app.bot.client
  (see the architecture-guard test at the bottom of this module), so it
  only writes a Redis flag here; delivery.py's existing event-delivery
  loop (which already imports send_scanner_with_retry) polls for it and
  sends the actual Telegram message. See delivery.py's
  `_check_regime_alerts` for the consuming side.
  """
  if not shares:
    return
  threshold = max(0.0, min(1.0, float(settings.regime_chop_alert_share)))
  chop_share = shares.get("chop", 0.0)
  if chop_share <= threshold:
    return
  payload = json.dumps({
    "symbol": symbol.upper(),
    "chop_share": chop_share,
    "trend_share": shares.get("trend", 0.0),
    "breakout_share": shares.get("breakout", 0.0),
    "flagged_at": int(datetime.now(timezone.utc).timestamp()),
  })
  # SETNX + TTL: only (re)flag once per cooldown window per symbol, even
  # though this runs on every bar close while the condition holds.
  await client.set(
    _regime_alert_key(symbol),
    payload,
    ex=_REGIME_ALERT_COOLDOWN_SECONDS,
    nx=True,
  )


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
) -> dict[str, Any]:
  rail = decision.rail
  target = decision.target
  box = decision.box
  trend_routed = (
    gate_source == "private_ohlc"
    and trend_decision is not None
    and trend_decision.state == "candidate"
    and (
      decision.state != "candidate"
      # Box-scalp is not a real routing candidate outside chop (see
      # box_selected in _handle_event) - telemetry must agree with what
      # actually got published, or /auto_status would show "Range Box
      # Scalp" selected while the trend candidate is what actually fired.
      or (regime is not None and regime.state != "chop")
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
        f"breakout retest waiting at {float(zone_low):.2f}-{float(zone_high):.2f}",
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
      if settings.auto_trade_trend_enabled
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
  if strategy_match is not None:
    selected_strategy = strategy_match.strategy
    selected_timeframe = strategy_match.source_tf
  elif trend_routed and trend_decision is not None:
    selected_strategy = _TREND_SETUP_LABELS.get(
      trend_decision.mode or "",
      "Trend Strategy",
    )
    selected_timeframe = EXECUTION_TIMEFRAME
  elif decision.state == "candidate":
    selected_strategy = "Range Box Scalp"
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
    if direction in {"BUY", "SELL"} and crossed:
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
    if price >= midpoint:
      await client.delete(_box_edge_key(symbol, box.box_id, "BUY"))
    if price <= midpoint:
      await client.delete(_box_edge_key(symbol, box.box_id, "SELL"))
  if decision.state == "box_broken":
    await client.set(
      key,
      "1",
      ex=max(300, settings.auto_trade_box_retire_seconds),
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


async def _handle_event(
  data: object,
  *,
  source: RedisOHLCSource | None = None,
  client: Any | None = None,
) -> AutoScalpDecision | None:
  parsed = _parse_bar_event(data)
  if parsed is None:
    return None
  symbol, timeframe, event_ts = parsed
  if timeframe != EXECUTION_TIMEFRAME or symbol not in _symbols():
    return None

  client = client or redis_state.get_client()
  source = source or RedisOHLCSource(client)
  frames = await _load_frames(source, symbol)
  spot = await _load_spot(client, symbol)
  await _rearm_scanner_range_edges(client, symbol, spot)
  # Advance mapped thesis rearm tracking on every closed M1 before detection.
  try:
    from app.analysis.indicators import atr as atr_indicator
    m1 = frames.get(EXECUTION_TIMEFRAME) or frames.get("M1")
    if m1 is not None and len(m1) >= 15:
      atr_series = atr_indicator(m1, int(getattr(settings, "atr_length", 14)))
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
  scanner_strategy_matches = await _load_strategy_matches(client, symbol)
  cached_market_map = decode_market_map(
    await client.get(market_map_key(symbol))
  )
  displayed_market_map = decode_market_map(
    await client.get(market_map_display_key(symbol))
  )
  market_map_decision = evaluate_market_map_strategy(
    frames,
    symbol=symbol,
    event_ts=event_ts,
    spot_price=(
      spot.price if spot is not None and spot.fresh else None
    ),
    cfg=settings,
    market_map=cached_market_map,
    rendered_map=displayed_market_map,
  )
  await _record_market_map_strategy_telemetry(
    client,
    symbol,
    market_map_decision,
  )
  strategy_matches = list(scanner_strategy_matches)
  if market_map_decision.match is not None:
    strategy_matches.append(market_map_decision.match)
  if settings.auto_trade_multi_match_enabled and strategy_matches:
    strategy_matches, _ = dedupe_matches(
      strategy_matches,
      atr=strategy_matches[0].atr,
    )
  elif strategy_matches:
    strategy_matches = [strategy_matches[0]]
  strategy_match = select_primary(strategy_matches)
  decision = private_decision
  gate_source = (
    "multi_strategy_match"
    if len(strategy_matches) > 1
    else "scanner_strategy_match"
    if scanner_strategy_matches
    else "market_map_strategy"
    if market_map_decision.match is not None
    else "private_ohlc"
  )
  regime = classify_regime(frames, decision, settings)
  now_ts = int(datetime.now(timezone.utc).timestamp())
  try:
    await _record_regime(client, symbol, regime.state, now_ts)
    shares = await regime_share_24h(client, symbol)
    await _maybe_flag_regime_alert(client, symbol, shares)
  except Exception:
    log.exception("regime instrumentation failed symbol=%s", symbol)
  trend_decision = (
    evaluate_trend_gate(
      frames,
      regime,
      decision,
      symbol=symbol,
      spot_price=None if spot is None or not spot.fresh else spot.price,
      cfg=settings,
    )
    if strategy_match is None or settings.auto_trade_multi_match_enabled
    else TrendDecision("no_setup")
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
  box_selected = (
    decision.state == "candidate"
    and (
      strategy_match is None
      or settings.auto_trade_multi_match_enabled
    )
    # Box-scalp is a mean-reversion play on an actual consolidation; outside
    # chop it must lose the selection entirely (not just get rejected inside
    # _publish_candidate after already winning here), or trend_selected below
    # would wrongly stay False too and nothing would publish at all.
    and regime.state == "chop"
    and (
      settings.auto_trade_multi_match_enabled
      or trend_decision.state != "candidate"
      or decision.confluence >= trend_decision.confluence
    )
  )
  trend_selected = (
    trend_decision.state == "candidate"
    and (
      strategy_match is None
      or settings.auto_trade_multi_match_enabled
    )
    and (settings.auto_trade_multi_match_enabled or not box_selected)
  )
  scale_context = (
    build_auto_scale_context(
      frames,
      decision.direction or "",
      spot_price=spot.price,
      cfg=settings,
      target_low=None if decision.target is None else decision.target.low,
      target_high=None if decision.target is None else decision.target.high,
    )
    if (
      box_selected
      and spot is not None
      and spot.fresh
    ) else None
  )
  htf_zones = _htf_zones(frames, settings)
  htf_levels = _htf_levels(frames, settings)
  strategy_candidate_ids: list[str] = []
  for routed_match in strategy_matches:
    published = await _publish_strategy_match(
      client,
      symbol,
      spot,
      routed_match,
      consume_redis_match=(
        not settings.auto_trade_multi_match_enabled
        and bool(scanner_strategy_matches)
      ),
      match_source=gate_source,
      htf_zones=htf_zones,
      htf_levels=htf_levels,
      regime=regime,
      market_map=cached_market_map,
      frames=frames,
    )
    if published is not None:
      strategy_candidate_ids.append(published)
  box_candidate_id = (
    await _publish_candidate(
      client,
      symbol,
      event_ts,
      spot,
      decision,
      scale_context,
      regime=regime,
      htf_zones=htf_zones,
      htf_levels=htf_levels,
      gate_source=gate_source,
      market_map=cached_market_map,
      frames=frames,
    )
    if box_selected else None
  )
  trend_candidate_id = (
    await _publish_trend_candidate(
      client,
      symbol,
      event_ts,
      spot,
      regime,
      trend_decision,
      htf_zones=htf_zones,
      htf_levels=htf_levels,
      market_map=cached_market_map,
      frames=frames,
    )
    if trend_selected else None
  )
  if _has_overlapping_zones(cached_market_map):
    await client.incr(f"auto_trade:zone_overlap:{symbol.upper()}")
  candidate_ids = [
    *strategy_candidate_ids,
    *([box_candidate_id] if box_candidate_id is not None else []),
    *([trend_candidate_id] if trend_candidate_id is not None else []),
  ]
  candidate_id = candidate_ids[0] if candidate_ids else None
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
    strategy_match=strategy_match,
    market_map_decision=market_map_decision,
    breakout_retest=await load_breakout_retest_watch(client, symbol),
    resolved_range=resolved_range,
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
      "buy_rail": "armed",
      "sell_rail": "armed",
    }
  )
  payload["range_context_comparison"] = range_comparison
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set("auto_trade:last_gate", encoded)
  await client.set(f"auto_trade:last_gate:{symbol}", encoded)
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


async def auto_scalp_loop() -> None:
  """Route scanner strategy matches and private Algo strategies."""
  if not settings.auto_trade_enabled:
    log.info("ApexVoid Algo gate disabled: AUTO_TRADE_ENABLED=false")
    return

  client = redis_state.get_client()
  source = RedisOHLCSource(client)
  try:
    await _reconcile_legacy_mapped_thesis_claims(client)
  except Exception:
    log.exception("legacy mapped thesis claim reconcile failed")
  pubsub = client.pubsub()
  await pubsub.subscribe("bars:new")
  log.info(
    "ApexVoid Algo watching %s on M1/M5 with strategy_bridge=%s thesis_lock=%s",
    ",".join(sorted(_symbols())),
    settings.auto_trade_strategy_bridge_enabled,
    _thesis_lock_enabled(),
  )
  if not _thesis_lock_enabled():
    log.warning(
      "AUTO_TRADE_MAP_THESIS_LOCK_ENABLED=false — mapped theses may open "
      "multiple active groups; disable only for intentional diagnostics"
    )
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      try:
        await _handle_event(message.get("data"), source=source, client=client)
      except Exception:
        log.exception("ApexVoid Algo gate tick failed")
        try:
          await increment_metric(client, "lifecycle_error")
        except Exception:
          log.exception("ApexVoid Algo lifecycle_error metric failed")
  finally:
    await pubsub.unsubscribe("bars:new")
    await pubsub.close()
