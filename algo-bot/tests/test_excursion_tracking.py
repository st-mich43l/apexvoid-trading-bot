"""PR-OBS1: MFE/MAE of every algo_auto trade on closed M1 bars, and the scalp excursion that used to never move.

Run from ``algo-bot``:  ``python -m unittest tests.test_excursion_tracking``  (stdlib unittest; needs the app's runtime dependencies and ``fakeredis``).
XAU has a 0.1 pip, so a 10.0 price move is 100 pips.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

try:
  import fakeredis.aioredis as fake_aioredis

  HAVE_FAKEREDIS = True
except ImportError:  # pragma: no cover
  HAVE_FAKEREDIS = False

from app.autotrade import excursion  # noqa: E402
from app.autotrade import stats_ingestion  # noqa: E402
from app.autotrade import worker  # noqa: E402
from app.persistence import store  # noqa: E402
from app.scalping import outcomes  # noqa: E402

PIP = 0.1
GROUP = "v8:go_opp_aaaa"
FILL_AT = 1_000  # the fill minute opens at 960 and is EXCLUDED; the first counted bar opens at 1020


def fill(**over) -> dict:
  event = {
    "type": "order_filled", "group_id": GROUP, "position_id": 7, "symbol": "XAU", "direction": "BUY",
    "price": 100.0, "stop_loss": 95.0, "timestamp": FILL_AT, "setup": "Key Level", "stream": "algo_auto",
  }
  event.update(over)
  return event


async def metric(client, name: str, symbol: str = "XAU") -> int:
  value = await client.hget(f"auto_trade:metrics:{symbol}", name)
  return int(value or 0)


@unittest.skipUnless(HAVE_FAKEREDIS, "fakeredis is not installed")
class ExcursionTrackingTest(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.client = fake_aioredis.FakeRedis(decode_responses=True)

  async def asyncTearDown(self):
    await self.client.aclose()

  async def run_bars(self, bars, symbol="XAU"):
    async def load():
      return bars

    return await excursion.advance(self.client, symbol, load)

  # 1. BUY: the figures come from the FIRST fill's price and the ORIGINAL stop; the fill minute is excluded.
  async def test_buy_excursion_from_first_fill_and_original_stop(self):
    self.assertTrue(await excursion.start(self.client, fill(), pip_size=PIP))
    bars = [(960, 120.0, 80.0), (1020, 105.0, 98.0), (1080, 110.0, 99.0)]  # 960 contains the fill: its 120/80 must not count
    self.assertEqual(await self.run_bars(bars), 1)
    state = await excursion.load(self.client, GROUP)
    columns = excursion.compute(state, PIP)
    self.assertAlmostEqual(columns["mfe_pips"], 100.0)  # 110 - 100
    self.assertAlmostEqual(columns["mae_pips"], 20.0)  # 100 - 98
    self.assertAlmostEqual(columns["mfe_r"], 2.0)  # 100 pips over the 50-pip original stop
    self.assertAlmostEqual(columns["mae_r"], 0.4)
    self.assertEqual((columns["mfe_at"], columns["first_fill_at"], columns["excursion_bars"], columns["excursion_method"]), (1080, FILL_AT, 2, "m1_bar_v1"))

  # 2. SELL mirror.
  async def test_sell_excursion_mirrors(self):
    await excursion.start(self.client, fill(direction="SELL", stop_loss=105.0), pip_size=PIP)
    await self.run_bars([(960, 120.0, 80.0), (1020, 102.0, 90.0), (1080, 101.0, 95.0)])
    columns = excursion.compute(await excursion.load(self.client, GROUP), PIP)
    self.assertAlmostEqual(columns["mfe_pips"], 100.0)  # 100 - 90
    self.assertAlmostEqual(columns["mae_pips"], 20.0)  # 102 - 100
    self.assertAlmostEqual(columns["mfe_r"], 2.0)
    self.assertEqual(columns["mfe_at"], 1020)

  # 3. A later fill never moves the reference or the stop.
  async def test_second_fill_does_not_move_the_reference(self):
    self.assertTrue(await excursion.start(self.client, fill(), pip_size=PIP))
    self.assertFalse(await excursion.start(self.client, fill(price=97.0, stop_loss=90.0, timestamp=FILL_AT + 60, position_id=8), pip_size=PIP))
    state = await excursion.load(self.client, GROUP)
    self.assertEqual((state.reference_price, state.original_stop_price, state.first_fill_at), (100.0, 95.0, FILL_AT))
    self.assertEqual(await metric(self.client, "excursion_started"), 1)

  # 4. Every strategy is tracked, scalp or not (the early-return regression).
  async def test_scalp_and_non_scalp_are_both_tracked(self):
    for setup, group in (("Key Level", "v8:go_opp_kl"), ("impulse_pullback", "v8:go_opp_ip")):
      await stats_ingestion._track_excursion_start(self.client, fill(setup=setup, group_id=group))
      self.assertIsNotNone(await excursion.load(self.client, group), setup)
    # a manual /algo trade is not an algo_auto group
    await stats_ingestion._track_excursion_start(self.client, fill(stream="algo_manual", group_id="manual:1", setup="Manual Algo"))
    self.assertIsNone(await excursion.load(self.client, "manual:1"))

  # The same bar is never applied twice, and a missed event is caught up.
  async def test_bars_are_applied_once_and_caught_up(self):
    await excursion.start(self.client, fill(), pip_size=PIP)
    await self.run_bars([(1020, 105.0, 99.0)])
    await self.run_bars([(1020, 105.0, 99.0)])  # re-delivered event
    await self.run_bars([(1020, 105.0, 99.0), (1080, 106.0, 99.0), (1140, 107.0, 99.0)])  # two events missed
    self.assertEqual((await excursion.load(self.client, GROUP)).bars, 3)

  # A trade that closes in its fill minute observed nothing: NULL, never a fabricated 0.
  async def test_no_bar_after_the_fill_is_null_not_zero(self):
    await excursion.start(self.client, fill(), pip_size=PIP)
    await self.run_bars([(960, 120.0, 80.0)])
    columns = excursion.compute(await excursion.load(self.client, GROUP), PIP)
    self.assertIsNone(columns["mfe_pips"])
    self.assertIsNone(columns["mae_pips"])
    self.assertEqual(columns["excursion_bars"], 0)

  # 5. A scalp outcome now reports a real MFE; without the tracker it keeps the old entry-only value.
  def test_scalp_outcome_reports_the_tracked_mfe(self):
    state = outcomes.ExcursionState(
      opportunity_id="opp", episode_id="ep", symbol="XAU", archetype="impulse_pullback", direction="BUY", session="london", htf_bias="", regime="",
      entry_price=100.0, invalidation_price=95.0, stop_pips=50.0, planned_target_pips=100.0, planned_rr=2.0, group_id=GROUP, match_id=GROUP,
      opened_at=FILL_AT, pip_size=PIP, max_high=100.0, min_low=100.0,
    )
    kwargs = dict(exit_path=outcomes.EXIT_FULL_STOP, realized_pips=-50.0, closed_at=FILL_AT + 600)
    untracked = outcomes.finalize_live_outcome(state, **kwargs)
    self.assertEqual((untracked.mfe_pips, untracked.mae_pips), (0.0, 0.0))  # the old, never-moving figure
    tracked = outcomes.finalize_live_outcome(state, **kwargs, tracked_excursion={"mfe_pips": 80.0, "mae_pips": 30.0})
    self.assertEqual((tracked.mfe_pips, tracked.mae_pips), (80.0, 30.0))
    nothing_seen = outcomes.finalize_live_outcome(state, **kwargs, tracked_excursion={"mfe_pips": None, "mae_pips": None})
    self.assertEqual((nothing_seen.mfe_pips, nothing_seen.mae_pips), (0.0, 0.0))

  # 6. A tracker failure never breaks the bar handler or the result write.
  async def test_failures_never_raise_into_the_trading_path(self):
    with self.assertLogs(level="ERROR"):  # every failure is logged, none is raised
      await self._failures_never_raise()

  async def _failures_never_raise(self):
    await excursion.start(self.client, fill(), pip_size=PIP)

    async def broken_bars():
      raise RuntimeError("redis down")

    self.assertEqual(await excursion.advance(self.client, "XAU", broken_bars), 0)
    self.assertEqual(await metric(self.client, "excursion_update_failed"), 1)

    class Down:
      async def smembers(self, *a, **k):
        raise ConnectionError("down")

      async def hincrby(self, *a, **k):
        raise ConnectionError("down")  # even the counter is down

    self.assertEqual(await worker._advance_excursions(Down(), mock.Mock(), "XAU"), None)  # the worker hook swallows it
    await stats_ingestion._track_excursion_start(Down(), fill())
    await stats_ingestion._track_excursion_end(Down(), {"type": "group_result", "group_id": GROUP, "symbol": "XAU"})

    class DbStub:
      async def fetchval(self, *a, **k):
        return None

    with mock.patch("app.persistence.redis_state.get_client", return_value=Down()):
      columns = await store._excursion_result_columns(DbStub(), GROUP, {"trade_stream": "algo_auto", "symbol": "XAU"})
    self.assertEqual(columns, store._NO_EXCURSION)  # the result row is still written, with NULL excursion

  # 7. A close with no state writes NULLs and is counted once.
  async def test_missing_state_at_close_writes_nulls_and_counts_once(self):
    class DbStub:
      recorded = None

      async def fetchval(self, *a, **k):
        return self.recorded

    fill_row = {"trade_stream": "algo_auto", "symbol": "XAU"}
    with mock.patch("app.persistence.redis_state.get_client", return_value=self.client):
      for _ in range(2):  # two close events of the same untracked group
        columns = await store._excursion_result_columns(DbStub(), GROUP, fill_row)
        self.assertEqual(columns, store._NO_EXCURSION)
      self.assertEqual(await metric(self.client, "excursion_missing_at_close"), 1)
      done = DbStub()
      done.recorded = "m1_bar_v1"  # a re-delivered close after the state was deleted: already recorded, not a defect
      await store._excursion_result_columns(done, "v8:go_opp_other", fill_row)
      self.assertEqual(await metric(self.client, "excursion_missing_at_close"), 1)
      # a non-algo_auto group is never tracked and never counted
      self.assertEqual(await store._excursion_result_columns(DbStub(), "manual:1", {"trade_stream": "algo_manual", "symbol": "XAU"}), store._NO_EXCURSION)
      self.assertEqual(await metric(self.client, "excursion_missing_at_close"), 1)

  # The result row gets the figures; the state is deleted only when the whole group ended, and an update never resurrects it.
  async def test_state_lives_until_the_group_ends(self):
    await excursion.start(self.client, fill(), pip_size=PIP)
    await self.run_bars([(1020, 110.0, 99.0)])
    with mock.patch("app.persistence.redis_state.get_client", return_value=self.client):
      class DbStub:
        async def fetchval(self, *a, **k):
          return None

      partial = await store._excursion_result_columns(DbStub(), GROUP, {"trade_stream": "algo_auto", "symbol": "XAU"})
    self.assertAlmostEqual(partial["mfe_pips"], 100.0)
    await stats_ingestion._track_excursion_end(self.client, {"type": "position_closed", "group_id": GROUP, "symbol": "XAU", "remaining_volume": 100})
    self.assertIsNotNone(await excursion.load(self.client, GROUP), "a leg closing must not end the tracking")
    await self.run_bars([(1080, 112.0, 99.0)])
    self.assertAlmostEqual(excursion.compute(await excursion.load(self.client, GROUP), PIP)["mfe_pips"], 120.0)  # fuller on the next close
    await stats_ingestion._track_excursion_end(self.client, {"type": "group_result", "group_id": GROUP, "symbol": "XAU"})
    self.assertIsNone(await excursion.load(self.client, GROUP))
    self.assertEqual(await metric(self.client, "excursion_finalized"), 1)
    self.assertEqual(await self.run_bars([(1140, 130.0, 99.0)]), 0)  # nothing left to advance, nothing resurrected
    self.assertIsNone(await excursion.load(self.client, GROUP))


if __name__ == "__main__":
  unittest.main()
