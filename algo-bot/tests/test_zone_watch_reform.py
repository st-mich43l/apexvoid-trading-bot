"""A dead zone re-forms only on a NEW structural confirmation (owner 2026-09-21).

A price bucket that was INVALIDATED once refused every later reaction at the
same level for the whole 7-day retention ("zone_watch_locked_or_terminal
state=invalidated"), even when price came back and reacted there again.
"""

from __future__ import annotations

import pytest

from app.analysis.confluence_zone import confluence_zone_id
from app.autotrade import zone_watch as zw
from app.persistence import redis_state

pytestmark = pytest.mark.no_database

ZONE_ID = confluence_zone_id("XAU", "sell", 4357.0, 4368.0, ("supply",), atr=6.0, pip_size=0.1)
DEAD_AT = 10_000


async def _discover(client, *, confirmed_at=None, now=DEAD_AT + 60):
  return await zw.discover_zone_watch(
    client,
    zone_id=ZONE_ID,
    symbol="XAU",
    direction="SELL",
    low=4357.0,
    high=4368.0,
    source_timeframe="M5",
    structural_sources=("key_level",),
    confluence_tags=("supply",),
    grade=zw.GRADE_A,
    now=now,
    confirmed_at=confirmed_at,
  )


async def _seed_dead(client, state):
  record, _ = await _discover(client, now=DEAD_AT - 600)
  await zw.transition_zone_watch(client, ZONE_ID, zw.WATCHING_RETEST, reason_code="t")
  if state == zw.CONSUMED:
    await zw.transition_zone_watch(client, ZONE_ID, zw.PUBLISHED_LOCKED, reason_code="t")
  return await zw.transition_zone_watch(
    client, ZONE_ID, state, reason_code="t", terminal_at=DEAD_AT,
  )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [zw.INVALIDATED, zw.EXPIRED])
async def test_new_confirmation_after_cooldown_reforms_a_dead_zone(state):
  client = redis_state.get_client()
  await _seed_dead(client, state)

  record, created = await _discover(
    client, confirmed_at=DEAD_AT + zw.ZONE_REFORM_COOLDOWN_SECONDS + 1,
  )

  assert created is True
  assert record.state == zw.DISCOVERED
  assert record.touch_count == 0 and record.episode_id is None
  assert record.last_rearm_reason == f"reformed_after_{state}"
  assert record in await zw.list_active_zone_watches(client, symbol="XAU")


@pytest.mark.asyncio
async def test_confirmation_inside_the_cooldown_or_before_death_stays_dead():
  client = redis_state.get_client()
  await _seed_dead(client, zw.INVALIDATED)

  for confirmed_at in (DEAD_AT - 5, DEAD_AT, DEAD_AT + zw.ZONE_REFORM_COOLDOWN_SECONDS):
    record, created = await _discover(client, confirmed_at=confirmed_at)
    assert created is False
    assert record.state == zw.INVALIDATED


@pytest.mark.asyncio
async def test_a_detector_that_only_re_sees_the_old_structure_never_resurrects():
  client = redis_state.get_client()
  await _seed_dead(client, zw.INVALIDATED)

  record, created = await _discover(client, confirmed_at=None)

  assert created is False
  assert record.state == zw.INVALIDATED


@pytest.mark.asyncio
async def test_consumed_zone_never_reforms():
  client = redis_state.get_client()
  await _seed_dead(client, zw.CONSUMED)

  record, created = await _discover(client, confirmed_at=DEAD_AT + 10_000)

  assert created is False
  assert record.state == zw.CONSUMED


@pytest.mark.asyncio
async def test_transition_to_terminal_stamps_terminal_at_and_it_round_trips():
  client = redis_state.get_client()
  await _discover(client, now=DEAD_AT - 600)
  await zw.transition_zone_watch(client, ZONE_ID, zw.WATCHING_RETEST, reason_code="t")

  record, _ = await zw.transition_zone_watch(
    client, ZONE_ID, zw.INVALIDATED, reason_code="zone_decisively_broken",
  )

  assert record.terminal_at is not None and record.terminal_at > 0
  again = zw.ZoneWatch.from_dict(record.to_dict())
  assert again.terminal_at == record.terminal_at


@pytest.mark.asyncio
async def test_reformed_zone_can_be_watched_and_invalidated_again():
  client = redis_state.get_client()
  await _seed_dead(client, zw.INVALIDATED)
  await _discover(client, confirmed_at=DEAD_AT + 1_000)
  await zw.transition_zone_watch(client, ZONE_ID, zw.WATCHING_RETEST, reason_code="t")

  record, _ = await zw.transition_zone_watch(
    client, ZONE_ID, zw.INVALIDATED, reason_code="zone_decisively_broken",
  )

  assert record.state == zw.INVALIDATED
  assert record.terminal_at is not None and record.terminal_at > DEAD_AT
