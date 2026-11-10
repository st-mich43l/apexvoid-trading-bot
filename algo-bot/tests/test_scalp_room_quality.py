"""SQ-1: a Range Scalp opportunity with measured room reaches the Algo Bot intact.

Run from ``algo-bot``:  ``python -m unittest tests.test_scalp_room_quality``  (stdlib only; needs the app's runtime dependencies).
Both envelopes are real replayed XAU ``range_scalp`` v2 opportunities from the Go engine (analysis-engine replay, XAU capture of 2-9 Nov 2026): ``TWO_R`` has
200+ pips of room to the far edge of its range and carries a 2R ladder; ``ONE_R`` has 49.7 pips of room against a 30-pip stop, so only the 1R rung fits. The
target is Go's: Python derives the ladder from it and never from a room of its own.
"""
from __future__ import annotations

import json
import math
import os
import unittest
from decimal import Decimal

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.analysis_client.models import parse_analysis_event  # noqa: E402
from app.autotrade import go_opportunity_policy as go  # noqa: E402
from app.autotrade import units  # noqa: E402
from app.autotrade.execution_policy import _instrument_context  # noqa: E402
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match  # noqa: E402
from app.core.config import runtime_config  # noqa: E402

TWO_R = r'''{"event_id":"replay-opp_2fbdf4716079b577ace4d83a5818d5f369305083d0d7a9030308a4c086bf23f0","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793340300,"produced_at":1793340300,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_2fbdf4716079b577ace4d83a5818d5f369305083d0d7a9030308a4c086bf23f0","config_version":3,"config_fingerprint":"ac77552067815f99282d12194c8581b461b5d98d85bb8933377fff26c2733dd1","payload":{"id":"opp_2fbdf4716079b577ace4d83a5818d5f369305083d0d7a9030308a4c086bf23f0","strategy":"range_scalp","symbol":"XAU","timeframe":"M5","direction":"BUY","entry":{"low":3989.78,"high":3990.08},"invalidation":{"price":3986.73,"label":"range_edge_lost"},"targets":[{"price":{"price":3996.76,"label":"scalp_ladder"}}],"evidence":[{"code":"m5_range_sideways"},{"code":"m5_range_edge_probe"},{"code":"m5_range_close_inside"}],"quality":{"overall":0.9868651462731552,"components":{"available_room_pips":214.90000000000236,"barrier_room_pips":669.1928571428585,"efficiency_ratio":0.001044495508673095,"entry_range_position":0.11962310528472084,"objective_room_pips":214.90000000000236,"probe_depth_atr":0.4545584022787794,"quality_model_version":2,"range_high":4011.57,"range_low":3987.16,"range_midpoint":3999.365,"range_width_atr":5.776038833746057,"room_limited_by_barrier":0,"room_reward_risk":6.429111712849198,"score_edge_probe":1,"score_entry_location":0.9509422367881979,"score_range_integrity":0.996518348304423,"score_room_reward_risk":1,"target_pips":66.85215924013391,"target_r":2,"utc_hour":6}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793340300,"created_at":1793340300,"expires_at":1793341200,"technical_context":{"atr":4.4714285714285325,"reference_price":3989.93,"reference_time":1793340300,"higher_timeframes":[{"timeframe":"H1","direction":"SELL","layer":"internal","reference_time":1793336400},{"timeframe":"M15","direction":"SELL","layer":"internal","reference_time":1793339100}],"candle_evidence":{"version":2,"direction":"BUY","rejection":{"score":0.19164156397377205,"patterns":["sweep_reclaim"],"wick_fraction":0.2171428571429195,"body_fraction":0.4257142857142234,"close_location":0.6428571428571429,"sweep":true,"sweep_penetration_atr":0.46964856230040497,"reclaim":true,"reclaim_depth_atr":0.03354632587851318},"displacement":{"score":0.14746405750792746,"patterns":["body_close","strong_reclaim"],"body_atr":0.333226837060657,"range_atr":0.7827476038338727,"body_dominance":0.4257142857142234,"close_location":0.6428571428571429,"reclaim":true,"reclaim_depth_atr":0.03354632587851318,"engulfing":false},"indecision":{"doji":false,"spinning_top":false,"inside_bar":true,"body_fraction":0.4257142857142234,"compression_score":0.5107827476038296},"base_score":0.1651350600942653,"synergy_bonus":0.12,"final_score":0.28513506009426526,"primary_pattern":"sweep_reclaim","all_patterns":["sweep_reclaim","body_close","strong_reclaim"]},"mad":{"version":2,"phase":"accum","confidence":0.8439737798832267,"affinity":0,"reclaim":false,"range_quality_atr":8.78019169329086,"displacement_atr":2.7261980830671284,"acceptance_closes":0,"reason_code":"asia_building_accum"},"confluence":{"version":"v1","selected_stars":1,"v1_stars":1,"v2_stars":1,"v2_raw":0,"raw_factor_score":0,"zone_quality_score":0,"mad_bonus":0,"factors":{"htf_aligned":false,"touches":0,"wick_rejection":false,"displacement_grade":false,"session_context":false,"structural_agreement":false,"fib_touch":false,"choch":false}}},"stop_envelope":{"floor_pips":30,"cap_pips":40,"desired_minimum_pips":40,"source":"scalp_stop_envelope"}}}'''
ONE_R = r'''{"event_id":"replay-opp_2aeff98c4474f4362a9cfbaf6adf733b7b9f44a36a1588aa04775e296ad8f99e","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793663700,"produced_at":1793663700,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_2aeff98c4474f4362a9cfbaf6adf733b7b9f44a36a1588aa04775e296ad8f99e","config_version":3,"config_fingerprint":"ac77552067815f99282d12194c8581b461b5d98d85bb8933377fff26c2733dd1","payload":{"id":"opp_2aeff98c4474f4362a9cfbaf6adf733b7b9f44a36a1588aa04775e296ad8f99e","strategy":"range_scalp","symbol":"XAU","timeframe":"M5","direction":"SELL","entry":{"low":3947.93,"high":3948.23},"invalidation":{"price":3950.93,"label":"range_edge_lost"},"targets":[{"price":{"price":3944.93,"label":"scalp_ladder"}}],"evidence":[{"code":"m5_range_sideways"},{"code":"m5_range_edge_probe"},{"code":"m5_range_close_inside"}],"quality":{"overall":0.7343194987010638,"components":{"available_room_pips":49.699999999998,"barrier_room_pips":385.62249999999665,"efficiency_ratio":0.0584323040380089,"entry_range_position":0.17851239669427266,"objective_room_pips":49.699999999998,"probe_depth_atr":0.1518410393565734,"quality_model_version":2,"range_high":3949.01,"range_low":3942.96,"range_midpoint":3945.985,"range_width_atr":2.5877134876260555,"room_limited_by_barrier":0,"room_reward_risk":1.6566666666666,"score_edge_probe":1,"score_entry_location":0.8037190082643184,"score_range_integrity":0.805225653206637,"score_room_reward_risk":0.32833333333329995,"target_pips":30,"target_r":1,"utc_hour":23}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793663700,"created_at":1793663700,"expires_at":1793664600,"technical_context":{"atr":1.9850000000000299,"reference_price":3948.08,"reference_time":1793663700,"higher_timeframes":[{"timeframe":"H1","direction":"BUY","layer":"micro","reference_time":1793660400},{"timeframe":"M15","direction":"SELL","layer":"internal","reference_time":1793663100}],"candle_evidence":{"version":2,"direction":"SELL","rejection":{"score":0.47223341729671464,"patterns":["wick_rejection","sweep_reclaim"],"wick_fraction":0.48571428571446207,"body_fraction":0.37857142857122206,"close_location":0.5142857142855379,"sweep":true,"sweep_penetration_atr":0.26700251889178445,"reclaim":true,"reclaim_depth_atr":0.0755667506297676},"displacement":{"score":0.1668765743073438,"patterns":["strong_reclaim"],"body_atr":0.26700251889155535,"range_atr":0.7052896725441158,"body_dominance":0,"close_location":0.5142857142855379,"reclaim":true,"reclaim_depth_atr":0.0755667506297676,"engulfing":false},"indecision":{"doji":false,"spinning_top":false,"inside_bar":false,"body_fraction":0.37857142857122206,"compression_score":0.5591939546599276},"base_score":0.2890193115030921,"synergy_bonus":0.12,"final_score":0.4090193115030921,"primary_pattern":"sweep_reclaim","all_patterns":["wick_rejection","sweep_reclaim","strong_reclaim"]},"mad":{"version":2,"phase":"accum","confidence":0.5968904716407559,"affinity":0,"reclaim":false,"range_quality_atr":3.047858942065537,"displacement_atr":1.0579345088160592,"acceptance_closes":0,"reason_code":"asia_box_accum"},"confluence":{"version":"v1","selected_stars":1,"v1_stars":1,"v2_stars":1,"v2_raw":4,"raw_factor_score":4,"zone_quality_score":0,"mad_bonus":0,"factors":{"htf_aligned":true,"touches":0,"wick_rejection":false,"displacement_grade":false,"session_context":false,"structural_agreement":false,"fib_touch":false,"choch":false}}},"stop_envelope":{"floor_pips":30,"cap_pips":40,"desired_minimum_pips":30,"source":"scalp_stop_envelope"}}}'''

NEW_DIMENSIONS = ("score_range_integrity", "score_edge_probe", "score_entry_location", "score_room_reward_risk")


def event_of(raw: str):
  return parse_analysis_event("analysis.opportunity.v1", raw)


def match_of(raw: str):
  event = event_of(raw)
  return go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["range_scalp"], now=event.payload.created_at + 30)


def plan_of(raw: str):
  payload = event_of(raw).payload
  pip = units.pip_size("XAU")
  spot = payload.technical_context.reference_price
  return build_trade_plan_from_strategy_match(
    match_of(raw), plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=Decimal(str(pip)), spot_price=spot, regime="range",
    cfg=_instrument_context("XAU", runtime_config), executable_quote=spot, max_volume=100_000_000,
  )


class QualityOnTheWireTests(unittest.TestCase):
  def test_the_published_quality_is_finite_and_the_overall_is_the_mean_of_the_dimensions(self):
    for raw in (TWO_R, ONE_R):
      quality = event_of(raw).payload.quality
      scores = {name: value for name, value in quality.components.items() if name.startswith("score_")}
      for name in NEW_DIMENSIONS:
        self.assertIn(name, scores)
      for name, value in quality.components.items():
        self.assertTrue(math.isfinite(value), name)
      for name, value in scores.items():
        self.assertTrue(0.0 <= value <= 1.0, name)
      self.assertAlmostEqual(quality.overall, sum(scores.values()) / len(scores), places=9)

  def test_room_is_published_as_a_finite_measurement(self):
    for raw in (TWO_R, ONE_R):
      components = event_of(raw).payload.quality.components
      self.assertGreater(components["available_room_pips"], 0)
      self.assertLessEqual(components["target_pips"], components["available_room_pips"] + 1e-6)

  def test_the_room_score_follows_the_room_not_the_rung(self):
    two = event_of(TWO_R).payload.quality.components
    one = event_of(ONE_R).payload.quality.components
    self.assertEqual(two["target_r"], 2.0)
    self.assertEqual(one["target_r"], 1.0)
    self.assertGreater(two["score_room_reward_risk"], one["score_room_reward_risk"])


class AlgoBotCompatibilityTests(unittest.TestCase):
  def test_the_adapter_accepts_the_envelopes(self):
    for raw in (TWO_R, ONE_R):
      match = match_of(raw)
      self.assertEqual(match.strategy, "Range Scalp")

  def test_the_ladder_the_bot_plans_is_the_one_go_fitted_to_the_room(self):
    self.assertEqual(len(plan_of(TWO_R).targets), 2)  # 1R and 2R
    self.assertEqual(len(plan_of(ONE_R).targets), 1)  # only 1R fits the room


if __name__ == "__main__":
  unittest.main()
