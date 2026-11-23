"""FX3: the planner estimates a JPY-quoted pair's money with the live USDJPY pip value, the executor's rule, and keeps the configured value as the fallback.

Run from ``algo-bot``:  ``python -m unittest tests.test_fx3_jpy_pip_value``  (the Redis-backed cases need ``fakeredis``).
Executor reference: ctrader-engine PipValue.JpyQuotedUsdPerLot (pip size x contract size / USDJPY mid, 4 places, quote at most 300 s old, mid in 50-400).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import unittest
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.autotrade import units as pip_units  # noqa: E402
from app.autotrade.group_exposure import planned_plan_risk  # noqa: E402
from app.autotrade.trade_plan import TradePlanEntryLeg  # noqa: E402
from app.autotrade.trade_plan_builder import fit_entry_legs_to_volume, pack_risk_sizing  # noqa: E402
from app.core import instrument_geometry  # noqa: E402

try:
  import fakeredis.aioredis as fake_aioredis

  HAVE_FAKEREDIS = True
except ImportError:  # pragma: no cover
  HAVE_FAKEREDIS = False

NOW = 1_800_000_000.0


@dataclass(frozen=True)
class FakeEntry:
  """The one attribute fit_entry_legs_to_volume reads and replaces."""

  legs: tuple
JPY_PIP, JPY_CONTRACT = 0.01, 100_000.0


def run(coro):
  return asyncio.run(coro)


def live(bid=159.19, ask=159.21, ts=NOW, **kw):
  return pip_units.jpy_quoted_pip_value(pip_size=JPY_PIP, contract_units_per_lot=JPY_CONTRACT, bid=bid, ask=ask, quote_ts=ts, now=NOW, **kw)


class RuleTests(unittest.TestCase):
  def test_a_live_quote_of_159_2_gives_6_2814(self):
    self.assertEqual(live(), 6.2814)

  def test_the_value_follows_the_rate_it_is_not_a_constant(self):
    self.assertEqual(live(bid=142.85, ask=142.87), 6.9999)  # 1000 / 142.86, where the old 7.0 was set
    self.assertEqual(live(bid=159.0, ask=159.0), 6.2893)

  def test_a_stale_quote_is_not_used_but_300_seconds_still_is(self):
    self.assertEqual(live(ts=NOW - 300), 6.2814)
    self.assertIsNone(live(ts=NOW - 301))
    self.assertEqual(live(ts=0), 6.2814)  # a zero timestamp is not aged (the executor's rule)

  def test_an_unusable_quote_is_none(self):
    for kwargs in ({"bid": 0.0, "ask": 0.0}, {"bid": 159.3, "ask": 159.1}, {"bid": 19.0, "ask": 19.1}, {"bid": 640.0, "ask": 640.2}):
      with self.subTest(kwargs=kwargs):
        self.assertIsNone(live(**kwargs))
    self.assertIsNone(pip_units.jpy_quoted_pip_value(pip_size=0, contract_units_per_lot=JPY_CONTRACT, bid=159.19, ask=159.21, quote_ts=NOW, now=NOW))

  def test_only_pairs_quoted_in_jpy(self):
    self.assertTrue(all(pip_units.is_jpy_quoted(s) for s in ("USDJPY", "GBPJPY", "gbpjpy")))
    self.assertFalse(any(pip_units.is_jpy_quoted(s) for s in ("EURUSD", "GBPUSD", "XAU", "XAUUSD", "")))


@unittest.skipUnless(HAVE_FAKEREDIS, "fakeredis is not installed")
class RedisTests(unittest.TestCase):
  def seeded(self, **quote):
    client = fake_aioredis.FakeRedis(decode_responses=True)
    if quote is not None:
      payload = {"bid": 159.19, "ask": 159.21, "ts": int(NOW)}
      payload.update(quote)
      run(client.set("price:USDJPY:spot", json.dumps(payload)))
    return client

  def units_of(self, symbol):
    return instrument_geometry.instrument_runtime(symbol).units

  def test_the_live_quote_replaces_the_configured_value_for_both_jpy_pairs(self):
    client = self.seeded()
    for pair, configured in (("USDJPY", 6.27), ("GBPJPY", 6.28)):
      with self.subTest(pair=pair):
        units = self.units_of(pair)
        self.assertEqual(float(units.pip_value_per_lot), configured)
        self.assertEqual(run(pip_units.live_pip_value_per_lot(client, pair, units, now=NOW)), 6.2814)
        effective = run(pip_units.effective_units(client, pair, units, now=NOW))
        self.assertEqual(effective.pip_value_per_lot, 6.2814)
        self.assertEqual((effective.pip_size, effective.contract_units_per_lot, effective.volume_units_per_lot), (units.pip_size, units.contract_units_per_lot, units.volume_units_per_lot))

  def test_a_stale_missing_or_unreadable_quote_falls_back_to_the_configured_value(self):
    cases = {
      "stale": self.seeded(ts=int(NOW) - 301),
      "missing": fake_aioredis.FakeRedis(decode_responses=True),
      "bad rate": self.seeded(bid=19.0, ask=19.1),
    }
    garbage = fake_aioredis.FakeRedis(decode_responses=True)
    run(garbage.set("price:USDJPY:spot", "not json"))
    cases["garbage"] = garbage
    for name, client in cases.items():
      with self.subTest(case=name):
        units = self.units_of("GBPJPY")
        self.assertEqual(run(pip_units.live_pip_value_per_lot(client, "GBPJPY", units, now=NOW)), 6.28)
        self.assertIs(run(pip_units.effective_units(client, "GBPJPY", units, now=NOW)), units)

  def test_eurusd_gbpusd_and_xau_are_unchanged_even_with_a_live_usdjpy_quote(self):
    client = self.seeded()
    for symbol in ("EURUSD", "GBPUSD", "XAU"):
      with self.subTest(symbol=symbol):
        units = self.units_of(symbol)
        self.assertEqual(run(pip_units.live_pip_value_per_lot(client, symbol, units, now=NOW)), float(units.pip_value_per_lot))
        self.assertIs(run(pip_units.effective_units(client, symbol, units, now=NOW)), units)  # the very same object

  def test_a_redis_failure_is_no_quote(self):
    class Broken:
      async def get(self, key):
        raise ConnectionError("redis down")

    units = self.units_of("USDJPY")
    self.assertEqual(run(pip_units.live_pip_value_per_lot(Broken(), "USDJPY", units, now=NOW)), 6.27)


class ConsumerTests(unittest.TestCase):
  """The planner's money estimates take the value they are handed, and without one they read the configured value as before."""

  def test_the_risk_leg_loss_uses_the_value_it_is_given(self):
    cfg = instrument_geometry.instrument_runtime("GBPJPY")
    leg = SimpleNamespace(lots=0.02, price=Decimal("210.00"))
    kwargs = dict(account_equity=708.0, risk_leg=leg, stop_price=Decimal("209.50"), pip_size=0.01)
    configured = pack_risk_sizing(cfg, **kwargs)
    live_value = pack_risk_sizing(cfg, **kwargs, pip_value_per_lot=7.0)
    self.assertEqual(configured, (Decimal("1.1130"), Decimal("2.0")))  # 0.02 lot x 50 pips x 6.28 = 6.28 = 0.887% of 708
    self.assertLess(live_value[0], configured[0])  # a larger pip value makes the risk leg's loss larger, so the main ladder carries less
    self.assertEqual(pack_risk_sizing(cfg, **kwargs, pip_value_per_lot=None), configured)

  def test_the_group_risk_amount_scales_with_the_live_pip_value(self):
    plan = SimpleNamespace(
      symbol="GBPJPY", analysis=SimpleNamespace(direction="BUY"), stop=SimpleNamespace(price=209.80),
      entry=SimpleNamespace(entry_prices=lambda: [210.00]),
      sizing=SimpleNamespace(mode="equity_table"), risk=SimpleNamespace(risk_multiplier=1.5, risk_percent=1.0),
    )
    base = instrument_geometry.instrument_runtime("GBPJPY").units
    configured = planned_plan_risk(plan, equity=708.0, units=base)
    from dataclasses import replace

    live_units = replace(base, pip_value_per_lot=6.2814)
    live_risk = planned_plan_risk(plan, equity=708.0, units=live_units)
    self.assertIsNotNone(configured)
    self.assertAlmostEqual(live_risk / configured, 6.2814 / 6.28, places=6)

  def test_entry_leg_fitting_uses_the_value_it_is_given_and_otherwise_the_configured_one(self):
    cfg = instrument_geometry.instrument_runtime("GBPJPY")
    legs = tuple(TradePlanEntryLeg(leg_id=f"L{i}", price=Decimal(str(price)), volume_ratio=Decimal("1") / 3) for i, price in enumerate((210.0, 209.9, 209.8), start=1))
    entry = FakeEntry(legs=legs)
    # $200 at 2% over a 30-pip stop: 4 / (30 x 6.28) = 0.021 lot -> two legs fit; at a pip value of 7.0 it is 0.019 lot -> only one, so the entry is left alone.
    kwargs = dict(
      symbol="GBPJPY", direction="BUY", stop_price=Decimal("209.70"), pip_size=0.01, account_equity=200.0, cfg=cfg, risk_sized=True,
      main_risk_percent=Decimal("2.0"), risk_multiplier=Decimal("1.0"), risk_percent=Decimal("2.0"),
    )
    self.assertEqual(len(fit_entry_legs_to_volume(entry, **kwargs).legs), 2)
    self.assertEqual(len(fit_entry_legs_to_volume(entry, **kwargs, pip_value_per_lot=None).legs), 2)
    self.assertEqual(len(fit_entry_legs_to_volume(entry, **kwargs, pip_value_per_lot=7.0).legs), 3)


if __name__ == "__main__":
  unittest.main()
