"""FX3: every instrument that declares ``exposure.currency`` declares the same cap.

Run from ``algo-bot``:  ``python -m unittest tests.test_fx3_currency_cap_agreement``.
The currency cap counts groups across pairs but is read from the INCOMING pair's block; two pairs with different caps would judge the same currency side against different
limits. The check runs when such an instrument is loaded, over a registry built from the shipped configuration with one value changed at a time.
"""
from __future__ import annotations

import copy
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.core.config import runtime_config  # noqa: E402
from app.runtime.instruments import CurrencyExposurePolicy, EffectiveInstrumentError, for_instrument  # noqa: E402

FX = ("EURUSD", "GBPUSD", "USDJPY", "GBPJPY")


def runtime(mutate=None):
  """A fresh runtime shaped like the shipped one (instruments and packs as plain data), optionally changed by ``mutate(instruments, packs)``."""
  instruments = copy.deepcopy(runtime_config.instruments.to_dict())
  packs = copy.deepcopy(runtime_config.instrument_packs.to_dict())
  if mutate:
    mutate(instruments, packs)
  attributes = {key: getattr(runtime_config, key) for key in runtime_config.keys()}
  attributes.update(instruments=instruments, instrument_packs=packs)
  return SimpleNamespace(**attributes)


class AgreeingConfigurationTests(unittest.TestCase):
  def test_the_shipped_configuration_loads_and_every_fx_pair_has_the_same_cap(self):
    rt = runtime()
    policies = {pair: for_instrument(rt, pair).currency_exposure_policy for pair in FX}
    self.assertEqual(set(policies.values()), {CurrencyExposurePolicy(max_groups_per_currency_direction=2)})
    self.assertIsNone(for_instrument(rt, "XAU").currency_exposure_policy)

  def test_one_instrument_declaring_it_alone_is_fine(self):
    def only_eurusd(instruments, packs):
      for pack in packs.values():
        pack.get("exposure", {}).pop("currency", None)
      instruments["EURUSD"].setdefault("exposure", {})["currency"] = {"max_groups_per_currency_direction": 3}

    rt = runtime(only_eurusd)
    self.assertEqual(for_instrument(rt, "EURUSD").currency_exposure_policy.max_groups_per_currency_direction, 3)
    self.assertIsNone(for_instrument(rt, "GBPJPY").currency_exposure_policy)

  def test_a_matching_override_on_one_instrument_is_fine(self):
    def same(instruments, packs):
      instruments["EURUSD"].setdefault("exposure", {})["currency"] = {"max_groups_per_currency_direction": 2}

    for pair in FX:
      for_instrument(runtime(same), pair)


class DisagreeingConfigurationTests(unittest.TestCase):
  def assertRefusesWithBoth(self, rt, symbol, first, second):
    with self.assertRaises(EffectiveInstrumentError) as raised:
      for_instrument(rt, symbol)
    message = str(raised.exception)
    for expected in (first, second):
      self.assertIn(expected, message)
    return message

  def test_two_packs_with_different_caps_refuse_to_load_on_either_side(self):
    def disagree(instruments, packs):
      packs["fx_jpy_cross_fixed_2r_v1"]["exposure"]["currency"]["max_groups_per_currency_direction"] = 3

    rt = runtime(disagree)
    for pair in ("USDJPY", "GBPJPY"):  # the JPY pack declares 3 ...
      self.assertRefusesWithBoth(rt, pair, f"{pair} has max_groups_per_currency_direction=3", "max_groups_per_currency_direction=2")
    for pair in ("EURUSD", "GBPUSD"):  # ... the USD pack 2: the same message from the other side
      self.assertRefusesWithBoth(rt, pair, f"{pair} has max_groups_per_currency_direction=2", "max_groups_per_currency_direction=3")

  def test_the_message_names_both_instruments(self):
    def disagree(instruments, packs):
      instruments["USDJPY"].setdefault("exposure", {})["currency"] = {"max_groups_per_currency_direction": 1}

    message = self.assertRefusesWithBoth(runtime(disagree), "EURUSD", "EURUSD has", "USDJPY has max_groups_per_currency_direction=1")
    self.assertIn("one cap per currency side", message)

  def test_a_different_reservation_ttl_is_a_disagreement_too(self):
    def disagree(instruments, packs):
      packs["fx_usd_major_fixed_2r_v1"]["exposure"]["currency"]["reservation_ttl_seconds"] = 900

    self.assertRefusesWithBoth(runtime(disagree), "GBPJPY", "reservation_ttl_seconds=600", "reservation_ttl_seconds=900")

  def test_xau_never_declares_it_and_is_never_refused(self):
    def disagree(instruments, packs):
      packs["fx_jpy_cross_fixed_2r_v1"]["exposure"]["currency"]["max_groups_per_currency_direction"] = 3

    rt = runtime(disagree)
    self.assertIsNone(for_instrument(rt, "XAU").currency_exposure_policy)

  def test_a_verified_registry_is_remembered_but_a_new_disagreeing_one_is_still_caught(self):
    good = runtime()
    for_instrument(good, "EURUSD")
    for_instrument(good, "EURUSD")

    def disagree(instruments, packs):
      packs["fx_jpy_cross_fixed_2r_v1"]["exposure"]["currency"]["max_groups_per_currency_direction"] = 3

    with self.assertRaises(EffectiveInstrumentError):
      for_instrument(runtime(disagree), "EURUSD")


if __name__ == "__main__":
  unittest.main()
