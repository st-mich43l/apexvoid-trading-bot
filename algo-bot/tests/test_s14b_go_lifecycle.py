"""S14B: activation boundary, freshness, withdrawal and rollback on real Postgres + real Redis.

Nothing here is mocked below the policy: the ledger is PostgreSQL, the match /
setup / plan / cancel-intent state is a real Redis (production Lua included) and
the plan is built and published by the real worker path. Set ``REAL_REDIS_URL``
to a disposable database, exactly as the other production-Lua suites do.
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from redis.asyncio import Redis

from app.analysis_client.consumer import AnalysisOpportunityConsumer, run_consumer_loop
from app.analysis_client.models import InvalidationTopic, OpportunityTopic, parse_analysis_event
from app.autotrade import go_opportunity_policy as pol
from app.autotrade import worker
from app.autotrade.go_plan_cancel import (
  plan_cancel_key,
  read_plan_cancel,
  read_plan_cancel_ack,
  registered_go_plans,
  request_plan_cancel,
  withdraw_go_scope,
)
from app.autotrade.multi_match import deserialize_matches, strategy_matches_key
from app.autotrade.setup_lifecycle import EXPIRED, INVALIDATED, PLAN_PUBLISHED, load_setup
from app.autotrade.trade_plan_stream import read_plan_state
from app.persistence import redis_state
from tests.configuration.canonical_fixtures import install_runtime_overrides
from tests.test_analysis_client_models import _invalidated
from tests.test_go_opportunity_policy import Harness, _outcome, _spot, golden
from tests.test_publish_trade_plan_v8 import (  # noqa: F401 - autouse fixtures
  _freeze_technique_killzone_hour,
  _m1_trigger_bar,
  _no_news_by_default,
)

pytestmark = pytest.mark.real_redis
CLIENT_KEY = strategy_matches_key("XAU")


@pytest.fixture
def real_redis_client(monkeypatch, event_loop):
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.fail("REAL_REDIS_URL is required for the S14B lifecycle tests")
  client = Redis.from_url(url, decode_responses=True)
  event_loop.run_until_complete(client.ping())
  event_loop.run_until_complete(client.flushdb())
  monkeypatch.setattr(redis_state, "_client", client)
  try:
    yield client
  finally:
    event_loop.run_until_complete(client.flushdb())
    event_loop.run_until_complete(client.aclose())


@pytest.fixture
def h(sql, monkeypatch, real_redis_client):
  install_runtime_overrides(monkeypatch, {"analysis.technical_authority.consumer_enabled": True})
  return Harness(sql, monkeypatch)


def make_event(now, opp="opp_golden_supply_xau", *, observed_ago=60, **payload):
  """A confirmed supply opportunity observed ``observed_ago`` seconds before ``now``."""
  raw = golden(int(now) + 60 - observed_ago, id=opp, **payload)
  raw["event_id"] = f"evt-{opp}"
  return parse_analysis_event(OpportunityTopic, json.dumps(raw))


def terminal(h, opp="opp_golden_supply_xau", reason="ZONE_INVALIDATED"):
  return parse_analysis_event(InvalidationTopic, json.dumps(_invalidated(
    event_id=f"evt-term-{opp}-{reason}",
    payload={"opportunity_id": opp, "symbol": "XAU", "strategy": "supply", "reason_code": reason, "invalidated_at": int(h.clock.now)},
  )))


async def deliver(h, ev, *, published_at=None, topic=OpportunityTopic):
  await h._ensure()
  h.offset += 1
  result = await h.repo.apply(ev, topic=topic, partition=0, offset=h.offset)
  if topic == OpportunityTopic:
    return await h.policy.on_creation(ev, result, published_at=published_at)
  return await h.policy.on_terminal(ev, result)


async def matches(client):
  return [m.match_id for m in deserialize_matches(await client.get(CLIENT_KEY))]


async def decision_rows(h):
  await h._ensure()
  rows = await h.sql.fetch("SELECT outcome, reason, details FROM analysis_shadow_decisions ORDER BY decision_id")
  return [{"outcome": r["outcome"], "reason": r["reason"], "details": json.loads(r["details"]) if isinstance(r["details"], str) else r["details"]} for r in rows]


async def publish(client, match):
  return await worker._publish_trade_plan_v8(client, "XAU", _spot(4354.1, 4354.3), match, frames={"M1": _m1_trigger_bar()})


# ---- activation boundary and freshness --------------------------------------------

@pytest.mark.asyncio
async def test_history_observed_before_the_activation_boundary_never_becomes_a_match(h, real_redis_client):
  await h.grant()
  ev = make_event(h.clock.now, observed_ago=180 + 120)      # observed before the grant took effect
  assert await deliver(h, ev) == "not_adapted"
  row = (await decision_rows(h))[-1]
  assert row["reason"] == "pre_activation_event" and row["details"]["observed_at"] < row["details"]["activation_boundary"]
  assert await matches(real_redis_client) == []
  assert await h.repo.opportunity_state("opp_golden_supply_xau") == "active"     # ledger keeps the history


@pytest.mark.asyncio
async def test_a_new_observation_after_activation_adapts_and_records_all_three_times(h, real_redis_client):
  await h.grant()
  now = int(h.clock.now)
  assert await deliver(h, make_event(now), published_at=now - 20) == "match_written"
  row = (await decision_rows(h))[-1]
  d = row["details"]
  assert row["outcome"] == "match_written"
  assert (d["observed_at"], d["published_at"], d["consumed_at"]) == (now - 60, now - 20, now)
  assert (d["event_age_seconds"], d["delivery_lag_seconds"]) == (60, 20)
  assert d["activation_boundary"] < d["observed_at"] and d["epoch"] == 1
  assert await matches(real_redis_client) == ["go_opp_golden_supply_xau"]


@pytest.mark.asyncio
async def test_backlog_older_than_the_event_age_limit_is_recorded_never_traded(h, real_redis_client):
  await h.grant()
  ev = make_event(h.clock.now)
  h.clock.advance(1_000)
  assert await deliver(h, ev) == "not_adapted"
  assert (await decision_rows(h))[-1]["reason"] == "event_too_old"
  assert await matches(real_redis_client) == []


@pytest.mark.asyncio
async def test_a_young_setup_held_up_in_kafka_is_a_backlog_too(h, real_redis_client):
  await h.grant()
  now = int(h.clock.now)
  assert await deliver(h, make_event(now), published_at=now - 400) == "not_adapted"
  assert (await decision_rows(h))[-1]["reason"] == "delivery_lag_exceeded"
  assert await matches(real_redis_client) == []


@pytest.mark.asyncio
async def test_technically_expired_opportunity_is_not_adapted(h, real_redis_client):
  await h.grant()
  now = int(h.clock.now)
  assert await deliver(h, make_event(now, expires_at=now - 30)) == "not_adapted"
  assert (await decision_rows(h))[-1]["reason"] == "opportunity_expired"
  assert await matches(real_redis_client) == []


@pytest.mark.asyncio
async def test_new_consumer_group_replaying_history_only_rebuilds_the_ledger(h, real_redis_client):
  """A wiped/renamed consumer group re-reads the topic from the beginning."""
  await h.grant()
  now = int(h.clock.now)
  outcomes = []
  for i, ago in enumerate((3_000, 2_400, 1_800, 1_200)):     # all after activation, all stale by now
    outcomes.append(await deliver(h, make_event(now, f"opp-old-{i}", observed_ago=ago), published_at=now - ago))
  outcomes.append(await deliver(h, make_event(now, "opp-new", observed_ago=45), published_at=now - 5))
  assert outcomes == ["not_adapted"] * 4 + ["match_written"]
  assert await matches(real_redis_client) == ["go_opp-new"]
  assert {(await h.repo.opportunity_state(f"opp-old-{i}")) for i in range(4)} == {"active"}


@pytest.mark.asyncio
async def test_redelivery_of_an_adapted_opportunity_is_idempotent_even_once_stale(h, real_redis_client):
  await h.grant()
  ev = make_event(h.clock.now)
  assert await deliver(h, ev) == "match_written"
  h.clock.advance(2_000)                                     # far past the age limit, well inside the 24h expiry
  assert await deliver(h, ev) == "match_written"
  assert await matches(real_redis_client) == ["go_opp_golden_supply_xau"]
  assert "event_too_old" not in [r["reason"] for r in await decision_rows(h)]


@pytest.mark.asyncio
async def test_crash_after_the_match_write_recovers_on_redelivery_without_duplicating(h, real_redis_client):
  await h.grant()
  ev = make_event(h.clock.now)
  real_decide, armed = h.policy._decide, {"crash": True}

  async def flaky(event, outcome, reason, **details):
    if outcome == "match_written" and armed["crash"]:
      armed["crash"] = False
      raise ConnectionError("process died before the decision row")
    return await real_decide(event, outcome, reason, **details)

  h.policy._decide = flaky
  with pytest.raises(ConnectionError):
    await deliver(h, ev)
  assert await matches(real_redis_client) == ["go_opp_golden_supply_xau"]      # match already durable
  h.clock.advance(2_000)                                                        # the retry happens much later
  assert await deliver(h, ev) == "match_written"
  assert await matches(real_redis_client) == ["go_opp_golden_supply_xau"]
  assert [r["outcome"] for r in await decision_rows(h)] == ["match_written"]


@pytest.mark.asyncio
async def test_kafka_record_timestamp_reaches_the_gate_through_the_consumer(h, real_redis_client):
  await h.grant()
  now = int(h.clock.now)
  consumer = AnalysisOpportunityConsumer(h.repo, shadow=None, mode="go", policy=h.policy)
  stale_publish = SimpleNamespace(
    topic=OpportunityTopic, partition=0, offset=1, timestamp=(now - 400) * 1000,
    value=json.dumps(golden(now, id="opp-lag")).encode(),
  )
  raw = json.loads(stale_publish.value)
  raw["event_id"] = "evt-opp-lag"
  stale_publish.value = json.dumps(raw).encode()
  await h._ensure()
  await consumer.process_record(stale_publish)
  assert (await decision_rows(h))[-1]["reason"] == "delivery_lag_exceeded"


# ---- invalidation / expiry withdrawal ---------------------------------------------

@pytest.mark.asyncio
async def test_invalidation_withdraws_match_setup_and_tombstones_the_plan(h, real_redis_client):
  await h.grant()
  await deliver(h, make_event(h.clock.now))
  assert await deliver(h, terminal(h), topic=InvalidationTopic) == "match_withdrawn"
  assert await matches(real_redis_client) == []
  assert (await load_setup(real_redis_client, "go_opp_golden_supply_xau")).state == INVALIDATED
  intent = await read_plan_cancel(real_redis_client, "v8:go_opp_golden_supply_xau")
  assert intent["source"] == "opportunity_invalidated" and intent["reason"] == "zone_invalidated"
  assert intent["opportunity_id"] == "opp_golden_supply_xau"
  assert 0 < await real_redis_client.ttl(plan_cancel_key("v8:go_opp_golden_supply_xau")) <= 7 * 86400


@pytest.mark.asyncio
async def test_expiry_is_recorded_as_expiry_and_expires_the_setup(h, real_redis_client):
  await h.grant()
  await deliver(h, make_event(h.clock.now))
  await deliver(h, terminal(h, reason="SETUP_EXPIRED"), topic=InvalidationTopic)
  assert (await load_setup(real_redis_client, "go_opp_golden_supply_xau")).state == EXPIRED
  assert (await read_plan_cancel(real_redis_client, "v8:go_opp_golden_supply_xau"))["source"] == "opportunity_expired"


@pytest.mark.asyncio
async def test_invalidation_of_something_never_adapted_leaves_no_tombstone(h, real_redis_client):
  # Python-owned scope: the creation was recorded but never adapted.
  await deliver(h, make_event(h.clock.now))
  assert await deliver(h, terminal(h), topic=InvalidationTopic) == "match_withdrawn"
  assert await real_redis_client.keys("execution:plan_cancel:*") == []


@pytest.mark.asyncio
async def test_first_cancel_reason_wins_and_repeats_are_no_ops(h, real_redis_client):
  assert await request_plan_cancel(real_redis_client, "v8:x", reason="a", source="opportunity_invalidated", requested_at=1)
  assert not await request_plan_cancel(real_redis_client, "v8:x", reason="b", source="authority_rollback", requested_at=2)
  assert (await read_plan_cancel(real_redis_client, "v8:x"))["reason"] == "a"


@pytest.mark.asyncio
async def test_a_match_racing_the_invalidation_cannot_become_a_plan(h, real_redis_client):
  await h.grant()
  await deliver(h, make_event(h.clock.now))
  match = deserialize_matches(await real_redis_client.get(CLIENT_KEY))[0]
  # The worker cycle already holds `match` in memory when the withdrawal lands
  # (tombstone written, setup not yet observed terminal by this cycle).
  await request_plan_cancel(real_redis_client, "v8:go_opp_golden_supply_xau", reason="zone_invalidated", source="opportunity_invalidated", requested_at=int(h.clock.now))
  assert await publish(real_redis_client, match) is None
  assert (await _outcome(real_redis_client, match))["reason_code"] == "go_plan_withdrawn"
  assert await real_redis_client.xlen("execution:trade_plans") == 0
  assert await registered_go_plans(real_redis_client) == []


@pytest.mark.asyncio
async def test_after_a_full_invalidation_the_stale_in_memory_match_still_cannot_publish(h, real_redis_client):
  await h.grant()
  await deliver(h, make_event(h.clock.now))
  match = deserialize_matches(await real_redis_client.get(CLIENT_KEY))[0]
  await deliver(h, terminal(h), topic=InvalidationTopic)
  assert await publish(real_redis_client, match) is None
  assert await real_redis_client.xlen("execution:trade_plans") == 0


@pytest.mark.asyncio
async def test_published_go_plan_is_registered_and_its_invalidation_requests_the_cancel(h, real_redis_client):
  await h.grant()
  await deliver(h, make_event(h.clock.now))
  match = deserialize_matches(await real_redis_client.get(CLIENT_KEY))[0]
  plan_id = await publish(real_redis_client, match)
  assert plan_id == "v8:go_opp_golden_supply_xau"
  registry = await registered_go_plans(real_redis_client, symbol="XAU", scope="supply")
  assert [(p["plan_id"], p["epoch"], p["match_id"]) for p in registry] == [(plan_id, 1, "go_opp_golden_supply_xau")]
  await deliver(h, terminal(h), topic=InvalidationTopic)
  assert (await read_plan_cancel(real_redis_client, plan_id))["source"] == "opportunity_invalidated"
  # Python never edits executor state: only the executor moves it, and reports back.
  assert await read_plan_state(real_redis_client, plan_id) == "published"
  assert await read_plan_cancel_ack(real_redis_client, plan_id) is None
  assert (await load_setup(real_redis_client, "go_opp_golden_supply_xau")).state == PLAN_PUBLISHED


# ---- rollback sequence ------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_sequence_fence_first_then_withdraw_and_it_is_rerunnable(h, real_redis_client):
  await h.grant()
  now = int(h.clock.now)
  await deliver(h, make_event(now, "opp-a"))
  await deliver(h, make_event(now, "opp-b"))
  first = next(m for m in deserialize_matches(await real_redis_client.get(CLIENT_KEY)) if m.match_id == "go_opp-a")
  assert await publish(real_redis_client, first) == "v8:go_opp-a"

  # 1) fence: Go stops immediately, nothing new can be created or published.
  await h.fence.rollback("XAU", "supply", expected_epoch=1, actor="oncall", reason="rollback drill", drain_seconds=30)
  assert await deliver(h, make_event(now, "opp-c", observed_ago=10)) == "not_adapted"
  assert (await decision_rows(h))[-1]["reason"].startswith("not_go_owner:")
  second = next(m for m in deserialize_matches(await real_redis_client.get(CLIENT_KEY)) if m.match_id == "go_opp-b")
  assert await publish(real_redis_client, second) is None
  assert (await _outcome(real_redis_client, second))["reason_code"] == "authority_fenced"

  # 2) withdraw: matches, unpublished setups and queued plans.
  report = await withdraw_go_scope(real_redis_client, symbol="XAU", scope="supply", reason="rollback drill", source="authority_rollback", now=int(h.clock.now))
  assert sorted(report.matches_removed) == ["go_opp-a", "go_opp-b"]
  assert report.setups_withdrawn == ["go_opp-b"]            # the published one is the executor's now
  assert sorted(report.plans_cancel_requested) == ["v8:go_opp-a", "v8:go_opp-b"]
  assert await matches(real_redis_client) == []
  assert (await load_setup(real_redis_client, "go_opp-b")).state == INVALIDATED
  assert (await load_setup(real_redis_client, "go_opp-a")).state == PLAN_PUBLISHED
  assert (await read_plan_cancel(real_redis_client, "v8:go_opp-a"))["source"] == "authority_rollback"

  # Re-running (the operator's recovery from a crash mid-sequence) changes nothing.
  again = await withdraw_go_scope(real_redis_client, symbol="XAU", scope="supply", reason="rerun", source="authority_rollback", now=int(h.clock.now))
  assert again.matches_removed == [] and again.plans_cancel_requested == []
  assert sorted(again.plans_already_requested) == ["v8:go_opp-a"]
  assert (await read_plan_cancel(real_redis_client, "v8:go_opp-a"))["reason"] == "rollback drill"

  # 3) Python only resumes after the drain.
  assert not (await h.fence.authorize_python_publication("XAU", ["supply"])).allowed
  h.clock.advance(30)
  assert (await h.fence.authorize_python_publication("XAU", ["supply"])).allowed


@pytest.mark.asyncio
async def test_withdrawal_is_scoped_to_the_named_scope(h, real_redis_client):
  await h.grant()
  await deliver(h, make_event(h.clock.now))
  report = await withdraw_go_scope(real_redis_client, symbol="XAU", scope="demand", reason="x", source="authority_rollback", now=int(h.clock.now))
  assert report.matches_removed == [] and report.plans_cancel_requested == []
  assert await matches(real_redis_client) == ["go_opp_golden_supply_xau"]
  everything = await withdraw_go_scope(real_redis_client, symbol="XAU", scope=None, reason="x", source="authority_rollback", now=int(h.clock.now))
  assert everything.matches_removed == ["go_opp_golden_supply_xau"]


def test_plan_id_derivation_matches_the_worker():
  from app.autotrade.go_plan_cancel import plan_id_for_match
  match = SimpleNamespace(match_id="go_opp-9")
  assert plan_id_for_match(match.match_id) == worker._v8_plan_id(match)


# ---- consumer commit discipline across a crash/restart -----------------------------

class _FakeKafka:
  """A partition with a committed offset; a restart re-reads from it."""

  def __init__(self, records):
    self.records, self.committed = records, 0
    self.cursor = 0
    self.commits: list[int] = []

  def restart(self):
    self.cursor = self.committed

  async def getone(self):
    if self.cursor >= len(self.records):
      raise asyncio.CancelledError
    record = self.records[self.cursor]
    self.cursor += 1
    return record

  async def commit(self, offsets):
    (offset,) = offsets.values()
    self.committed = offset
    self.commits.append(offset)


@pytest.mark.asyncio
async def test_restart_redelivers_the_uncommitted_record_and_completes_once(h, real_redis_client):
  await h.grant()
  now = int(h.clock.now)
  raw = golden(now, id="opp-crash")
  raw["event_id"] = "evt-opp-crash"
  record = SimpleNamespace(topic=OpportunityTopic, partition=0, offset=41, timestamp=(now - 5) * 1000, value=json.dumps(raw).encode())
  kafka = _FakeKafka([record])
  handler = AnalysisOpportunityConsumer(h.repo, shadow=None, mode="go", policy=h.policy)
  real_decide, armed = h.policy._decide, {"crash": True}

  async def flaky(event, outcome, reason, **details):
    if outcome == "match_written" and armed["crash"]:
      armed["crash"] = False
      raise ConnectionError("killed before the offset commit")
    return await real_decide(event, outcome, reason, **details)

  h.policy._decide = flaky
  await h._ensure()
  with pytest.raises(ConnectionError):
    await run_consumer_loop(kafka, handler, partition_key=lambda t, p: (t, p))
  assert kafka.commits == []                                   # nothing committed past an unfinished record
  kafka.restart()
  h.clock.advance(3_000)                                       # restart is slow; the event is long stale
  with pytest.raises(asyncio.CancelledError):
    await run_consumer_loop(kafka, handler, partition_key=lambda t, p: (t, p))
  assert kafka.commits == [42]
  assert await matches(real_redis_client) == ["go_opp-crash"]
  assert [r["outcome"] for r in await decision_rows(h)] == ["match_written"]


@pytest.mark.asyncio
async def test_poison_record_is_recorded_and_the_partition_moves_on(h, real_redis_client):
  await h._ensure()
  poison = SimpleNamespace(topic=OpportunityTopic, partition=0, offset=7, timestamp=None, value=b"{not json")
  kafka = _FakeKafka([poison])
  handler = AnalysisOpportunityConsumer(h.repo, shadow=None, mode="go", policy=h.policy)
  with pytest.raises(asyncio.CancelledError):
    await run_consumer_loop(kafka, handler, partition_key=lambda t, p: (t, p))
  assert kafka.commits == [8]
  assert await h.sql.val("SELECT count(*) FROM analysis_opportunity_rejections") == 1
