"""FX1: FX plans are sized by risk (2% of equity); XAU keeps the owner equity table.

Run from ``algo-bot``:  ``python -m unittest tests.test_fx1_sizing``.
Until FX1 ``EffectiveInstrument`` had no ``sizing`` attribute, so the planner (``pack_risk_sizing``) never saw any pack's ``sizing`` block and every plan fell back to the
equity table x 1.5 (about 3% of equity per FX trade, rising as the account fell). The envelope below is a real production EURUSD Key Level opportunity (19 Nov 2026).
"""
from __future__ import annotations

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
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match, pack_risk_sizing  # noqa: E402
from app.core.config import runtime_config  # noqa: E402
from tests.test_kl12_strategy_targeting import PIP, XAU, build_plan, kl_match  # noqa: E402

EURUSD_ENVELOPE = '{"payload":{"id":"opp_a562a4c06b2b9c959677591d0350ff9668022c9cbceaea4a9f83b13badf64314","entry":{"low":1.09685,"high":1.0972},"symbol":"EURUSD","quality":{"overall":0.516208791208839,"components":{"level_kind":1.0,"score_room":0.0,"level_origin":-1.0,"level_age_bars":93.0,"level_band_atr":0.31502606360174873,"level_coverage":0.3025793650795417,"level_episodes":7.0,"deep_entry_pips":0.0,"entry_chase_atr":0.0,"score_htf_level":0.5,"score_rejection":0.4134615384618735,"score_structure":0.7,"effective_room_r":0.6107876200387811,"room_r_effective":0.6107876200387811,"room_reward_risk":0.9397418805376967,"htf_level_touches":2.0,"level_round_class":0.0,"effective_risk_pips":12.0,"closes_through_level":65.0,"closes_through_share":0.4744525547445255,"score_entry_location":1.0,"structural_risk_pips":7.799430452404277,"quality_model_version":2.0,"score_level_integrity":0.0,"score_level_significance":1.0,"entry_favourable_depth_atr":0.19392549667491496}},"targets":[{"price":{"label":"nearest_opposing_liquidity","price":1.09612}}],"evidence":[{"code":"m5_key_level_round"},{"code":"m5_key_level_touches_sufficient"},{"code":"m5_key_level_role_ambiguous"},{"code":"m5_key_level_rejection_confirmed"},{"code":"invalidation_beyond_reaction_extreme"},{"code":"key_level_reaction_swept"},{"code":"stop_budget_floor"}],"strategy":"key_level","direction":"SELL","execution":null,"formed_at":1795164000,"structure":{"low":1.09685,"high":1.0972,"swing":null,"formed_at":1795164000,"timeframe":"M5"},"timeframe":"M5","created_at":1795164000,"entry_plan":null,"expires_at":1795250400,"invalidation":{"label":"key_level_invalidated","price":1.09764},"recovered_at":null,"stop_envelope":{"source":"reaction_room","cap_pips":20.0,"floor_pips":12.0,"desired_minimum_pips":12.0},"algorithm_version":{"liquidity":"v1","structure":"v2"},"technical_context":{"atr":0.00039071428571429187,"mad":{"phase":"unclear","range":{"id":"asia:1795125600","low":1.09612,"high":1.099,"kind":"asia","bar_count":120,"sealed_at":1795161600,"timeframe":"M5","formed_from":1795125600,"expected_bars":120},"active":false,"reclaim":false,"version":3,"affinity":0.0,"relation":"unavailable","sequence":null,"direction":null,"timeframe":"M5","confidence":0.0,"sweep_side":null,"observed_at":1795164000,"reason_code":"no_mad_signature","evidence_ids":[],"displacement_atr":1.6380255941503843,"relation_reasons":["no_mad_signature"],"acceptance_closes":0,"range_quality_atr":7.371115173674457,"reclaim_depth_atr":null,"break_distance_atr":null,"sweep_penetration_atr":null},"bias":{"layer":"intermediate","direction":"BUY"},"confluence":{"v2_raw":8.5,"factors":{"choch":false,"touches":6,"fib_touch":true,"htf_aligned":false,"wick_rejection":false,"session_context":false,"displacement_grade":false,"structural_agreement":true},"version":"v1","v1_stars":2,"v2_stars":1,"fib_level":null,"mad_bonus":0.0,"grade_a_grab":null,"selected_stars":2,"raw_factor_score":8.5,"zone_quality_score":0.0},"confirmation":{"pattern":"sweep_reclaim","zone_id":"keylevel:round:1.09703","reaction_type":"rejection","touch_bar_time":1795164000,"confirmation_bar_time":1795164000},"reference_time":1795164000,"candle_evidence":{"version":2,"sequence":null,"direction":"SELL","rejection":{"score":0.45329670329685645,"sweep":true,"reclaim":true,"patterns":["sweep_reclaim"],"body_fraction":0.8307692307694988,"wick_fraction":0.0,"close_location":0.1692307692305012,"reclaim_depth_atr":0.7166361974407222,"sweep_penetration_atr":0.6654478976237712},"base_score":0.6420879120882415,"indecision":{"doji":false,"inside_bar":false,"spinning_top":false,"body_fraction":0.8307692307694988,"compression_score":0.0},"final_score":0.7620879120882414,"all_patterns":["sweep_reclaim","strong_close","body_close","strong_reclaim","displacement_candle"],"displacement":{"score":0.7679487179491649,"reclaim":true,"body_atr":1.3820840950644933,"patterns":["strong_close","body_close","strong_reclaim","displacement_candle"],"engulfing":false,"range_atr":1.6636197440585756,"body_dominance":0.8307692307694988,"close_location":0.1692307692305012,"engulfing_quality":null,"reclaim_depth_atr":0.7166361974407222},"synergy_bonus":0.12,"primary_pattern":"sweep_reclaim"},"reference_price":1.09692,"higher_timeframes":[{"layer":"intermediate","direction":"SELL","timeframe":"H1","reference_time":1795158000},{"layer":"internal","direction":"BUY","timeframe":"M15","reference_time":1795162500}]},"structure_timeframe":null},"event_id":"01a1f821-7190-7dd7-9617-d5bfe93e65b0","producer":"apexvoid-analysis-engine","event_type":"analysis.opportunity.v1","occurred_at":1795164000,"produced_at":1795164303,"causation_id":null,"event_version":1,"config_version":3,"correlation_id":"01a1f821-7190-7dd7-9617-d5bfe93e65b0","config_fingerprint":"45539a71aa9d869cd5ee62e909bab5ec"}'
FX = ("EURUSD", "GBPUSD", "USDJPY", "GBPJPY")


class SizingBlockTests(unittest.TestCase):
  def test_every_fx_pack_exposes_its_risk_sizing_to_the_planner(self):
    for symbol in FX:
      with self.subTest(symbol=symbol):
        ctx = _instrument_context(symbol, runtime_config)
        self.assertEqual((str(ctx.sizing.mode), float(ctx.sizing.risk_percent)), ("risk", 2.0))
        sized = pack_risk_sizing(ctx, account_equity=708.0, risk_leg=None, stop_price=Decimal("1.0"), pip_size=Decimal("0.0001"))
        self.assertEqual(sized, (Decimal("2.0"), Decimal("2.0")))

  def test_xau_declares_its_rule_but_keeps_the_equity_table(self):
    ctx = _instrument_context("XAU", runtime_config)
    self.assertEqual(str(ctx.sizing.mode), "risk")
    self.assertIs(ctx.sizing.apply, False)
    self.assertIsNone(pack_risk_sizing(ctx, account_equity=708.0, risk_leg=None, stop_price=Decimal("3900"), pip_size=Decimal("0.1")))


class PlanTests(unittest.TestCase):
  def test_a_real_fx_plan_is_risk_sized_at_two_percent(self):
    event = parse_analysis_event("analysis.opportunity.v1", EURUSD_ENVELOPE)
    payload = event.payload
    match = go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["key_level"], now=payload.created_at + 30)
    ctx = _instrument_context("EURUSD", runtime_config)
    quote = (payload.entry.low + payload.entry.high) / 2
    for equity in (None, 708.0, 1500.0):
      with self.subTest(equity=equity):
        kwargs = {} if equity is None else {"account_equity": equity}
        plan = build_trade_plan_from_strategy_match(
          match, plan_id="p", setup_id="s", thesis_id="t", pip_size=0.0001, spot_price=quote, regime="trend", cfg=ctx, executable_quote=quote,
          max_volume=100_000_000, **kwargs,
        )
        self.assertEqual(plan.sizing.mode, "risk")
        self.assertEqual(plan.risk.risk_percent, Decimal("2.0"))
        self.assertEqual(plan.risk.max_group_risk_percent, Decimal("2.0"))

  def test_an_xau_plan_is_unchanged_equity_table(self):
    match, spot = kl_match()
    plan = build_plan(match, spot)
    self.assertEqual(plan.sizing.mode, "equity_table")
    self.assertEqual((plan.risk.risk_percent, plan.risk.risk_multiplier), (Decimal("1.0"), Decimal("1.0")))


if __name__ == "__main__":
  unittest.main()
