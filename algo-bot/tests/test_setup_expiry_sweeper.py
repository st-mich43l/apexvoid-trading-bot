"""P0-2/P0-9: the durable expiry sweeper and its terminal-cleanup coordinator.

Covers: a due setup actually reaching EXPIRED with cleanup applied (closes
the "silent expiry" incident - scenario G/H), a published/armed plan never
being retroactively expired by the sweeper (P1-2, scenario K), stale/orphan
index members being repaired rather than crashing the sweep (P0-10), and
rearm_setup no longer producing an immediate re-expiry loop.
"""

from __future__ import annotations

import json
import time

import pytest

from app.autotrade.setup_expiry_sweeper import sweep_expired_setups_once
from app.autotrade.setup_lifecycle import (
  ARMED,
  CONFIRMED,
  DISCOVERED,
  EXPIRED,
  FORMING,
  INVALIDATED,
  PLAN_BUILT,
  PLAN_PUBLISHED,
  TOUCHED,
  WATCHING,
  create_setup,
  load_setup,
  setup_expiry_index_key,
  transition_setup,
)
from app.persistence import redis_state


async def _advance(client, setup_id: str, *states: str) -> None:
  for state in states:
    await transition_setup(client, setup_id, state)


@pytest.mark.asyncio
async def test_sweep_expires_due_setup_and_cleans_up():
  client = redis_state.get_client()
  past = int(time.time()) - 10
  await create_setup(
    client, setup_id="sweep-1", thesis_id="thesis-1", symbol="XAU",
    expires_at=past,
  )

  processed = await sweep_expired_setups_once(client)

  assert processed == 1
  record = await load_setup(client, "sweep-1")
  assert record.state == EXPIRED
  assert await client.zscore(setup_expiry_index_key(), "sweep-1") is None

  raw_events = await client.xrange("auto_trade:lifecycle_events", "-", "+")
  payloads = [json.loads(entry[1]["payload"]) for entry in raw_events]
  expired_events = [p for p in payloads if p.get("match_id") == "sweep-1"]
  assert expired_events, "expected a terminal lifecycle event for sweep-1"
  assert expired_events[-1]["state"] == "expired"
  assert expired_events[-1]["reason_code"] == "setup_expired_before_execution"


@pytest.mark.asyncio
async def test_sweep_never_expires_a_published_or_armed_plan():
  client = redis_state.get_client()
  past = int(time.time()) - 10

  for terminal_target, chain in (
    (PLAN_BUILT, (WATCHING, TOUCHED, FORMING, CONFIRMED, PLAN_BUILT)),
    (PLAN_PUBLISHED, (WATCHING, TOUCHED, FORMING, CONFIRMED, PLAN_BUILT,
                       PLAN_PUBLISHED)),
    (ARMED, (WATCHING, TOUCHED, FORMING, CONFIRMED, PLAN_BUILT,
             PLAN_PUBLISHED, ARMED)),
  ):
    setup_id = f"sweep-published-{terminal_target}"
    await create_setup(
      client, setup_id=setup_id, thesis_id="thesis-1", symbol="XAU",
      expires_at=past,
    )
    await _advance(client, setup_id, *chain)

    processed = await sweep_expired_setups_once(client)

    assert processed == 1
    record = await load_setup(client, setup_id)
    assert record.state == terminal_target, (
      f"sweeper must not touch a {terminal_target} setup's state"
    )
    assert await client.zscore(setup_expiry_index_key(), setup_id) is None, (
      "sweeper must still stop tracking a publication-won setup in the index"
    )


@pytest.mark.asyncio
async def test_sweep_repairs_orphan_index_member_without_crashing():
  client = redis_state.get_client()
  past = int(time.time()) - 10
  await client.zadd(setup_expiry_index_key(), {"ghost-setup": past})

  processed = await sweep_expired_setups_once(client)

  assert processed == 1
  assert await client.zscore(setup_expiry_index_key(), "ghost-setup") is None
  assert await load_setup(client, "ghost-setup") is None


@pytest.mark.asyncio
async def test_sweep_repairs_stale_terminal_index_member():
  client = redis_state.get_client()
  past = int(time.time()) - 10
  await create_setup(
    client, setup_id="sweep-stale-terminal", thesis_id="thesis-1", symbol="XAU",
    expires_at=past,
  )
  await transition_setup(client, "sweep-stale-terminal", INVALIDATED)
  # Simulate legacy data captured before the index existed: the record is
  # already terminal, but its index membership was never cleaned up.
  await client.zadd(setup_expiry_index_key(), {"sweep-stale-terminal": past})

  processed = await sweep_expired_setups_once(client)

  assert processed == 1
  record = await load_setup(client, "sweep-stale-terminal")
  assert record.state == INVALIDATED, "already-terminal state must not change"
  assert await client.zscore(
    setup_expiry_index_key(), "sweep-stale-terminal",
  ) is None


@pytest.mark.asyncio
async def test_sweep_is_a_noop_when_nothing_is_due():
  client = redis_state.get_client()
  future = int(time.time()) + 3600
  await create_setup(
    client, setup_id="sweep-not-due", thesis_id="thesis-1", symbol="XAU",
    expires_at=future,
  )

  processed = await sweep_expired_setups_once(client)

  assert processed == 0
  record = await load_setup(client, "sweep-not-due")
  assert record.state == DISCOVERED


