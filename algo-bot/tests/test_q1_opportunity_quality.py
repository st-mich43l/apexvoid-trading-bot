"""tools/q1_opportunity_quality.py: the arithmetic the Q1 conclusions rest on."""

import random

import pytest

from tools import q1_opportunity_quality as q1

pytestmark = pytest.mark.no_database


def _record(i, **over):
  base = {
    "id": f"opp_{i}", "symbol": "EURUSD", "strategy": "key_level", "direction": "BUY", "eval_time": 1_790_000_000 + i * 300,
    "observed_tf": "M5", "entry_low": 1.1000, "entry_high": 1.1004, "invalidation": 1.0990, "targets": [1.1040], "target_labels": ["fixed_reward_risk"],
    "ref_price": 1.1006, "atr": 0.0003, "formed_at": 1_790_000_000, "created_at": 1_790_000_000, "reaction_touch": 1_790_000_000 - 600,
    "reaction_confirm": 1_790_000_000 - 300, "executable": True, "tech_reward_pips": 36.0, "tech_risk_pips": 14.0, "components": {"reaction": 1.0, "confluence": 0.667},
    "evidence": ["a", "b"],
  }
  base.update(over)
  return base


def test_thin_keeps_one_opportunity_per_symbol_strategy_direction_and_hour():
  records = [_record(i) for i in range(10)] + [_record(20, eval_time=1_790_000_000 + 3 * 3600)] + [_record(30, direction="SELL")]
  kept = q1.thin(records)
  assert len(kept) == 3, "ten BUYs in one UTC hour collapse to one, the BUY in a later hour and the SELL stay"


def test_validity_flags_a_confirmation_after_the_closed_bar_and_price_beyond_the_invalidation():
  late = _record(1, reaction_confirm=1_790_000_000 + 10_000)
  broken = _record(2, ref_price=1.0985)
  out, examples = q1.validity([_record(0), late, broken])
  counts = out["key_level"]["counts"]
  assert counts["confirmation_after_the_closed_bar"] == 1
  assert counts["price_already_beyond_invalidation"] == 1
  assert examples["price_already_beyond_invalidation"][0]["id"] == "opp_2"
  assert out["key_level"]["constant_components"]["reaction"] == 1.0


def test_validity_reports_when_price_has_already_reached_the_first_target():
  out, _ = q1.validity([_record(0, ref_price=1.1045)])
  assert out["key_level"]["counts"]["price_already_at_first_target"] == 1


def test_bootstrap_interval_brackets_the_mean_and_needs_enough_clusters():
  rnd = random.Random(1)
  clusters = {i: [rnd.gauss(0.2, 0.5) for _ in range(5)] for i in range(40)}
  low, high = q1.boot(clusters)
  assert low < 0.2 < high
  assert q1.boot({1: [0.1], 2: [0.2]}) is None


def test_versus_random_pairs_each_opportunity_with_its_own_random_baseline():
  records, randoms = [], {}
  for i in range(60):
    r = _record(i * 12, eval_time=1_790_000_000 + i * 3600, strategy="demand")
    r["sim"] = {"ladder_cost": {"result": "stop", "r": -1.0}}
    records.append(r)
    randoms[r["id"]] = {"id": r["id"], "mean_r": -0.25}
  out = q1.versus_random(records, randoms)
  pooled = out["pooled"]["all"]
  assert pooled["n"] == 60 and pooled["mean_difference_r"] == pytest.approx(-0.75)
  assert set(out["by_strategy"]) == {"demand"}
