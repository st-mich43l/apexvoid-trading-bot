"""Setup lifecycle state machine (Phase 2 of the TradePlan V8 migration).

Covers: idempotent discovery, the full valid transition path, repeated
detector cycles not re-emitting a transition, illegal transitions being
rejected (including re-confirming an already-planned setup), rearm requiring
an explicit condition, and one-active-TradePlan-per-thesis claiming.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.autotrade import setup_lifecycle
from app.autotrade.setup_lifecycle import (
  ARMED,
  ARMED_WAITING_TRIGGER,
  CANCELLED,
  CONFIRMED,
  CONSUMED,
  DISCOVERED,
  FORMING,
  INVALIDATED,
  PLAN_BUILT,
  PLAN_PUBLISHED,
  READY_EVENT_ENQUEUED,
  TOUCHED,
  WATCHING,
  SetupLifecycleError,
  SetupRecord,
  SetupTransitionConflict,
  WORKER_ACKNOWLEDGED,
  claim_active_thesis,
  create_setup,
  is_publishable_setup_state,
  load_setup,
  normalize_setup_state,
  rearm_setup,
  release_active_thesis,
  setup_key,
  transition_setup,
)
from app.autotrade.worker import (
  parse_cycle_owner_intent_id,
  terminal_state_for_preflight_failure,
)
from app.persistence import redis_state


def test_parse_cycle_owner_intent_id_extracts_from_json_payload():
  """P1-5: publish_ranked_cycle stores the cycle owner as a JSON object -
  winner_intent_id must be the parsed intent_id, not the raw blob.
  """
  raw = (
    b'{"symbol":"XAU","cycle_id":"1722168600","intent_id":"strategy:abc",'
    b'"setup_id":"abc","plan_id":"v8:abc","published_at":1722168600}'
  )
  assert parse_cycle_owner_intent_id(raw) == "strategy:abc"


def test_parse_cycle_owner_intent_id_falls_back_for_legacy_scalar_values():
  assert parse_cycle_owner_intent_id(b"strategy:legacy-owner") == (
    "strategy:legacy-owner"
  )
  assert parse_cycle_owner_intent_id("strategy:legacy-owner") == (
    "strategy:legacy-owner"
  )


def test_parse_cycle_owner_intent_id_handles_missing_and_malformed_values():
  assert parse_cycle_owner_intent_id(None) is None
  assert parse_cycle_owner_intent_id(b"{not valid json") == "{not valid json"
  assert parse_cycle_owner_intent_id(b'{"cycle_id":"1"}') == '{"cycle_id":"1"}'


def test_terminal_preflight_failure_mapping_never_invalidates_post_plan_states():
  assert terminal_state_for_preflight_failure(DISCOVERED) == INVALIDATED
  assert terminal_state_for_preflight_failure(READY_EVENT_ENQUEUED) == INVALIDATED
  assert terminal_state_for_preflight_failure(PLAN_BUILT) == CANCELLED
  assert terminal_state_for_preflight_failure(PLAN_PUBLISHED) is None
  assert terminal_state_for_preflight_failure(ARMED) is None
  assert terminal_state_for_preflight_failure(CONSUMED) is None


def test_legacy_handoff_states_normalize_to_confirmed_for_publish():
  for legacy in (
    READY_EVENT_ENQUEUED, WORKER_ACKNOWLEDGED, ARMED_WAITING_TRIGGER,
  ):
    assert normalize_setup_state(legacy) == CONFIRMED
    assert is_publishable_setup_state(legacy) is True
  assert normalize_setup_state(CONFIRMED) == CONFIRMED
  assert is_publishable_setup_state(PLAN_BUILT) is True
  assert is_publishable_setup_state(PLAN_PUBLISHED) is False


@pytest.mark.asyncio
async def test_create_setup_is_idempotent():
  client = redis_state.get_client()

  first, created_first = await create_setup(
    client, setup_id="setup-1", thesis_id="thesis-1", symbol="XAU",
  )
  second, created_second = await create_setup(
    client, setup_id="setup-1", thesis_id="thesis-1", symbol="XAU",
  )

  assert created_first is True
  assert created_second is False
  assert first.state == DISCOVERED
  assert second.setup_id == first.setup_id


@pytest.mark.asyncio
async def test_full_valid_transition_path_reaches_consumed():
  client = redis_state.get_client()
  await create_setup(client, setup_id="setup-2", thesis_id="thesis-2", symbol="XAU")

  path = [
    WATCHING, TOUCHED, FORMING, CONFIRMED, PLAN_BUILT, PLAN_PUBLISHED,
    ARMED, CONSUMED,
  ]
  for state in path:
    record, changed = await transition_setup(client, "setup-2", state)
    assert changed is True
    assert record.state == state

  final = await load_setup(client, "setup-2")
  assert final.state == CONSUMED


@pytest.mark.asyncio
async def test_repeated_same_state_transition_does_not_change():
  client = redis_state.get_client()
  await create_setup(client, setup_id="setup-3", thesis_id="thesis-3", symbol="XAU")
  await transition_setup(client, "setup-3", WATCHING)
  await transition_setup(client, "setup-3", TOUCHED)
  await transition_setup(client, "setup-3", FORMING)

  # A repeated detector cycle re-reporting FORMING must not be treated as a
  # new transition - this is what stops a new Telegram card being emitted
  # every scanner cycle for the same setup.
  record, changed = await transition_setup(client, "setup-3", FORMING)

  assert changed is False
  assert record.state == FORMING


@pytest.mark.asyncio
async def test_illegal_transition_is_rejected():
  client = redis_state.get_client()
  await create_setup(client, setup_id="setup-4", thesis_id="thesis-4", symbol="XAU")

  with pytest.raises(SetupLifecycleError, match="illegal setup transition"):
    await transition_setup(client, "setup-4", CONFIRMED)


@pytest.mark.asyncio
async def test_confirmed_can_publish_directly_to_plan_built():
  # Direct publication path: CONFIRMED may jump straight to PLAN_BUILT
  # without READY / ACK / ARMED wait nodes.
  client = redis_state.get_client()
  await create_setup(client, setup_id="setup-4b", thesis_id="thesis-4b", symbol="XAU")
  await transition_setup(client, "setup-4b", WATCHING)
  await transition_setup(client, "setup-4b", TOUCHED)
  await transition_setup(client, "setup-4b", FORMING)
  await transition_setup(client, "setup-4b", CONFIRMED)

  record, changed = await transition_setup(client, "setup-4b", PLAN_BUILT)
  assert changed is True
  assert record.state == PLAN_BUILT


@pytest.mark.asyncio
async def test_legacy_pre_plan_redis_states_can_only_advance_to_plan_built():
  # In-flight Redis records may still carry removed handoff nodes. New code
  # never writes them, but migration allows them to finish into PLAN_BUILT.
  import json
  import time

  client = redis_state.get_client()
  for legacy in (
    READY_EVENT_ENQUEUED, WORKER_ACKNOWLEDGED, ARMED_WAITING_TRIGGER,
  ):
    setup_id = f"setup-legacy-{legacy}"
    await create_setup(
      client, setup_id=setup_id, thesis_id=f"thesis-{legacy}", symbol="XAU",
    )
    for state in (WATCHING, TOUCHED, FORMING, CONFIRMED):
      await transition_setup(client, setup_id, state)
    current = await load_setup(client, setup_id)
    assert current is not None
    seeded = SetupRecord(
      **{**current.to_dict(), "state": legacy, "updated_at": int(time.time())},
    )
    await client.set(
      setup_key(setup_id),
      json.dumps(seeded.to_dict(), separators=(",", ":")),
    )

    with pytest.raises(SetupLifecycleError, match="unknown setup state"):
      await transition_setup(client, setup_id, legacy)

    record, changed = await transition_setup(client, setup_id, PLAN_BUILT)
    assert changed is True
    assert record.state == PLAN_BUILT


@pytest.mark.asyncio
async def test_cannot_re_confirm_an_already_planned_setup():
  # This is the "old confirmations must not create new plans repeatedly"
  # requirement: once a setup has moved past CONFIRMED, the graph has no
  # edge back into it.
  client = redis_state.get_client()
  await create_setup(client, setup_id="setup-5", thesis_id="thesis-5", symbol="XAU")
  await transition_setup(client, "setup-5", WATCHING)
  await transition_setup(client, "setup-5", TOUCHED)
  await transition_setup(client, "setup-5", FORMING)
  await transition_setup(client, "setup-5", CONFIRMED)
  await transition_setup(client, "setup-5", PLAN_BUILT)

  with pytest.raises(SetupLifecycleError, match="illegal setup transition"):
    await transition_setup(client, "setup-5", CONFIRMED)


@pytest.mark.asyncio
async def test_transition_on_unknown_setup_id_raises():
  client = redis_state.get_client()

  with pytest.raises(SetupLifecycleError, match="unknown setup_id"):
    await transition_setup(client, "does-not-exist", WATCHING)


@pytest.mark.asyncio
async def test_rearm_requires_condition_and_terminal_state():
  client = redis_state.get_client()
  await create_setup(client, setup_id="setup-6", thesis_id="thesis-6", symbol="XAU")

  with pytest.raises(SetupLifecycleError, match="rearm_condition"):
    await rearm_setup(client, "setup-6", rearm_condition="")

  with pytest.raises(SetupLifecycleError, match="not invalidated/expired"):
    await rearm_setup(client, "setup-6", rearm_condition="new structure formed")

  await transition_setup(client, "setup-6", WATCHING)
  await transition_setup(client, "setup-6", INVALIDATED)

  rearmed = await rearm_setup(
    client, "setup-6", rearm_condition="liquidity swept, fresh demand formed",
  )
  assert rearmed.state == DISCOVERED
  assert rearmed.invalidation_condition is None


@pytest.mark.asyncio
async def test_claim_active_thesis_blocks_a_second_distinct_setup():
  client = redis_state.get_client()

  first = await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-7", setup_id="setup-7a",
  )
  second = await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-7", setup_id="setup-7b",
  )
  same_owner_again = await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-7", setup_id="setup-7a",
  )

  assert first is True
  assert second is False
  assert same_owner_again is True


@pytest.mark.asyncio
async def test_release_active_thesis_allows_a_new_claim():
  client = redis_state.get_client()
  await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-8", setup_id="setup-8a",
  )

  await release_active_thesis(
    client, symbol="XAU", thesis_id="thesis-8", setup_id="setup-8a",
  )
  reclaimed = await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-8", setup_id="setup-8b",
  )

  assert reclaimed is True


@pytest.mark.asyncio
async def test_release_active_thesis_is_a_no_op_for_a_non_owner():
  client = redis_state.get_client()
  await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-9", setup_id="setup-9a",
  )

  # A stale setup trying to release a thesis it lost the claim race for
  # must not evict the real owner.
  await release_active_thesis(
    client, symbol="XAU", thesis_id="thesis-9", setup_id="setup-9-not-the-owner",
  )
  still_owned_by_a = await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-9", setup_id="setup-9a",
  )

  assert still_owned_by_a is True


@pytest.mark.asyncio
async def test_concurrent_transitions_from_a_stale_read_never_silently_overwrite(
  monkeypatch,
):
  """P0-3: two workers racing the same setup_id from the same source state
  must not both believe their stale read is still current - the loser must
  see SetupTransitionConflict, never a silent last-write-wins clobber.

  Exercises transition_setup's real compare-and-set path (the Lua script
  under real Redis, the equivalent Python fallback under fakeredis) by
  using asyncio.Event to force a slow reader's write to land AFTER a fast
  competing transition has already moved the setup somewhere else.
  """
  client = redis_state.get_client()
  await create_setup(client, setup_id="cas-race", thesis_id="t1", symbol="XAU")
  await transition_setup(client, "cas-race", WATCHING)

  real_load_setup = setup_lifecycle.load_setup
  first_read_done = asyncio.Event()
  second_may_proceed = asyncio.Event()
  read_count = {"n": 0}

  async def delayed_load_setup(client_, setup_id):
    record = await real_load_setup(client_, setup_id)
    if setup_id == "cas-race":
      read_count["n"] += 1
      if read_count["n"] == 1:
        first_read_done.set()
        await second_may_proceed.wait()
    return record

  monkeypatch.setattr(setup_lifecycle, "load_setup", delayed_load_setup)

  async def slow_transition():
    return await transition_setup(
      client, "cas-race", TOUCHED, reason_code="slow",
    )

  async def fast_transition():
    await first_read_done.wait()
    result = await transition_setup(
      client, "cas-race", INVALIDATED, reason_code="fast",
    )
    second_may_proceed.set()
    return result

  slow_task = asyncio.create_task(slow_transition())
  fast_task = asyncio.create_task(fast_transition())

  fast_record, fast_changed = await fast_task
  assert fast_changed is True
  assert fast_record.state == INVALIDATED

  with pytest.raises(SetupTransitionConflict):
    await slow_task

  final = await load_setup(client, "cas-race")
  assert final.state == INVALIDATED, (
    "the slow, stale-read transition must never have overwritten the "
    "faster competing transition's result"
  )


def test_active_setup_ttl_floor_survives_a_quiet_weekend():
  """Owner-reported live bug: a filled position's canonical record must
  not lapse just because nothing touched it for a normal weekend gap
  (fill Friday, market closed until Sunday night, ~60h of silence). The
  record's TTL re-anchors from ``expires_at`` (the setup's short business
  deadline), not from activation, so once ``now`` is past it the floor
  alone decides how long the record survives with no further event.

  A too-short floor let this record vanish mid-weekend on a real USDJPY
  position; startup reconciliation then read load_setup() as None and
  deleted the still-needed forming-card Telegram mapping, so the next
  real event (order_filled / position_closed) found no card and posted a
  duplicate root card instead of threading onto the original.
  """
  weekend_ago = int(time.time()) - 3 * 24 * 3600
  record = SetupRecord(
    setup_id="ttl-weekend-gap",
    thesis_id="thesis-1",
    symbol="USDJPY",
    state=PLAN_BUILT,
    expires_at=weekend_ago,
  )

  ttl = setup_lifecycle._ttl_for(record)

  assert ttl >= 30 * 24 * 3600


def test_terminal_setup_ttl_floor_is_at_least_thirty_days():
  record = SetupRecord(
    setup_id="ttl-terminal",
    thesis_id="thesis-1",
    symbol="XAU",
    state=CONSUMED,
  )

  ttl = setup_lifecycle._ttl_for(record)

  assert ttl == 30 * 24 * 3600
