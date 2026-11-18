"""Supply/demand structure and entry derivation at the Algo Bot boundary: recorded verbatim, never a decision input.

Run from ``algo-bot``:  ``python -m unittest tests.test_sd_zone_passthrough``.
The Go Analysis Engine owns the swing-anchored zone (``structure.swing``) and the entry it recommends (``entry_plan``). Algo Bot copies a few of those facts
onto the match and the plan. These tests use the real replayed EURUSD demand opportunity of ``test_candle_confirmation_optional`` and prove that an older event
(neither block) parses unchanged, that the facts pass through, that a malformed block is refused at the contract, and that no value in either block changes
admission, a price, a stop or a volume.
"""
from __future__ import annotations

import copy
import json
import unittest

from tests.test_amd_context_passthrough import economics, plan_of  # noqa: F401  (also sets the config environment)
from tests.test_candle_confirmation_optional import CONFIRMED, match_of

from app.analysis_client.models import parse_analysis_event  # noqa: E402
from app.autotrade.trade_plan import TradePlan  # noqa: E402

PAYLOAD = CONFIRMED["payload"]
LOW, HIGH = PAYLOAD["entry"]["low"], PAYLOAD["entry"]["high"]
SWING = {
  "zone_id": "technique:supply_demand:buy:1793317500@M15", "major_swing_class": "MAJOR", "zone_strength": "strong", "swing_anchor_price": 1.11817,
  "proximal_price": 1.119, "distal_price": 1.11817, "swing_confirmed_at": 1793318400, "base_formed_at": 1793317500, "departure_confirmed_at": 1793319300,
  "base_candles": 4, "prominence_atr": 6.1, "departure_atr": 4.4, "efficiency": 0.66, "external": True, "shift": "CHoCH",
}
PLAN = {
  "model": "zone_candidates_v1", "kind": "proximal", "price": HIGH, "depth_fraction": 0.0, "stop_pips": 8.0, "effective_risk": 0.0008, "room_r": 1.4,
  "target": 1.12029, "target_label": "nearest_opposing_structure",
  "candidates": [{"kind": "proximal", "price": HIGH, "depth_fraction": 0.0, "feasible": True}, {"kind": "midpoint", "price": (LOW + HIGH) / 2, "depth_fraction": 0.5, "feasible": True}],
}


def variant(swing=None, plan=None):
  out = copy.deepcopy(CONFIRMED)
  if swing is not None:
    out["payload"]["structure"]["swing"] = copy.deepcopy(swing)
  if plan is not None:
    out["payload"]["entry_plan"] = copy.deepcopy(plan)
  return out


class PassThroughTests(unittest.TestCase):
  def test_an_older_event_without_either_block_parses_unchanged(self):
    match = match_of(CONFIRMED, required=True)
    self.assertIsNone(match.structure_swing_class)
    self.assertIsNone(match.entry_plan_kind)

  def test_the_facts_reach_the_match_and_the_plan_and_survive_the_json_round_trip(self):
    raw = variant(SWING, PLAN)
    match = match_of(raw, required=True)
    self.assertEqual((match.structure_swing_class, match.structure_zone_strength), ("MAJOR", "strong"))
    self.assertEqual((match.entry_plan_kind, match.entry_plan_price, match.entry_plan_room_r), ("proximal", HIGH, 1.4))
    _, plan = plan_of(match, raw["payload"])
    stored = plan.to_dict()["analysis"]
    self.assertEqual(stored["structure_zone_strength"], "strong")
    self.assertEqual(stored["entry_plan_kind"], "proximal")
    again = TradePlan.from_dict(json.loads(json.dumps(plan.to_dict()))).to_dict()["analysis"]
    self.assertEqual(again["structure_swing_class"], "MAJOR")
    self.assertEqual(again["entry_plan_effective_risk"], 0.0008)

  def test_malformed_blocks_are_refused_at_the_contract(self):
    for name, bad in {
      "unknown class": variant({**SWING, "major_swing_class": "HUGE"}),
      "unknown strength": variant({**SWING, "zone_strength": "excellent"}),
      "proximal outside the structure": variant({**SWING, "proximal_price": 1.5}),
      "plan price outside the band": variant(None, {**PLAN, "price": HIGH + 0.01}),
      "unknown plan kind": variant(None, {**PLAN, "kind": "optimal"}),
      "no effective risk": variant(None, {**PLAN, "effective_risk": 0}),
    }.items():
      with self.subTest(name), self.assertRaises(Exception):
        parse_analysis_event("analysis.opportunity.v1", json.dumps(bad))


class NeverAnInputTests(unittest.TestCase):
  def test_admission_and_economics_do_not_depend_on_either_block(self):
    weak = {**SWING, "major_swing_class": "MINOR", "zone_strength": "weak", "external": False}
    deep = {**PLAN, "kind": "deep", "depth_fraction": 0.75, "room_r": 3.0}
    baseline = None
    for name, raw in {"none": variant(), "strong": variant(SWING, PLAN), "weak and deep": variant(weak, deep), "swing only": variant(SWING), "plan only": variant(None, PLAN)}.items():
      with self.subTest(name):
        match = match_of(raw, required=True)
        decision, plan = plan_of(match, raw["payload"])
        outcome = (getattr(decision, "allowed", getattr(decision, "approved", False)), self._economics(plan))
        baseline = baseline or outcome
        self.assertEqual(outcome, baseline)

  @staticmethod
  def _economics(plan):
    data = copy.deepcopy(plan.to_dict())
    data["analysis"] = {k: v for k, v in data["analysis"].items() if not k.startswith(("mad_", "structure_swing", "structure_zone", "entry_plan_"))}
    return data

  def test_no_decision_module_reads_them(self):
    import inspect

    from app.autotrade import execution_policy, execution_route, xau_ladder

    for module in (execution_policy, execution_route, xau_ladder):
      source = inspect.getsource(module)
      for token in ("entry_plan_kind", "entry_plan_price", "entry_plan_room_r", "entry_plan_effective_risk", "structure_zone_strength", "structure_swing_class"):
        self.assertNotIn(token, source, f"{module.__name__} must not read {token}")


class XauLadderStillHoldsTests(unittest.TestCase):
  """The planner may publish a band that starts deep in the zone (a cut band of at least 30 pips). Algo Bot's mechanical ladder is unchanged: distinct prices at least
  10 pips apart from the band's near edge, or no plan at all (never a repeated price)."""

  def test_a_planner_band_still_builds_a_distinct_spaced_ladder(self):
    from decimal import Decimal

    from app.autotrade import xau_ladder

    settings = xau_ladder.LadderSettings()
    for direction, start, far in (("SELL", 4301.00, 4304.00), ("SELL", 4302.25, 4307.25), ("BUY", 4304.00, 4301.00), ("BUY", 4307.25, 4302.25)):
      with self.subTest(direction=direction, start=start):
        prices, ratios = xau_ladder.price_ladder(direction, start, far, settings=settings, strict=True)
        self.assertEqual(len(prices), len(set(prices)), "no repeated ladder price")
        self.assertGreaterEqual(len(prices), 2)
        for a, b in zip(prices, prices[1:]):
          self.assertGreaterEqual(abs(Decimal(str(a)) - Decimal(str(b))), Decimal("1.0"), "legs at least 10 pips (1.00) apart")
        self.assertAlmostEqual(sum(ratios), 1.0, places=6)
        self.assertEqual(prices[0], start, "the ladder starts at the band's near edge, the price the engine recommends")


if __name__ == "__main__":
  unittest.main()
