"""Range retirement and breakout-retest handoff after a confirmed break."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from typing import Any

from app.autotrade.gate import AutoScalpBox, AutoScalpDecision
from app.autotrade.range_context import RangeContext


BREAKOUT_RETEST_KEY_PREFIX = "auto_trade:breakout_retest"
RANGE_RETIRED_KEY_PREFIX = "auto_trade:box:retired"


def range_retired_key(symbol: str, range_id: str) -> str:
  return f"{RANGE_RETIRED_KEY_PREFIX}:{symbol.upper()}:{range_id}"


def breakout_retest_key(symbol: str) -> str:
  return f"{BREAKOUT_RETEST_KEY_PREFIX}:{symbol.upper()}"


def break_direction(
  *,
  price: float,
  lower: float,
  upper: float,
) -> str | None:
  if not math.isfinite(price):
    return None
  if price > upper:
    return "BUY"
  if price < lower:
    return "SELL"
  return None


def retire_range_context(
  context: RangeContext,
  *,
  direction: str,
  now: int | None = None,
) -> RangeContext:
  """Mark a broken range retired and stamp breakout metadata."""
  stamp = int(now or datetime.now(timezone.utc).timestamp())
  side = direction.upper()
  reason = (
    "bullish breakout" if side == "BUY" else "bearish breakout"
  )
  return replace(
    context,
    state="retired",
    breakout_state="accepted",
    invalidation_reason=reason,
    generated_at=max(context.generated_at, stamp),
  )


def retest_zone_for_break(
  *,
  direction: str,
  lower: float,
  upper: float,
) -> tuple[float, float]:
  """Broken edge becomes the retest zone (high for BUY, low for SELL)."""
  if direction.upper() == "BUY":
    width = max(0.5, (upper - lower) * 0.25)
    return (upper - width, upper)
  width = max(0.5, (upper - lower) * 0.25)
  return (lower, lower + width)


async def persist_breakout_retest_watch(
  client: Any,
  *,
  symbol: str,
  range_id: str,
  direction: str,
  lower: float,
  upper: float,
  ttl: int,
) -> dict[str, Any]:
  zone_low, zone_high = retest_zone_for_break(
    direction=direction,
    lower=lower,
    upper=upper,
  )
  now = int(datetime.now(timezone.utc).timestamp())
  payload = {
    "symbol": symbol.upper(),
    "range_id": range_id,
    "direction": direction.upper(),
    "state": "waiting",
    "zone_low": zone_low,
    "zone_high": zone_high,
    "broken_edge": upper if direction.upper() == "BUY" else lower,
    "break_side": (
      "bullish" if direction.upper() == "BUY" else "bearish"
    ),
    "updated_at": now,
  }
  await client.set(
    breakout_retest_key(symbol),
    json.dumps(payload, separators=(",", ":"), sort_keys=True),
    ex=max(300, ttl),
  )
  return payload


async def load_breakout_retest_watch(
  client: Any,
  symbol: str,
) -> dict[str, Any] | None:
  raw = await client.get(breakout_retest_key(symbol))
  if raw is None:
    return None
  try:
    payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
  except (TypeError, ValueError, json.JSONDecodeError):
    return None
  return payload if isinstance(payload, dict) else None


async def mark_range_retired(
  client: Any,
  *,
  symbol: str,
  range_id: str,
  ttl: int,
) -> None:
  await client.set(
    range_retired_key(symbol, range_id),
    "1",
    ex=max(300, ttl),
  )


async def range_is_retired(
  client: Any,
  *,
  symbol: str,
  range_id: str,
) -> bool:
  return bool(await client.exists(range_retired_key(symbol, range_id)))


def disarmed_side_payload(
  *,
  context: RangeContext,
  direction: str,
  existing: dict[str, Any] | None = None,
  now: int | None = None,
) -> dict[str, Any]:
  stamp = int(now or datetime.now(timezone.utc).timestamp())
  barrier = (
    context.lower_barrier if direction.upper() == "BUY"
    else context.upper_barrier
  )
  prior = existing or {}
  return {
    "range_id": context.range_id,
    "symbol": context.symbol,
    "direction": direction.upper(),
    "state": "DISARMED",
    "candidate_id": prior.get("candidate_id"),
    "pending_order_ids": [],
    "position_ids": prior.get("position_ids", []),
    "target_state": "expired",
    "invalidation_state": context.invalidation_reason or "retired",
    "last_trigger_bar": stamp,
    "last_confirmed_touch": prior.get("last_confirmed_touch"),
    "execution_count": int(prior.get("execution_count") or 0),
    "barrier": {
      "low": barrier.low,
      "high": barrier.high,
      "level": barrier.level,
    },
    "updated_at": stamp,
  }


def status_label_for_retired(context: RangeContext) -> str:
  reason = str(context.invalidation_reason or "breakout")
  return f"retired — {reason}"


def box_break_direction(decision: AutoScalpDecision, price: float) -> str | None:
  box = decision.box
  if box is None:
    return None
  return break_direction(
    price=price,
    lower=float(box.lower.level),
    upper=float(box.upper.level),
  )


def decision_box_levels(
  decision: AutoScalpDecision,
) -> tuple[float, float] | None:
  box = decision.box
  if isinstance(box, AutoScalpBox):
    return float(box.lower.level), float(box.upper.level)
  return None
