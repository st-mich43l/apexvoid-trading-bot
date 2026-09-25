"""The fence guards the one live executable-plan path, and nothing else.

Exercises ``worker._publish_trade_plan_v8`` for real (fakeredis-backed client,
real TradePlan build/validate/publish), so a regression means a live plan was
created by the wrong publisher, not that a mock was called.
"""

from __future__ import annotations

import json
import time

import pytest

from app.analysis_client import authority as auth
from app.analysis_client.authority import AuthorityFence
from app.autotrade import worker
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.trade_plan_stream import read_plan_state, read_trade_plan
from app.persistence import redis_state
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
from app.core.config import runtime_config
from tests.test_analysis_authority_fence import Clock, MemoryStore
from tests.test_publish_trade_plan_v8 import (  # noqa: F401 - autouse fixtures
  _confirm_setup,
  _freeze_technique_killzone_hour,
  _m1_trigger_bar,
  _match,
  _no_news_by_default,
)

pytestmark = pytest.mark.no_database

SCOPE = "momentum_ride"  # _match() defaults to strategy "Momentum Ride"


def _spot() -> worker.AutoTradeSpot:
  return worker.AutoTradeSpot(price=4089.0, ts=int(time.time()), fresh=True, bid=4088.9, ask=4089.1)


def _enable_consumer(monkeypatch):
  install_runtime_overrides(monkeypatch, {"analysis.technical_authority.consumer_enabled": True})


@pytest.fixture
def fence(monkeypatch):
  clock = Clock(now=time.time())
  instance = AuthorityFence(MemoryStore(), cache_ttl=0.0, clock=clock)
  monkeypatch.setattr(auth, "_default_fence", instance)
  instance.clock = clock  # test handle
  return instance


async def _hand_to_go(fence, clock):
  await fence.record_acceptance("XAU", SCOPE, "ev", approved_by="owner", ttl_seconds=3600)
  await fence.begin_transfer("XAU", SCOPE, "go", expected_epoch=0, actor="owner", reason="approved", evidence_ref="ev", drain_seconds=5)
  clock.advance(5)


async def _published_count(client) -> int:
  return await client.xlen(leaf(runtime_config, "auto_trade_trade_plan_stream"))


@pytest.mark.asyncio
async def test_default_deployment_is_unchanged_python_still_publishes():
  client = redis_state.get_client()
  match = _match()
  await _confirm_setup(client, match)
  plan_id = await worker._publish_trade_plan_v8(client, "XAU", _spot(), match, frames={"M1": _m1_trigger_bar()})
  assert plan_id is not None
  assert await read_plan_state(client, plan_id) == "published"


@pytest.mark.asyncio
async def test_python_publishes_while_it_owns_the_scope_even_with_consumer_enabled(monkeypatch, fence):
  _enable_consumer(monkeypatch)
  client = redis_state.get_client()
  match = _match()
  await _confirm_setup(client, match)
  plan_id = await worker._publish_trade_plan_v8(client, "XAU", _spot(), match, frames={"M1": _m1_trigger_bar()})
  assert plan_id is not None


@pytest.mark.asyncio
async def test_legacy_python_match_cannot_create_a_plan_in_a_go_owned_scope(monkeypatch, fence):
  _enable_consumer(monkeypatch)
  await _hand_to_go(fence, fence.clock)
  client = redis_state.get_client()
  match = _match()
  await _confirm_setup(client, match)
  before = await _published_count(client)

  plan_id = await worker._publish_trade_plan_v8(client, "XAU", _spot(), match, frames={"M1": _m1_trigger_bar()})

  assert plan_id is None
  assert await _published_count(client) == before, "no executable plan may reach the stream"
  raw = await client.get(route_outcome_key(match.symbol, match.match_id))
  outcome = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
  assert outcome["reason_code"] == "authority_fenced" and outcome["status"] == "blocked"
  assert outcome["measured"]["authority_owner"] == "go"


@pytest.mark.asyncio
async def test_no_publisher_may_create_a_plan_while_a_handover_drains(monkeypatch, fence):
  _enable_consumer(monkeypatch)
  await fence.record_acceptance("XAU", SCOPE, "ev", approved_by="owner", ttl_seconds=3600)
  await fence.begin_transfer("XAU", SCOPE, "go", expected_epoch=0, actor="owner", reason="approved", evidence_ref="ev", drain_seconds=60)
  client = redis_state.get_client()
  legacy = _match()
  await _confirm_setup(client, legacy)
  assert await worker._publish_trade_plan_v8(client, "XAU", _spot(), legacy, frames={"M1": _m1_trigger_bar()}) is None

  go = _match(match_id="go-1", thesis_id="thesis-go-1", tags=(auth.GO_ORIGIN_TAG, f"catalog:{SCOPE}", "authority_epoch:1"))
  await _confirm_setup(client, go)
  assert await worker._publish_trade_plan_v8(client, "XAU", _spot(), go, frames={"M1": _m1_trigger_bar()}) is None


@pytest.mark.asyncio
async def test_go_origin_match_publishes_only_while_go_owns_it_at_the_accepted_epoch(monkeypatch, fence):
  _enable_consumer(monkeypatch)
  await _hand_to_go(fence, fence.clock)
  client = redis_state.get_client()
  tags = (auth.GO_ORIGIN_TAG, f"catalog:{SCOPE}", "authority_epoch:1")
  stale = _match(match_id="go-stale", thesis_id="thesis-stale", tags=(auth.GO_ORIGIN_TAG, f"catalog:{SCOPE}", "authority_epoch:0"))
  good = _match(match_id="go-good", thesis_id="thesis-good", tags=tags)
  for m in (stale, good):
    await _confirm_setup(client, m)

  assert await worker._publish_trade_plan_v8(client, "XAU", _spot(), stale, frames={"M1": _m1_trigger_bar()}) is None
  plan_id = await worker._publish_trade_plan_v8(client, "XAU", _spot(), good, frames={"M1": _m1_trigger_bar()})
  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None and plan.setup_id == "go-good"

  # Rollback: a second Go match is refused immediately, before the drain ends.
  await fence.rollback("XAU", SCOPE, expected_epoch=1, actor="oncall", reason="rollback", drain_seconds=30)
  late = _match(match_id="go-late", thesis_id="thesis-late", tags=tags)
  await _confirm_setup(client, late)
  assert await worker._publish_trade_plan_v8(client, "XAU", _spot(), late, frames={"M1": _m1_trigger_bar()}) is None


@pytest.mark.asyncio
async def test_unfenced_and_retired_strategies_keep_publishing(monkeypatch, fence):
  _enable_consumer(monkeypatch)
  await _hand_to_go(fence, fence.clock)          # momentum_ride is Go-owned...
  client = redis_state.get_client()
  other = _match(match_id="ob-1", thesis_id="thesis-ob", strategy="Order Block", family="order_block")
  await _confirm_setup(client, other)            # ...order_block is not
  assert await worker._publish_trade_plan_v8(client, "XAU", _spot(), other, frames={"M1": _m1_trigger_bar()}) is not None


@pytest.mark.asyncio
async def test_unreadable_fence_blocks_publication(monkeypatch):
  _enable_consumer(monkeypatch)

  class Down:
    async def get(self, *_):
      raise ConnectionError("postgres down")

  monkeypatch.setattr(auth, "_default_fence", AuthorityFence(Down(), cache_ttl=0.0))
  client = redis_state.get_client()
  match = _match()
  await _confirm_setup(client, match)
  before = await _published_count(client)
  assert await worker._publish_trade_plan_v8(client, "XAU", _spot(), match, frames={"M1": _m1_trigger_bar()}) is None
  assert await _published_count(client) == before


@pytest.mark.asyncio
async def test_reconciling_an_already_published_plan_is_never_fenced(monkeypatch, fence):
  """Ownership governs plan creation, not management of existing plans."""
  client = redis_state.get_client()
  match = _match()
  await _confirm_setup(client, match)
  plan_id = await worker._publish_trade_plan_v8(client, "XAU", _spot(), match, frames={"M1": _m1_trigger_bar()})
  assert plan_id is not None

  _enable_consumer(monkeypatch)
  await _hand_to_go(fence, fence.clock)          # scope moves to Go after publication
  again = await worker._publish_trade_plan_v8(client, "XAU", _spot(), match, frames={"M1": _m1_trigger_bar()})
  assert again == plan_id
