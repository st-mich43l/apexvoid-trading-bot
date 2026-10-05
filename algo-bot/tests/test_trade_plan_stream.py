"""execution:trade_plans stream plumbing - publish/read round trip only.

Phase 1: no broker execution, no ownership/claim logic yet (that is Phase 3,
when the C# TradePlanExecutionEngine actually consumes this stream).
"""

from __future__ import annotations

import json
import os

import pytest
from redis.asyncio import Redis

from app.autotrade.trade_plan import TradePlan
from app.autotrade.trade_plan_stream import (
  plan_dedup_key,
  plan_key,
  plan_state_key,
  publish_trade_plan,
  read_plan_state,
  read_trade_plan,
)
from app.persistence import redis_state


def _plan(**overrides) -> TradePlan:
  base = json.loads(
    (
      __import__("pathlib").Path(__file__).resolve().parents[2]
      / "contracts" / "autotrade" / "trade-plan-v8.json"
    ).read_text()
  )
  plan_dict = next(
    case["plan"] for case in base["valid_plans"] if case["name"] == "market_watch_buy"
  )
  plan_dict = {**plan_dict, **overrides}
  return TradePlan.from_dict(plan_dict)


@pytest.mark.asyncio
async def test_publish_writes_stream_entry_and_plan_key():
  client = redis_state.get_client()
  plan = _plan(plan_id="plan-stream-1")

  event_id = await publish_trade_plan(client, plan)

  assert event_id
  assert await client.xlen("execution:trade_plans") == 1
  stored = await client.get(plan_key("plan-stream-1"))
  assert json.loads(stored)["plan_id"] == "plan-stream-1"


@pytest.mark.asyncio
async def test_publish_sets_plan_state_to_published():
  client = redis_state.get_client()
  plan = _plan(plan_id="plan-stream-2")

  await publish_trade_plan(client, plan)

  assert await read_plan_state(client, "plan-stream-2") == "published"
  assert await client.get(plan_state_key("plan-stream-2")) is not None


@pytest.mark.asyncio
async def test_read_trade_plan_round_trips_through_redis():
  client = redis_state.get_client()
  plan = _plan(plan_id="plan-stream-3")

  await publish_trade_plan(client, plan)
  reread = await read_trade_plan(client, "plan-stream-3")

  assert reread is not None
  assert reread.plan_id == plan.plan_id
  assert reread.stop.price == plan.stop.price


@pytest.mark.asyncio
async def test_read_trade_plan_returns_none_for_unknown_id():
  client = redis_state.get_client()

  assert await read_trade_plan(client, "does-not-exist") is None
  assert await read_plan_state(client, "does-not-exist") is None


@pytest.mark.asyncio
async def test_dedup_tombstone_prevents_republish_after_payload_expiry():
  client = redis_state.get_client()
  plan = _plan(plan_id="plan-stream-durable-dedup")

  first = await publish_trade_plan(client, plan)
  await client.delete(
    plan_key(plan.plan_id),
    plan_state_key(plan.plan_id),
  )
  second = await publish_trade_plan(client, plan)

  assert first != "existing"
  assert second == "existing"
  assert await client.xlen("execution:trade_plans") == 1
  assert await client.exists(plan_dedup_key(plan.plan_id))
  assert await client.ttl(plan_dedup_key(plan.plan_id)) >= 86_000


@pytest.mark.real_redis
@pytest.mark.asyncio
async def test_real_redis_plan_dedup_survives_payload_deletion():
  configured = os.getenv("REAL_REDIS_URL")
  if not configured:
    pytest.fail("REAL_REDIS_URL is required")
  source = configured.rsplit("/", 1)[0]
  client = Redis.from_url(f"{source}/11", decode_responses=True)
  await client.flushdb()
  plan = _plan(plan_id="real-plan-dedup")
  try:
    first = await publish_trade_plan(client, plan)
    await client.delete(plan_key(plan.plan_id), plan_state_key(plan.plan_id))
    second = await publish_trade_plan(client, plan)

    assert first != "existing"
    assert second == "existing"
    assert await client.xlen("execution:trade_plans") == 1
    assert await client.ttl(plan_dedup_key(plan.plan_id)) >= 86_000
  finally:
    await client.flushdb()
    await client.aclose()
