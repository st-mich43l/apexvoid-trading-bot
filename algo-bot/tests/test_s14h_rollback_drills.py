"""S14H: emergency rollback, proved end to end on real PostgreSQL + Redis.

* no dual publication at any instant of grant -> drain -> Go -> rollback -> drain -> Python;
* the withdrawal step needs only Redis, so it is usable when PostgreSQL is down;
* a dependency outage is a hard stop (fail closed), never an unsafe fallback;
* the exact cancel-intent bytes Python's rollback writes are the fixture the executor's drills
  (ctrader-engine GoRollbackDrillTests) run against for every stage of the work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from redis.asyncio import Redis

from app.analysis_client import authority as auth
from app.analysis_client.authority import AuthorityFence, PostgresAuthorityStore, authorize_legacy_match
from app.autotrade.go_plan_cancel import SOURCE_ROLLBACK, plan_cancel_key, read_plan_cancel, withdraw_go_scope
from app.persistence import redis_state, store
from app.scripts import analysis_authority as cli
from tests.test_analysis_authority_fence import Clock
from tests.test_s14d_go_full_chain import cycle, granted_and_delivered, h, live_inputs, plans, prod  # noqa: F401 - fixtures/helpers
from tests.test_publish_trade_plan_v8 import (  # noqa: F401 - autouse fixtures
  _freeze_technique_killzone_hour,
  _no_news_by_default,
)

pytestmark = pytest.mark.real_redis

FIXTURE = Path(__file__).resolve().parents[2] / "contracts" / "autotrade" / "go-rollback-cancel-intent.json"
GO_TAGS_TEMPLATE = (auth.GO_ORIGIN_TAG, "catalog:supply")


async def who_may_publish(fence: AuthorityFence, epoch: int) -> tuple[bool, bool]:
  legacy = await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=True, fence=fence)
  go = await authorize_legacy_match(
    symbol="XAU", strategy_name="Supply Demand", direction="SELL", tags=(*GO_TAGS_TEMPLATE, f"authority_epoch:{epoch}"), consumer_enabled=True, fence=fence,
  )
  return legacy.allowed, go.allowed


@pytest.mark.asyncio
async def test_there_is_never_a_second_publisher_at_any_instant_of_the_handover_and_the_rollback(sql):
  await store.init_db()
  clock = Clock(1_000_000.0)
  fence = AuthorityFence(PostgresAuthorityStore(), cache_ttl=0.0, clock=clock)
  timeline: list[tuple[float, str, bool, bool]] = []

  async def sample(label: str, epoch: int):
    timeline.append((clock.now, label, *await who_may_publish(fence, epoch)))

  await sample("python_owned", 1)
  assert timeline[-1][2:] == (True, False)                                    # only legacy Python
  await fence.record_acceptance("XAU", "supply", "drill-evidence", approved_by="drill-operator", ttl_seconds=3600)
  await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor="drill-operator", reason="drill", evidence_ref="drill-evidence", drain_seconds=10)
  for _ in range(10):                                                          # the drain: nobody publishes
    await sample("draining_to_go", 1)
    clock.advance(1)
  await sample("go_owned", 1)
  assert timeline[-1][2:] == (False, True)                                    # only Go
  clock.advance(40)
  await fence.rollback("XAU", "supply", expected_epoch=1, actor="oncall", reason="drill rollback", drain_seconds=10)
  for _ in range(10):                                                          # Go stops now, Python waits out the drain
    await sample("draining_to_python", 1)
    clock.advance(1)
  await sample("python_restored", 1)
  assert timeline[-1][2:] == (True, False)                                    # Python again, and Go's old epoch is refused

  assert all(not (legacy and go) for _t, _l, legacy, go in timeline)          # never two publishers
  assert all(legacy or go for _t, label, legacy, go in timeline if label in {"python_owned", "go_owned", "python_restored"})
  assert all(not legacy and not go for _t, label, legacy, go in timeline if label.startswith("draining"))


@pytest.mark.asyncio
async def test_disabling_the_consumer_mid_go_ownership_still_never_lets_legacy_python_publish(sql):
  await store.init_db()
  clock = Clock(1_000_000.0)
  fence = AuthorityFence(PostgresAuthorityStore(), cache_ttl=0.0, clock=clock)
  await fence.record_acceptance("XAU", "supply", "drill-evidence", approved_by="drill-operator", ttl_seconds=3600)
  await fence.begin_transfer("XAU", "supply", "go", expected_epoch=0, actor="drill-operator", reason="drill", evidence_ref="drill-evidence", drain_seconds=5)
  clock.advance(5)
  auth.reset_snapshot_for_tests()
  snapshot = auth.get_snapshot()
  await snapshot.refresh(PostgresAuthorityStore())
  denied = await authorize_legacy_match(symbol="XAU", strategy_name="Supply Demand", direction="SELL", consumer_enabled=False, snapshot=snapshot)
  assert not denied.allowed and denied.reason.startswith("scope_owned_by_")
  auth.reset_snapshot_for_tests()


@pytest.mark.asyncio
async def test_a_dependency_outage_is_a_hard_stop_not_an_unsafe_fallback(sql):
  class Down:
    async def get(self, *_):
      raise ConnectionError("postgres unreachable")

  fence = AuthorityFence(Down(), cache_ttl=0.0)
  assert await who_may_publish(fence, 1) == (False, False)                    # neither side publishes: skipped plans, not two publishers


@pytest.mark.asyncio
async def test_the_withdrawal_step_needs_only_redis_so_it_works_with_postgres_down(h, prod, monkeypatch):
  await granted_and_delivered(h)
  await cycle(prod)
  assert len(await plans(prod)) == 1

  async def postgres_is_down():
    raise ConnectionError("postgres unreachable")

  monkeypatch.setattr(store, "init_db", postgres_is_down)
  args = cli.build_parser().parse_args(["withdraw", "--symbol", "XAU", "--scope", "supply", "--actor", "oncall", "--reason", "db outage drill"])
  out = await cli.run(args)
  assert out["withdrawal"]["plans_cancel_requested"] == ["v8:go_opp_chain"] and "error" not in out["withdrawal"]
  assert await read_plan_cancel(prod, "v8:go_opp_chain")
  with pytest.raises(ConnectionError):                                          # ...whereas the fence commands refuse loudly
    await cli.run(cli.build_parser().parse_args(["status"]))


@pytest.mark.asyncio
async def test_rollback_withdraws_a_published_plan_and_its_intent_is_the_shared_executor_fixture(h, prod):
  await granted_and_delivered(h)
  await cycle(prod)
  await h.fence.rollback("XAU", "supply", expected_epoch=1, actor="oncall", reason="drill", drain_seconds=30)
  report = await withdraw_go_scope(prod, symbol="XAU", scope="supply", reason="emergency rollback drill", source=SOURCE_ROLLBACK, now=1_790_000_200)
  assert report.plans_cancel_requested == ["v8:go_opp_chain"]
  intent = json.loads(await prod.get(plan_cancel_key("v8:go_opp_chain")))
  assert intent["source"] == "authority_rollback" and intent["epoch"] == 1
  if os.getenv("UPDATE_GOLDEN") == "1":
    FIXTURE.write_text(json.dumps(intent, indent=2, sort_keys=True) + "\n")
  assert json.loads(FIXTURE.read_text()) == intent, "the rollback intent changed: regenerate with UPDATE_GOLDEN=1 and update the executor drills"
