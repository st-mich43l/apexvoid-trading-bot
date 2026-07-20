"""Redis worker for the independent automatic M1 range-scalp gate.

This worker consumes only cTrader OHLC/spot keys and writes executable
candidates to the auto-trade Redis stream. It deliberately has no scanner,
forming-signal, Market Map, detector, or Telegram dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from typing import Any

from app import redis_state
from app.auto_scalp_gate import AutoScalpDecision, evaluate_auto_scalp_gate
from app.config import settings
from app.dedup import event_in_window
from app.ohlc_source import RedisOHLCSource


log = logging.getLogger(__name__)
EXECUTION_TIMEFRAME = "M1"
CONTEXT_TIMEFRAMES = ("M5", "M15")


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


def _candidate_id(
  symbol: str,
  trigger_ts: str,
  decision: AutoScalpDecision,
) -> str:
  rail = decision.rail
  if rail is None or decision.direction is None:
    raise ValueError("candidate decision requires a rail and direction")
  raw = (
    f"v1|auto-range|{symbol.upper()}|M1|{trigger_ts}|"
    f"{decision.direction.upper()}|{rail.low:.5f}|{rail.high:.5f}"
  )
  return hashlib.sha256(raw.encode("ascii")).hexdigest()


async def _publish_candidate(
  client: Any,
  symbol: str,
  event_ts: str,
  spot: AutoTradeSpot | None,
  decision: AutoScalpDecision,
) -> str | None:
  if (
    not settings.auto_trade_enabled
    or spot is None
    or not spot.fresh
    or decision.state != "candidate"
    or decision.rail is None
    or decision.direction is None
    or decision.confluence < max(1, settings.auto_trade_min_confluence)
  ):
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
    "version": 1,
    "candidate_id": candidate_id,
    "symbol": symbol.upper(),
    "timeframe": EXECUTION_TIMEFRAME,
    "setup": "Auto Range Scalp",
    "mode": "auto_range_scalp",
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
    "auto-scalp candidate published id=%s symbol=%s direction=%s",
    candidate_id[:12],
    symbol,
    decision.direction,
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
) -> dict[str, Any]:
  rail = decision.rail
  target = decision.target
  return {
    "state": decision.state,
    "symbol": symbol,
    "tf": EXECUTION_TIMEFRAME,
    "event_ts": event_ts,
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "trigger": decision.trigger,
    "direction": decision.direction,
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
    "rail_count": decision.rail_count,
    "spot_fresh": None if spot is None else spot.fresh,
    "candidate_id": candidate_id,
    "published": candidate_id is not None,
    "frames": {
      timeframe: len(frame)
      for timeframe, frame in sorted(frames.items())
    },
  }


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
  candidate_id = await _publish_candidate(
    client,
    symbol,
    event_ts,
    spot,
    decision,
  )
  payload = _status_payload(
    decision,
    symbol=symbol,
    event_ts=event_ts,
    frames=frames,
    spot=spot,
    candidate_id=candidate_id,
  )
  encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
  await client.set("auto_trade:last_gate", encoded)
  await client.set(f"auto_trade:last_gate:{symbol}", encoded)
  log.info(
    "independent auto-scalp gate symbol=%s state=%s trigger=%s "
    "direction=%s candidate=%s",
    symbol,
    decision.state,
    decision.trigger or "-",
    decision.direction or "-",
    candidate_id[:12] if candidate_id else "-",
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
