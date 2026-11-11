"""TL-LIVE-2: Trendline is traded at ONE price, the projected line, and never as a zone.

Run from ``algo-bot``:  ``python -m unittest tests.test_tl_live2_single_price``  (stdlib only; needs the app's runtime dependencies).
The envelopes are the real production-replayed Trendline opportunities of tests/test_trendline_timeframes.py (XAU M5 BUY, EURUSD M15 SELL) with the engine's typed
``execution`` block added, and XAU M15 SELL / EURUSD M5 variants derived from them. Everything below runs the real adapter, execution policy and plan builder.
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from decimal import Decimal

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.analysis_client.models import parse_analysis_event  # noqa: E402
from app.autotrade import go_opportunity_policy as go  # noqa: E402
from app.autotrade import trade_plan_builder as builder  # noqa: E402
from app.autotrade.execution_policy import _instrument_context, evaluate_execution_policy  # noqa: E402
from app.autotrade.execution_route import resolve_single_price_route  # noqa: E402
from app.autotrade.strategy_match import StrategyMatch, is_single_price_match  # noqa: E402
from app.autotrade.trade_plan import TradePlan, TradePlanError  # noqa: E402
from app.core.config import runtime_config  # noqa: E402
from tests.test_trendline_timeframes import ENVELOPES, PIP  # noqa: E402

STEP = {"M5": 300, "M15": 900}


def with_execution(raw, price, *, bars=6, proximity=None, slope=0.05):
  raw = copy.deepcopy(raw)
  payload = raw["payload"]
  timeframe = payload["timeframe"]
  atr = payload["technical_context"]["atr"]
  projected_at = payload["created_at"] + STEP[timeframe]
  payload["execution"] = {
    "model": "single_price", "price": price, "timeframe": timeframe, "projected_at": projected_at,
    "valid_until": projected_at + bars * STEP[timeframe], "proximity": 0.1 * atr if proximity is None else proximity, "slope_per_bar": slope,
  }
  payload["expires_at"] = max(payload["expires_at"], payload["execution"]["valid_until"])
  return raw


def xau_m15_sell():
  raw = copy.deepcopy(ENVELOPES["m5"])
  payload = raw["payload"]
  payload["direction"] = "SELL"
  payload["timeframe"] = payload["structure_timeframe"] = "M15"
  payload["entry"] = {"low": 3910.00, "high": 3910.80}
  payload["invalidation"] = {"price": 3914.00, "label": "trendline_close_violation"}
  payload["targets"] = [{"price": {"price": 3880.0, "label": "nearest_opposing_liquidity"}}]
  payload["evidence"] = [{"code": "m15_trendline_causal_anchors"}, {"code": "m15_trendline_reaction_confirmed"}]
  context = payload["technical_context"]
  context["reference_price"] = 3909.0
  context["confirmation"]["zone_id"] = "trendline:M15:resistance:1793616300:1793621100"
  context["candle_evidence"]["direction"] = "SELL"
  context["higher_timeframes"] = [{"timeframe": "H1", "direction": "BUY", "layer": "intermediate", "reference_time": 1793620800}]
  return raw


def eurusd_m5():
  raw = copy.deepcopy(ENVELOPES["m15"])
  payload = raw["payload"]
  payload["timeframe"] = payload["structure_timeframe"] = "M5"
  payload["evidence"] = [{"code": "m5_trendline_causal_anchors"}, {"code": "m5_trendline_reaction_confirmed"}]
  confirmation = payload["technical_context"]["confirmation"]
  confirmation["zone_id"] = "trendline:M5:resistance:1793646000:1793664900"
  confirmation["touch_bar_time"] = confirmation["confirmation_bar_time"] = payload["technical_context"]["reference_time"]
  payload["technical_context"]["higher_timeframes"] = [{"timeframe": "M15", "direction": "SELL", "layer": "internal", "reference_time": 1793665800}]
  return raw


def parse(raw):
  return parse_analysis_event("analysis.opportunity.v1", json.dumps(raw))


def match_for(raw, *, now=None):
  event = parse(raw)
  return go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["trendline"], now=raw["payload"]["created_at"] + 30 if now is None else now)


# name -> (symbol, envelope factory, projected price inside the corridor, a quote at/near the line on the trade's side, the same published `now`)
XAU_M5_BUY = ("XAU", lambda: ENVELOPES["m5"], 3900.64)         # corridor 3899.99-3901.28, invalidation 3899.19
XAU_M15_SELL = ("XAU", xau_m15_sell, 3910.40)                  # corridor 3910.00-3910.80, invalidation 3914.00
EUR_M15_SELL = ("EURUSD", lambda: ENVELOPES["m15"], 1.11970)   # corridor 1.11961-1.11982, invalidation 1.11994
EUR_M5_SELL = ("EURUSD", eurusd_m5, 1.11970)


class AdmissionTests(unittest.TestCase):
  def test_a_declared_single_price_becomes_a_one_price_match_and_keeps_the_corridor(self):
    for name, (symbol, factory, price) in {"XAU M5": XAU_M5_BUY, "XAU M15": XAU_M15_SELL, "EURUSD M15": EUR_M15_SELL, "EURUSD M5": EUR_M5_SELL}.items():
      with self.subTest(name):
        raw = with_execution(factory(), price)
        match = match_for(raw)
        self.assertTrue(is_single_price_match(match))
        self.assertEqual(match.entry_low, price)
        self.assertEqual(match.entry_high, price)
        self.assertEqual(match.execution_price, price)
        self.assertEqual((match.interaction_low, match.interaction_high), (raw["payload"]["entry"]["low"], raw["payload"]["entry"]["high"]))
        self.assertEqual(match.execution_valid_until, raw["payload"]["execution"]["valid_until"])
        self.assertEqual(match.execution_timeframe, raw["payload"]["timeframe"])
        # The match round-trips through the Redis cache with its typed execution.
        again = StrategyMatch.from_json(match.to_json())
        self.assertIsNotNone(again)
        self.assertTrue(is_single_price_match(again))
        self.assertEqual(again.execution_proximity, match.execution_proximity)

  def test_an_event_without_the_block_is_still_a_zone(self):
    match = match_for(ENVELOPES["m5"])
    self.assertFalse(is_single_price_match(match))
    self.assertGreater(match.entry_high, match.entry_low)
    self.assertEqual(match.execution_model, "zone")

  def test_no_other_strategy_may_declare_a_single_price(self):
    raw = with_execution(ENVELOPES["m5"], 3900.64)
    raw["payload"]["strategy"] = "key_level"
    raw["payload"]["evidence"] = [{"code": "m5_key_level_reaction_confirmed"}]
    event = parse(raw)
    with self.assertRaises(go.AdapterRejection) as refused:
      go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["key_level"], now=raw["payload"]["created_at"] + 30)
    self.assertEqual(refused.exception.code, "single_price_not_approved")

  def test_the_event_contract_is_validated(self):
    for name, mutate in {
      "price outside the corridor": lambda p: p["execution"].update(price=3902.0),
      "projected at or before the observation (look-ahead)": lambda p: p["execution"].update(projected_at=p["created_at"]),
      "valid_until not after projected_at": lambda p: p["execution"].update(valid_until=p["execution"]["projected_at"]),
      "valid beyond the opportunity's life": lambda p: p["execution"].update(valid_until=p["expires_at"] + 1),
      "unknown model": lambda p: p["execution"].update(model="ladder"),
      "BUY price beyond the invalidation": lambda p: p["execution"].update(price=3899.5),
      "unknown field": lambda p: p["execution"].update(rounding="whole"),
    }.items():
      with self.subTest(name):
        raw = with_execution(ENVELOPES["m5"], 3900.64)
        mutate(raw["payload"])
        with self.assertRaises(Exception):
          parse(raw)

  def test_a_stale_projection_is_never_admitted(self):
    raw = with_execution(ENVELOPES["m5"], 3900.64, bars=6)
    valid_until = raw["payload"]["execution"]["valid_until"]
    with self.assertRaises(go.AdapterRejection) as refused:
      match_for(raw, now=valid_until)
    self.assertEqual(refused.exception.code, "projection_stale")
    match_for(raw, now=valid_until - 1)  # one second left is still admitted


class RouteTests(unittest.TestCase):
  def route(self, direction, quote, price=4300.0, proximity=0.2, now=100, valid_until=1000, digits=2):
    return resolve_single_price_route(
      direction=direction, price=price, executable_quote=quote, proximity=proximity, valid_until=valid_until, now=now, digits=digits,
    )

  def test_price_above_the_line_rests_one_limit_at_the_line(self):
    plan = self.route("BUY", 4302.0)
    self.assertTrue(plan.valid)
    self.assertEqual((plan.route, plan.planned_entry_price, plan.planned_leg_entry_prices), ("single_limit", 4300.0, (4300.0,)))

  def test_price_touching_the_line_is_the_same_single_limit(self):
    for quote in (4300.0, 4299.9):  # at the line, and just through it (within proximity): the limit fills at the quote or better, never above the line
      self.assertTrue(self.route("BUY", quote).valid, quote)
    self.assertTrue(self.route("SELL", 4300.1).valid)

  def test_price_already_beyond_the_line_is_not_chased(self):
    buy = self.route("BUY", 4299.5)
    sell = self.route("SELL", 4300.5)
    for plan in (buy, sell):
      self.assertFalse(plan.valid)
      self.assertEqual(plan.reject_code, "entry_beyond_projected_line")
      self.assertEqual(plan.planned_leg_entry_prices, ())

  def test_a_stale_projection_is_refused_whatever_the_quote(self):
    plan = self.route("BUY", 4300.0, now=1000, valid_until=1000)
    self.assertFalse(plan.valid)
    self.assertEqual(plan.reject_code, "projection_stale")

  def test_the_price_is_only_rounded_to_the_brokers_digits(self):
    self.assertEqual(self.route("BUY", 4302.0, price=4300.1537).planned_entry_price, 4300.15)
    self.assertEqual(self.route("SELL", 1.1196, price=1.119704, proximity=0.0001, digits=5).planned_entry_price, 1.1197)


class PlanningTests(unittest.TestCase):
  def plan(self, symbol, factory, price, *, quote, now=None, raw=None):
    raw = raw or with_execution(factory(), price)
    match = match_for(raw)
    now_ts = raw["payload"]["created_at"] + 40 if now is None else now
    return builder.build_trade_plan_from_strategy_match(
      match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=PIP.get(symbol, Decimal("0.0001")), spot_price=quote, regime=None,
      executable_quote=quote, cfg=_instrument_context(symbol, runtime_config), max_volume=100_000_000, now_ts=now_ts,
    ), raw

  def assert_one_price(self, plan, price, raw):
    self.assertEqual(plan.entry.type, "single_limit")
    self.assertEqual(plan.entry.legs, (), "no L2, no L3, no ladder")
    self.assertIsNone(plan.entry.risk_leg, "no Tricky Risky Leg")
    self.assertIsNone(plan.entry.min_leg_spacing)
    self.assertEqual(plan.entry.order_price, Decimal(str(price)), "the order is the projected line, to the digit")
    self.assertIsNone(plan.entry.zone_low)
    self.assertIsNone(plan.entry.zone_high)
    self.assertGreater(plan.entry.proximity, 0)
    self.assertEqual(plan.entry.expires_at, raw["payload"]["execution"]["valid_until"], "the plan is valid exactly as long as the projection")
    self.assertEqual(plan.expires_at, raw["payload"]["execution"]["valid_until"])
    TradePlan.from_dict(plan.to_dict())  # the plan round-trips and validates

  def test_xau_m5_support_buy(self):
    symbol, factory, price = XAU_M5_BUY
    plan, raw = self.plan(symbol, factory, price, quote=3903.0)
    self.assert_one_price(plan, price, raw)
    self.assertEqual(plan.analysis.direction, "BUY")
    self.assertLess(plan.stop.price, plan.entry.order_price)
    # The 12.9-pip corridor that the 30-pip rule refuses as a ZONE is not a zone here; the XAU stop envelope still applies as for every intraday setup.
    stop_pips = float((plan.entry.order_price - plan.stop.price) / PIP["XAU"])
    self.assertGreaterEqual(stop_pips, 50)
    self.assertLessEqual(stop_pips, 70)

  def test_xau_m15_resistance_sell(self):
    symbol, factory, price = XAU_M15_SELL
    plan, raw = self.plan(symbol, factory, price, quote=3908.0)
    self.assert_one_price(plan, price, raw)
    self.assertEqual(plan.analysis.direction, "SELL")
    self.assertGreater(plan.stop.price, plan.entry.order_price)

  def test_eurusd_m15_and_m5(self):
    for symbol, factory, price in (EUR_M15_SELL, EUR_M5_SELL):
      with self.subTest(factory.__name__):
        plan, raw = self.plan(symbol, factory, price, quote=1.11930)
        self.assert_one_price(plan, price, raw)
        self.assertEqual(plan.analysis.direction, "SELL")

  def test_price_touching_the_line_plans_the_same_single_order(self):
    symbol, factory, price = XAU_M5_BUY
    plan, raw = self.plan(symbol, factory, price, quote=3900.64)
    self.assert_one_price(plan, price, raw)

  def test_price_already_beyond_the_line_is_refused_and_never_chased(self):
    symbol, factory, price = XAU_M5_BUY
    with self.assertRaises(builder.TradePlanBuildRejected) as refused:
      self.plan(symbol, factory, price, quote=3899.80)  # 0.84 below the line; proximity is 0.1 ATR = 0.32
    self.assertEqual(refused.exception.reason_code, "entry_beyond_projected_line")

  def test_a_projection_that_went_stale_is_refused_at_planning_time(self):
    symbol, factory, price = XAU_M5_BUY
    raw = with_execution(factory(), price, bars=2)
    stale_now = raw["payload"]["execution"]["valid_until"]
    with self.assertRaises(builder.TradePlanBuildRejected) as refused:
      self.plan(symbol, factory, price, quote=3903.0, now=stale_now, raw=raw)
    self.assertEqual(refused.exception.reason_code, "projection_stale")

  def test_a_re_anchored_publication_never_extends_the_projection(self):
    symbol, factory, price = XAU_M5_BUY
    raw = with_execution(factory(), price, bars=3)
    plan, _ = self.plan(symbol, factory, price, quote=3903.0, now=raw["payload"]["created_at"] + 200, raw=raw)
    self.assertEqual(plan.entry.expires_at, raw["payload"]["execution"]["valid_until"])  # not now + the 12 hour TTL

  def test_the_zone_rules_are_untouched_for_a_trendline_that_does_not_declare_it(self):
    # The same real 12.9-pip zone, with no execution block (the strategy's switch off): the 30-pip XAU minimum still refuses it.
    match = match_for(ENVELOPES["m5"])
    with self.assertRaises(builder.TradePlanBuildRejected) as refused:
      builder.build_trade_plan_from_strategy_match(
        match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=PIP["XAU"], spot_price=3903.0, regime=None, executable_quote=3903.0,
        cfg=_instrument_context("XAU", runtime_config), max_volume=100_000_000, now_ts=1793623840,
      )
    self.assertEqual(refused.exception.reason_code, "entry_zone_below_minimum_width")

  def test_the_policy_itself_exempts_only_the_declared_model(self):
    symbol, factory, price = XAU_M5_BUY
    match = match_for(with_execution(factory(), price))
    cfg = _instrument_context(symbol, runtime_config)
    declared = evaluate_execution_policy(match, spot_price=3903.0, regime=None, pip_size=0.1, cfg=cfg, executable_quote=3903.0, now=match.issued_at + 40)
    self.assertNotEqual(declared.reason_code, "entry_zone_below_minimum_width")
    self.assertEqual(declared.measured["execution_model"], "single_price")
    self.assertEqual(declared.measured["planned_leg_entry_prices"], [price])
    self.assertFalse(declared.measured["execution_zone_expanded"])


class PlanContractTests(unittest.TestCase):
  def test_proximity_is_only_valid_on_a_single_limit_and_not_negative(self):
    symbol, factory, price = XAU_M5_BUY
    plan, _ = PlanningTests().plan(symbol, factory, price, quote=3903.0)
    data = plan.to_dict()
    data["entry"]["proximity"] = "-1"
    with self.assertRaises(TradePlanError):
      TradePlan.from_dict(data)
    data = plan.to_dict()
    data["entry"].update(type="limit_ladder", legs=[{"leg_id": "L1", "price": "3900.64", "volume_ratio": "1"}])
    with self.assertRaises(TradePlanError):
      TradePlan.from_dict(data)

  def test_an_older_plan_without_proximity_still_loads(self):
    symbol, factory, price = XAU_M5_BUY
    plan, _ = PlanningTests().plan(symbol, factory, price, quote=3903.0)
    data = plan.to_dict()
    data["entry"].pop("proximity")
    self.assertIsNone(TradePlan.from_dict(data).entry.proximity)


if __name__ == "__main__":
  unittest.main()
