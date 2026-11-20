"""FX1 part B: correlated exposure across FX pairs is capped per currency side.

Run from ``algo-bot``:  ``python -m unittest tests.test_fx1_currency_exposure``  (the concurrent-admission test needs ``fakeredis`` and ``lupa``: the reservation is a Lua script).
"""
from __future__ import annotations

import asyncio
import os
import unittest

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.autotrade import currency_exposure as cx  # noqa: E402
from app.autotrade.group_exposure import EXPOSURE_UNKNOWN, GroupExposure, IncomingGroup  # noqa: E402
from app.runtime.instruments import CurrencyExposurePolicy, EffectiveInstrumentError, _currency_exposure_policy  # noqa: E402

try:
  import fakeredis.aioredis as fake_aioredis

  HAVE_FAKEREDIS = True
except ImportError:  # pragma: no cover
  HAVE_FAKEREDIS = False

CAP2 = CurrencyExposurePolicy(max_groups_per_currency_direction=2)
CAP1 = CurrencyExposurePolicy(max_groups_per_currency_direction=1)


def live(symbol: str, direction: str, n: int, **over) -> GroupExposure:
  return GroupExposure(plan_id=f"v8:plan{n}", setup_id=f"setup{n}", symbol=symbol, direction=direction, source="runtime", stage="open", **over)


def incoming(symbol: str, direction: str, n: int = 99) -> IncomingGroup:
  return IncomingGroup(setup_id=f"setup{n}", plan_id=f"v8:plan{n}", symbol=symbol, direction=direction, strategy="Key Level", thesis_id=None, structural_id=None, risk_amount=None)


class SidesTests(unittest.TestCase):
  def test_a_group_takes_a_side_of_both_currencies_of_its_pair(self):
    self.assertEqual(cx.currency_sides("EURUSD", "BUY"), {"EUR": "LONG", "USD": "SHORT"})
    self.assertEqual(cx.currency_sides("EURUSD", "SELL"), {"EUR": "SHORT", "USD": "LONG"})
    self.assertEqual(cx.currency_sides("USDJPY", "SELL"), {"USD": "SHORT", "JPY": "LONG"})
    self.assertEqual(cx.currency_sides("GBPJPY", "BUY"), {"GBP": "LONG", "JPY": "SHORT"})
    self.assertEqual(cx.currency_sides("usdjpy", "buy"), {"USD": "LONG", "JPY": "SHORT"})

  def test_xau_and_directionless_groups_are_in_no_currency_group(self):
    self.assertEqual(cx.currency_sides("XAU", "BUY"), {})
    self.assertEqual(cx.currency_sides("XAUUSD", "SELL"), {})
    self.assertEqual(cx.currency_sides("EURUSD", ""), {})


class EvaluateTests(unittest.TestCase):
  def test_the_third_long_usd_group_across_three_pairs_is_refused(self):
    groups = [live("EURUSD", "SELL", 1), live("GBPUSD", "SELL", 2)]  # long USD twice
    decision = cx.evaluate_currency_exposure(CAP2, incoming("USDJPY", "BUY"), groups)  # long USD a third time
    self.assertFalse(decision.allowed)
    self.assertEqual((decision.reason_code, decision.measured["currency"], decision.measured["currency_side"]), (cx.CURRENCY_EXPOSURE_LIMIT, "USD", "LONG"))
    self.assertTrue(cx.evaluate_currency_exposure(CAP2, incoming("USDJPY", "BUY"), groups[:1]).allowed)  # the second is fine

  def test_buy_gbpjpy_and_buy_usdjpy_count_against_short_jpy(self):
    groups = [live("GBPJPY", "BUY", 1)]
    self.assertTrue(cx.evaluate_currency_exposure(CAP1, incoming("USDJPY", "SELL", 2), groups).allowed)  # SELL USDJPY is LONG JPY: the other side
    blocked = cx.evaluate_currency_exposure(CAP1, incoming("USDJPY", "BUY", 2), groups)
    self.assertEqual((blocked.allowed, blocked.measured.get("currency"), blocked.measured.get("currency_side")), (False, "JPY", "SHORT"))
    self.assertTrue(cx.evaluate_currency_exposure(CAP2, incoming("USDJPY", "BUY", 2), groups).allowed)

  def test_one_group_on_each_side_of_usd_is_not_blocked(self):
    groups = [live("EURUSD", "SELL", 1)]  # long USD, short EUR
    self.assertTrue(cx.evaluate_currency_exposure(CAP1, incoming("GBPUSD", "BUY", 2), groups).allowed)  # long GBP, short USD
    self.assertTrue(cx.evaluate_currency_exposure(CAP1, incoming("USDJPY", "SELL", 2), groups).allowed)  # short USD, long JPY

  def test_a_group_that_booked_tp1_still_counts(self):
    groups = [live("EURUSD", "SELL", 1, risk_amount=0.0, highest_booked_target_index=0)]  # risk free after TP1
    self.assertFalse(cx.evaluate_currency_exposure(CAP1, incoming("USDJPY", "BUY", 2), groups).allowed)

  def test_unknown_exposure_refuses(self):
    decision = cx.evaluate_currency_exposure(CAP2, incoming("EURUSD", "BUY"), [], unknown_reasons=["stale_executor_snapshot"])
    self.assertEqual((decision.allowed, decision.reason_code), (False, EXPOSURE_UNKNOWN))

  def test_xau_candidates_are_never_affected(self):
    crowded = [live("EURUSD", "SELL", 1), live("GBPUSD", "SELL", 2), live("USDJPY", "BUY", 3)]
    self.assertTrue(cx.evaluate_currency_exposure(CAP1, incoming("XAU", "BUY"), crowded).allowed)
    self.assertTrue(cx.evaluate_currency_exposure(CAP1, incoming("XAU", "SELL"), crowded).allowed)

  def test_a_group_of_unknown_direction_counts_on_both_sides(self):
    groups = [live("EURUSD", "UNKNOWN", 1)]
    self.assertFalse(cx.evaluate_currency_exposure(CAP1, incoming("GBPUSD", "BUY", 2), groups).allowed)
    self.assertFalse(cx.evaluate_currency_exposure(CAP1, incoming("GBPUSD", "SELL", 2), groups).allowed)

  def test_the_groups_own_setup_is_ignored(self):
    self.assertTrue(cx.evaluate_currency_exposure(CAP1, incoming("EURUSD", "BUY", 1), [live("EURUSD", "BUY", 1)]).allowed)


class PolicyParsingTests(unittest.TestCase):
  def test_absent_is_none_and_a_valid_block_parses(self):
    self.assertIsNone(_currency_exposure_policy("XAU", {"opposite_position": {"allowed": True}}))
    self.assertIsNone(_currency_exposure_policy("XAU", None))
    self.assertEqual(_currency_exposure_policy("EURUSD", {"currency": {"max_groups_per_currency_direction": 2}}), CAP2)

  def test_invalid_blocks_are_rejected_at_load(self):
    for bad in (
      {"currency": {"max_groups_per_currency_direction": 0}},
      {"currency": {"max_groups_per_currency_direction": 2.5}},
      {"currency": {"max_groups_per_currency_direction": True}},
      {"currency": {}},
      {"currency": {"max_groups_per_currency_direction": 2, "bogus": 1}},
      {"currency": {"max_groups_per_currency_direction": 2, "reservation_ttl_seconds": 10}},
      {"currency": 3},
    ):
      with self.subTest(bad=bad):
        with self.assertRaises(EffectiveInstrumentError):
          _currency_exposure_policy("EURUSD", bad)


class ShippedConfigTests(unittest.TestCase):
  def test_every_fx_pair_is_capped_at_two_and_xau_is_in_no_currency_group(self):
    from app.core import instrument_geometry

    for pair in cx.FX_PAIRS:
      with self.subTest(pair=pair):
        policy = instrument_geometry.currency_exposure_policy(pair)
        self.assertEqual(policy, CAP2)
    self.assertIsNone(instrument_geometry.currency_exposure_policy("XAU"))


@unittest.skipUnless(HAVE_FAKEREDIS, "fakeredis and lupa are not installed")
class AtomicAdmissionTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.client = fake_aioredis.FakeRedis(decode_responses=False)

  async def asyncTearDown(self):
    await self.client.aclose()

  async def reserve(self, inc, runtime=(), policy=CAP2, now=1_000_000):
    return await cx.reserve_currency_slot(self.client, policy=policy, incoming=inc, runtime_groups=list(runtime), now=now)

  async def test_two_workers_never_both_take_the_last_usd_slot(self):
    runtime = [live("EURUSD", "SELL", 1)]  # long USD once; the cap is 2, one slot left
    results = await asyncio.gather(
      self.reserve(incoming("GBPUSD", "SELL", 2), runtime),
      self.reserve(incoming("USDJPY", "BUY", 3), runtime),
    )
    self.assertEqual(sorted(r.allowed for r in results), [False, True])  # exactly one admitted
    loser = next(r for r in results if not r.allowed)
    self.assertEqual((loser.reason_code, loser.measured["currency"], loser.measured["currency_side"]), (cx.CURRENCY_EXPOSURE_LIMIT, "USD", "LONG"))

  async def test_many_concurrent_workers_admit_exactly_the_free_slots(self):
    results = await asyncio.gather(*[
      self.reserve(incoming(pair, direction, 10 + i)) for i, (pair, direction) in enumerate([("EURUSD", "SELL"), ("GBPUSD", "SELL"), ("USDJPY", "BUY")] * 2)
    ])
    self.assertEqual(sum(1 for r in results if r.allowed), 2)  # cap 2 on long USD, six candidates

  async def test_a_reservation_counts_until_released(self):
    self.assertTrue((await self.reserve(incoming("EURUSD", "SELL", 1), policy=CAP1)).allowed)
    self.assertFalse((await self.reserve(incoming("GBPUSD", "SELL", 2), policy=CAP1)).allowed)
    await cx.release_currency_slot(self.client, symbol="EURUSD", direction="SELL", setup_id="setup1")
    self.assertTrue((await self.reserve(incoming("GBPUSD", "SELL", 2), policy=CAP1)).allowed)

  async def test_a_reservation_whose_plan_is_in_the_runtime_is_counted_once(self):
    self.assertTrue((await self.reserve(incoming("EURUSD", "SELL", 1), policy=CAP2)).allowed)
    runtime = [live("EURUSD", "SELL", 1)]  # the executor now shows it
    again = await self.reserve(incoming("GBPUSD", "SELL", 2), runtime, policy=CAP2)
    self.assertTrue(again.allowed)  # one group, not two
    self.assertEqual(again.measured["counts"]["USD:LONG"], 1.0)

  async def test_an_expired_reservation_stops_counting(self):
    self.assertTrue((await self.reserve(incoming("EURUSD", "SELL", 1), policy=CAP1, now=1_000_000)).allowed)
    self.assertTrue((await self.reserve(incoming("GBPUSD", "SELL", 2), policy=CAP1, now=1_000_000 + 100_000)).allowed)

  async def test_a_missing_snapshot_of_another_pair_is_unknown_exposure_and_refuses(self):
    from app.autotrade.group_exposure import SymbolExposure

    exposures = {pair: SymbolExposure(symbol=pair) for pair in cx.FX_PAIRS}
    exposures["GBPJPY"].unknown_reasons.append("executor_snapshot_missing")
    decision = await cx.admit_currency_group(self.client, policy=CAP2, incoming=incoming("EURUSD", "BUY"), exposures=exposures)
    self.assertEqual((decision.allowed, decision.reason_code), (False, EXPOSURE_UNKNOWN))
    self.assertEqual(await self.client.keys("autotrade:currency_exposure:*"), [])  # nothing reserved

  async def test_xau_never_reserves_anything(self):
    decision = await cx.admit_currency_group(self.client, policy=CAP1, incoming=incoming("XAU", "BUY"), exposures={})
    self.assertTrue(decision.allowed)
    self.assertEqual(await self.client.keys("autotrade:currency_exposure:*"), [])


if __name__ == "__main__":
  unittest.main()
