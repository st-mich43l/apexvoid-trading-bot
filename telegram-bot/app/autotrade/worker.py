"""Redis worker for the independent automatic M1 range-scalp gate.

This worker consumes only cTrader OHLC/spot keys and writes executable
candidates to the auto-trade Redis stream. It deliberately has no scanner,
forming-signal, Market Map, detector, or Telegram dependency.
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
from app.autotrade.gate import (
  AutoScalpBox,
  AutoScalpDecision,
  AutoScalpRail,
  evaluate_auto_scalp_gate,
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
from app.analysis.types import Zone
from app.analysis.zones import displacement, mark_mitigation, supply_demand


log = logging.getLogger(__name__)
EXECUTION_TIMEFRAME = "M1"
CONTEXT_TIMEFRAMES = ("M5", "M15")
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
    # Mutual exclusion with the trend/breakout strategy family: a box
    # candidate only ever ships while the regime router says "chop".
    # `regime is None` preserves pre-regime-router callers/tests.
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
    "regime": regime.state if regime is not None else "chop",
    "opposing_zone_low": None if opposing_zone is None else opposing_zone.low,
    "opposing_zone_high": None if opposing_zone is None else opposing_zone.high,
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
) -> dict[str, Any]:
  rail = decision.rail
  target = decision.target
  box = decision.box
  trend_routed = regime is not None and regime.state in ("trend", "breakout")
  state = decision.state
  direction = decision.direction
  if trend_routed and trend_decision is not None:
    state = (
      trend_decision.state
      if settings.auto_trade_trend_enabled
      else "trend_disabled"
    )
    direction = trend_decision.direction
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
  decision = evaluate_auto_scalp_gate(
    frames,
    symbol=symbol,
    spot_price=None if spot is None or not spot.fresh else spot.price,
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
    if regime.state in ("trend", "breakout")
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
  scale_context = (
    build_auto_scale_context(
      frames,
      decision,
      spot_price=spot.price,
      cfg=settings,
    )
    if (
      decision.state == "candidate"
      and spot is not None
      and spot.fresh
    ) else None
  )
  htf_zones = _htf_zones(frames, settings)
  box_candidate_id = await _publish_candidate(
    client,
    symbol,
    event_ts,
    spot,
    decision,
    scale_context,
    regime=regime,
    htf_zones=htf_zones,
  )
  trend_candidate_id = await _publish_trend_candidate(
    client,
    symbol,
    event_ts,
    spot,
    regime,
    trend_decision,
    htf_zones=htf_zones,
  )
  candidate_id = box_candidate_id or trend_candidate_id
  if candidate_id is None:
    if decision.state != "candidate":
      await _record_gate_reject(client, symbol, decision.state)
    if regime.state in ("trend", "breakout") and trend_decision.state != "candidate":
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
  )
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set("auto_trade:last_gate", encoded)
  await client.set(f"auto_trade:last_gate:{symbol}", encoded)
  log.info(
    "independent auto-scalp gate symbol=%s state=%s trigger=%s "
    "direction=%s candidate=%s regime=%s",
    symbol,
    payload["state"],
    decision.trigger or "-",
    payload["direction"] or "-",
    candidate_id[:12] if candidate_id else "-",
    regime.state,
  )
  return decision


async def auto_scalp_loop() -> None:
  """Run the auto executor's private OHLC gate subscriber."""
  if not settings.auto_trade_enabled:
    log.info("Independent auto-scalp gate disabled: AUTO_TRADE_ENABLED=false")
    return

  client = redis_state.get_client()
  source = RedisOHLCSource(client)
  pubsub = client.pubsub()
  await pubsub.subscribe("bars:new")
  log.info(
    "Independent auto-scalp gate watching %s on M1 with M5/M15 context",
    ",".join(sorted(_symbols())),
  )
  try:
    async for message in pubsub.listen():
      if message.get("type") != "message":
        continue
      try:
        await _handle_event(message.get("data"), source=source, client=client)
      except Exception:
        log.exception("independent auto-scalp tick failed")
  finally:
    await pubsub.unsubscribe("bars:new")
    await pubsub.close()
