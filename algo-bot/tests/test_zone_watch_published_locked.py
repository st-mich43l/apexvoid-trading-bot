"""ZoneWatch PUBLISHED_LOCKED / CONSUMED / rearm (no_database + fakeredis)."""

from __future__ import annotations

import fakeredis
import pytest

from app.autotrade import zone_watch as zw


pytestmark = pytest.mark.no_database


@pytest.fixture
def client():
  redis = fakeredis.FakeAsyncRedis(decode_responses=True)
  redis._apexvoid_allow_non_atomic_test_fallback = True
  return redis


async def _seed(client, zone_id: str = "zone-lock-1") -> str:
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id=zone_id,
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M5",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
  )
  await zw.transition_zone_watch(client, record.zone_id, zw.WATCHING_RETEST)
  await zw.transition_zone_watch(client, record.zone_id, zw.EVALUATING)
  return record.zone_id


@pytest.mark.asyncio
async def test_successful_handoff_locks_published(client):
  zone_id = await _seed(client)
  locked = await zw.lock_zone_watch_published(
    client, zone_id, plan_id="v8:plan-1",
  )
  assert locked.state == zw.PUBLISHED_LOCKED
  assert locked.last_plan_id == "v8:plan-1"
  assert not zw.is_actively_watchable(locked)


@pytest.mark.asyncio
async def test_broker_reject_rearms_valid_structure(client):
  zone_id = await _seed(client)
  await zw.lock_zone_watch_published(client, zone_id, plan_id="v8:plan-1")
  rearmed = await zw.rearm_zone_watch(
    client, zone_id, reason_code="broker_reject_before_fill",
  )
  assert rearmed.state == zw.WATCHING_RETEST
  assert rearmed.last_plan_id == "v8:plan-1"
  assert rearmed.last_rearm_reason == "broker_reject_before_fill"
  assert zw.is_actively_watchable(rearmed)


@pytest.mark.asyncio
async def test_plan_expiry_rearms_via_outcome_helper(client):
  zone_id = await _seed(client)
  await zw.lock_zone_watch_published(client, zone_id, plan_id="v8:plan-2")
  updated = await zw.apply_zone_watch_plan_outcome(
    client, zone_id, outcome="expired", reason_code="no_fill_expiry",
  )
  assert updated is not None
  assert updated.state == zw.WATCHING_RETEST
  assert updated.last_rearm_reason == "no_fill_expiry"


@pytest.mark.asyncio
async def test_first_fill_consumes_thesis(client):
  zone_id = await _seed(client)
  await zw.lock_zone_watch_published(client, zone_id, plan_id="v8:plan-3")
  consumed = await zw.apply_zone_watch_plan_outcome(
    client, zone_id, outcome="fill", reason_code="broker_fill",
  )
  assert consumed is not None
  assert consumed.state == zw.CONSUMED
  assert not zw.is_actively_watchable(consumed)


@pytest.mark.asyncio
async def test_touch_count_alone_never_terminals(client):
  zone_id = await _seed(client)
  for _ in range(12):
    await zw.record_zone_touch(client, zone_id)
  record = await zw.load_zone_watch(client, zone_id)
  assert record is not None
  assert record.touch_count == 12
  assert record.state not in zw.TERMINAL_ZONE_WATCH_STATES


def test_zone_watch_version_bumped_for_lifecycle():
  assert zw.ZONE_WATCH_VERSION >= 3
  assert zw.PUBLISHED_LOCKED in zw.ZONE_WATCH_STATES
  assert zw.CONSUMED in zw.ZONE_WATCH_STATES
  assert zw.EXPIRED in zw.ZONE_WATCH_STATES


def test_exhausted_alias_migrates_on_load():
  record = zw.ZoneWatch.from_dict({
    "zone_id": "z",
    "symbol": "XAU",
    "direction": "SELL",
    "low": 1.0,
    "high": 2.0,
    "state": "exhausted",
    "grade": "A",
  })
  assert record.state == zw.EXPIRED
