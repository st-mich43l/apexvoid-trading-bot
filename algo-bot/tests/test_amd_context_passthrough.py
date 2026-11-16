"""AMD context at the Algo Bot boundary: recorded verbatim, never a gate.

Run from ``algo-bot``:  ``python -m unittest tests.test_amd_context_passthrough``.
The Go Analysis Engine computes every AMD fact (``mad`` v3: dated range, sweep, reclaim, expansion and the setup's relation). Algo Bot only copies them
onto the match and the plan. These tests use the real replayed EURUSD demand opportunities of ``test_candle_confirmation_optional`` and prove that an
older event (no AMD fields) still parses, that the new fields pass through unchanged, and that no AMD value (a conflicting relation, an inactive
context, a stale sequence) changes whether the setup is admitted or any price, volume or stop of the plan.
"""
from __future__ import annotations

import copy
import json
import unittest
from decimal import Decimal

from tests.test_candle_confirmation_optional import CONFIRMED, match_of  # noqa: F401  (also sets the config environment)

from app.analysis_client.models import parse_analysis_event  # noqa: E402
from app.autotrade import units  # noqa: E402
from app.autotrade.execution_policy import _instrument_context, evaluate_execution_policy  # noqa: E402
from app.autotrade.trade_plan import TradePlan  # noqa: E402
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match  # noqa: E402
from app.core.config import runtime_config  # noqa: E402

AMD = {
  "version": 3, "phase": "manip", "confidence": 0.62, "affinity": 0.0, "direction": "BUY", "sweep_side": "low", "reclaim": True,
  "reason_code": "asia_sweep_reclaim", "timeframe": "M5", "active": True, "observed_at": 1793339100,
  "range": {"id": "asia:1793300400", "kind": "asia", "timeframe": "M5", "high": 1.1195, "low": 1.1172, "formed_from": 1793300400,
            "sealed_at": 1793329200, "bar_count": 108, "expected_bars": 108},
  "sequence": {"id": "asia:1793300400:low:1793334000", "status": "partial", "stages": ["range", "sweep", "reclaim"], "direction": "BUY",
               "sweep_side": "low", "swept_level": 1.1172, "sweep_at": 1793334000, "reclaim_at": 1793334000,
               "sweep_penetration_atr": 0.9, "reclaim_depth_atr": 0.4},
  "relation": "aligned", "relation_reasons": ["sweep_reclaim_in_setup_direction"],
  "evidence_ids": ["asia:1793300400", "asia:1793300400:low:1793334000"],
}


def with_mad(raw: dict, mad: dict | None) -> dict:
  out = copy.deepcopy(raw)
  tech = out["payload"]["technical_context"]
  if mad is None:
    tech.pop("mad", None)
  else:
    tech["mad"] = copy.deepcopy(mad)
  return out


def plan_of(match, payload):
  symbol = payload["symbol"]
  pip = float(units.pip_size(symbol))
  cfg = _instrument_context(symbol, runtime_config)
  spot = payload["technical_context"]["reference_price"]
  decision = evaluate_execution_policy(match, spot_price=spot, regime="range", pip_size=pip, cfg=cfg, executable_quote=spot)
  plan = build_trade_plan_from_strategy_match(
    match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=Decimal(str(pip)), spot_price=spot, regime="range",
    cfg=cfg, executable_quote=spot, max_volume=100_000_000,
  )
  return decision, plan


def economics(plan: TradePlan) -> dict:
  """Everything that is price, size or approval: it must not depend on AMD."""
  data = copy.deepcopy(plan.to_dict())
  data["analysis"] = {k: v for k, v in data["analysis"].items() if not k.startswith("mad_")}
  return data


class PassThroughTests(unittest.TestCase):
  def test_an_older_event_without_amd_fields_still_parses(self):
    # The replayed event carries the v2 mad block (no v3 fields).
    match = match_of(CONFIRMED, required=True)
    self.assertEqual(match.mad_version, 2)
    self.assertIsNone(match.mad_alignment)
    self.assertIsNone(match.mad_sequence_id)
    self.assertIsNone(match.mad_alignment_reasons)

  def test_an_event_with_no_mad_block_at_all_still_parses(self):
    match = match_of(with_mad(CONFIRMED, None), required=True)
    self.assertIsNone(match.mad_version)
    self.assertIsNone(match.mad_alignment)

  def test_the_new_fields_are_copied_verbatim_onto_match_and_plan(self):
    raw = with_mad(CONFIRMED, AMD)
    match = match_of(raw, required=True)
    self.assertEqual(match.mad_timeframe, "M5")
    self.assertIs(match.mad_active, True)
    self.assertEqual(match.mad_alignment, "aligned")
    self.assertEqual(match.mad_alignment_reasons, ["sweep_reclaim_in_setup_direction"])
    self.assertEqual(match.mad_range_id, "asia:1793300400")
    self.assertEqual(match.mad_sequence_id, "asia:1793300400:low:1793334000")
    self.assertEqual(match.mad_sequence_status, "partial")
    _, plan = plan_of(match, raw["payload"])
    stored = plan.to_dict()
    self.assertEqual(stored["analysis"]["mad_alignment"], "aligned")
    self.assertEqual(stored["analysis"]["mad_sequence_id"], "asia:1793300400:low:1793334000")
    # and it survives the persisted JSON round trip of the plan
    again = TradePlan.from_dict(json.loads(json.dumps(stored))).to_dict()["analysis"]
    self.assertEqual(again["mad_alignment"], "aligned")
    self.assertEqual(again["mad_range_id"], "asia:1793300400")
    self.assertEqual(again["mad_alignment_reasons"], ["sweep_reclaim_in_setup_direction"])

  def test_a_malformed_relation_is_rejected_at_the_contract(self):
    bad = with_mad(CONFIRMED, {**AMD, "relation": "excellent"})
    with self.assertRaises(Exception):
      parse_analysis_event("analysis.opportunity.v1", json.dumps(bad))
    bad = with_mad(CONFIRMED, {**AMD, "sequence": {**AMD["sequence"], "status": "great"}})
    with self.assertRaises(Exception):
      parse_analysis_event("analysis.opportunity.v1", json.dumps(bad))


class NeverAGateTests(unittest.TestCase):
  """Admission, prices, volume and stop are identical whatever AMD says."""

  VARIANTS = {
    "none": None,
    "aligned": AMD,
    "conflicting": {**AMD, "relation": "conflicting", "relation_reasons": ["sweep_direction_opposes_setup"]},
    "unavailable": {**AMD, "active": False, "relation": "unavailable", "relation_reasons": ["mad_evidence_stale"],
                    "sequence": {**AMD["sequence"], "status": "stale"}},
    "invalidated": {**AMD, "active": False, "relation": "conflicting", "relation_reasons": ["sweep_extreme_lost"],
                    "sequence": {**AMD["sequence"], "status": "invalidated", "invalidation_reason": "sweep_extreme_lost"}},
    "irrelevant": {**AMD, "relation": "irrelevant", "relation_reasons": ["strategy_not_amd_relevant"], "sequence": None, "range": None},
  }

  def test_admission_and_plan_economics_do_not_depend_on_amd(self):
    baseline = None
    for name, mad in self.VARIANTS.items():
      with self.subTest(variant=name):
        raw = with_mad(CONFIRMED, mad if mad is None or mad.get("sequence") is not None or mad.get("range") is not None else {k: v for k, v in mad.items() if k not in ("sequence", "range")})
        match = match_of(raw, required=True)
        decision, plan = plan_of(match, raw["payload"])
        outcome = (getattr(decision, "allowed", getattr(decision, "approved", False)), economics(plan))
        if baseline is None:
          baseline = outcome
        self.assertEqual(outcome[0], baseline[0], "admission must not depend on AMD")
        self.assertEqual(outcome[1], baseline[1], "no price, volume, ladder or stop may depend on AMD")

  def test_the_policy_source_never_reads_an_amd_field(self):
    import inspect

    from app.autotrade import execution_policy, execution_route, trade_plan_builder

    for module in (execution_policy, execution_route):
      source = inspect.getsource(module)
      self.assertNotIn("mad_", source, f"{module.__name__} must not read AMD facts")
    builder = inspect.getsource(trade_plan_builder)
    # The builder only copies the recorded facts; it never branches on them.
    for line in builder.splitlines():
      if "mad_" in line:
        self.assertRegex(line.strip(), r"^mad_\w+=match\.mad_\w+,$", f"unexpected AMD use: {line!r}")


if __name__ == "__main__":
  unittest.main()
