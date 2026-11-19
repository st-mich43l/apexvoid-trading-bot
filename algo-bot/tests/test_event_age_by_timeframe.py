"""Trendline M15 events are no longer refused as ``event_too_old`` (the age limit is per timeframe).

Run from ``algo-bot``:  ``python -m unittest tests.test_event_age_by_timeframe``  (needs ``fakeredis`` for the end-to-end cases; the unit cases do not).
Production 11-18 Nov 2026: 22 of 23 M15 Trendline events were refused ``event_too_old`` with ``event_age_seconds`` 901-909 against ``max_event_age_seconds`` 900, because an
observation is stamped with the OPEN time of its bar and published when the bar closes. The envelope below is a real one (USDJPY M15 SELL, 16 Nov 07:30 UTC).
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault(
  "APEXVOID_CONFIG_FILE",
  os.path.join(os.path.dirname(__file__), "..", "..", "config", "apexvoid.demo.yml"),
)

from app.analysis_client import freshness  # noqa: E402
from app.analysis_client.freshness import FreshnessLimits, evaluate_freshness, limits_for_timeframe  # noqa: E402

try:
  import fakeredis.aioredis as fake_aioredis

  HAVE_FAKEREDIS = True
except ImportError:  # pragma: no cover
  HAVE_FAKEREDIS = False

LIMITS = FreshnessLimits(900, 300)
M15_ENVELOPE = '{"payload":{"id":"opp_b4c624605aef413379f233d159c88c8858bc2faed2ac9d837534ccfac78c7924","entry":{"low":159.53,"high":159.571},"symbol":"USDJPY","quality":{"overall":0.4495064446497014,"components":{"score_room":0.0,"barrier_ahead":1.0,"line_span_bars":23.0,"score_approach":0.0,"entry_chase_atr":1.253474075609048,"room_structural":1.0,"score_rejection":0.0,"score_structure":1.0,"effective_room_r":0.45019841269859423,"room_reward_risk":3.9306040644408813,"target_room_pips":22.30000000000416,"barrier_room_pips":8.103571428575812,"timeframe_minutes":15.0,"validation_touches":2.0,"available_room_pips":8.103571428575812,"effective_risk_pips":18.0,"score_entry_location":0.19722073951276153,"score_line_integrity":1.0,"structural_risk_pips":5.673428214697651,"approach_distance_atr":0.29844620847856024,"confirmation_age_bars":2.0,"quality_model_version":2.0,"score_line_validation":1.0,"line_slope_atr_per_bar":0.13394342762060524,"room_limited_by_barrier":1.0,"score_line_significance":0.3988308176848497,"line_violations_weighted":0.0,"reaction_at_second_anchor":0.0,"entry_favourable_depth_atr":0.0}},"targets":[{"price":{"label":"nearest_opposing_liquidity","price":159.316}}],"evidence":[{"code":"m15_trendline_causal_anchors"},{"code":"candle_confirmation_optional"},{"code":"stop_budget_floor"}],"strategy":"trendline","direction":"SELL","execution":{"model":"single_price","price":159.539,"proximity":0.010052062699327173,"timeframe":"M15","valid_until":1794816900,"projected_at":1794815100,"slope_per_bar":-0.011500000000012278},"formed_at":1794812400,"structure":null,"timeframe":"M15","created_at":1794814200,"expires_at":1794816900,"invalidation":{"label":"trendline_close_violation","price":159.596},"recovered_at":null,"stop_envelope":{"source":"reaction_room","cap_pips":28.0,"floor_pips":18.0,"desired_minimum_pips":21.0},"algorithm_version":{"liquidity":"v1","structure":"v2"},"technical_context":{"atr":0.10478571428571724,"mad":{"phase":"unclear","range":{"id":"asia:1794780000","low":159.362,"high":159.888,"kind":"asia","bar_count":39,"sealed_at":null,"timeframe":"M15","formed_from":1794780000,"expected_bars":40},"active":false,"reclaim":false,"version":3,"affinity":0.0,"relation":"unavailable","sequence":null,"direction":null,"timeframe":"M15","confidence":0.0,"sweep_side":null,"observed_at":1794814200,"reason_code":"asia_range_forming","evidence_ids":[],"displacement_atr":0.6966598500338639,"relation_reasons":["asia_range_forming"],"acceptance_closes":0,"range_quality_atr":5.019768234492119,"reclaim_depth_atr":null,"break_distance_atr":null,"sweep_penetration_atr":null},"bias":{"layer":"intermediate","direction":"SELL"},"confluence":{"v2_raw":9.5,"factors":{"choch":false,"touches":4,"fib_touch":true,"htf_aligned":true,"wick_rejection":false,"session_context":false,"displacement_grade":false,"structural_agreement":false},"version":"v1","v1_stars":2,"v2_stars":1,"fib_level":null,"mad_bonus":0.0,"grade_a_grab":null,"selected_stars":2,"raw_factor_score":9.5,"zone_quality_score":0.0},"confirmation":null,"reference_time":1794814200,"candle_evidence":{"version":2,"sequence":null,"direction":"SELL","rejection":null,"base_score":0.29613156100887716,"indecision":{"doji":true,"inside_bar":false,"spinning_top":false,"body_fraction":0.040816326530801646,"compression_score":0.4154737559644913},"final_score":0.29613156100887716,"all_patterns":["strong_reclaim"],"displacement":{"score":0.29613156100887716,"reclaim":true,"body_atr":0.038173142467802186,"patterns":["strong_reclaim"],"engulfing":false,"range_atr":0.9352419904568139,"body_dominance":0.0,"close_location":0.3469387755103639,"engulfing_quality":null,"reclaim_depth_atr":1.5078391274708631},"synergy_bonus":0.0,"primary_pattern":"strong_reclaim"},"reference_price":159.413,"higher_timeframes":[{"layer":"internal","direction":"SELL","timeframe":"H1","reference_time":1794808800}]},"structure_timeframe":"M15"},"event_id":"01a1e351-1a52-7617-bcea-ede977382256","producer":"apexvoid-analysis-engine","event_type":"analysis.opportunity.v1","occurred_at":1794814200,"produced_at":1794815106,"causation_id":null,"event_version":1,"config_version":3,"correlation_id":"01a1e351-1a52-7617-bcea-ede977382256","config_fingerprint":"d3a20296261a1854af0620a1bfe7f608"}'


def verdict(age: int, timeframe: str, lag: int = 1):
  return evaluate_freshness(
    observed_at=1_000_000, expires_at=1_000_000 + 10_000, produced_at=1_000_000 + age - lag, published_at=1_000_000 + age - lag,
    consumed_at=1_000_000 + age, limits=limits_for_timeframe(LIMITS, timeframe),
  )


class LimitTests(unittest.TestCase):
  def test_m5_and_shorter_timeframes_are_unchanged(self):
    for timeframe in ("M5", "M1", "m5", None, "", "UNKNOWN"):
      with self.subTest(timeframe=timeframe):
        self.assertEqual(limits_for_timeframe(LIMITS, timeframe), LIMITS)
    self.assertTrue(verdict(900, "M5").ok)
    self.assertEqual(verdict(901, "M5").code, freshness.TOO_OLD)

  def test_m15_gets_its_extra_bar_length_so_the_grace_after_the_bar_closed_is_the_same(self):
    self.assertEqual(limits_for_timeframe(LIMITS, "M15"), FreshnessLimits(1500, 300))
    for age in (901, 903, 908, 909):  # the ages production refused
      with self.subTest(age=age):
        self.assertTrue(verdict(age, "M15").ok)
    self.assertTrue(verdict(1500, "M15").ok)
    self.assertEqual(verdict(1501, "M15").code, freshness.TOO_OLD)  # a backlog is still refused
    self.assertEqual(1500 - 900, 900 - 300)  # 600 s of grace after the bar closed, on both timeframes

  def test_only_the_age_limit_moves_the_other_limits_still_refuse(self):
    self.assertEqual(verdict(909, "M15", lag=301).code, freshness.LAG_EXCEEDED)  # delivery lag unchanged
    expired = evaluate_freshness(
      observed_at=1_000_000, expires_at=1_000_500, produced_at=1_000_900, published_at=1_000_900, consumed_at=1_000_909,
      limits=limits_for_timeframe(LIMITS, "M15"),
    )
    self.assertEqual(expired.code, freshness.EXPIRED)  # the engine's own expiry is unchanged


@unittest.skipUnless(HAVE_FAKEREDIS, "fakeredis is not installed")
class AdmissionTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.client = fake_aioredis.FakeRedis(decode_responses=False)

  async def asyncTearDown(self):
    await self.client.aclose()

  async def adapt(self, age: int):
    from app.analysis_client.models import parse_analysis_event
    from app.analysis_client.repository import LifecycleResult
    from app.autotrade.go_opportunity_policy import GoOpportunityPolicy

    event = parse_analysis_event("analysis.opportunity.v1", M15_ENVELOPE)
    decisions = []

    class Repository:
      async def opportunity_state(self, opportunity_id):
        return "active"

      async def record_shadow_decision(self, **kwargs):
        decisions.append(kwargs)

    now = event.payload.created_at + age
    policy = GoOpportunityPolicy(Repository(), client_factory=lambda: self.client, clock=lambda: now, freshness_limits=lambda: LIMITS)
    outcome = await policy.on_creation(event, LifecycleResult("created", event.payload.id), published_at=now)
    return outcome, decisions[-1]

  async def test_a_real_m15_trendline_event_that_production_refused_is_now_adapted(self):
    outcome, decision = await self.adapt(903)  # production: event_too_old, event_age_seconds 903 > 900
    self.assertEqual((outcome, decision["outcome"], decision["reason"]), ("match_written", "match_written", "go_live"))
    self.assertEqual(decision["details"]["max_event_age_seconds"], 1500)

  async def test_a_stale_m15_event_is_still_refused(self):
    outcome, decision = await self.adapt(1501)
    self.assertEqual((outcome, decision["reason"]), ("not_adapted", "event_too_old"))


if __name__ == "__main__":
  unittest.main()
