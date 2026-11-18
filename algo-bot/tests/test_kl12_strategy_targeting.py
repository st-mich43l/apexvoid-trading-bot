"""KL12: XAU Key Level closes everything at 1R, through a strategy-scoped exit ladder (``overrides.execution.targeting_by_strategy``).

Run from ``algo-bot``:  ``python -m unittest tests.test_kl12_strategy_targeting``  (stdlib only; needs the app's runtime dependencies).
The Key Level envelope is a real replayed XAU opportunity from the Go engine (the same fixture ``test_scalp_stop_cap`` uses).
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
from app.autotrade.execution_policy import evaluate_execution_policy  # noqa: E402
from app.autotrade.strategy_taxonomy import M1_SCALP_STRATEGIES  # noqa: E402
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match  # noqa: E402
from app.core import instrument_geometry  # noqa: E402
from app.core.config import runtime_config  # noqa: E402
from app.persistence import store  # noqa: E402
from app.runtime import instruments  # noqa: E402
from app.runtime.instruments import EffectiveInstrument, EffectiveInstrumentError, InstrumentTargetMode  # noqa: E402
from tests.test_scalp_stop_cap import PIP, TEMPLATES, XAU  # noqa: E402

LIVE_LADDER = ((1.0, 2.0, 3.0, 4.0), (0.4, 0.2, 0.2, 0.2), 1.0, 4.0)
FX_LADDER = ((1.0, 2.0), (0.5, 0.5), 1.0, 2.0)
ONE_R = ((1.0,), (1.0,), None, 1.0)


def ladder(targeting) -> tuple:
  return (tuple(targeting.target_r_multiples), tuple(targeting.close_ratios), targeting.breakeven_after_r, targeting.reward_risk)


def kl_match():
  raw = copy.deepcopy(TEMPLATES["kl"])
  payload = raw["payload"]
  worst = payload["entry"]["high"]
  payload["invalidation"]["price"] = round(worst - 6.5, 2)  # a 65-pip structural stop, inside XAU's 50-70 envelope
  payload["targets"] = [{"price": {"price": round(worst + 19.5, 2), "label": "nearest_opposing_liquidity"}}]
  event = parse_analysis_event("analysis.opportunity.v1", json.dumps(raw))
  match = go.build_strategy_match(event, profile=go.REVIEWED_SCOPES["key_level"], now=payload["created_at"] + 30)
  return match, payload["technical_context"]["reference_price"]


def build_plan(match, spot):
  return build_trade_plan_from_strategy_match(
    match, plan_id="plan", setup_id="setup", thesis_id="thesis", pip_size=PIP, spot_price=spot, regime="trend", cfg=XAU, executable_quote=spot,
    max_volume=100_000_000,
  )


class ResolutionTests(unittest.TestCase):
  def test_xau_key_level_resolves_the_one_r_ladder_under_either_name(self):
    for name in ("Key Level", "key_level", "KEY LEVEL"):
      with self.subTest(name=name):
        self.assertEqual(ladder(instrument_geometry.technique_fixed_rr_targeting("XAU", name, runtime_config)), ONE_R)

  def test_every_other_xau_strategy_keeps_the_instrument_ladder_exactly(self):
    for name in ("Supply Demand", "Confluence Zone", "Break & Retest", "Order Block", "FVG", "iFVG", "Flip Zone", "Round Number", "Trendline"):
      with self.subTest(name=name):
        self.assertEqual(ladder(instrument_geometry.technique_fixed_rr_targeting("XAU", name, runtime_config)), LIVE_LADDER)
    # no strategy named: the instrument's own targeting, untouched
    self.assertEqual(ladder(instrument_geometry.technique_fixed_rr_targeting("XAU", None, runtime_config)), LIVE_LADDER)

  def test_key_level_on_fx_is_unchanged(self):
    for symbol in ("EURUSD", "GBPUSD", "USDJPY", "GBPJPY"):
      for name in ("Key Level", "key_level", "Supply Demand"):
        with self.subTest(symbol=symbol, name=name):
          self.assertEqual(ladder(instrument_geometry.technique_fixed_rr_targeting(symbol, name, runtime_config)), FX_LADDER)

  def test_the_scalp_book_never_reads_a_technique_override(self):
    self.assertTrue(M1_SCALP_STRATEGIES)
    for name in sorted(M1_SCALP_STRATEGIES):
      with self.subTest(name=name):
        self.assertIsNone(instrument_geometry.technique_fixed_rr_targeting("XAU", name, runtime_config))

  def test_the_override_keeps_every_other_targeting_leaf_of_the_instrument(self):
    base = instruments.for_instrument(runtime_config, "XAU").targeting
    scoped = instrument_geometry.technique_fixed_rr_targeting("XAU", "Key Level", runtime_config)
    self.assertEqual(scoped.mode, InstrumentTargetMode.FIXED_RR)
    self.assertEqual(getattr(scoped, "entry_clips", None), getattr(base, "entry_clips", None))


class ValidationTests(unittest.TestCase):
  BASE = {"mode": InstrumentTargetMode.FIXED_RR, "reward_risk": 4.0, "target_r_multiples": (1.0, 2.0, 3.0, 4.0), "close_ratios": (0.4, 0.2, 0.2, 0.2),
          "breakeven_after_r": 1.0, "entry_clips": 3}

  def table(self, **block):
    good = {"mode": "fixed_rr", "reward_risk": 2.0, "target_r_multiples": [1.0, 2.0], "close_ratios": [0.5, 0.5], "breakeven_after_r": 1.0}
    good.update(block)
    return instruments._scoped_targeting_table("XAU", self.BASE, {"key_level": good})

  def test_a_valid_block_resolves_over_the_instrument(self):
    resolved = self.table()["key_level"]
    self.assertEqual((resolved["target_r_multiples"], resolved["close_ratios"], resolved["breakeven_after_r"]), ((1.0, 2.0), (0.5, 0.5), 1.0))
    self.assertEqual(resolved["entry_clips"], 3)  # inherited
    self.assertEqual(self.table(breakeven_after_r=None)["key_level"]["breakeven_after_r"], None)
    self.assertEqual(instruments._scoped_targeting_table("XAU", self.BASE, None), {})  # absent is safe

  def test_invalid_blocks_are_rejected_at_load(self):
    cases = {
      "ratios do not sum to 1": dict(close_ratios=[0.5, 0.4]),
      "ratios over 1": dict(close_ratios=[0.6, 0.6]),
      "reward_risk is not the last multiple": dict(reward_risk=3.0),
      "length mismatch": dict(close_ratios=[1.0]),
      "multiples not ascending": dict(target_r_multiples=[2.0, 1.0], reward_risk=1.0),
      "non-positive multiple": dict(target_r_multiples=[0.0, 2.0]),
      "non-positive ratio": dict(close_ratios=[1.0, 0.0]),
      "negative breakeven": dict(breakeven_after_r=-1.0),
      "unknown leaf": dict(trail_after_r=2.0),
      "not fixed_rr": dict(mode="ladder_pips"),
      "NaN": dict(reward_risk=float("nan")),
      "bad entry_clips": dict(entry_clips=0),
    }
    for name, block in cases.items():
      with self.subTest(name):
        with self.assertRaises(EffectiveInstrumentError):
          self.table(**block)
    with self.assertRaises(EffectiveInstrumentError):  # a scoped ladder needs a fixed_rr instrument
      instruments._scoped_targeting_table("XAU", {**self.BASE, "mode": InstrumentTargetMode.LADDER_PIPS}, {"key_level": {
        "mode": "fixed_rr", "reward_risk": 1.0, "target_r_multiples": [1.0], "close_ratios": [1.0], "breakeven_after_r": None}})

  def test_an_invalid_block_fails_the_instrument_build(self):
    declaration = instruments._instrument_declaration(runtime_config, "XAU")
    bad = copy.deepcopy(declaration[1])
    bad["overrides"]["execution"]["targeting_by_strategy"]["key_level"]["close_ratios"] = [0.9]
    with mock.patch.object(instruments, "_instrument_declaration", return_value=(declaration[0], bad)):
      with self.assertRaises(EffectiveInstrumentError):
        EffectiveInstrument(runtime_config, "XAU")


class PlanTests(unittest.TestCase):
  def test_the_xau_key_level_plan_has_one_target_at_1r_and_no_breakeven(self):
    match, spot = kl_match()
    self.assertEqual(match.strategy, "Key Level")
    policy = evaluate_execution_policy(match, spot_price=spot, regime="trend", pip_size=0.1, cfg=XAU, executable_quote=spot)
    self.assertTrue(policy.allowed, policy.reason_code)
    self.assertEqual(policy.measured["planned_target_r_multiples"], ["1.0"])
    self.assertEqual(policy.measured["planned_target_close_ratios"], ["1.0"])
    self.assertIsNone(policy.measured["breakeven_after_r"])
    plan = build_plan(match, spot)
    self.assertEqual([(t.target_id, t.close_ratio) for t in plan.targets], [("TP1", Decimal("1.0"))])
    self.assertIsNone(plan.management.be_after_target_id)
    # TP1 is exactly one R from the plan's worst entry to its stop
    legs = [Decimal(str(leg.price)) for leg in plan.entry.legs]
    stop = Decimal(str(plan.stop.price))
    worst = max(legs) if plan.stop.price < min(legs) else min(legs)
    self.assertEqual(abs(plan.targets[0].price - worst), abs(worst - stop))
    # the entry, the stop and the risk leg are the ones the live plan has: only the exit ladder differs
    with mock.patch.object(EffectiveInstrument, "targeting_for_strategy", return_value=None):
      baseline_match, baseline_spot = kl_match()
      baseline = build_plan(baseline_match, baseline_spot)
    self.assertEqual([(t.target_id, t.close_ratio) for t in baseline.targets], [("TP1", Decimal("0.4")), ("TP2", Decimal("0.2")), ("TP3", Decimal("0.2")), ("TP4", Decimal("0.2"))])
    self.assertEqual(baseline.management.be_after_target_id, "TP1")
    self.assertEqual(baseline.entry, plan.entry)
    self.assertEqual(baseline.stop, plan.stop)

  def test_a_completed_one_target_group_is_labelled_tp1_full(self):
    # exit_path: the executor reports planned_reward_risk 1.0 for a one-target plan; two or more targets report 2.0.
    derive = store._derive_exit_path
    event = {"type": "group_result", "reason_code": "take_profit"}
    self.assertEqual(derive(event, booked_tp_count=1, break_even_applied=False, planned_reward_risk=1.0), "tp1_full")
    self.assertEqual(derive(event, booked_tp_count=1, break_even_applied=False, planned_reward_risk=2.0), "tp1_stop")
    self.assertEqual(derive(event, booked_tp_count=1, break_even_applied=True, planned_reward_risk=2.0), "tp1_be")
    self.assertEqual(derive(event, booked_tp_count=1, break_even_applied=False), "tp1_stop")  # unknown plan shape: the old label
    self.assertEqual(derive(event, booked_tp_count=0, break_even_applied=False, planned_reward_risk=1.0), "full_stop")
    self.assertEqual(derive(event, booked_tp_count=2, break_even_applied=False, planned_reward_risk=2.0), "tp2_full")


if __name__ == "__main__":
  unittest.main()
