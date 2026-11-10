"""AE-CF1: ``analysis.require_candle_confirmation`` at the Algo Bot boundary.

Run from ``algo-bot``:  ``python -m unittest tests.test_candle_confirmation_optional``  (stdlib only; needs the app's runtime dependencies).
Both envelopes are real replayed EURUSD ``demand`` opportunities from the Go engine on one zone: ``TOUCH`` stands on its structure and a touch of
its entry band (Go published it with ``require_candle_confirmation: false``; it carries no reaction and says so with the
``candle_confirmation_optional`` evidence code) and ``CONFIRMED`` is the same zone's later opportunity with a confirming engulfing candle.
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from decimal import Decimal
from unittest import mock

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.analysis_client.models import parse_analysis_event  # noqa: E402
from app.autotrade import go_opportunity_policy as go  # noqa: E402
from app.autotrade import units  # noqa: E402
from app.autotrade.execution_policy import _instrument_context, evaluate_execution_policy  # noqa: E402
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match  # noqa: E402
from app.core.config import runtime_config  # noqa: E402

TOUCH = json.loads(r'''{"event_id":"replay-opp_776ef7a7e1e3c6b87163d57ba2c75e14ce4aa067a5c6919dff1c5d97e95bd8c1","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793333700,"produced_at":1793333700,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_776ef7a7e1e3c6b87163d57ba2c75e14ce4aa067a5c6919dff1c5d97e95bd8c1","config_version":3,"config_fingerprint":"f76fa2b7c3c439f5756dbd03f9d8c4302eb571a9f47030f2d9cc9c021d129312","payload":{"id":"opp_776ef7a7e1e3c6b87163d57ba2c75e14ce4aa067a5c6919dff1c5d97e95bd8c1","strategy":"demand","symbol":"EURUSD","timeframe":"M5","structure_timeframe":"M15","structure":{"timeframe":"M15","low":1.11817,"high":1.119,"formed_at":1793317500},"direction":"BUY","entry":{"low":1.11817,"high":1.119},"invalidation":{"price":1.11803,"label":"demand_zone_invalidated"},"targets":[{"price":{"price":1.12029,"label":"nearest_opposing_structure"}}],"evidence":[{"code":"m5_demand_zone_confirmed"},{"code":"candle_confirmation_optional"},{"code":"htf_zone_m15"},{"code":"m5_demand_zone_discount"},{"code":"stop_budget_floor"}],"quality":{"overall":0.4807244826637222,"components":{"deep_entry_pips":0,"entry_chase_atr":0.11236384483077642,"pd_position":0.1400000000000237,"quality_model_version":1,"room_r":1.338875797611921,"room_reward_risk":1.338875797611921,"score_entry_location":1,"score_rejection":2.7755575615628914e-13,"score_room":0.1694378988059605,"score_structure":1,"score_zone_departure":0,"score_zone_freshness":1,"score_zone_precision":0.19563347983981716,"zone_touches":1,"zone_width_atr":3.1087330403203657}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793333700,"created_at":1793333700,"expires_at":1793420100,"technical_context":{"atr":0.0002542857142857307,"reference_price":1.11903,"reference_time":1793333700,"higher_timeframes":[{"timeframe":"H1","direction":"SELL","layer":"intermediate","reference_time":1793329200}],"candle_evidence":{"version":2,"direction":"BUY","displacement":{"score":0.2728932584269448,"patterns":["strong_reclaim"],"body_atr":0.5898876404497718,"range_atr":0.7865168539324469,"body_dominance":0,"close_location":0.20000000000022206,"reclaim":true,"reclaim_depth_atr":3.382022471909347,"engulfing":false},"indecision":{"doji":false,"spinning_top":false,"inside_bar":false,"body_fraction":0.7500000000005551,"compression_score":0.5084269662922207},"base_score":0.2728932584269448,"synergy_bonus":0,"final_score":0.2728932584269448,"primary_pattern":"strong_reclaim","all_patterns":["strong_reclaim"]},"mad":{"version":2,"phase":"accum","confidence":0.7197791553661304,"affinity":0,"reclaim":false,"range_quality_atr":5.898876404494225,"displacement_atr":0.23595505617955942,"acceptance_closes":0,"reason_code":"asia_box_accum"},"confluence":{"version":"v1","selected_stars":2,"v1_stars":2,"v2_stars":1,"v2_raw":8.887755102040817,"raw_factor_score":7.5,"zone_quality_score":1.3877551020408163,"mad_bonus":0,"factors":{"htf_aligned":true,"touches":1,"wick_rejection":false,"displacement_grade":false,"session_context":false,"structural_agreement":false,"fib_touch":true,"choch":false}}},"stop_envelope":{"floor_pips":12,"cap_pips":20,"desired_minimum_pips":12,"source":"strategy_default"}}}''')
CONFIRMED = json.loads(r'''{"event_id":"replay-opp_a15c02554e956989a5b38d10fdf148d83466d23368009276e52132e99568c663","event_type":"analysis.opportunity.v1","event_version":1,"occurred_at":1793339400,"produced_at":1793339400,"producer":"apexvoid-analysis-engine","correlation_id":"replay-opp_a15c02554e956989a5b38d10fdf148d83466d23368009276e52132e99568c663","config_version":3,"config_fingerprint":"f76fa2b7c3c439f5756dbd03f9d8c4302eb571a9f47030f2d9cc9c021d129312","payload":{"id":"opp_a15c02554e956989a5b38d10fdf148d83466d23368009276e52132e99568c663","strategy":"demand","symbol":"EURUSD","timeframe":"M5","structure_timeframe":"M15","structure":{"timeframe":"M15","low":1.11817,"high":1.119,"formed_at":1793317500},"direction":"BUY","entry":{"low":1.11817,"high":1.119},"invalidation":{"price":1.11806,"label":"demand_zone_invalidated"},"targets":[{"price":{"price":1.12029,"label":"nearest_opposing_structure"}}],"evidence":[{"code":"m5_demand_zone_confirmed"},{"code":"m5_demand_zone_rejection_confirmed"},{"code":"htf_zone_m15"},{"code":"m5_demand_zone_discount"},{"code":"stop_budget_floor"}],"quality":{"overall":0.21983011003332445,"components":{"deep_entry_pips":0,"entry_chase_atr":2.9136294553788855,"pd_position":0.41363636363635214,"quality_model_version":1,"room_r":1.3776215404665422,"room_reward_risk":1.3776215404665422,"score_entry_location":0,"score_rejection":0.5,"score_room":0.18881077023327109,"score_structure":0.1,"score_zone_departure":0,"score_zone_freshness":0.75,"score_zone_precision":0,"zone_touches":2,"zone_width_atr":3.900503948328856}},"algorithm_version":{"structure":"v2","liquidity":"v1"},"formed_at":1793339400,"created_at":1793339400,"expires_at":1793425800,"technical_context":{"atr":0.00020071428571428424,"reference_price":1.11838,"reference_time":1793339400,"bias":{"direction":"SELL","layer":"internal"},"higher_timeframes":[{"timeframe":"H1","direction":"SELL","layer":"intermediate","reference_time":1793332800}],"confirmation":{"zone_id":"technique:supply_demand:buy:1793317500@M15","touch_bar_time":1793339400,"confirmation_bar_time":1793339400,"reaction_type":"rejection","pattern":"engulfing"},"candle_evidence":{"version":2,"direction":"BUY","displacement":{"score":0.8072952943710232,"patterns":["strong_close","body_close","engulfing","strong_reclaim","displacement_candle"],"body_atr":0.7473309608534626,"range_atr":0.8469750889679951,"body_dominance":0.8823529411757023,"close_location":0.9411764705878511,"reclaim":true,"reclaim_depth_atr":1.0462633451948475,"engulfing":true,"engulfing_quality":0.6417112299453985},"indecision":{"doji":false,"spinning_top":false,"inside_bar":false,"body_fraction":0.8823529411757023,"compression_score":0.4706405693950031},"base_score":0.8072952943710232,"synergy_bonus":0,"final_score":0.8072952943710232,"primary_pattern":"strong_reclaim","all_patterns":["strong_close","body_close","engulfing","strong_reclaim","displacement_candle"]},"mad":{"version":2,"phase":"unclear","confidence":0,"affinity":0,"reclaim":false,"range_quality_atr":10.96085409252667,"displacement_atr":0.24911032028411878,"acceptance_closes":0,"reason_code":"no_mad_signature"},"confluence":{"version":"v1","selected_stars":2,"v1_stars":2,"v2_stars":1,"v2_raw":9.051020408163264,"raw_factor_score":7.5,"zone_quality_score":1.5510204081632653,"mad_bonus":0,"factors":{"htf_aligned":false,"touches":2,"wick_rejection":false,"displacement_grade":true,"session_context":false,"structural_agreement":false,"fib_touch":true,"choch":false}}},"stop_envelope":{"floor_pips":12,"cap_pips":20,"desired_minimum_pips":12,"source":"strategy_default"}}}''')

REQUIRED = "app.autotrade.go_opportunity_policy.candle_confirmation_required"


def match_of(raw: dict, *, required: bool):
  event = parse_analysis_event("analysis.opportunity.v1", json.dumps(raw))
  profile = go.REVIEWED_SCOPES[raw["payload"]["strategy"]]
  with mock.patch(REQUIRED, return_value=required):
    return go.build_strategy_match(event, profile=profile, now=raw["payload"]["created_at"] + 30)


def without_marker(raw: dict) -> dict:
  out = copy.deepcopy(raw)
  out["payload"]["evidence"] = [e for e in out["payload"]["evidence"] if e["code"] != go.CANDLE_CONFIRMATION_OPTIONAL_EVIDENCE]
  return out


class ConfigurationTests(unittest.TestCase):
  def test_the_checked_in_config_turns_the_requirement_off(self):
    self.assertIs(runtime_config.analysis.require_candle_confirmation, False)
    self.assertFalse(go.candle_confirmation_required())

  def test_an_absent_leaf_means_required(self):
    class Bare:
      pass

    class Runtime:
      analysis = Bare()

    with mock.patch("app.core.config.runtime_config", Runtime()):
      self.assertTrue(go.candle_confirmation_required())


class RequiredTests(unittest.TestCase):
  """require_candle_confirmation: true keeps the existing contract exactly."""

  def test_a_touch_only_setup_is_rejected(self):
    with self.assertRaises(go.AdapterRejection) as ctx:
      match_of(TOUCH, required=True)
    self.assertEqual(ctx.exception.code, "reaction_confirmation_unavailable")

  def test_a_confirmed_setup_is_accepted_with_its_reaction(self):
    match = match_of(CONFIRMED, required=True)
    self.assertEqual(match.reaction_type, "rejection")
    self.assertEqual(match.confirmation_bar_ts, str(CONFIRMED["payload"]["technical_context"]["confirmation"]["confirmation_bar_time"]))


class OptionalTests(unittest.TestCase):
  """require_candle_confirmation: false: a valid structural setup is not refused for the absence of a confirming candle."""

  def test_a_touch_only_setup_is_accepted_without_inventing_a_reaction(self):
    match = match_of(TOUCH, required=False)
    self.assertIsNone(match.reaction_type)
    self.assertIsNone(match.confirmation_bar_ts)
    self.assertIsNone(match.m5_confirmation_bar_ts)
    self.assertIsNone(match.m5_reaction_type)
    self.assertNotIn("go_reaction:rejection", match.tags)
    self.assertIn("go_evidence:" + go.CANDLE_CONFIRMATION_OPTIONAL_EVIDENCE, match.tags)

  def test_a_resting_observation_stays_non_executable(self):
    # No reaction and no Go marker: the resting-zone observation. The option must not make it executable.
    with self.assertRaises(go.AdapterRejection) as ctx:
      match_of(without_marker(TOUCH), required=False)
    self.assertEqual(ctx.exception.code, "reaction_confirmation_unavailable")

  def test_a_confirmed_setup_is_unchanged(self):
    on, off = match_of(CONFIRMED, required=True), match_of(CONFIRMED, required=False)
    self.assertEqual(on, off)

  def test_the_touch_only_setup_clears_policy_and_builds_a_plan(self):
    match = match_of(TOUCH, required=False)
    payload = TOUCH["payload"]
    symbol = payload["symbol"]
    pip = float(units.pip_size(symbol))
    cfg = _instrument_context(symbol, runtime_config)
    spot = payload["technical_context"]["reference_price"]
    decision = evaluate_execution_policy(
      match, spot_price=spot, regime="range", pip_size=pip, cfg=cfg, executable_quote=spot,
    )
    self.assertTrue(getattr(decision, "allowed", getattr(decision, "approved", False)), decision)
    plan = build_trade_plan_from_strategy_match(
      match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=Decimal(str(pip)), spot_price=spot, regime="range",
      cfg=cfg, executable_quote=spot, max_volume=100_000_000,
    )
    # Go still owns the geometry: the plan is on its symbol and its stop sits beyond its invalidation (BUY: at or below it).
    self.assertEqual(plan.symbol, symbol)
    self.assertEqual(payload["direction"], "BUY")
    self.assertLessEqual(plan.stop.price, Decimal(str(payload["invalidation"]["price"])))


if __name__ == "__main__":
  unittest.main()
