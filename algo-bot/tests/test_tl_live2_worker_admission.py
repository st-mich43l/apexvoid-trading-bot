"""TL-LIVE-2: the real Algo Bot admission path (``worker._publish_trade_plan_v8``) for a Trendline one-price opportunity, against an in-memory Redis.

Needs ``fakeredis`` and ``lupa`` (the entry-overlap reservation is a Lua script); the CI step installs them, and the tests skip where they are absent. Only three
things are replaced: the news calendar (no events), the Postgres plan-snapshot write (best-effort telemetry) and the wall clock the worker reads (pinned to the
opportunity's own time). The adapter, setup lifecycle, exposure, overlap, target-room, execution policy, plan builder and plan stream are the production code.
"""
from __future__ import annotations

import datetime
import json
import os
import time
import unittest

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

try:
  import fakeredis.aioredis as fake_aioredis  # noqa: F401
  import lupa  # noqa: F401
  HAVE_FAKEREDIS = True
except ImportError:  # pragma: no cover - environments without the test extras
  HAVE_FAKEREDIS = False

if HAVE_FAKEREDIS:
  from app.autotrade import worker
  from app.autotrade.go_opportunity_policy import GoOpportunityPolicy
  from app.autotrade.worker import AutoTradeSpot, _publish_trade_plan_v8
  from tests.test_tl_live2_single_price import EUR_M15_SELL, XAU_M5_BUY, match_for, with_execution


@unittest.skipUnless(HAVE_FAKEREDIS, "fakeredis and lupa are not installed")
class WorkerAdmissionTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self._saved = (worker._news_guard_hit, worker.datetime, worker.record_plan_snapshot)

    async def no_news(symbol, now):
      return None

    async def no_snapshot(*args, **kwargs):
      return None

    worker._news_guard_hit = no_news
    worker.record_plan_snapshot = no_snapshot

  def tearDown(self):
    worker._news_guard_hit, worker.datetime, worker.record_plan_snapshot = self._saved

  async def publish(self, scenario, bid, ask, *, now_offset=40, client=None, raw=None):
    symbol, factory, price = scenario
    client = client or fake_aioredis.FakeRedis(decode_responses=True)
    raw = raw or with_execution(factory(), price)
    match = match_for(raw)
    created = raw["payload"]["created_at"]
    clock = created + now_offset

    class PinnedDatetime:
      @staticmethod
      def now(tz=None):
        return datetime.datetime.fromtimestamp(clock, tz)

    worker.datetime = PinnedDatetime
    await GoOpportunityPolicy._store_match(client, match, created + 30)
    await GoOpportunityPolicy._advance_setup(client, match)
    await client.set(f"analysis:zone_book:{symbol}", json.dumps({"symbol": symbol, "generated_at": clock, "barriers": []}))
    await client.set(f"auto_trade:executor_snapshot:{symbol}", json.dumps({"account_equity": 10000.0, "updated_at": time.time(), "group_ids": [], "ready": True}))
    spot = AutoTradeSpot(price=(bid + ask) / 2, ts=clock, fresh=True, bid=bid, ask=ask)
    plan_id = await _publish_trade_plan_v8(client, symbol, spot, match, regime=None, frames=None)
    stream = await client.xrange("execution:trade_plans")
    plans = [json.loads(fields["payload"] if "payload" in fields else list(fields.values())[0]) for _id, fields in stream]
    outcome = None
    for key in await client.keys("auto_trade:route_outcome:*"):
      outcome = json.loads(await client.get(key))
    return plan_id, plans, outcome, client, raw

  async def test_xau_m5_support_buy_above_the_line_is_published_as_one_resting_limit(self):
    plan_id, plans, _, _, raw = await self.publish(XAU_M5_BUY, 3902.80, 3903.00)
    self.assertIsNotNone(plan_id)
    entry = plans[0]["entry"]
    self.assertEqual(len(plans), 1)
    self.assertEqual((entry["type"], entry["order_price"], entry["legs"]), ("single_limit", "3900.64", []))
    self.assertIsNone(entry.get("risk_leg"))
    self.assertEqual(entry["expires_at"], raw["payload"]["execution"]["valid_until"])
    self.assertGreater(float(entry["proximity"]), 0)

  async def test_eurusd_m15_resistance_sell_is_published_as_one_limit(self):
    plan_id, plans, _, _, _ = await self.publish(EUR_M15_SELL, 1.11930, 1.11932)
    self.assertIsNotNone(plan_id)
    entry = plans[0]["entry"]
    self.assertEqual((entry["type"], entry["order_price"], entry["legs"]), ("single_limit", "1.1197", []))

  async def test_price_already_beyond_the_line_publishes_nothing(self):
    plan_id, plans, outcome, _, _ = await self.publish(XAU_M5_BUY, 3899.60, 3899.80)
    self.assertIsNone(plan_id)
    self.assertEqual(plans, [])
    self.assertEqual(outcome["reason_code"], "entry_beyond_projected_line")

  async def test_a_projection_gone_stale_publishes_nothing(self):
    raw = with_execution(XAU_M5_BUY[1](), XAU_M5_BUY[2], bars=2)
    stale = raw["payload"]["execution"]["valid_until"] - raw["payload"]["created_at"]
    plan_id, plans, _, _, _ = await self.publish(XAU_M5_BUY, 3902.80, 3903.00, now_offset=stale, raw=raw)
    self.assertIsNone(plan_id)
    self.assertEqual(plans, [])

  async def test_a_duplicate_publication_is_one_plan(self):
    first, plans, _, client, raw = await self.publish(XAU_M5_BUY, 3902.80, 3903.00)
    again, plans_again, _, _, _ = await self.publish(XAU_M5_BUY, 3902.80, 3903.00, client=client, raw=raw)
    self.assertIsNotNone(first)
    self.assertEqual(again, first)
    self.assertEqual(len(plans_again), 1)


if __name__ == "__main__":
  unittest.main()
