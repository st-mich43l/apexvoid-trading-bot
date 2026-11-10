"""Trendline on M5 and M15 reaches Algo Bot: both timeframes are accepted by the adapter and become TradePlans; nothing else changed.

Run from ``algo-bot``:  ``python -m unittest tests.test_trendline_timeframes``  (stdlib only; needs the app's runtime dependencies).
The two envelopes are real opportunities replayed by the Go engine from production closed bars (XAU M5 BUY, EURUSD M15 SELL; second-anchor reactions).
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
from app.autotrade.execution_policy import _instrument_context  # noqa: E402
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match  # noqa: E402
from app.core.config import runtime_config  # noqa: E402

ENVELOPES = json.loads('{"m5":{"event_id":"replay-opp_1a1fe2f23ad54adce48f574620312c09d66972aef16f1bc985a98e1dec9d5bc1","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793623800,"produced_at":1793623800,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_1a1fe2f23ad54adce48f574620312c09d66972aef16f1bc985a98e1dec9d5bc1","config_version":3,"config_fingerprint":"00021f4e4c853972c39d2d9652228b5a605dcc7fcd7858e0cb7a2aaccd40c2ed","payload":{"id":"opp_1a1fe2f23ad54adce48f574620312c09d66972aef16f1bc985a98e1dec9d5bc1","strategy":"trendline","symbol":"XAU","timeframe":"M5","structure_timeframe":"M5","direction":"BUY","entry":{"low":3899.99,"high":3901.28},"invalidation":{"price":3899.19,"label":"trendline_close_violation"},"targets":[{"price":{"price":3905.43,"label":"nearest_opposing_liquidity"}}],"evidence":[{"code":"m5_trendline_causal_anchors"},{"code":"m5_trendline_reaction_confirmed"},{"code":"stop_budget_floor"}],"quality":{"overall":0.5742968163764846,"components":{"approach_distance_atr":0.2728590648256133,"confirmation_age_bars":0,"entry_chase_atr":0.41354085697126014,"line_slope_atr_per_bar":0.0660655910576831,"line_span_bars":21,"line_violations_weighted":0,"quality_model_version":1,"reaction_at_second_anchor":0,"room_reward_risk":2.0065433946413602,"score_approach":0,"score_entry_location":0.8691673144229919,"score_line_integrity":1,"score_line_significance":0.48329915563188125,"score_line_validation":0.5,"score_rejection":0.23863636363632293,"score_room":0.5032716973206801,"score_structure":1,"timeframe_minutes":5,"validation_touches":1}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793623800,"created_at":1793623800,"expires_at":1793667000,"technical_context":{"atr":3.217142857142822,"reference_price":3902.59,"reference_time":1793623800,"higher_timeframes":[{"timeframe":"M15","direction":"SELL","layer":"intermediate","reference_time":1793622600}],"confirmation":{"zone_id":"trendline:M5:support:1793616300:1793621100","touch_bar_time":1793623800,"confirmation_bar_time":1793623800,"reaction_type":"rejection","pattern":"engulfing"},"candle_evidence":{"version":2,"direction":"BUY","displacement":{"score":0.3729212595340422,"patterns":["body_close","engulfing","strong_reclaim"],"body_atr":0.41030195381888307,"range_atr":1.025754884547137,"body_dominance":0.40000000000002756,"close_location":0.645454545454543,"reclaim":true,"reclaim_depth_atr":0.8081705150978129,"engulfing":true,"engulfing_quality":0.6264705882355202},"indecision":{"doji":false,"spinning_top":false,"inside_bar":false,"body_fraction":0.40000000000002756,"compression_score":0.3589031971580394},"base_score":0.3729212595340422,"synergy_bonus":0,"final_score":0.3729212595340422,"primary_pattern":"strong_reclaim","all_patterns":["body_close","engulfing","strong_reclaim"]},"mad":{"version":2,"phase":"unclear","confidence":0,"affinity":0,"reclaim":false,"range_quality_atr":11.584813499112022,"displacement_atr":0.27353463587911403,"acceptance_closes":0,"reason_code":"no_mad_signature"},"confluence":{"version":"v1","selected_stars":3,"v1_stars":3,"v2_stars":2,"v2_raw":12.5,"raw_factor_score":12.5,"zone_quality_score":0,"mad_bonus":0,"factors":{"htf_aligned":true,"touches":3,"wick_rejection":false,"displacement_grade":true,"session_context":false,"structural_agreement":false,"fib_touch":true,"choch":false}}},"stop_envelope":{"floor_pips":50,"cap_pips":70,"desired_minimum_pips":50,"source":"reaction_room"}}},"m15":{"event_id":"replay-opp_96bf275659797858ac0d0f657642e3141f6b0b4bd2ac1e8c8965977bd1b4415c","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793666700,"produced_at":1793666700,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_96bf275659797858ac0d0f657642e3141f6b0b4bd2ac1e8c8965977bd1b4415c","config_version":3,"config_fingerprint":"00021f4e4c853972c39d2d9652228b5a605dcc7fcd7858e0cb7a2aaccd40c2ed","payload":{"id":"opp_96bf275659797858ac0d0f657642e3141f6b0b4bd2ac1e8c8965977bd1b4415c","strategy":"trendline","symbol":"EURUSD","timeframe":"M15","structure_timeframe":"M15","direction":"SELL","entry":{"low":1.11961,"high":1.11982},"invalidation":{"price":1.11994,"label":"trendline_close_violation"},"targets":[{"price":{"price":1.11797,"label":"nearest_opposing_liquidity"}}],"evidence":[{"code":"m15_trendline_causal_anchors"},{"code":"m15_trendline_reaction_confirmed"},{"code":"stop_budget_floor"}],"quality":{"overall":0.5796688710245799,"components":{"approach_distance_atr":0.5073211618361884,"confirmation_age_bars":2,"entry_chase_atr":1.5976177584148088,"line_slope_atr_per_bar":0.04398148148148014,"line_span_bars":21,"line_violations_weighted":0,"quality_model_version":1,"reaction_at_second_anchor":1,"room_reward_risk":5.2211834933818855,"score_approach":0.1727676348634903,"score_entry_location":0,"score_line_integrity":1,"score_line_significance":0.50625,"score_line_validation":0,"score_rejection":0.9583333333331483,"score_room":1,"score_structure":1,"timeframe_minutes":15,"validation_touches":0}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793664900,"created_at":1793666700,"expires_at":1793709900,"technical_context":{"atr":0.00040571428571429494,"reference_price":1.11884,"reference_time":1793666700,"bias":{"direction":"SELL","layer":"internal"},"higher_timeframes":[{"timeframe":"H1","direction":"SELL","layer":"internal","reference_time":1793664000}],"confirmation":{"zone_id":"trendline:M15:resistance:1793646000:1793664900","touch_bar_time":1793664900,"confirmation_bar_time":1793664900,"reaction_type":"rejection","pattern":"wick_rejection"},"candle_evidence":{"version":2,"direction":"SELL","displacement":{"score":0.3387103873238929,"patterns":["strong_reclaim"],"body_atr":0.6654929577464451,"range_atr":1.2077464788729149,"body_dominance":0,"close_location":0.5510204081633855,"reclaim":true,"reclaim_depth_atr":2.415492957746377,"engulfing":false},"indecision":{"doji":false,"spinning_top":false,"inside_bar":true,"body_fraction":0.5510204081633855,"compression_score":0.2451584507044282},"base_score":0.3387103873238929,"synergy_bonus":0,"final_score":0.3387103873238929,"primary_pattern":"strong_reclaim","all_patterns":["strong_reclaim"]},"mad":{"version":2,"phase":"unclear","confidence":0,"affinity":0,"reclaim":false,"range_quality_atr":6.2493765586035845,"displacement_atr":2.2344139650872763,"acceptance_closes":0,"reason_code":"no_mad_signature"},"confluence":{"version":"v1","selected_stars":2,"v1_stars":2,"v2_stars":1,"v2_raw":9,"raw_factor_score":9,"zone_quality_score":0,"mad_bonus":0,"factors":{"htf_aligned":true,"touches":2,"wick_rejection":true,"displacement_grade":false,"session_context":false,"structural_agreement":false,"fib_touch":false,"choch":false}}},"stop_envelope":{"floor_pips":12,"cap_pips":20,"desired_minimum_pips":16,"source":"reaction_room"}}}}')
PIP = {"XAU": Decimal("0.1"), "EURUSD": Decimal("0.0001")}


def parse(raw):
  return parse_analysis_event("analysis.opportunity.v1", json.dumps(raw))


def match_for(raw):
  event = parse(raw)
  return go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["trendline"], now=raw["payload"]["created_at"] + 30)


class TrendlineTimeframeTests(unittest.TestCase):
  def test_both_timeframes_are_in_the_reviewed_scope(self):
    self.assertEqual(go.REVIEWED_SCOPES["trendline"].allowed_timeframes, frozenset({"M5", "M15"}))
    self.assertEqual(go.REVIEWED_SCOPES["break_retest"].allowed_timeframes, frozenset({"M5"}))  # no other strategy was opened
    self.assertEqual(go.REVIEWED_SCOPES["key_level"].allowed_timeframes, frozenset({"M5"}))

  def test_m5_and_m15_opportunities_are_adapted_with_their_own_identity(self):
    matches = {key: match_for(raw) for key, raw in ENVELOPES.items()}
    for key, raw in ENVELOPES.items():
      payload = raw["payload"]
      self.assertEqual(payload["timeframe"], key.upper())
      self.assertEqual(payload["structure_timeframe"], key.upper())
      self.assertTrue(any(e["code"].startswith(key + "_trendline_") for e in payload["evidence"]))
      self.assertTrue(payload["technical_context"]["confirmation"]["zone_id"].startswith("trendline:" + key.upper() + ":"))
      self.assertIsNotNone(matches[key])
    self.assertNotEqual(matches["m5"].structural_zone_id, matches["m15"].structural_zone_id)

  def test_every_adapted_opportunity_becomes_a_trade_plan(self):
    for key, raw in ENVELOPES.items():
      with self.subTest(timeframe=key):
        symbol = raw["payload"]["symbol"]
        match = match_for(raw)
        spot = raw["payload"]["technical_context"]["reference_price"]
        plan = build_trade_plan_from_strategy_match(
          match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=PIP[symbol], spot_price=spot, regime=None,
          executable_quote=spot, cfg=_instrument_context(symbol, runtime_config), max_volume=100_000_000,
        )
        self.assertEqual(plan.analysis.direction, raw["payload"]["direction"])
        self.assertGreater(len(plan.targets), 0)
        self.assertEqual(plan.expires_at - plan.created_at, 43200)  # 12 hours, in elapsed time on both frames

  def test_an_unreviewed_timeframe_is_still_refused(self):
    raw = copy.deepcopy(ENVELOPES["m15"])
    raw["payload"]["timeframe"] = "M30"
    raw["payload"]["structure_timeframe"] = "M30"
    with self.assertRaises(go.AdapterRejection) as ctx:
      match_for(raw)
    self.assertTrue(str(ctx.exception).startswith("confirmation_timeframe_unreviewed"))

  def test_the_evidence_contract_is_still_enforced(self):
    raw = copy.deepcopy(ENVELOPES["m15"])
    raw["payload"]["evidence"] = [{"code": "m15_something_else"}]
    with self.assertRaises(go.AdapterRejection) as ctx:
      match_for(raw)
    self.assertTrue(str(ctx.exception).startswith("strategy_evidence_mismatch"))


if __name__ == "__main__":
  unittest.main()
