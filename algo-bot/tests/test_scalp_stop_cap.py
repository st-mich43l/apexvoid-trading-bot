"""Scalp stop cap: 40 pips is a hard limit on the planned stop of the M1 scalp lane (impulse_pullback, range_scalp, impulse_retrace).

Run from ``algo-bot``:  ``python -m unittest tests.test_scalp_stop_cap``  (stdlib only; needs the app's runtime dependencies).
The envelopes below are real replayed opportunities from the Go engine (range_scalp BUY/SELL, key_level BUY on XAU); each test moves only
the invalidation, target and stop envelope so the structural distance is an exact number of pips.
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
from app.autotrade.execution_policy import _instrument_context, evaluate_execution_policy  # noqa: E402
from app.autotrade.protective_stop import (  # noqa: E402
  ProtectiveStopError,
  plan_go_invalidation_stop,
  scalp_stop_limits,
)
from app.autotrade.trade_plan_builder import (  # noqa: E402
  TradePlanBuildRejected,
  build_trade_plan_from_strategy_match,
  scalp_slippage_ceiling_ticks,
)

PIP = Decimal("0.1")
# The per-instrument configuration the live worker passes to the policy and the plan builder (it carries strategies.scalping.*).
from app.core.config import runtime_config  # noqa: E402

XAU = _instrument_context("XAU", runtime_config)
CAP = 40
SCALP_ENVELOPE = {"floor_pips": 30.0, "cap_pips": 40.0, "desired_minimum_pips": 30.0, "source": "scalp_stop_envelope"}
OLD_SCALP_ENVELOPE = {"floor_pips": 30.0, "cap_pips": 70.0, "desired_minimum_pips": 30.0, "source": "scalp_stop_envelope"}

TEMPLATES = json.loads('{"buy":{"event_id":"replay-opp_229e0c5d68011ed6ab8a37fea59934dfca1f89a7953460b79bb1e4218f7f979f","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793340300,"produced_at":1793340300,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_229e0c5d68011ed6ab8a37fea59934dfca1f89a7953460b79bb1e4218f7f979f","config_version":3,"config_fingerprint":"2ec7d7f7356eeb26790eb8fa4a63612d974c3d2b4a66d3cca59016fa7c39d9d2","payload":{"id":"opp_229e0c5d68011ed6ab8a37fea59934dfca1f89a7953460b79bb1e4218f7f979f","strategy":"range_scalp","symbol":"XAU","timeframe":"M5","direction":"BUY","entry":{"low":3989.78,"high":3990.08},"invalidation":{"price":3986.73,"label":"range_edge_lost"},"targets":[{"price":{"price":3996.76,"label":"scalp_ladder"}}],"evidence":[{"code":"m5_range_sideways"},{"code":"m5_range_edge_probe"},{"code":"m5_range_close_inside"}],"quality":{"overall":0.7255383752916011,"components":{"efficiency_ratio":0.001044495508673095,"quality_model_version":1,"range_high":4011.57,"range_low":3987.16,"range_midpoint":3999.365,"range_width_atr":5.776038833746057,"score_edge_probe":0.4545584022787794,"score_sideways":0.996518348304423,"utc_hour":6}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793340300,"created_at":1793340300,"expires_at":1793341200,"technical_context":{"atr":4.4714285714285325,"reference_price":3989.93,"reference_time":1793340300,"higher_timeframes":[{"timeframe":"H1","direction":"SELL","layer":"internal","reference_time":1793336400},{"timeframe":"M15","direction":"SELL","layer":"internal","reference_time":1793339100}],"candle_evidence":{"version":2,"direction":"BUY","rejection":{"score":0.19164156397377205,"patterns":["sweep_reclaim"],"wick_fraction":0.2171428571429195,"body_fraction":0.4257142857142234,"close_location":0.6428571428571429,"sweep":true,"sweep_penetration_atr":0.46964856230040497,"reclaim":true,"reclaim_depth_atr":0.03354632587851318},"displacement":{"score":0.14746405750792746,"patterns":["body_close","strong_reclaim"],"body_atr":0.333226837060657,"range_atr":0.7827476038338727,"body_dominance":0.4257142857142234,"close_location":0.6428571428571429,"reclaim":true,"reclaim_depth_atr":0.03354632587851318,"engulfing":false},"indecision":{"doji":false,"spinning_top":false,"inside_bar":true,"body_fraction":0.4257142857142234,"compression_score":0.5107827476038296},"base_score":0.1651350600942653,"synergy_bonus":0.12,"final_score":0.28513506009426526,"primary_pattern":"sweep_reclaim","all_patterns":["sweep_reclaim","body_close","strong_reclaim"]},"mad":{"version":2,"phase":"accum","confidence":0.8439737798832267,"affinity":0,"reclaim":false,"range_quality_atr":8.78019169329086,"displacement_atr":2.7261980830671284,"acceptance_closes":0,"reason_code":"asia_building_accum"},"confluence":{"version":"v1","selected_stars":1,"v1_stars":1,"v2_stars":1,"v2_raw":0,"raw_factor_score":0,"zone_quality_score":0,"mad_bonus":0,"factors":{"htf_aligned":false,"touches":0,"wick_rejection":false,"displacement_grade":false,"session_context":false,"structural_agreement":false,"fib_touch":false,"choch":false}}},"stop_envelope":{"floor_pips":30,"cap_pips":40,"desired_minimum_pips":40,"source":"scalp_stop_envelope"}}},"sell":{"event_id":"replay-opp_ab83d9fc54718f2c7c46f6c220320569ca933d0fa03002d9551fd2c38dea69ce","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793581200,"produced_at":1793581200,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_ab83d9fc54718f2c7c46f6c220320569ca933d0fa03002d9551fd2c38dea69ce","config_version":3,"config_fingerprint":"2ec7d7f7356eeb26790eb8fa4a63612d974c3d2b4a66d3cca59016fa7c39d9d2","payload":{"id":"opp_ab83d9fc54718f2c7c46f6c220320569ca933d0fa03002d9551fd2c38dea69ce","strategy":"range_scalp","symbol":"XAU","timeframe":"M5","direction":"SELL","entry":{"low":3901.08,"high":3901.38},"invalidation":{"price":3904.53,"label":"range_edge_lost"},"targets":[{"price":{"price":3894.19,"label":"scalp_ladder"}}],"evidence":[{"code":"m5_range_sideways"},{"code":"m5_range_edge_probe"},{"code":"m5_range_close_inside"}],"quality":{"overall":0.5414228284881337,"components":{"efficiency_ratio":0.15184678522571915,"quality_model_version":1,"range_high":3903.68,"range_low":3881.93,"range_midpoint":3892.805,"range_width_atr":4.95581623524024,"score_edge_probe":0.5890016077286646,"score_sideways":0.49384404924760283,"utc_hour":1}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793581200,"created_at":1793581200,"expires_at":1793582100,"technical_context":{"atr":4.587142857142843,"reference_price":3901.23,"reference_time":1793581200,"bias":{"direction":"SELL","layer":"intermediate"},"higher_timeframes":[{"timeframe":"H1","direction":"SELL","layer":"internal","reference_time":1793577600},{"timeframe":"M15","direction":"SELL","layer":"intermediate","reference_time":1793580300}],"candle_evidence":{"version":2,"direction":"SELL","rejection":{"score":0.3054219159708299,"patterns":["inverted_hammer","sweep_reclaim"],"wick_fraction":0.17535971223023492,"body_fraction":0.08183453237408508,"close_location":0.74280575539568,"sweep":true,"sweep_penetration_atr":0.5907816879476896,"reclaim":true,"reclaim_depth_atr":0.032700093428858296},"displacement":{"score":0.27452507007164373,"patterns":["body_close","strong_reclaim"],"body_atr":0.19838056680158833,"range_atr":2.4241669261913006,"body_dominance":0.08183453237408508,"close_location":0.74280575539568,"reclaim":true,"reclaim_depth_atr":0.032700093428858296,"engulfing":false},"indecision":{"doji":true,"spinning_top":false,"inside_bar":false,"body_fraction":0.08183453237408508,"compression_score":0},"base_score":0.28688380843131817,"synergy_bonus":0.12,"final_score":0.40688380843131816,"primary_pattern":"sweep_reclaim","all_patterns":["inverted_hammer","sweep_reclaim","body_close","strong_reclaim"]},"mad":{"version":2,"phase":"unclear","confidence":0,"affinity":0,"reclaim":false,"range_quality_atr":4.8308938025538035,"displacement_atr":1.227343506695761,"acceptance_closes":0,"reason_code":"no_mad_signature"},"confluence":{"version":"v1","selected_stars":1,"v1_stars":1,"v2_stars":1,"v2_raw":6.5,"raw_factor_score":6.5,"zone_quality_score":0,"mad_bonus":0,"factors":{"htf_aligned":true,"touches":0,"wick_rejection":false,"displacement_grade":false,"session_context":false,"structural_agreement":false,"fib_touch":true,"choch":false},"fib_level":{"ratio":0.236,"price":3901.5678,"kind":"retracement","distance_atr":0.07364061040169807}}},"stop_envelope":{"floor_pips":30,"cap_pips":40,"desired_minimum_pips":40,"source":"scalp_stop_envelope"}}},"kl":{"event_id":"replay-opp_e5a93114224d049be2276c1fd090292040e658119cf7a969ddb8497be0554c16","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793335800,"produced_at":1793335800,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_e5a93114224d049be2276c1fd090292040e658119cf7a969ddb8497be0554c16","config_version":3,"config_fingerprint":"2ec7d7f7356eeb26790eb8fa4a63612d974c3d2b4a66d3cca59016fa7c39d9d2","payload":{"id":"opp_e5a93114224d049be2276c1fd090292040e658119cf7a969ddb8497be0554c16","strategy":"key_level","symbol":"XAU","timeframe":"M5","direction":"BUY","entry":{"low":3993.95,"high":3997.38},"invalidation":{"price":3991.69,"label":"key_level_invalidated"},"targets":[{"price":{"price":4032.57,"label":"nearest_opposing_liquidity"}}],"evidence":[{"code":"m5_key_level_round"},{"code":"m5_key_level_touches_sufficient"},{"code":"m5_key_level_role_ambiguous"},{"code":"m5_key_level_rejection_confirmed"},{"code":"invalidation_beyond_reaction_extreme"},{"code":"key_level_reaction_swept"},{"code":"stop_budget_structural"}],"quality":{"overall":0.8227536848072559,"components":{"closes_through_level":7,"closes_through_share":0.15555555555555556,"deep_entry_pips":0,"entry_chase_atr":0.38781781962441886,"htf_level_touches":3,"level_age_bars":4,"level_band_atr":0.4805717379193826,"level_coverage":0.37539612118140747,"level_episodes":8,"level_kind":1,"level_origin":1,"level_round_class":0,"quality_model_version":2,"room_r_effective":6.192204499904362,"room_reward_risk":6.192204499904362,"score_entry_location":1,"score_htf_level":1,"score_level_integrity":0.6984126984126984,"score_level_significance":1,"score_rejection":0.36086309523809174,"score_room":1,"score_structure":0.7}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793335800,"created_at":1793335800,"expires_at":1793422200,"technical_context":{"atr":3,"reference_price":3996.05,"reference_time":1793335800,"bias":{"direction":"SELL","layer":"intermediate"},"higher_timeframes":[{"timeframe":"H1","direction":"SELL","layer":"internal","reference_time":1793329200},{"timeframe":"M15","direction":"SELL","layer":"internal","reference_time":1793334600}],"confirmation":{"zone_id":"keylevel:round:3995.67","touch_bar_time":1793335800,"confirmation_bar_time":1793335800,"reaction_type":"rejection","pattern":"strong_reclaim"},"candle_evidence":{"version":2,"direction":"BUY","rejection":{"score":0.4167517006802023,"patterns":["sweep_reclaim"],"wick_fraction":0.1190476190476416,"body_fraction":0.6696428571428318,"close_location":0.7886904761904734,"sweep":true,"sweep_penetration_atr":0.1833333333332424,"reclaim":true,"reclaim_depth_atr":0.7000000000001213},"displacement":{"score":0.5037698412698207,"patterns":["strong_close","body_close","strong_reclaim","displacement_candle"],"body_atr":0.75,"range_atr":1.1200000000000425,"body_dominance":0.6696428571428318,"close_location":0.7886904761904734,"reclaim":true,"reclaim_depth_atr":0.7000000000001213,"engulfing":false},"indecision":{"doji":false,"spinning_top":false,"inside_bar":false,"body_fraction":0.6696428571428318,"compression_score":0.2999999999999735},"base_score":0.46896258503397337,"synergy_bonus":0.12,"final_score":0.5889625850339734,"primary_pattern":"sweep_reclaim","all_patterns":["sweep_reclaim","strong_close","body_close","strong_reclaim","displacement_candle"]},"mad":{"version":2,"phase":"unclear","confidence":0,"affinity":0,"reclaim":false,"range_quality_atr":12.976666666666764,"displacement_atr":0.7200000000001031,"acceptance_closes":0,"reason_code":"no_mad_signature"},"confluence":{"version":"v1","selected_stars":2,"v1_stars":2,"v2_stars":1,"v2_raw":8.5,"raw_factor_score":8.5,"zone_quality_score":0,"mad_bonus":0,"factors":{"htf_aligned":false,"touches":3,"wick_rejection":false,"displacement_grade":false,"session_context":false,"structural_agreement":true,"fib_touch":true,"choch":false}}},"stop_envelope":{"floor_pips":50,"cap_pips":70,"desired_minimum_pips":70,"source":"reaction_room"}}}}')


def pips(a, b) -> Decimal:
  return abs(Decimal(str(a)) - Decimal(str(b))) / PIP


def scalp_match(direction: str, structural_pips: float, *, envelope="scalp"):
  """A Go range_scalp opportunity whose structural stop, from the worst zone edge, is exactly ``structural_pips``."""
  raw = copy.deepcopy(TEMPLATES["buy" if direction == "BUY" else "sell"])
  payload = raw["payload"]
  entry = payload["entry"]
  worst = entry["high"] if direction == "BUY" else entry["low"]
  sign = -1 if direction == "BUY" else 1
  payload["invalidation"]["price"] = round(worst + sign * structural_pips * 0.1, 2)
  payload["targets"] = [{"price": {"price": round(worst - sign * 2 * structural_pips * 0.1, 2), "label": "scalp_ladder"}}]
  if envelope == "scalp":
    payload["stop_envelope"] = dict(SCALP_ENVELOPE)
  elif envelope == "old":
    payload["stop_envelope"] = dict(OLD_SCALP_ENVELOPE)
  else:
    payload.pop("stop_envelope", None)
  event = parse_analysis_event("analysis.opportunity.v1", json.dumps(raw))
  match = go.build_strategy_match(event, profile=go.REVIEWED_SCOPES[payload["strategy"]], now=payload["created_at"] + 30)
  return match, payload["technical_context"]["reference_price"], worst


def evaluate(match, spot, quote=None):
  return evaluate_execution_policy(
    match, spot_price=spot, regime="range", pip_size=0.1, cfg=XAU, executable_quote=spot if quote is None else quote,
  )


def build(match, spot, quote=None):
  return build_trade_plan_from_strategy_match(
    match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=PIP, spot_price=spot, regime="range",
    cfg=XAU, executable_quote=spot if quote is None else quote, max_volume=100_000_000,
  )


class ConfigurationTests(unittest.TestCase):
  def test_the_configured_scalp_book_is_30_to_40(self):
    self.assertEqual(scalp_stop_limits(XAU), (30, 40))  # read from auto_algo.strategies.scalping.stop
    self.assertEqual(XAU.strategies.scalping.stop.maximum_pips, 40)
    self.assertEqual(scalp_stop_limits(None), (30, 40))  # and the built-in fallback is 40, not the old 70


class StopPlanTests(unittest.TestCase):
  """plan_go_invalidation_stop with the scalp book: the cap is checked on every planned leg and on the rounded absolute SL."""

  def plan(self, direction, legs, invalidation, *, rounded=True, digits=2):
    legs = [Decimal(str(leg)) for leg in legs]
    return plan_go_invalidation_stop(
      direction=direction, entry_zone_low=min(legs), entry_zone_high=max(legs) + Decimal("0.01"),
      planned_leg_prices=legs, resolved_leg_volumes=[1.0 / len(legs)] * len(legs), invalidation_price=Decimal(str(invalidation)),
      minimum_stop_pips=30, maximum_stop_pips=CAP, pip_size=PIP, digits=digits, widen_to_minimum=True, enforce_rounded_maximum=rounded,
    )

  def test_structural_distances_buy_and_sell(self):
    for direction, sign in (("BUY", -1), ("SELL", 1)):
      for structural, expected in ((24, 30), (35, 35), (40, 40)):
        with self.subTest(direction=direction, structural=structural):
          plan = self.plan(direction, [4000.0], round(4000.0 + sign * structural * 0.1, 2))
          self.assertEqual(plan.final_stop_pips, Decimal(expected))
          self.assertLessEqual(plan.final_stop_pips, CAP)
          # Never closer than Go's invalidation: only a stop narrower than the 30 minimum moves, and it moves outward.
          self.assertEqual(plan.final_stop_price, Decimal(str(round(4000.0 + sign * expected * 0.1, 2))))
      with self.subTest(direction=direction, structural=48):
        with self.assertRaises(ProtectiveStopError) as ctx:
          self.plan(direction, [4000.0], round(4000.0 + sign * 48 * 0.1, 2))
        self.assertEqual(str(ctx.exception), "stop_exceeds_go_invalidation_envelope")
        self.assertEqual(ctx.exception.measured["stop_reject_detail"], "go_invalidation_over_max")
        self.assertEqual(Decimal(ctx.exception.measured["furthest_leg_stop_pips"]), Decimal(48))

  def test_every_planned_leg_is_measured_not_only_the_zone_edge(self):
    # Legs across a zone: the leg furthest from the stop sets the distance (BUY: the highest price).
    legs = [4001.0, 4000.5, 4000.0]
    ok = self.plan("BUY", legs, 3997.1)
    self.assertEqual(ok.final_stop_pips, Decimal("39.0"))
    with self.assertRaises(ProtectiveStopError) as ctx:
      self.plan("BUY", legs, 3996.9)
    self.assertEqual(Decimal(ctx.exception.measured["furthest_leg_stop_pips"]), Decimal("41.0"))
    ok = self.plan("SELL", [3999.0, 3999.5, 4000.0], 4002.9)
    self.assertEqual(ok.final_stop_pips, Decimal("39.0"))
    with self.assertRaises(ProtectiveStopError):
      self.plan("SELL", [3999.0, 3999.5, 4000.0], 4003.1)

  def test_price_rounding_that_crosses_the_cap_is_refused_for_scalps_only(self):
    # A leg off the tick grid: the unrounded stop is exactly 40.0 pips away, the stop that is sent (2 digits) is 40.04 pips away.
    for direction, leg, invalidation in (("BUY", 4000.004, 3996.004), ("SELL", 3999.996, 4003.996)):
      with self.subTest(direction=direction):
        with self.assertRaises(ProtectiveStopError) as ctx:
          self.plan(direction, [leg], invalidation, rounded=True)
        self.assertEqual(ctx.exception.measured["stop_reject_detail"], "go_invalidation_over_max_after_rounding")
        self.assertGreater(Decimal(ctx.exception.measured["furthest_leg_stop_pips"]), CAP)
        # Every other Go strategy keeps its behaviour: the new check is off unless the caller is a scalp.
        other = self.plan(direction, [leg], invalidation, rounded=False)
        self.assertGreater(other.final_stop_pips, CAP)

  def test_the_instrument_stop_floor_never_widens_a_scalp(self):
    # XAU's normal strategies have a 50-70 pip envelope; a 24-pip scalp structure is widened to the scalp minimum (30), not to 50.
    plan = self.plan("BUY", [4000.0], 3997.6)
    self.assertEqual(plan.final_stop_pips, Decimal(30))


class SlippageBudgetTests(unittest.TestCase):
  def test_the_fill_budget_is_the_headroom_under_the_cap(self):
    def ticks(direction, stop, legs):
      return scalp_slippage_ceiling_ticks(
        direction=direction, stop_price=Decimal(str(stop)), leg_prices=legs, pip_size=PIP, price_digits=2, cap_pips=CAP,
      )

    self.assertEqual(ticks("BUY", 3997.0, [4000.0]), 100)  # 30 pips planned: 10 pips (100 ticks) of fill budget
    self.assertEqual(ticks("BUY", 3996.5, [4000.0]), 50)  # 35 pips
    self.assertEqual(ticks("BUY", 3996.0, [4000.0]), 0)  # 40 pips: the leg may fill at its price or better, no worse
    self.assertEqual(ticks("SELL", 4003.5, [4000.0]), 50)
    self.assertEqual(ticks("BUY", 3997.0, [4000.0, 3999.0]), 100)  # the leg furthest from the stop decides
    self.assertEqual(ticks("BUY", 3995.0, [4000.0]), 0)  # never negative


class PlanPipelineTests(unittest.TestCase):
  """Adapter -> execution policy -> TradePlan for a Go scalp, with the stop envelope Go attaches (30-40)."""

  def test_fitting_setups_plan_within_the_cap_and_the_fill_budget(self):
    for direction in ("BUY", "SELL"):
      for structural, planned in ((24, 30), (35, 33.5), (40, 38.5)):
        with self.subTest(direction=direction, structural=structural):
          match, spot, _worst = scalp_match(direction, structural)
          policy = evaluate(match, spot)
          self.assertTrue(policy.allowed, policy.reason_code)
          self.assertIsNone(policy.measured.get("planned_stop_error"))
          plan = build(match, spot)
          entry = plan.entry.order_price or plan.entry.legs[0].price
          distance = pips(entry, plan.stop.price)
          self.assertEqual(distance, Decimal(str(planned)))
          self.assertGreaterEqual(distance, 30)
          self.assertLessEqual(distance, CAP)
          self.assertEqual(plan.sizing.mode, "equity_table")  # sizing is untouched: the cap lowers the largest stop, never the volume
          self.assertEqual(plan.risk.risk_percent, Decimal("0.5"))
          # A market leg filling worse than planned by its whole fill budget still sits within the cap.
          self.assertLessEqual(distance + Decimal(plan.entry.max_slippage_ticks) / 10, CAP)

  def test_wider_structures_are_refused_with_a_measured_distance_never_compressed(self):
    for direction in ("BUY", "SELL"):
      match, spot, _worst = scalp_match(direction, 48)
      policy = evaluate(match, spot)
      self.assertFalse(policy.allowed)
      self.assertEqual(policy.reason_code, "stop_exceeds_go_invalidation_envelope")
      self.assertGreater(policy.measured["furthest_leg_stop_pips"], "40")
      with self.assertRaises(TradePlanBuildRejected) as ctx:
        build(match, spot)
      self.assertEqual(ctx.exception.reason_code, "stop_exceeds_go_invalidation_envelope")

  def test_a_quote_that_moves_away_from_the_stop_revalidates_the_cap(self):
    match, spot, _worst = scalp_match("BUY", 35)
    with self.assertRaises(TradePlanBuildRejected) as ctx:
      build(match, spot, quote=spot + 3.0)  # chased 30 pips past the planned entry: 63.5 pips from the stop
    self.assertEqual(ctx.exception.reason_code, "stop_exceeds_go_invalidation_envelope")
    self.assertGreater(Decimal(ctx.exception.measured["furthest_leg_stop_pips"]), CAP)
    match, spot, _worst = scalp_match("SELL", 35)
    with self.assertRaises(TradePlanBuildRejected):
      build(match, spot, quote=spot - 3.0)

  def test_a_quote_that_moves_toward_the_stop_never_tightens_it(self):
    match, spot, _worst = scalp_match("BUY", 35)
    plan = build(match, spot, quote=spot - 2.0)
    entry = plan.entry.order_price or plan.entry.legs[0].price
    self.assertGreaterEqual(pips(entry, plan.stop.price), 30)
    self.assertLessEqual(pips(entry, plan.stop.price), CAP)
    self.assertLessEqual(plan.stop.price, Decimal(str(match.go_invalidation_price)))  # outward only

  def test_an_envelope_published_under_the_old_70_cap_cannot_lift_the_cap(self):
    for envelope in ("old", "none"):
      with self.subTest(envelope=envelope):
        match, spot, _worst = scalp_match("BUY", 45, envelope=envelope)
        policy = evaluate(match, spot)
        self.assertFalse(policy.allowed)
        self.assertEqual(policy.reason_code, "stop_exceeds_go_invalidation_envelope")
    match, spot, _worst = scalp_match("BUY", 45, envelope="old")
    self.assertEqual(evaluate(match, spot).measured["stop_bounds_scalp_hard_capped_pips"], 40)
    match, spot, _worst = scalp_match("SELL", 35, envelope="none")
    self.assertTrue(evaluate(match, spot).allowed)  # without an envelope the fallback book is the same 30-40


class NormalStrategiesAreUntouchedTests(unittest.TestCase):
  def test_xau_key_level_keeps_its_own_50_to_70_envelope(self):
    raw = copy.deepcopy(TEMPLATES["kl"])
    payload = raw["payload"]
    worst = payload["entry"]["high"]
    payload["invalidation"]["price"] = round(worst - 6.5, 2)  # 65 pips: far above the scalp cap
    payload["targets"] = [{"price": {"price": round(worst + 19.5, 2), "label": "nearest_opposing_liquidity"}}]
    event = parse_analysis_event("analysis.opportunity.v1", json.dumps(raw))
    match = go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["key_level"], now=payload["created_at"] + 30)
    spot = payload["technical_context"]["reference_price"]
    policy = evaluate_execution_policy(match, spot_price=spot, regime="trend", pip_size=0.1, cfg=XAU, executable_quote=spot)
    self.assertTrue(policy.allowed, policy.reason_code)
    self.assertIsNone(policy.measured.get("planned_stop_error"))
    self.assertEqual((policy.measured["go_stop_envelope_floor_pips"], policy.measured["go_stop_envelope_cap_pips"]), (50.0, 70.0))
    self.assertNotIn("stop_bounds_scalp_hard_capped_pips", policy.measured)


if __name__ == "__main__":
  unittest.main()
