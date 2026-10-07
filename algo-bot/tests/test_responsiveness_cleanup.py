"""Event-loop responsiveness: stale scalp armed sweep."""

from __future__ import annotations

import time

import fakeredis
import pytest
import pytest_asyncio

from app.autotrade.setup_expiry_sweeper import sweep_stale_scalp_armed_once
from app.scalping.lifecycle import active_key, save_lifecycle
from app.scalping.models import ARMED, EXPIRED, ScalpLifecycleRecord


pytestmark = pytest.mark.no_database


@pytest_asyncio.fixture
async def client():
  redis = fakeredis.FakeAsyncRedis(decode_responses=True)
  redis._apexvoid_allow_non_atomic_test_fallback = True
  try:
    yield redis
  finally:
    await redis.aclose()


@pytest.mark.asyncio
async def test_sweep_stale_scalp_armed_removes_old_members(client):
  now = int(time.time())
  stale = ScalpLifecycleRecord(
    opportunity_id="opp-stale",
    episode_id="ep-1",
    state=ARMED,
    context_id="ctx-1",
    updated_at=now - 7200,
    reason_code="armed",
    measured={},
  )
  fresh = ScalpLifecycleRecord(
    opportunity_id="opp-fresh",
    episode_id="ep-2",
    state=ARMED,
    context_id="ctx-2",
    updated_at=now - 60,
    reason_code="armed",
    measured={},
  )
  await save_lifecycle(client, "XAU", stale)
  await save_lifecycle(client, "XAU", fresh)
  await client.sadd(active_key("XAU"), "orphan-missing")

  cleaned = await sweep_stale_scalp_armed_once(client, now=now, max_age_seconds=3600)
  assert cleaned == 2
  members = await client.smembers(active_key("XAU"))
  assert members == {"opp-fresh"}
  updated = await client.get("scalp:lifecycle:XAU:opp-stale")
  assert EXPIRED in updated
