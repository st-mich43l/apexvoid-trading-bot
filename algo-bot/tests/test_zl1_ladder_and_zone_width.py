"""ZL-1: the XAU entry zone is the zone the analysis engine found (at least 30 pips, never stretched), and a non-scalp XAU entry is a price ladder with distinct,
meaningfully separated prices or no plan at all.

Run from ``algo-bot``:  ``python -m unittest tests.test_zl1_ladder_and_zone_width``  (stdlib only; needs the app's runtime dependencies).
The planning cases run a real production-replayed XAU Trendline opportunity (the 3899.99-3901.28 M5 BUY of tests/test_trendline_timeframes.py) through the real
adapter, execution policy and plan builder, with its entry band replaced by the band under test.
"""
from __future__ import annotations

import copy
import dataclasses
import os
import random
import unittest
from decimal import Decimal

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.analysis_client.models import GO_EXPIRY_REASONS  # noqa: E402
from app.analysis_client.repository import PostgresAnalysisOpportunityRepository  # noqa: E402
from app.autotrade import trade_plan_builder as builder  # noqa: E402
from app.autotrade import xau_ladder  # noqa: E402
from app.autotrade.execution_policy import _instrument_context  # noqa: E402
from app.autotrade.execution_route import resolve_execution_route_plan  # noqa: E402
from app.autotrade.trade_plan import TradePlan, TradePlanError  # noqa: E402
from app.core.config import runtime_config  # noqa: E402
from tests.test_trendline_timeframes import ENVELOPES, PIP, match_for  # noqa: E402

LADDER = xau_ladder.LadderSettings()  # 10 pips, spread across the zone
ONE_UNIT = Decimal("1.0")


def _gaps(prices):
  return [abs(Decimal(str(a)) - Decimal(str(b))) for a, b in zip(prices, prices[1:])]


class LadderPricesTests(unittest.TestCase):
  def test_the_briefs_example_zone_gets_three_distinct_prices_spread_across_it(self):
    prices, ratios = xau_ladder.price_ladder("BUY", 4303.00, 4300.00, settings=LADDER, strict=True)
    self.assertEqual(prices, (4303.00, 4301.50, 4300.00))
    self.assertEqual(ratios, (0.6, 0.2, 0.2))

  def test_a_sell_ladder_mirrors_it(self):
    prices, ratios = xau_ladder.price_ladder("SELL", 4300.00, 4303.00, settings=LADDER, strict=True)
    self.assertEqual(prices, (4300.00, 4301.50, 4303.00))
    self.assertEqual(ratios, (0.6, 0.2, 0.2))

  def test_the_fixed_step_mode_keeps_the_old_one_unit_spacing(self):
    fixed = xau_ladder.LadderSettings(spread_across_zone=False)
    prices, _ = xau_ladder.price_ladder("BUY", 4303.00, 4300.00, settings=fixed, strict=True)
    self.assertEqual(prices, (4303.00, 4302.00, 4301.00))

  def test_every_gap_is_at_least_the_minimum_after_rounding_for_any_zone(self):
    rng = random.Random(21)
    for _ in range(2000):
      start = round(rng.uniform(1800, 5000) + rng.choice((0, 0.005, 0.004, 0.0049)), 3)
      depth = round(rng.uniform(0.4, 9.0), rng.choice((2, 3)))
      for side, far in (("BUY", start - depth), ("SELL", start + depth)):
        try:
          prices, _ = xau_ladder.price_ladder(side, start, far, settings=LADDER, strict=True)
        except xau_ladder.LadderGeometryUnavailable:
          self.assertLess(depth, 1.0 + 0.011, f"a {depth} deep zone must hold a ladder: start={start}")
          continue
        self.assertGreaterEqual(len(prices), 2)
        self.assertEqual(len(set(prices)), len(prices), "prices must be unique after rounding")
        self.assertTrue(all(gap >= ONE_UNIT for gap in _gaps(prices)), f"{side} {start}->{far}: {prices}")
        ordered = list(prices) == sorted(prices, reverse=(side == "BUY"))
        self.assertTrue(ordered, f"{side} ladder must step deeper: {prices}")
        lo, hi = min(start, far) - 0.006, max(start, far) + 0.006
        self.assertTrue(all(lo <= price <= hi for price in prices), f"legs must stay inside the zone: {prices} vs {start}->{far}")

  def test_a_zone_too_shallow_for_two_prices_is_unavailable_not_a_single_entry(self):
    with self.assertRaises(xau_ladder.LadderGeometryUnavailable):
      xau_ladder.price_ladder("BUY", 4303.00, 4302.50, settings=LADDER, strict=True)
    # A manual single-price intent still gets the price the owner typed.
    self.assertEqual(xau_ladder.price_ladder("BUY", 4303.00, 4302.50, settings=LADDER)[0], (4303.0,))

  def test_the_geometry_check_names_the_problem(self):
    check = xau_ladder.ladder_geometry_problem
    self.assertIsNone(check("BUY", [4303.00, 4301.50, 4300.00], min_spacing=1.0))
    self.assertEqual(check("BUY", [4303.00, 4303.00, 4302.90], min_spacing=1.0), "duplicate_leg_price")
    self.assertEqual(check("BUY", [4303.00, 4302.90], min_spacing=1.0), "leg_spacing_below_minimum")
    self.assertEqual(check("BUY", [4300.00, 4301.50], min_spacing=1.0), "leg_prices_not_ordered")
    self.assertEqual(check("SELL", [4303.00, 4301.50], min_spacing=1.0), "leg_prices_not_ordered")


class SpecFileTests(unittest.TestCase):
  """The reviewed specification (contracts/autotrade/xau-ladder-spec.json) is the table the ladder is held to."""

  def test_every_hand_computed_ladder_case_is_reproduced(self):
    import json

    spec_path = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "autotrade", "xau-ladder-spec.json")
    with open(spec_path, encoding="utf-8") as handle:
      spec = json.load(handle)
    self.assertEqual(Decimal(spec["min_leg_spacing_pips"]), xau_ladder.DEFAULT_MIN_SPACING_PIPS)
    for case in spec["ladder_price_cases"]:
      with self.subTest(case=case["name"]):
        args = (case["direction"], Decimal(case["start"]), Decimal(case["far"]))
        if case.get("unavailable"):
          with self.assertRaises(xau_ladder.LadderGeometryUnavailable):
            xau_ladder.price_ladder(*args, settings=LADDER, strict=True)
          continue
        prices, ratios = xau_ladder.price_ladder(*args, settings=LADDER, strict=True)
        self.assertEqual([Decimal(str(p)) for p in prices], [Decimal(p) for p in case["prices"]])
        self.assertEqual([Decimal(str(r)) for r in ratios], [Decimal(r) for r in case["ratios"]])


class RouteTests(unittest.TestCase):
  def route(self, direction, quote, low, high, strategy="Demand", preference="limit", distribution="zone_scale"):
    return resolve_execution_route_plan(
      direction=direction, order_type_preference=preference, entry_distribution=distribution, executable_quote=quote,
      zone_low=low, zone_high=high, atr=7.0, zone_fill_enabled=True, zone_fill_min_atr=0.5, digits=2, pip_size=0.1,
      strategy=strategy, manual_xau_ladder=True, ladder_settings=LADDER,
    )

  def test_price_above_a_buy_zone_rests_three_distinct_limits_in_the_zone(self):
    plan = self.route("BUY", 4310.0, 4300.0, 4303.0)
    self.assertTrue(plan.valid)
    self.assertEqual(plan.planned_leg_entry_prices, (4303.0, 4301.5, 4300.0))
    self.assertTrue(all(price <= 4303.0 and price >= 4300.0 for price in plan.planned_leg_entry_prices))

  def test_price_inside_the_zone_starts_the_ladder_at_the_quote_never_above_it(self):
    plan = self.route("BUY", 4302.0, 4300.0, 4303.0)
    self.assertEqual(plan.planned_leg_entry_prices, (4302.0, 4301.0, 4300.0))
    self.assertTrue(all(price <= 4302.0 for price in plan.planned_leg_entry_prices), "no leg above the live quote: it would fill at once")

  def test_a_sell_zone_mirrors_it(self):
    plan = self.route("SELL", 4301.0, 4300.0, 4303.0)
    self.assertEqual(plan.planned_leg_entry_prices, (4301.0, 4302.0, 4303.0))
    self.assertTrue(all(price >= 4301.0 for price in plan.planned_leg_entry_prices))

  def test_price_nearly_at_the_far_edge_leaves_no_ladder_and_is_refused_with_its_code(self):
    plan = self.route("BUY", 4300.4, 4300.0, 4303.0)
    self.assertFalse(plan.valid)
    self.assertEqual(plan.reject_code, "ladder_geometry_unavailable")
    self.assertEqual(plan.planned_leg_entry_prices, ())

  def test_price_already_through_the_zone_is_refused_not_turned_into_market_legs(self):
    plan = self.route("BUY", 4299.0, 4300.0, 4303.0)
    self.assertFalse(plan.valid)
    self.assertEqual(plan.reject_code, "ladder_geometry_unavailable")

  def test_a_thin_zone_is_not_stretched_toward_the_stop_to_make_room(self):
    plan = self.route("BUY", 4302.0, 4300.0, 4300.4)
    self.assertFalse(plan.valid)
    self.assertEqual(plan.reject_code, "ladder_geometry_unavailable")

  def test_an_m1_scalp_still_enters_at_one_price(self):
    plan = resolve_execution_route_plan(
      direction="BUY", order_type_preference="market", entry_distribution="single", executable_quote=4302.0, zone_low=4301.9, zone_high=4302.1,
      atr=7.0, zone_fill_enabled=True, zone_fill_min_atr=0.5, digits=2, pip_size=0.1, strategy="Impulse Pullback Scalp",
      manual_xau_ladder=False, ladder_settings=LADDER,
    )
    self.assertTrue(plan.valid)
    self.assertEqual(plan.planned_leg_entry_prices, ())


class FxStaysSingleEntryTests(unittest.TestCase):
  def test_an_fx_zone_is_untouched_by_the_xau_ladder_rules(self):
    plan = resolve_execution_route_plan(
      direction="BUY", order_type_preference="limit", entry_distribution="single", executable_quote=1.1010, zone_low=1.1000, zone_high=1.1002, atr=0.0008,
      zone_fill_enabled=True, zone_fill_min_atr=0.5, digits=5, pip_size=0.0001, strategy="Supply Demand", manual_xau_ladder=False, ladder_settings=LADDER,
    )
    self.assertTrue(plan.valid)
    self.assertEqual(plan.route, "single_limit")
    self.assertEqual(len(plan.planned_leg_entry_prices), 1)


class PlanningTests(unittest.TestCase):
  """The real planner on a real production opportunity, with the entry band replaced by the width under test."""

  symbol = "XAU"

  @classmethod
  def setUpClass(cls):
    cls.cfg = _instrument_context(cls.symbol, runtime_config)
    cls.match = match_for(ENVELOPES["m5"])  # BUY, entry 3899.99-3901.28 (12.9 pips), invalidation 3899.19

  def build(self, low, high, *, match=None, quote=3905.0):
    match = dataclasses.replace(match or self.match, entry_low=low, entry_high=high)
    return builder.build_trade_plan_from_strategy_match(
      match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=PIP[self.symbol], spot_price=quote, regime=None,
      executable_quote=quote, cfg=self.cfg, max_volume=100_000_000,
    )

  def test_zones_under_thirty_pips_are_refused_as_the_engine_found_them(self):
    for pips in (10, 20, 29.9):
      with self.subTest(pips=pips):
        with self.assertRaises(builder.TradePlanBuildRejected) as refused:
          self.build(3899.99, round(3899.99 + pips * 0.1, 2))
        self.assertEqual(refused.exception.reason_code, "entry_zone_below_minimum_width")
        self.assertAlmostEqual(refused.exception.measured["entry_zone_pips"], pips, places=1)
        self.assertEqual(refused.exception.measured["minimum_entry_zone_pips"], 30.0)

  def test_thirty_and_forty_pip_zones_become_distinct_spaced_ladders_with_the_stop_where_structure_put_it(self):
    for pips in (30, 40):
      with self.subTest(pips=pips):
        plan = self.build(3899.99, round(3899.99 + pips * 0.1, 2))
        prices = [leg.price for leg in plan.entry.legs]
        self.assertGreaterEqual(len(prices), 2)
        self.assertEqual(len(set(prices)), len(prices))
        self.assertEqual(prices, sorted(prices, reverse=True), "a BUY ladder steps down")
        self.assertTrue(all(gap >= ONE_UNIT for gap in _gaps(prices)))
        self.assertEqual(plan.entry.min_leg_spacing, Decimal("1.0"), "the planner declares the spacing it checked")
        # The zone was never widened toward the stop and the invalidation never moved to make room: Go's stop is the plan's stop (rounded to the card price).
        self.assertGreaterEqual(plan.stop.price, Decimal("3897.0"))
        self.assertLess(plan.stop.price, min(prices))
        if plan.entry.risk_leg is not None:
          self.assertTrue(all(abs(plan.entry.risk_leg.price - price) >= ONE_UNIT for price in prices), "the risk leg never sits on an entry leg")

  def test_a_sell_zone_of_thirty_pips_plans_a_mirrored_ladder(self):
    raw = copy.deepcopy(ENVELOPES["m5"])
    payload = raw["payload"]
    payload["direction"] = "SELL"
    payload["entry"] = {"low": 3910.00, "high": 3913.00}
    payload["invalidation"] = {"price": 3916.00, "label": "trendline_close_violation"}
    payload["targets"] = [{"price": {"price": 3880.0, "label": "nearest_opposing_liquidity"}}]
    payload["technical_context"]["reference_price"] = 3905.0
    payload["technical_context"]["confirmation"]["zone_id"] = "trendline:M5:resistance:1793616300:1793621100"
    payload["technical_context"]["candle_evidence"]["direction"] = "SELL"
    payload["technical_context"]["higher_timeframes"] = [{"timeframe": "M15", "direction": "BUY", "layer": "intermediate", "reference_time": 1793622600}]
    plan = self.build(3910.00, 3913.00, match=match_for(raw), quote=3905.0)
    prices = [leg.price for leg in plan.entry.legs]
    self.assertEqual(plan.analysis.direction, "SELL")
    self.assertEqual(len(set(prices)), len(prices))
    self.assertEqual(prices, sorted(prices), "a SELL ladder steps up")
    self.assertTrue(all(gap >= ONE_UNIT for gap in _gaps(prices)))

  def test_the_thirty_pip_boundary_is_exact(self):
    with self.assertRaises(builder.TradePlanBuildRejected):
      self.build(3899.99, 3902.98)  # 29.9
    self.build(3899.99, 3902.99)  # 30.0 exactly


class PlanContractTests(unittest.TestCase):
  def plan(self, legs, spacing="1.0", risk_price=None):
    cfg = _instrument_context("XAU", runtime_config)
    match = match_for(ENVELOPES["m5"])
    built = builder.build_trade_plan_from_strategy_match(
      dataclasses.replace(match, entry_low=3899.99, entry_high=3902.99), plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=PIP["XAU"],
      spot_price=3905.0, regime=None, executable_quote=3905.0, cfg=cfg, max_volume=100_000_000,
    )
    data = built.to_dict()
    data["entry"]["legs"] = [{"leg_id": f"L{i + 1}", "price": str(price), "volume_ratio": ratio} for i, (price, ratio) in enumerate(legs)]
    data["entry"]["min_leg_spacing"] = spacing
    if risk_price is not None:
      data["entry"]["risk_leg"] = {"price": str(risk_price), "lots": "0.02"}
    else:
      data["entry"].pop("risk_leg", None)
    return data

  def test_the_invalid_ladder_from_the_brief_never_passes_validation(self):
    for legs in ([("4303.00", "0.6"), ("4303.00", "0.2"), ("4302.90", "0.2")], [("4303.00", "0.6"), ("4302.90", "0.4")]):
      with self.assertRaises(TradePlanError):
        TradePlan.from_dict(self.plan(legs))

  def test_a_valid_ladder_round_trips_with_its_declared_spacing(self):
    plan = TradePlan.from_dict(self.plan([("3903.00", "0.6"), ("3901.50", "0.2"), ("3900.00", "0.2")]))
    self.assertEqual(plan.entry.min_leg_spacing, Decimal("1.0"))
    self.assertEqual(TradePlan.from_dict(plan.to_dict()).entry.min_leg_spacing, Decimal("1.0"))

  def test_a_buy_ladder_that_steps_up_is_refused(self):
    with self.assertRaises(TradePlanError):
      TradePlan.from_dict(self.plan([("3900.00", "0.6"), ("3901.50", "0.2"), ("3903.00", "0.2")]))

  def test_a_risk_leg_on_an_entry_leg_price_is_refused(self):
    with self.assertRaises(TradePlanError):
      TradePlan.from_dict(self.plan([("3903.00", "0.6"), ("3901.50", "0.2"), ("3900.00", "0.2")], risk_price="3900.40"))

  def test_an_older_plan_without_a_declared_spacing_still_loads(self):
    data = self.plan([("3903.00", "0.6"), ("3901.50", "0.2"), ("3900.00", "0.2")])
    data["entry"].pop("min_leg_spacing")
    self.assertIsNone(TradePlan.from_dict(data).entry.min_leg_spacing)


class TerminalEventTests(unittest.TestCase):
  def test_a_zone_that_ran_out_is_an_expiry_like_the_opportunity_deadline(self):
    self.assertIn("ZONE_EXPIRED", GO_EXPIRY_REASONS)
    self.assertIn("SETUP_EXPIRED", GO_EXPIRY_REASONS)
    state = PostgresAnalysisOpportunityRepository._terminal_state
    self.assertEqual(state("ZONE_EXPIRED"), "expired")
    self.assertEqual(state("zone_expired"), "expired")
    self.assertEqual(state("SETUP_EXPIRED"), "expired")
    for broken in ("ZONE_INVALIDATED", "ZONE_MITIGATED", "STRUCTURE_CLOSED_THROUGH"):
      self.assertEqual(state(broken), "invalidated")


if __name__ == "__main__":
  unittest.main()
