"""Redis worker for ApexVoid Algo strategies and execution delivery.

The private OHLC strategies consume cTrader bars directly.  Scanner detectors
may also publish a typed completed strategy match; the worker transports that
decision to the executor without confirming it again or routing it by regime.
It never parses rendered Telegram text or imports scanner detector functions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from typing import Any

from app.persistence import redis_state
from app.autotrade import units
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


def _opposing_barrier_reason(
  direction: str,
  entry_reference: float,
  atr: float | None,
  zones: list[Zone],
  levels: list[Level],
  buffer_atr: float,
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
  """
  opposing_side = "supply" if direction == "BUY" else "demand"
  bounds = [
    (zone.low, zone.high, zone.side) for zone in zones if zone.side == opposing_side
  ]
  bounds += [
    (level.price - level.band, level.price + level.band, level.kind)
    for level in levels
  ]
  for low, high, kind in bounds:
    if low <= entry_reference <= high:
      return (
        f"entry {entry_reference:.2f} inside opposing {kind} "
        f"{low:.2f}-{high:.2f}"
      )
  if not atr or atr <= 0 or buffer_atr <= 0:
    return None
  ahead = []
  for low, high, kind in bounds:
    if direction == "BUY" and low > entry_reference:
      ahead.append((low - entry_reference, low, high, kind))
    elif direction == "SELL" and high < entry_reference:
      ahead.append((entry_reference - high, low, high, kind))
  if not ahead:
    return None
  distance, low, high, kind = min(ahead, key=lambda item: item[0])
  if distance > buffer_atr * atr:
    return None
  return (
    f"Opposing barrier ahead: {direction} into {kind} "
    f"{low:.2f}-{high:.2f} ({distance:.2f} away)"
  )


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

  if match.direction == "BUY":
    between = [
      zone for zone in zones
      if zone.side == "supply"
      and zone.high >= entry_reference
      and zone.low <= target
    ]
    barrier = _nearest_directional_zone("SELL", entry_reference, between)
  else:
    between = [
      zone for zone in zones
      if zone.side == "demand"
      and zone.low <= entry_reference
      and zone.high >= target
    ]
    barrier = _nearest_directional_zone("BUY", entry_reference, between)
  if barrier is not None:
    return (
      f"counter-bias target blocked before EQ {target:.2f} by "
      f"{barrier.side} {barrier.low:.2f}-{barrier.high:.2f}"
    )

  level_bounds = [
    (level.price - level.band, level.price + level.band, level.kind)
    for level in levels
  ]
  ahead = [
    (abs(entry_reference - low), low, high, kind)
    for low, high, kind in level_bounds
    if (
      match.direction == "BUY"
      and high >= entry_reference
      and low <= target
    ) or (
      match.direction == "SELL"
      and low <= entry_reference
      and high >= target
    )
  ]
  if not ahead:
    return None
  _, low, high, kind = min(ahead, key=lambda item: item[0])
  return (
    f"counter-bias target blocked before EQ {target:.2f} by "
    f"{kind} {low:.2f}-{high:.2f}"
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
  later). The marker is written by AutoTradeEngine.cs whenever a tracked
  position vanishes from the broker without the engine itself having closed
  it - always ambiguous (SL hit or manual close), so it's written
  unconditionally on every such close; a clean take-profit exit never
  produces one (see AutoTradeEngine.cs's reconcile stale-position branch).
  """
  if not atr or atr <= 0 or cooldown_atr <= 0:
    return None
  raw = await client.get(_zone_cooldown_key(symbol, direction))
  if raw is None:
    return None
  try:
    state = json.loads(raw)
    recorded_entry = float(state["entry_price"])
  except (TypeError, ValueError, KeyError, json.JSONDecodeError):
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
) -> str | None:
  if (
    not settings.auto_trade_enabled
    or spot is None
    or not spot.fresh
    or decision.state != "candidate"
    or decision.rail is None
    or decision.box is None
    or decision.direction is None
    or decision.full_tp_pips not in {50, 70}
    or scale_context is None
    or decision.confluence < max(1, settings.auto_trade_min_confluence)
    # Box-scalp is a mean-reversion play on an actual consolidation - it must
    # not fire once the regime classifier has already moved on to trend/
    # breakout (22 Jul incident: a box-labeled BUY filled straight into a
    # sharp post-rally pullback and was stopped in under a minute). regime
    # is only None in tests that don't care about this axis; a real caller
    # always passes it.
    or (regime is not None and regime.state != "chop")
  ):
    return None
  entry_reference = spot.price
  eq_reason = _eq_exclusion_reason(
    decision.box,
    entry_reference,
    settings.auto_trade_eq_exclusion_fraction,
  )
  if eq_reason is not None:
    log.info(
      "auto-scalp candidate blocked symbol=%s reason=%s", symbol, eq_reason,
    )
    await _record_gate_reject(client, symbol, "eq_exclusion")
    return None
  edge_reason = _edge_proximity_reason(
    decision.rail,
    entry_reference,
    scale_context.atr,
    settings.auto_trade_edge_proximity_atr,
  )
  if edge_reason is not None:
    log.info(
      "auto-scalp candidate blocked symbol=%s reason=%s", symbol, edge_reason,
    )
    await _record_gate_reject(client, symbol, "edge_proximity")
    return None
  opposing_zone = _nearest_directional_zone(
    decision.direction, entry_reference, htf_zones or [],
  )
  if settings.auto_trade_htf_veto_enabled:
    veto_reason = _htf_veto_reason(decision.direction, entry_reference, opposing_zone)
    if veto_reason is not None:
      log.info(
        "auto-scalp candidate blocked symbol=%s reason=%s", symbol, veto_reason,
      )
      await _record_gate_reject(client, symbol, "htf_veto")
      return None
  if settings.auto_trade_opposing_barrier_veto_enabled:
    barrier_reason = _opposing_barrier_reason(
      decision.direction, entry_reference, scale_context.atr,
      htf_zones or [], htf_levels or [],
      settings.auto_trade_opposing_barrier_atr,
    )
    if barrier_reason is not None:
      log.info(
        "auto-scalp candidate blocked symbol=%s reason=%s", symbol, barrier_reason,
      )
      await _record_gate_reject(
        client, symbol, _opposing_barrier_condition(barrier_reason),
      )
      return None
  cooldown_reason = await _zone_cooldown_reason(
    client, symbol, decision.direction, entry_reference,
    scale_context.atr, settings.auto_trade_zone_cooldown_atr,
  )
  if cooldown_reason is not None:
    log.info(
      "auto-scalp candidate blocked symbol=%s reason=%s", symbol, cooldown_reason,
    )
    await _record_gate_reject(client, symbol, "zone_cooldown")
    return None
  if settings.auto_trade_overlap_veto_enabled:
    overlap_reason = _overlapping_zone_conflict_reason(entry_reference, market_map)
    if overlap_reason is not None:
      log.info(
        "auto-scalp candidate blocked symbol=%s reason=%s", symbol, overlap_reason,
      )
      await _record_gate_reject(client, symbol, "overlapping_zone_conflict")
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
) -> str | None:
  """Publish a completed scanner strategy match without PA re-confirmation."""
  if (
    not settings.auto_trade_enabled
    or spot is None
    or not spot.fresh
    or match.symbol != symbol.upper()
    or match.confluence < max(1, settings.auto_trade_min_confluence)
  ):
    return None
  if match.is_range_edge:
    # Range Edge Scalp ("Range Box Scalp" label) is a mean-reversion play on
    # an actual consolidation, same as the private box gate above - it must
    # not fire once regime has moved past chop (22 Jul incident: this exact
    # path filled a BUY straight into a sharp post-rally pullback, stopped
    # in under a minute). Other strategy_match types (Box Breakout, Liquidity
    # Sweep, Mapped Zone Reaction, ...) are trend/breakout-appropriate by
    # design and stay ungated here.
    if regime is not None and regime.state != "chop":
      if consume_redis_match:
        await client.delete(strategy_match_key(symbol))
      await _record_gate_reject(client, symbol, "range_edge_not_chop")
      return None
    assert match.range_id is not None
    assert match.range_low is not None
    assert match.range_high is not None
    if await client.exists(_box_retired_key(symbol, match.range_id)):
      if consume_redis_match:
        await client.delete(strategy_match_key(symbol))
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
  counter_bias_barrier = _counter_bias_target_barrier_reason(
    match,
    spot.price,
    htf_zones or [],
    htf_levels or [],
  )
  if counter_bias_barrier is not None:
    log.info(
      "strategy match blocked symbol=%s strategy=%s reason=%s",
      symbol,
      match.strategy,
      counter_bias_barrier,
    )
    if consume_redis_match:
      await client.delete(strategy_match_key(symbol))
    await _record_gate_reject(
      client,
      symbol,
      "counter_bias_target_barrier",
    )
    return None
  if settings.auto_trade_opposing_barrier_veto_enabled:
    barrier_reason = _opposing_barrier_reason(
      match.direction, spot.price, match.atr,
      htf_zones or [], htf_levels or [],
      settings.auto_trade_opposing_barrier_atr,
    )
    if barrier_reason is not None:
      log.info(
        "strategy match blocked symbol=%s strategy=%s reason=%s",
        symbol, match.strategy, barrier_reason,
      )
      if consume_redis_match:
        await client.delete(strategy_match_key(symbol))
      await _record_gate_reject(
        client, symbol, _opposing_barrier_condition(barrier_reason),
      )
      return None
  cooldown_reason = await _zone_cooldown_reason(
    client, symbol, match.direction, spot.price,
    match.atr, settings.auto_trade_zone_cooldown_atr,
  )
  if cooldown_reason is not None:
    log.info(
      "strategy match blocked symbol=%s strategy=%s reason=%s",
      symbol, match.strategy, cooldown_reason,
    )
    if consume_redis_match:
      await client.delete(strategy_match_key(symbol))
    await _record_gate_reject(client, symbol, "zone_cooldown")
    return None
  if settings.auto_trade_overlap_veto_enabled:
    overlap_reason = _overlapping_zone_conflict_reason(spot.price, market_map)
    if overlap_reason is not None:
      log.info(
        "strategy match blocked symbol=%s strategy=%s reason=%s",
        symbol, match.strategy, overlap_reason,
      )
      if consume_redis_match:
        await client.delete(strategy_match_key(symbol))
      await _record_gate_reject(client, symbol, "overlapping_zone_conflict")
      return None
  distance = (
    match.entry_low - spot.price
    if spot.price < match.entry_low
    else spot.price - match.entry_high
    if spot.price > match.entry_high
    else 0.0
  )
  distance_pips = distance / units.pip_size(symbol)
  distance_limit = max(
    0.0,
    float(settings.auto_trade_max_entry_distance_pips),
  )
  if distance_pips > distance_limit:
    log.info(
      "strategy match skipped id=%s strategy=%s: entry moved %.1f pips",
      match.match_id[:12],
      match.strategy,
      distance_pips,
    )
    if consume_redis_match:
      await client.delete(strategy_match_key(symbol))
    await _record_gate_reject(client, symbol, "strategy_entry_moved")
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
  candidate_id = match.match_id
  claimed = await client.set(
    f"auto_trade:candidate:{candidate_id}",
    "published",
    ex=max(60, settings.auto_trade_candidate_ttl),
    nx=True,
  )
  if not claimed:
    if consume_redis_match:
      await client.delete(strategy_match_key(symbol))
    return None
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
    "range_id": match.range_id,
    "range_low": match.range_low,
    "range_high": match.range_high,
    "full_take_profit_pips": match.full_take_profit_pips,
    "regime": "strategy_match",
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
    raise
  if consume_redis_match:
    await client.delete(strategy_match_key(symbol))
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
  if settings.auto_trade_htf_veto_enabled:
    veto_reason = _htf_veto_reason(
      trend_decision.direction, entry_reference, opposing_zone,
    )
    if veto_reason is not None:
      log.info(
        "auto-trend candidate blocked symbol=%s reason=%s", symbol, veto_reason,
      )
      await _record_gate_reject(client, symbol, "htf_veto")
      return None
  if settings.auto_trade_opposing_barrier_veto_enabled:
    barrier_reason = _opposing_barrier_reason(
      trend_decision.direction, entry_reference, trend_decision.atr,
      htf_zones or [], htf_levels or [],
      settings.auto_trade_opposing_barrier_atr,
    )
    if barrier_reason is not None:
      log.info(
        "auto-trend candidate blocked symbol=%s reason=%s", symbol, barrier_reason,
      )
      await _record_gate_reject(
        client, symbol, _opposing_barrier_condition(barrier_reason),
      )
      return None
  cooldown_reason = await _zone_cooldown_reason(
    client, symbol, trend_decision.direction, entry_reference,
    trend_decision.atr, settings.auto_trade_zone_cooldown_atr,
  )
  if cooldown_reason is not None:
    log.info(
      "auto-trend candidate blocked symbol=%s reason=%s", symbol, cooldown_reason,
    )
    await _record_gate_reject(client, symbol, "zone_cooldown")
    return None
  if settings.auto_trade_overlap_veto_enabled:
    overlap_reason = _overlapping_zone_conflict_reason(entry_reference, market_map)
    if overlap_reason is not None:
      log.info(
        "auto-trend candidate blocked symbol=%s reason=%s", symbol, overlap_reason,
      )
      await _record_gate_reject(client, symbol, "overlapping_zone_conflict")
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
  payload = {
    "version": 3,
    "candidate_id": candidate_id,
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
  if strategy_match is not None:
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
  return {
    "state": state,
    "box_state": decision.state,
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
  private_decision = evaluate_auto_scalp_gate(
    frames,
    symbol=symbol,
    spot_price=None if spot is None or not spot.fresh else spot.price,
  )
  scanner_strategy_match = await _load_strategy_match(client, symbol)
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
  strategy_match = (
    scanner_strategy_match or market_map_decision.match
  )
  decision = private_decision
  gate_source = (
    "scanner_strategy_match"
    if scanner_strategy_match is not None
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
    if strategy_match is None
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
    strategy_match is None
    and decision.state == "candidate"
    # Box-scalp is a mean-reversion play on an actual consolidation; outside
    # chop it must lose the selection entirely (not just get rejected inside
    # _publish_candidate after already winning here), or trend_selected below
    # would wrongly stay False too and nothing would publish at all.
    and regime.state == "chop"
    and (
      trend_decision.state != "candidate"
      or decision.confluence >= trend_decision.confluence
    )
  )
  trend_selected = (
    strategy_match is None
    and trend_decision.state == "candidate"
    and not box_selected
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
  strategy_candidate_id = (
    await _publish_strategy_match(
      client,
      symbol,
      spot,
      strategy_match,
      consume_redis_match=scanner_strategy_match is not None,
      match_source=gate_source,
      htf_zones=htf_zones,
      htf_levels=htf_levels,
      regime=regime,
      market_map=cached_market_map,
    )
    if strategy_match is not None else None
  )
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
  candidate_id = strategy_candidate_id or box_candidate_id or trend_candidate_id
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
  )
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
  pubsub = client.pubsub()
  await pubsub.subscribe("bars:new")
  log.info(
    "ApexVoid Algo watching %s on M1/M5 with strategy_bridge=%s",
    ",".join(sorted(_symbols())),
    settings.auto_trade_strategy_bridge_enabled,
  )
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      try:
        await _handle_event(message.get("data"), source=source, client=client)
      except Exception:
        log.exception("ApexVoid Algo gate tick failed")
  finally:
    await pubsub.unsubscribe("bars:new")
    await pubsub.close()
