"""FX1 part C: ``stop_envelope.max_floor_widening_ratio`` refuses a plan whose executable stop is more than N times the technical stop.

Run from ``algo-bot``:  ``python -m unittest tests.test_fx1_stop_widening``.
The instrument floor widens a tight structural stop (EURUSD 12 pips, USDJPY 18, GBPJPY 22). The pre-registered replay since 4 Nov found that on the JPY pack the trades widened
2x or more lost -0.28R (n 44) while those widened less than 1.25x made +0.31R (n 36), so the two JPY pairs carry ``max_floor_widening_ratio: 2.0``. The USD majors failed the
rule (their narrow trades lost too) and carry no key. Each test moves only the invalidation of a real production EURUSD Key Level envelope, so the technical stop is an exact number of pips.
"""
from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.analysis_client.models import parse_analysis_event  # noqa: E402
from app.autotrade import go_opportunity_policy as go  # noqa: E402
from app.autotrade.execution_policy import _instrument_context, evaluate_execution_policy  # noqa: E402
from app.core.config import runtime_config  # noqa: E402
from app.runtime.instruments import EffectiveInstrumentError, _floor_widening_ratio  # noqa: E402
from tests.test_fx1_sizing import EURUSD_ENVELOPE  # noqa: E402
from tests.test_kl12_strategy_targeting import kl_match  # noqa: E402

REFUSAL = "stop_floor_widening_exceeded"


def evaluate(symbol: str, technical_pips: float):
  """The real policy on a BUY whose structural stop is ``technical_pips`` below the worst entry (the planner adds a tick of clearance)."""
  raw = json.loads(EURUSD_ENVELOPE)
  payload = raw["payload"]
  jpy = symbol.endswith("JPY")
  pip, digits, base = (0.01, 3, 150.0) if jpy else (0.0001, 5, 1.09)
  low, high = round(base, digits), round(base + 3 * pip, digits)
  payload["symbol"] = symbol
  payload["direction"] = "BUY"
  payload["entry"] = {"low": low, "high": high}
  payload["invalidation"]["price"] = round(low - technical_pips * pip, digits)
  payload["targets"] = [{"price": {"price": round(low + 40 * pip, digits), "label": "nearest_opposing_liquidity"}}]
  payload["technical_context"]["reference_price"] = (low + high) / 2
  payload.pop("stop_envelope", None)
  event = parse_analysis_event("analysis.opportunity.v1", json.dumps(raw))
  match = go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["key_level"], now=payload["created_at"] + 30)
  quote = (low + high) / 2
  return evaluate_execution_policy(match, spot_price=quote, regime="trend", pip_size=pip, cfg=runtime_config, executable_quote=quote)


class ShippedConfigTests(unittest.TestCase):
  def ratio(self, symbol):
    return getattr(_instrument_context(symbol, runtime_config).execution.reaction, "stop_floor_widening_max_ratio", None)

  def test_only_the_jpy_pack_carries_the_cap(self):
    self.assertEqual((self.ratio("USDJPY"), self.ratio("GBPJPY")), (2.0, 2.0))
    for symbol in ("EURUSD", "GBPUSD", "XAU"):
      self.assertIsNone(self.ratio(symbol), symbol)


class PolicyTests(unittest.TestCase):
  def test_a_jpy_stop_widened_past_twice_is_refused_with_its_measurements(self):
    result = evaluate("USDJPY", 4)  # technical 5.5 pips, floor 18 -> 3.27x
    self.assertFalse(result.allowed)
    self.assertEqual(result.reason_code, REFUSAL)
    self.assertGreater(result.measured["stop_widening_ratio"], 3.0)
    self.assertEqual(result.measured["stop_floor_widening_max_ratio"], 2.0)
    self.assertEqual((result.measured["planned_base_stop_pips"], result.measured["planned_final_stop_pips"]), (5.5, 18.0))

  def test_a_jpy_stop_widened_less_than_twice_is_unchanged(self):
    result = evaluate("USDJPY", 8)  # technical 9.5 pips, floor 18 -> 1.89x
    self.assertTrue(result.allowed)
    self.assertNotEqual(result.reason_code, REFUSAL)

  def test_the_second_jpy_pair_uses_its_own_floor(self):
    self.assertEqual(evaluate("GBPJPY", 8).reason_code, REFUSAL)  # 9.5 pips -> floor 22 = 2.32x
    self.assertTrue(evaluate("GBPJPY", 14).allowed)  # 15.5 pips -> 1.42x

  def test_a_structural_stop_at_or_above_the_floor_is_never_widened_so_never_refused(self):
    self.assertTrue(evaluate("USDJPY", 14).allowed)
    self.assertTrue(evaluate("GBPJPY", 30).allowed)

  def test_the_usd_majors_are_not_affected(self):
    for symbol in ("EURUSD", "GBPUSD"):
      with self.subTest(symbol=symbol):
        result = evaluate(symbol, 4)  # widened 2x or more, no key -> allowed exactly as before
        self.assertTrue(result.allowed)
        self.assertNotEqual(result.reason_code, REFUSAL)

  def test_xau_is_not_affected(self):
    match, spot = kl_match()
    result = evaluate_execution_policy(match, spot_price=spot, regime="trend", pip_size=0.1, cfg=runtime_config, executable_quote=spot)
    self.assertNotEqual(result.reason_code, REFUSAL)


class ValidationTests(unittest.TestCase):
  def test_the_key_is_optional(self):
    self.assertIsNone(_floor_widening_ratio("X", {}))
    self.assertEqual(_floor_widening_ratio("X", {"max_floor_widening_ratio": 2}), 2.0)
    self.assertEqual(_floor_widening_ratio("X", {"max_floor_widening_ratio": 1.5}), 1.5)

  def test_anything_that_is_not_a_number_above_one_is_refused(self):
    for bad in (1.0, 0.5, 0, -2, "2", True, [2]):
      with self.subTest(value=bad), self.assertRaises(EffectiveInstrumentError):
        _floor_widening_ratio("X", {"max_floor_widening_ratio": bad})


if __name__ == "__main__":
  unittest.main()
