"""Worker admission with an instrument-owned same-direction policy.

Reproduces the 2026-10-12 USDJPY shape on GBPUSD fixtures: a scalp opportunity is
admitted while another same-direction group is open. Without a declared policy
nothing changes; with one the group count is enforced and an unpublished plan
never holds a slot.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from app.analysis_client.provenance import GO_ORIGIN_TAG
from app.autotrade import worker
from app.autotrade.group_exposure import reservation_key
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.setup_lifecycle import CONFIRMED, load_setup
from app.persistence import redis_state
from app.runtime.instruments import (
  EffectiveInstrumentError,
  SameDirectionLimits,
  SameDirectionPolicy,
)
from tests.test_publish_trade_plan_v8 import (
  _confirm_setup,
  _gbpusd_match,
  _gbpusd_spot,
  _seed_live_plan,
)

pytestmark = pytest.mark.no_database


@pytest.fixture(autouse=True)
def _no_news(monkeypatch):
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))


@pytest.fixture
def rejections(monkeypatch):
  seen: list[tuple[str, dict]] = []
  real = worker._record_v8_build_rejected

  async def spy(client, symbol, match, reason_code, message, measured):
    seen.append((reason_code, dict(measured)))
    return await real(client, symbol, match, reason_code, message, measured)

  monkeypatch.setattr(worker, "_record_v8_build_rejected", spy)
  return seen


def _policy(max_groups=1, **limits):
  return SameDirectionPolicy(
    symbol="GBPUSD",
    limits=SameDirectionLimits(max_groups=max_groups, **limits),
  )


async def _executor_snapshot(client, groups=()):
  await client.set("auto_trade:executor_snapshot:GBPUSD", json.dumps({
    "symbol": "GBPUSD", "ready": True, "group_ids": list(groups),
    "updated_at": int(time.time()), "account_equity": 2005.63,
  }))


def _scalp(match_id, **overrides):
  return _gbpusd_match(
    match_id=match_id, direction="BUY", strategy="Fade Scalp", tags=(GO_ORIGIN_TAG,),
    thesis_id=f"thesis-{match_id}", structural_zone_id=f"zone-{match_id}", **overrides,
  )


async def _publish(client, match):
  return await worker._publish_trade_plan_v8(client, "GBPUSD", _gbpusd_spot(), match, frames={})


@pytest.mark.asyncio
async def test_without_a_policy_a_scalp_still_stacks_on_an_open_same_direction_group(monkeypatch, rejections):
  """The incident: nothing bounds the number of same-direction scalp groups where no policy is declared."""
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: None)
  client = redis_state.get_client()
  await _seed_live_plan(client, symbol="GBPUSD", direction="BUY", entry=1.2400)
  match = _scalp("stack-legacy")
  await _confirm_setup(client, match)

  assert await _publish(client, match) is not None, rejections
  assert not rejections


@pytest.mark.asyncio
async def test_with_a_one_group_policy_the_same_scalp_is_refused(monkeypatch, rejections):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(1))
  client = redis_state.get_client()
  await _seed_live_plan(client, symbol="GBPUSD", direction="BUY", entry=1.2400, plan_id="v8:setup-active")
  await _executor_snapshot(client, groups=["v8:setup-active"])
  match = _scalp("stack-capped")
  await _confirm_setup(client, match)

  assert await _publish(client, match) is None
  assert [code for code, _ in rejections] == ["same_direction_group_limit"]
  assert rejections[0][1]["same_direction_groups"] == 1
  assert await client.xlen("execution:trade_plans") == 0
  assert await client.hlen(reservation_key("GBPUSD", "BUY")) == 0


@pytest.mark.asyncio
async def test_the_first_admission_reserves_and_a_second_concurrent_one_is_refused(monkeypatch, rejections):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(1))
  # The incident condition: the 45-minute corridor window has already lapsed
  # for the first plan, so only the group policy stands between the two.
  monkeypatch.setattr(worker, "reserve_entry_zone", AsyncMock(return_value=None))
  client = redis_state.get_client()
  await _executor_snapshot(client)
  first, second = _scalp("first"), _scalp("second")
  await _confirm_setup(client, first)
  await _confirm_setup(client, second)

  assert await _publish(client, first) is not None, rejections
  assert await client.hlen(reservation_key("GBPUSD", "BUY")) == 1
  assert await _publish(client, second) is None
  assert [code for code, _ in rejections] == ["same_direction_group_limit"]


@pytest.mark.asyncio
async def test_a_publication_failure_gives_the_slot_back(monkeypatch, rejections):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(1))
  client = redis_state.get_client()
  await _executor_snapshot(client)
  match = _scalp("fails")
  await _confirm_setup(client, match)

  monkeypatch.setattr(worker, "publish_trade_plan", AsyncMock(side_effect=RuntimeError("stream down")))
  assert await _publish(client, match) is None
  assert await client.hlen(reservation_key("GBPUSD", "BUY")) == 0, "an unpublished plan must not hold a slot"


@pytest.mark.asyncio
async def test_an_unknown_executor_picture_refuses_instead_of_guessing(monkeypatch, rejections):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(3))
  client = redis_state.get_client()  # no executor snapshot
  match = _scalp("unknown")
  await _confirm_setup(client, match)

  assert await _publish(client, match) is None
  # not a verdict on the setup: the intent is kept and looked at again (no terminal rejection)
  assert rejections == []
  assert (await load_setup(client, match.match_id)).state == CONFIRMED
  assert json.loads(await client.get(route_outcome_key("GBPUSD", match.match_id)))["reason_code"] == "exposure_unknown"


@pytest.mark.asyncio
async def test_an_invalid_policy_declaration_refuses_with_its_own_reason(monkeypatch, rejections):
  def _boom(symbol):
    raise EffectiveInstrumentError("GBPUSD exposure.same_direction.max_groups must be an integer >= 1")

  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", _boom)
  client = redis_state.get_client()
  match = _scalp("invalid")
  await _confirm_setup(client, match)

  assert await _publish(client, match) is None
  assert [code for code, _ in rejections] == ["same_direction_policy_invalid"]


@pytest.mark.asyncio
async def test_opposite_exposure_keeps_its_own_independent_policy(monkeypatch, rejections):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(5))
  client = redis_state.get_client()
  await _seed_live_plan(client, symbol="GBPUSD", direction="SELL", entry=1.2501, plan_id="v8:setup-active")
  await _executor_snapshot(client, groups=["v8:setup-active"])
  match = _scalp("opposite")
  await _confirm_setup(client, match)

  assert await _publish(client, match) is None
  assert [code for code, _ in rejections] == ["fx_opposite_position_not_allowed"]


@pytest.mark.asyncio
async def test_a_redis_failure_while_measuring_exposure_leaks_no_claim_and_keeps_the_intent(monkeypatch, rejections):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(1))
  client = redis_state.get_client()
  await _executor_snapshot(client)
  match = _scalp("redis-down")
  await _confirm_setup(client, match)

  async def boom(*args, **kwargs):
    raise ConnectionError("redis went away")

  monkeypatch.setattr(worker, "admit_group", boom)
  assert await _publish(client, match) is None
  assert rejections == []
  assert not [key async for key in client.scan_iter(match="analysis:active_thesis:*")], "the thesis claim leaked"
  assert not [key async for key in client.scan_iter(match="autotrade:entry_overlap:*") if await client.get(key) not in (None, "[]")], "the corridor claim leaked"
  assert json.loads(await client.get(route_outcome_key("GBPUSD", match.match_id)))["reason_code"] == "group_exposure_reservation_unavailable"


@pytest.mark.asyncio
async def test_a_plan_already_on_the_stream_keeps_its_slot_when_a_later_step_fails(monkeypatch, rejections):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(1))
  monkeypatch.setattr(worker, "reserve_entry_zone", AsyncMock(return_value=None))
  client = redis_state.get_client()
  await _executor_snapshot(client)
  first, second = _scalp("on-stream"), _scalp("after")
  await _confirm_setup(client, first)
  await _confirm_setup(client, second)

  real = worker.transition_setup

  async def failing(client_, setup_id, state, **kwargs):
    if state == "plan_published":
      raise worker.SetupLifecycleError("lifecycle refused")
    return await real(client_, setup_id, state, **kwargs)

  monkeypatch.setattr(worker, "transition_setup", failing)
  assert await _publish(client, first) is None
  assert await client.xlen("execution:trade_plans") == 1, "the plan reached the stream"
  assert await client.hlen(reservation_key("GBPUSD", "BUY")) == 1, "so its reservation must stay until the executor shows it"
  monkeypatch.setattr(worker, "transition_setup", real)
  assert await _publish(client, second) is None
  assert [code for code, _ in rejections] == ["same_direction_group_limit"]


@pytest.mark.asyncio
async def test_a_money_limit_is_enforced_from_the_plans_own_stop_and_sizing(monkeypatch, rejections):
  tight = SameDirectionPolicy(symbol="GBPUSD", limits=SameDirectionLimits(max_cumulative_risk=0.5))
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: tight)
  client = redis_state.get_client()
  await _executor_snapshot(client)
  match = _scalp("tight")
  await _confirm_setup(client, match)
  assert await _publish(client, match) is None
  assert [code for code, _ in rejections] == ["same_direction_risk_budget"]
  assert rejections[0][1]["incoming_risk"] > 0.5, "the plan's planned loss is measured, not zero"

  roomy = SameDirectionPolicy(symbol="GBPUSD", limits=SameDirectionLimits(max_cumulative_risk=500.0))
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: roomy)
  other = _scalp("roomy")
  await _confirm_setup(client, other)
  assert await _publish(client, other) is not None
  held = await client.hgetall(reservation_key("GBPUSD", "BUY"))
  assert float(next(iter(held.values())).split("|")[2]) > 0


@pytest.mark.asyncio
async def test_a_refusal_releases_the_claims_it_took(monkeypatch, rejections):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(1))
  client = redis_state.get_client()
  await _seed_live_plan(client, symbol="GBPUSD", direction="BUY", entry=1.2400, plan_id="v8:setup-active")
  await _executor_snapshot(client, groups=["v8:setup-active"])
  match = _scalp("refused")
  await _confirm_setup(client, match)
  assert await _publish(client, match) is None
  assert [code for code, _ in rejections] == ["same_direction_group_limit"]
  assert not [key async for key in client.scan_iter(match="analysis:active_thesis:*")]
  assert not [key async for key in client.scan_iter(match="autotrade:entry_overlap:*") if await client.get(key) not in (None, "[]")]


@pytest.mark.asyncio
async def test_a_setup_that_turned_terminal_before_publication_gives_the_slot_back(monkeypatch):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(1))
  client = redis_state.get_client()
  await _executor_snapshot(client)
  match = _scalp("terminal")
  await _confirm_setup(client, match)
  real = worker.load_setup

  async def terminal(client_, setup_id):
    record = await real(client_, setup_id)
    return None if record is None else type(record)(**{**record.__dict__, "state": "invalidated"})

  monkeypatch.setattr(worker, "load_setup", terminal)
  assert await _publish(client, match) is None
  assert await client.hlen(reservation_key("GBPUSD", "BUY")) == 0


@pytest.mark.asyncio
async def test_a_lifecycle_refusal_before_the_stream_gives_the_slot_back(monkeypatch):
  monkeypatch.setattr(worker.instrument_geometry, "same_direction_policy", lambda symbol: _policy(1))
  client = redis_state.get_client()
  await _executor_snapshot(client)
  match = _scalp("lifecycle")
  await _confirm_setup(client, match)
  real = worker.transition_setup

  async def failing(client_, setup_id, state, **kwargs):
    if state == "plan_built":
      raise worker.SetupLifecycleError("lifecycle refused")
    return await real(client_, setup_id, state, **kwargs)

  monkeypatch.setattr(worker, "transition_setup", failing)
  assert await _publish(client, match) is None
  assert await client.xlen("execution:trade_plans") == 0
  assert await client.hlen(reservation_key("GBPUSD", "BUY")) == 0, "nothing reached the stream: the slot must be free"


@pytest.mark.asyncio
async def test_the_committed_fx_config_never_stacks_a_scalp(rejections):
  """No monkeypatch: GBPUSD's own configuration refuses a scalp on top of an open same-direction group."""
  client = redis_state.get_client()
  await _seed_live_plan(client, symbol="GBPUSD", direction="BUY", entry=1.2400, plan_id="v8:setup-active")
  await _executor_snapshot(client, groups=["v8:setup-active"])
  match = _scalp("fx-stack")
  await _confirm_setup(client, match)

  assert await _publish(client, match) is None
  assert [code for code, _ in rejections] == ["same_direction_group_limit"]
  assert await client.xlen("execution:trade_plans") == 0
