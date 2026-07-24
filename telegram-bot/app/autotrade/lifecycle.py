"""Candidate lifecycle and demo-evaluation metrics stored in Redis."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any

from app.core.config import settings


LIFECYCLE_STATES = (
  "detected",
  "analysis_only",
  "auto_ready",
  "tracked",
  "waiting_for_price",
  "candidate_published",
  "executor_received",
  "routing_selected",
  "order_planned",
  "order_submitted",
  "order_accepted",
  "order_filled",
  "managing",
  "partially_closed",
  "closed",
  "rejected",
  "expired",
  "invalidated",
  "cancelled",
  "error",
)


def lifecycle_key(candidate_id: str) -> str:
  return f"auto_trade:lifecycle:{candidate_id}"


async def emit_lifecycle(
  client: Any,
  state: str,
  *,
  symbol: str,
  candidate_id: str | None = None,
  correlation_id: str | None = None,
  match_id: str | None = None,
  range_id: str | None = None,
  group_id: str | None = None,
  strategy: str | None = None,
  strategy_family: str | None = None,
  direction: str | None = None,
  timeframe: str | None = None,
  entry_zone: dict[str, float] | None = None,
  current_price: float | None = None,
  target_plan: Any = None,
  stop_plan: Any = None,
  position_ids: list[int] | None = None,
  pending_order_ids: list[int] | None = None,
  reason_code: str | None = None,
  message: str = "",
  measured: dict[str, Any] | None = None,
  account_type: str | None = None,
  broker: str | None = None,
  publish_status: bool = False,
) -> dict[str, Any]:
  if state not in LIFECYCLE_STATES:
    raise ValueError(f"unsupported lifecycle state: {state}")
  candidate = candidate_id or match_id or correlation_id or "service"
  state_key = f"auto_trade:lifecycle_state:{candidate}"
  previous = await client.get(state_key)
  if isinstance(previous, bytes):
    previous = previous.decode()
  now = datetime.now(timezone.utc)
  event = {
    "lifecycle_id": uuid.uuid4().hex,
    "correlation_id": correlation_id or candidate,
    "candidate_id": candidate_id,
    "match_id": match_id,
    "range_id": range_id,
    "group_id": group_id,
    "symbol": symbol.upper(),
    "strategy": strategy,
    "strategy_family": strategy_family,
    "direction": None if direction is None else direction.upper(),
    "timeframe": timeframe,
    "entry_zone": entry_zone,
    "current_price": current_price,
    "target_plan": target_plan,
    "stop_plan": stop_plan,
    "position_ids": position_ids or [],
    "pending_order_ids": pending_order_ids or [],
    "timestamp": int(now.timestamp()),
    "timestamp_iso": now.isoformat(),
    "state": state,
    "previous_state": previous,
    "reason_code": reason_code,
    "message": message,
    "configuration_profile": settings.auto_trade_profile,
    "account_type": account_type,
    "broker": broker,
    "measured": measured or {},
  }
  encoded = json.dumps(event, separators=(",", ":"), sort_keys=True)
  pipe = client.pipeline()
  pipe.rpush(lifecycle_key(candidate), encoded)
  pipe.ltrim(lifecycle_key(candidate), -100, -1)
  pipe.expire(lifecycle_key(candidate), max(86400, settings.auto_trade_candidate_ttl))
  pipe.set(state_key, state, ex=max(86400, settings.auto_trade_candidate_ttl))
  pipe.set(f"auto_trade:last_lifecycle:{symbol.upper()}", encoded)
  pipe.xadd(
    "auto_trade:lifecycle_events",
    {"payload": encoded},
    maxlen=5000,
    approximate=True,
  )
  hour = now.strftime("%H")
  session = (
    "asia" if now.hour < 7
    else "london" if now.hour < 13
    else "new_york" if now.hour < 21
    else "rollover"
  )
  dimensions = {
    "strategy": strategy,
    "strategy_family": strategy_family,
    "direction": direction,
    "range_side": direction if range_id else None,
    "detector": strategy if match_id else None,
    "execution_route": state,
    "rejection_reason": reason_code,
    "hour_utc": hour,
    "session_utc": session,
  }
  for name, value in dimensions.items():
    if value:
      pipe.hincrby(
        f"auto_trade:evaluation:{symbol.upper()}:{name}",
        f"{state}:{value}",
        1,
      )
  if publish_status:
    pipe.xadd(
      settings.auto_trade_event_stream,
      {"payload": json.dumps({
        "type": state,
        **event,
      }, separators=(",", ":"), sort_keys=True)},
      maxlen=max(100, settings.auto_trade_stream_maxlen),
      approximate=True,
    )
  await pipe.execute()
  return event


async def increment_metric(
  client: Any,
  name: str,
  *,
  symbol: str = "XAU",
  dimensions: dict[str, str] | None = None,
) -> None:
  key = f"auto_trade:metrics:{symbol.upper()}"
  await client.hincrby(key, name, 1)
  if dimensions:
    suffix = ":".join(
      f"{key_}={value}"
      for key_, value in sorted(dimensions.items())
      if value
    )
    if suffix:
      await client.hincrby(f"{key}:{name}", suffix, 1)
