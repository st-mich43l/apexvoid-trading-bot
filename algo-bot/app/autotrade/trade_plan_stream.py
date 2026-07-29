"""execution:trade_plans stream plumbing for TradePlan V7.

Phase 1 of the V7 migration (docs/adr-trade-plan-v7-boundary.md): publishing
here has no broker execution side effect. C# does not consume this stream
for order placement until AUTO_TRADE_CONTRACT_MODE reaches v7_primary; under
shadow_v7 it parses and validates published plans but places no orders.

Key namespace (kept separate from the V6 auto_trade:* namespace so the two
contracts never collide or get silently reinterpreted as each other):

  execution:trade_plans          - XADD stream of published TradePlan V7 JSON
  execution:plan:{plan_id}       - full plan JSON, TTL-bound
  execution:plan_owner:{plan_id} - written by whichever side claims the plan
                                    for execution (Phase 3, not written here)
  execution:plan_state:{plan_id} - "published" | "armed" | ... lifecycle state
  execution:plan_event:{plan_id} - per-plan event history (Phase 2+)
"""

from __future__ import annotations

import json
from typing import Any

from app.autotrade.trade_plan import TradePlan
from app.core.config import settings


_PUBLISH_PLAN_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 'existing'
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('SET', KEYS[2], 'published', 'EX', ARGV[2])
return redis.call(
  'XADD', KEYS[3], 'MAXLEN', '~', ARGV[3], '*', 'payload', ARGV[1]
)
"""


def plan_key(plan_id: str) -> str:
  return f"execution:plan:{plan_id}"


def plan_owner_key(plan_id: str) -> str:
  return f"execution:plan_owner:{plan_id}"


def plan_state_key(plan_id: str) -> str:
  return f"execution:plan_state:{plan_id}"


def plan_event_key(plan_id: str) -> str:
  return f"execution:plan_event:{plan_id}"


async def publish_trade_plan(client: Any, plan: TradePlan) -> str:
  """XADD a validated TradePlan V7 onto execution:trade_plans.

  ``plan`` must already have passed ``TradePlan.validate()`` - this function
  does not re-derive or re-check any planning value, only publishes what
  Python already decided. Returns the stream event id.
  """
  payload = json.dumps(plan.to_dict(), separators=(",", ":"))
  ttl = max(60, plan.expires_at - plan.created_at)

  key = plan_key(plan.plan_id)
  state_key = plan_state_key(plan.plan_id)
  stream = settings.auto_trade_trade_plan_stream
  maxlen = max(100, settings.auto_trade_stream_maxlen)
  try:
    event_id = await client.eval(
      _PUBLISH_PLAN_LUA,
      3,
      key,
      state_key,
      stream,
      payload,
      ttl,
      maxlen,
    )
  except Exception:
    if not getattr(
      client,
      "_apexvoid_allow_non_atomic_test_fallback",
      False,
    ):
      raise
    if await client.exists(key):
      return "existing"
    pipe = client.pipeline(transaction=True)
    pipe.set(key, payload, ex=ttl)
    pipe.set(state_key, "published", ex=ttl)
    pipe.xadd(
      stream,
      {"payload": payload},
      maxlen=maxlen,
      approximate=True,
    )
    results = await pipe.execute()
    event_id = results[-1]
  return (
    event_id.decode()
    if isinstance(event_id, bytes)
    else str(event_id)
  )


async def read_trade_plan(client: Any, plan_id: str) -> TradePlan | None:
  """Look a plan up by id without scanning the stream."""
  raw = await client.get(plan_key(plan_id))
  if raw is None:
    return None
  data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
  return TradePlan.from_dict(data)


async def read_plan_state(client: Any, plan_id: str) -> str | None:
  raw = await client.get(plan_state_key(plan_id))
  if raw is None:
    return None
  return raw.decode() if isinstance(raw, bytes) else raw
