"""X3: the Auto Algo XAU non-scalp ladder splits its volume 20/20/60 (40/60 over two prices) when ``execution.xau_ladder.auto_ratios`` declares it; everything else
keeps its split. Owner 25 Nov 2026: the key is removed from the shipped config, so production is back on 60/20/20; the Vc cases below run with the key opted in
(``opt_in_vc``) to keep the opt-in path working.

Run from ``algo-bot``:  ``python -m unittest tests.test_x3_auto_ladder_ratios``.
PR-X0 rule D4 picked Vc (the owner's 23 Nov decision): prices, spacing and ``spread_across_zone`` are unchanged, only the volume weights move toward the deep legs. The
split is ``execution.xau_ladder.auto_ratios``; Manual /algo, scalps, FX and Trendline single-price never read it.
"""
from __future__ import annotations

import json
import os
import unittest
from decimal import Decimal
from unittest import mock

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.autotrade import xau_ladder  # noqa: E402
from app.autotrade.execution_route import resolve_execution_route_plan  # noqa: E402
from app.autotrade.trade_plan import TradePlanEntryLeg  # noqa: E402
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match, fit_entry_legs_to_volume  # noqa: E402
from app.core.config import ConfigurationError, load_runtime_config, runtime_config  # noqa: E402
from tests.test_fx1_sizing import EURUSD_ENVELOPE  # noqa: E402
from tests.test_kl12_strategy_targeting import PIP, XAU, kl_match  # noqa: E402

VC = xau_ladder.LadderSettings(auto_ratios=xau_ladder.parse_auto_ratios({3: [0.2, 0.2, 0.6], 2: [0.4, 0.6]}))
DEFAULT = xau_ladder.LadderSettings()
SPEC_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "autotrade", "xau-ladder-spec.json")


def opt_in_vc(test):
  """Run ``test`` as if ``auto_ratios`` were configured (the shipped config no longer declares it)."""
  for target in ("app.autotrade.execution_policy.ladder_settings", "app.autotrade.trade_plan_builder.ladder_settings"):
    patcher = mock.patch(target, lambda cfg: VC)
    patcher.start()
    test.addCleanup(patcher.stop)


def route(direction, quote, low, high, *, strategy="Demand", preference="limit", settings=VC):
  return resolve_execution_route_plan(
    direction=direction, order_type_preference=preference, entry_distribution="zone_scale", executable_quote=quote, zone_low=low, zone_high=high, atr=7.0,
    zone_fill_enabled=True, zone_fill_min_atr=0.5, digits=2, pip_size=0.1, strategy=strategy, manual_xau_ladder=True, ladder_settings=settings,
  )


def plan_of(match, spot, **kwargs):
  return build_trade_plan_from_strategy_match(
    match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=PIP, spot_price=spot, regime="trend", cfg=XAU, executable_quote=spot, max_volume=100_000_000, **kwargs,
  )


class ConfigTests(unittest.TestCase):
  def test_the_shipped_config_is_back_on_60_20_20(self):
    # Owner 25 Nov 2026: auto_ratios removed, the Auto Algo ladder uses the default 60/20/20 again; the Vc split stays available through the key (VC below).
    settings = xau_ladder.ladder_settings(runtime_config)
    self.assertIsNone(settings.auto_ratio_table())
    self.assertEqual((settings.min_spacing_pips, settings.spread_across_zone), (Decimal("10"), False))  # prices and spacing are untouched

  def test_the_default_split_is_still_60_20_20(self):
    self.assertEqual(xau_ladder.LADDER_RATIOS, {3: (0.6, 0.2, 0.2), 2: (0.6, 0.4), 1: (1.0,)})
    self.assertIsNone(xau_ladder.ladder_settings(object()).auto_ratio_table())

  def test_an_invalid_auto_ratios_value_is_refused_with_a_clear_error(self):
    bad = [
      ("not a mapping", [0.2, 0.2, 0.6]),
      ("a missing key", {3: [0.2, 0.2, 0.6]}),
      ("an unknown key", {3: [0.2, 0.2, 0.6], 2: [0.4, 0.6], 4: [0.25] * 4}),
      ("a wrong length", {3: [0.4, 0.6], 2: [0.4, 0.6]}),
      ("a sum over one", {3: [0.2, 0.2, 0.7], 2: [0.4, 0.6]}),
      ("a sum under one", {3: [0.2, 0.2, 0.5], 2: [0.4, 0.6]}),
      ("a zero ratio", {3: [0.0, 0.4, 0.6], 2: [0.4, 0.6]}),
      ("a negative ratio", {3: [-0.2, 0.6, 0.6], 2: [0.4, 0.6]}),
      ("a string ratio", {3: ["0.2", "0.2", "0.6"], 2: [0.4, 0.6]}),
      ("a boolean", {3: [True, 0.2, 0.0], 2: [0.4, 0.6]}),
      ("a scalar", {3: 1.0, 2: [0.4, 0.6]}),
    ]
    for name, value in bad:
      with self.subTest(case=name), self.assertRaises(xau_ladder.LadderConfigError):
        xau_ladder.parse_auto_ratios(value)

  def test_an_invalid_value_fails_configuration_loading(self):
    import tempfile

    import yaml

    root = os.path.join(os.path.dirname(__file__), "..", "..", "config")
    with tempfile.TemporaryDirectory() as tmp:
      import shutil

      shutil.copytree(root, os.path.join(tmp, "config"))
      path = os.path.join(tmp, "config", "execution.yml")
      with open(path) as f:
        document = yaml.safe_load(f)
      document["execution"]["xau_ladder"]["auto_ratios"] = {3: [0.5, 0.5, 0.5], 2: [0.4, 0.6]}
      with open(path, "w") as f:
        yaml.safe_dump(document, f)
      with self.assertRaises(ConfigurationError) as raised:
        load_runtime_config(os.path.join(tmp, "config", "apexvoid.demo.yml"))
      self.assertIn("auto_ratios[3] must sum to 1", str(raised.exception))
    load_runtime_config(os.path.join(root, "apexvoid.demo.yml"))  # the shipped value loads


class PriceLadderTests(unittest.TestCase):
  def test_prices_are_identical_with_or_without_the_ratios(self):
    for direction, start, far in (("BUY", 4303.0, 4300.0), ("SELL", 4300.0, 4303.0), ("BUY", 4303.0, 4301.5), ("SELL", 4300.0, 4301.5)):
      with self.subTest(direction=direction, far=far):
        plain = xau_ladder.price_ladder(direction, start, far, settings=DEFAULT, strict=True)
        vc = xau_ladder.price_ladder(direction, start, far, settings=VC, strict=True, ratios=VC.auto_ratio_table())
        self.assertEqual(plain[0], vc[0])
        self.assertNotEqual(plain[1], vc[1])

  def test_the_spec_file_cases_for_the_auto_split(self):
    with open(SPEC_PATH, encoding="utf-8") as handle:
      spec = json.load(handle)
    table = {int(k): tuple(float(x) for x in v) for k, v in spec["auto_ratios"].items()}
    self.assertEqual(table, VC.auto_ratio_table())
    for case in spec["auto_ladder_price_cases"]:
      with self.subTest(case=case["name"]):
        args = (case["direction"], Decimal(case["start"]), Decimal(case["far"]))
        if case.get("unavailable"):
          with self.assertRaises(xau_ladder.LadderGeometryUnavailable):
            xau_ladder.price_ladder(*args, settings=DEFAULT, strict=True, ratios=table)
          continue
        prices, ratios = xau_ladder.price_ladder(*args, settings=DEFAULT, strict=True, ratios=table)
        self.assertEqual([Decimal(str(p)) for p in prices], [Decimal(p) for p in case["prices"]])
        self.assertEqual([Decimal(str(r)) for r in ratios], [Decimal(r) for r in case["ratios"]])

  def test_the_default_spec_cases_are_unchanged(self):
    with open(SPEC_PATH, encoding="utf-8") as handle:
      spec = json.load(handle)
    for case in spec["ladder_price_cases"]:
      if case.get("unavailable"):
        continue
      prices, ratios = xau_ladder.price_ladder(case["direction"], Decimal(case["start"]), Decimal(case["far"]), settings=DEFAULT, strict=True)
      self.assertEqual([Decimal(str(r)) for r in ratios], [Decimal(r) for r in case["ratios"]], case["name"])


class RouteTests(unittest.TestCase):
  def test_an_auto_three_price_ladder_is_20_20_60_and_two_prices_40_60(self):
    three = route("BUY", 4310.0, 4300.0, 4303.0)
    self.assertEqual((three.planned_leg_entry_prices, three.planned_leg_volume_ratios), ((4303.0, 4302.0, 4301.0), (0.2, 0.2, 0.6)))
    two = route("BUY", 4310.0, 4301.5, 4303.0)
    self.assertEqual((two.planned_leg_entry_prices, two.planned_leg_volume_ratios), ((4303.0, 4302.0), (0.4, 0.6)))
    sell = route("SELL", 4290.0, 4300.0, 4303.0)
    self.assertEqual(sell.planned_leg_volume_ratios, (0.2, 0.2, 0.6))

  def test_the_prices_do_not_depend_on_the_ratios(self):
    for args in (("BUY", 4310.0, 4300.0, 4303.0), ("BUY", 4302.0, 4300.0, 4303.0), ("SELL", 4301.0, 4300.0, 4303.0)):
      self.assertEqual(route(*args).planned_leg_entry_prices, route(*args, settings=DEFAULT).planned_leg_entry_prices)

  def test_an_in_zone_market_l1_carries_20_percent(self):
    plan = route("BUY", 4302.0, 4300.0, 4303.0, preference="market")
    self.assertEqual(plan.route, "market_with_limit_scale")
    self.assertEqual((plan.planned_leg_entry_prices[0], plan.planned_leg_volume_ratios[0]), (4302.0, 0.2))

  def test_scalps_keep_60_20_20(self):
    for strategy in ("Range Scalp", "Impulse Pullback Scalp", "Impulse Retrace Scalp"):
      with self.subTest(strategy=strategy):
        plan = route("BUY", 4302.0, 4300.0, 4303.0, strategy=strategy)
        self.assertTrue(plan.planned_leg_volume_ratios == () or plan.planned_leg_volume_ratios == (0.6, 0.2, 0.2), (strategy, plan.planned_leg_volume_ratios))
        self.assertEqual(plan.planned_leg_volume_ratios, route("BUY", 4302.0, 4300.0, 4303.0, strategy=strategy, settings=DEFAULT).planned_leg_volume_ratios)


class PlanTests(unittest.TestCase):
  def test_the_shipped_auto_xau_plan_is_60_20_20(self):
    match, spot = kl_match()
    plan = plan_of(match, spot)
    self.assertEqual([str(leg.volume_ratio) for leg in plan.entry.legs], ["0.6", "0.2", "0.2"])

  def test_an_auto_xau_three_leg_plan_carries_20_20_60(self):
    opt_in_vc(self)
    match, spot = kl_match()
    plan = plan_of(match, spot)
    self.assertEqual([str(leg.volume_ratio) for leg in plan.entry.legs], ["0.2", "0.2", "0.6"])
    self.assertEqual(plan.sizing.leg_ratios, (Decimal("0.2"), Decimal("0.2"), Decimal("0.6")))
    self.assertEqual((plan.entry.legs[0].order_type, plan.entry.type), ("market", "market_with_limit_scale"))  # the in-zone market L1 carries 20%

  def test_only_the_ratios_differ_from_the_default_plan(self):
    match, spot = kl_match()
    with mock.patch("app.autotrade.execution_policy.ladder_settings", lambda cfg: VC), mock.patch("app.autotrade.trade_plan_builder.ladder_settings", lambda cfg: VC):
      vc = plan_of(match, spot).to_dict()
    with mock.patch("app.autotrade.execution_policy.ladder_settings", lambda cfg: DEFAULT), mock.patch("app.autotrade.trade_plan_builder.ladder_settings", lambda cfg: DEFAULT):
      default = plan_of(match, spot).to_dict()
    self.assertEqual([leg["price"] for leg in vc["entry"]["legs"]], [leg["price"] for leg in default["entry"]["legs"]])
    for key in ("stop", "targets", "risk"):
      self.assertEqual(vc[key], default[key], key)
    self.assertEqual(vc["entry"]["min_leg_spacing"], default["entry"]["min_leg_spacing"])
    self.assertEqual(vc["entry"]["risk_leg"], default["entry"]["risk_leg"])
    self.assertEqual([leg["volume_ratio"] for leg in default["entry"]["legs"]], ["0.6", "0.2", "0.2"])

  def test_fx_plans_are_byte_identical(self):
    from app.analysis_client.models import parse_analysis_event
    from app.autotrade import go_opportunity_policy as go
    from app.autotrade.execution_policy import _instrument_context

    event = parse_analysis_event("analysis.opportunity.v1", EURUSD_ENVELOPE)
    match = go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["key_level"], now=event.payload.created_at + 30)
    ctx = _instrument_context("EURUSD", runtime_config)
    quote = (event.payload.entry.low + event.payload.entry.high) / 2

    def build():
      return build_trade_plan_from_strategy_match(
        match, plan_id="p", setup_id="s", thesis_id="t", pip_size=0.0001, spot_price=quote, regime="trend", cfg=ctx, executable_quote=quote, max_volume=100_000_000, account_equity=708.0,
      ).to_dict()

    vc = build()
    with mock.patch("app.autotrade.execution_policy.ladder_settings", lambda cfg: DEFAULT), mock.patch("app.autotrade.trade_plan_builder.ladder_settings", lambda cfg: DEFAULT):
      default = build()
    self.assertEqual(vc, default)

  def test_the_manual_algo_ladder_keeps_its_own_ratios(self):
    from app.signals import manual_plan

    settings = VC
    self.assertIsNotNone(settings.auto_ratio_table())  # with the Auto split configured ...
    prices, ratios = xau_ladder.price_ladder("BUY", 4303.0, 4300.0, settings=settings)  # ... the manual path calls price_ladder without it
    self.assertEqual(ratios, (0.6, 0.2, 0.2))
    self.assertEqual(manual_plan.MANUAL_LADDER_RATIOS, (Decimal("0.8"), Decimal("0.2")))
    import inspect

    source = inspect.getsource(manual_plan)
    self.assertNotIn("auto_ratio", source)  # manual_plan never reads the Auto split


class VolumeFitTests(unittest.TestCase):
  def setUp(self):
    opt_in_vc(self)

  def entry(self, kind="market_with_limit_scale", ratios=("0.2", "0.2", "0.6")):
    from app.autotrade.trade_plan import TradePlanEntry

    prices = ["3996.0", "3995.0", "3994.0"][: len(ratios)]
    legs = tuple(
      TradePlanEntryLeg(leg_id=f"L{i + 1}", price=Decimal(p), volume_ratio=Decimal(r), order_type=("market" if i == 0 and kind == "market_with_limit_scale" else "limit"))
      for i, (p, r) in enumerate(zip(prices, ratios))
    )
    return TradePlanEntry(type=kind, expires_at=1_900_000_000, legs=legs, zone_low=Decimal("3994"), zone_high=Decimal("3996"), price_side="ask" if kind == "market_with_limit_scale" else None)

  def fit(self, entry, lots_equity, *, deep=True):
    # risk-sized: lots = equity x 1% / (stop pips x pip value) -> 0.01 lot steps
    return fit_entry_legs_to_volume(
      entry, symbol="XAU", direction="BUY", stop_price=Decimal("3989.5"), pip_size=0.1, account_equity=lots_equity, cfg=XAU, risk_sized=True,
      main_risk_percent=Decimal("1.0"), risk_multiplier=Decimal("1"), risk_percent=Decimal("1"), pip_value_per_lot=10.0, deep_legs_first=deep,
    )

  def test_enough_volume_leaves_the_ladder_alone(self):
    entry = self.entry()
    self.assertIs(self.fit(entry, 10_000.0), entry)  # worst leg 65 pips: ~0.15 lot -> three legs fit

  def test_a_volume_for_two_legs_keeps_the_deep_two_at_40_60_as_resting_limits(self):
    # The equity that sizes exactly 0.02 lot: lots x 100 steps x (worst-leg stop pips x pip value) / 1% risk.
    stop_pips = (3996.0 - 3989.5) / 0.1
    equity_for = lambda lots: lots * 100.0 * stop_pips * 10.0 + 1e-6
    fitted = self.fit(self.entry(), equity_for(0.02))
    self.assertEqual([leg.leg_id for leg in fitted.legs], ["L2", "L3"])
    self.assertEqual([str(leg.volume_ratio) for leg in fitted.legs], ["0.4", "0.6"])
    self.assertEqual(fitted.type, "limit_ladder")  # a market_with_limit_scale entry needs a market L1, which is gone
    self.assertTrue(all(leg.order_type == "limit" for leg in fitted.legs))
    self.assertEqual([str(leg.price) for leg in fitted.legs], ["3995.0", "3994.0"])  # prices untouched

  def test_a_volume_for_one_leg_keeps_l3_alone(self):
    stop_pips = (3996.0 - 3989.5) / 0.1
    fitted = self.fit(self.entry(), 0.01 * 100.0 * stop_pips * 10.0 + 1e-6)
    self.assertEqual([(leg.leg_id, str(leg.volume_ratio), str(leg.price)) for leg in fitted.legs], [("L3", "1", "3994.0")])
    self.assertEqual(fitted.type, "limit_ladder")

  def test_a_limit_ladder_keeps_its_type_when_it_drops_the_shallow_leg(self):
    stop_pips = (3996.0 - 3989.5) / 0.1
    fitted = self.fit(self.entry(kind="limit_ladder"), 0.02 * 100.0 * stop_pips * 10.0 + 1e-6)
    self.assertEqual((fitted.type, [leg.leg_id for leg in fitted.legs]), ("limit_ladder", ["L2", "L3"]))

  def test_two_legs_that_cannot_both_fill_become_the_deep_leg_alone(self):
    stop_pips = (3996.0 - 3989.5) / 0.1
    fitted = self.fit(self.entry(ratios=("0.4", "0.6")), 0.01 * 100.0 * stop_pips * 10.0 + 1e-6)
    self.assertEqual([(leg.leg_id, str(leg.volume_ratio)) for leg in fitted.legs], [("L2", "1")])

  def test_without_the_flag_the_old_rule_still_drops_the_deepest_leg(self):
    stop_pips = (3996.0 - 3989.5) / 0.1
    old = self.fit(self.entry(ratios=("0.6", "0.2", "0.2")), 0.02 * 100.0 * stop_pips * 10.0 + 1e-6, deep=False)
    self.assertEqual(([leg.leg_id for leg in old.legs], [str(leg.volume_ratio) for leg in old.legs]), (["L1", "L2"], ["0.6", "0.4"]))

  def test_a_real_plan_at_the_smallest_equity_keeps_the_deep_two(self):
    match, spot = kl_match()
    plan = plan_of(match, spot, account_equity=200.0)
    self.assertEqual((plan.entry.type, [leg.leg_id for leg in plan.entry.legs], [str(leg.volume_ratio) for leg in plan.entry.legs]), ("limit_ladder", ["L2", "L3"], ["0.4", "0.6"]))
    self.assertEqual(plan.sizing.leg_ratios, (Decimal("0.4"), Decimal("0.6")))
    self.assertEqual(plan.entry.min_leg_spacing, Decimal("1.0"))


class ProfileTests(unittest.TestCase):
  def test_the_profile_is_auto_vc_only_for_a_non_scalp_xau_ladder_with_the_auto_split(self):
    from app.autotrade.execution_policy import _ladder_ratio_profile as profile

    self.assertEqual(profile(symbol="XAU", strategy="Supply Demand", ratios=(0.2, 0.2, 0.6), settings=VC), "auto_vc")
    self.assertEqual(profile(symbol="XAU", strategy="Supply Demand", ratios=(0.4, 0.6), settings=VC), "auto_vc")
    self.assertEqual(profile(symbol="XAU", strategy="Supply Demand", ratios=(0.6, 0.2, 0.2), settings=VC), "default")
    self.assertEqual(profile(symbol="XAU", strategy="Range Scalp", ratios=(0.2, 0.2, 0.6), settings=VC), "default")
    self.assertEqual(profile(symbol="EURUSD", strategy="Key Level", ratios=(0.2, 0.2, 0.6), settings=VC), "default")
    self.assertEqual(profile(symbol="XAU", strategy="Supply Demand", ratios=(0.2, 0.2, 0.6), settings=DEFAULT), "default")

  def test_a_real_xau_policy_evaluation_measures_the_profile(self):
    from app.autotrade.execution_policy import evaluate_execution_policy

    match, spot = kl_match()
    shipped = evaluate_execution_policy(match, spot_price=spot, regime="trend", pip_size=0.1, cfg=runtime_config, executable_quote=spot)
    self.assertEqual(shipped.measured.get("planned_ladder_ratio_profile"), "default")
    opt_in_vc(self)
    result = evaluate_execution_policy(match, spot_price=spot, regime="trend", pip_size=0.1, cfg=runtime_config, executable_quote=spot)
    self.assertEqual(result.measured.get("planned_ladder_ratio_profile"), "auto_vc")


if __name__ == "__main__":
  unittest.main()
