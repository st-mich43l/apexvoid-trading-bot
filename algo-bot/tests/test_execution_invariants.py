"""Planner invariants for every strategy on gold, both directions (P3).

* BUY and SELL are mirror images: reflect a BUY setup about a price and the plan comes
  back reflected, leg for leg, stop and targets included (one broker tick of rounding
  slack on a leg that lands exactly on a half tick).
* Every allowed plan keeps the stop beyond every leg, targets beyond the entry in order,
  the stop inside the envelope Go declared for that strategy, scalps at or under 2R with
  no trail, and structural zone strategies at or above the 50 pip floor.
"""

from __future__ import annotations

import random

import pytest

from app.analysis_client.provenance import GO_ORIGIN_TAG
from app.autotrade.execution_policy import SCALP_MAX_TARGET_R, evaluate_execution_policy
from tests.test_zone_scale_execution import _cfg, _policy_match

pytestmark = pytest.mark.no_database

CFG = _cfg()
MIRROR = 10_000.0
ENVELOPE = {"structural": (50, 70), "scalp": (15, 45), "m1": (12, 45)}
STRATEGIES = {
  "structural": [
    "Key Level", "Supply Demand", "Order Block", "FVG", "iFVG", "CRT", "Confluence Zone",
    "Flip Zone", "Session Level", "Trendline", "Break & Retest", "Liquidity Sweep",
    "Snap-Back", "Momentum Ride", "Box Breakout",
  ],
  "scalp": ["Range Edge Scalp", "Fade Scalp"],
  "m1": ["Range Sweep Scalp", "Impulse Pullback Scalp", "Breakout Retest Scalp"],
}
CASES = [(kind, name) for kind, names in STRATEGIES.items() for name in names]
PRICE_KEYS = (
  "planned_entry_price", "planned_leg_entry_prices", "planned_stop_price", "planned_target_prices",
)


def _plan(strategy: str, kind: str, direction: str, low: float, high: float, quote: float, invalidation: float):
  floor, cap = ENVELOPE[kind]
  match = _policy_match(
    strategy=strategy, symbol="XAU", direction=direction, entry_low=low, entry_high=high,
    current_price=quote, atr=4.0, structure_swing=None, tags=(GO_ORIGIN_TAG,),
    go_invalidation_price=invalidation, go_stop_envelope_floor_pips=float(floor),
    go_stop_envelope_cap_pips=float(cap), go_stop_envelope_desired_minimum_pips=float(floor),
    go_stop_envelope_source="test", targets_pips=(30, 60, 90, 120),
  )
  return evaluate_execution_policy(
    match, spot_price=quote, executable_quote=quote, regime="range", pip_size=0.1, cfg=CFG,
  )


def _geometry(rng: random.Random, kind: str):
  width = rng.choice([0.4, 1.3, 2.2, 3.1, 4.4, 5.0, 7.5])
  low = round(rng.uniform(4100, 4200) + 0.037, 2)
  high = round(low + width, 2)
  risk = rng.choice([4.2, 5.1, 5.6, 6.3, 7.4] if kind == "structural" else [0.9, 2.3, 3.9, 5.2, 6.4, 8.0])
  quote = round(rng.choice([low + 0.2, (low + high) / 2, high - 0.1, high + 0.4, low - 0.3]), 2)
  return low, high, risk, quote


def _reflect(value):
  if isinstance(value, list):
    return [_reflect(item) for item in value]
  return round(MIRROR - float(value), 2)


@pytest.mark.parametrize("kind,strategy", CASES)
def test_buy_and_sell_plans_are_mirror_images(kind, strategy):
  rng = random.Random(f"mirror:{strategy}")
  compared = 0
  for _ in range(40):
    low, high, risk, quote = _geometry(rng, kind)
    invalidation = round(low - risk + 0.013, 2)
    buy = _plan(strategy, kind, "BUY", low, high, quote, invalidation)
    sell = _plan(
      strategy, kind, "SELL", round(MIRROR - high, 2), round(MIRROR - low, 2),
      round(MIRROR - quote, 2), round(MIRROR - invalidation, 2),
    )
    assert (buy.allowed, buy.reason_code) == (sell.allowed, sell.reason_code)
    if not buy.allowed:
      continue
    compared += 1
    assert buy.measured["planned_execution_route"] == sell.measured["planned_execution_route"]
    assert buy.measured["planned_stop_pips"] == sell.measured["planned_stop_pips"]
    assert buy.measured.get("planned_target_r_multiples") == sell.measured.get("planned_target_r_multiples")
    for key in PRICE_KEYS:
      mirrored, actual = buy.measured.get(key), sell.measured.get(key)
      if mirrored is None:
        assert actual is None
        continue
      mirrored = _reflect([float(v) for v in mirrored] if isinstance(mirrored, list) else mirrored)
      actual = [float(v) for v in actual] if isinstance(actual, list) else round(float(actual), 2)
      if isinstance(mirrored, list):
        assert len(mirrored) == len(actual)
        assert all(abs(a - b) <= 0.011 for a, b in zip(mirrored, actual)), (key, mirrored, actual)
      else:
        assert abs(mirrored - actual) <= 0.011, (key, mirrored, actual)
  assert compared > 0, "no allowed case was compared"


@pytest.mark.parametrize("kind,strategy", CASES)
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_every_allowed_plan_keeps_its_invariants(kind, strategy, direction):
  rng = random.Random(f"invariants:{strategy}:{direction}")
  floor, cap = ENVELOPE[kind]
  buy = direction == "BUY"
  checked = 0
  for _ in range(60):
    low, high, risk, quote = _geometry(rng, kind)
    invalidation = round(low - risk + 0.013, 2) if buy else round(high + risk - 0.013, 2)
    evaluation = _plan(strategy, kind, direction, low, high, quote, invalidation)
    if not evaluation.allowed:
      continue
    checked += 1
    measured = evaluation.measured
    stop = float(measured["planned_stop_price"])
    legs = [float(v) for v in (measured.get("planned_leg_entry_prices") or [measured["planned_entry_price"]])]
    targets = [float(v) for v in measured.get("planned_target_prices") or []]
    stop_pips = float(measured["planned_stop_pips"])
    assert all((leg - stop) * (1 if buy else -1) > 0 for leg in legs), "stop must be beyond every leg"
    nearest = max(legs) if buy else min(legs)
    assert all((t - nearest) * (1 if buy else -1) > 0 for t in targets), "targets must be beyond the entry"
    assert targets == sorted(targets, reverse=not buy), "targets must progress away from the entry"
    assert floor - 0.05 <= stop_pips <= cap + 0.05, (stop_pips, floor, cap)
    if kind == "structural":
      assert stop_pips >= 50 - 0.05
    else:
      multiples = [float(v) for v in measured.get("planned_target_r_multiples", [])]
      assert not multiples or max(multiples) <= SCALP_MAX_TARGET_R
      assert not measured.get("planned_trail_after_target_id")
      assert not measured.get("execution_zone_expanded")
  assert checked > 0, "no allowed case was checked"
